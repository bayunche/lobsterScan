#!/usr/bin/env node
/**
 * Kling AI — local-equivalent CLI for clawhub slug `klingai-dev/klingai`.
 *
 * Subcommands implemented:
 *   video    — text2video / image2video / 任务查询 (--task_id --download)
 *   account  — credentials 管理 (--import-env / --import-credentials / --check)
 *
 * Auth precedence (matches upstream):
 *   1. env KLING_TOKEN (session-only)
 *   2. credentials 文件 KLING_STORAGE_ROOT/credentials.json (默认 ~/.config/kling)
 *   3. 失败:打印 "no credentials, run: kling account --import-env" 并退出 2
 *
 * JWT 本地签 HS256(node:crypto,无第三方依赖)。
 *
 * 注意 — 这是 ABI 兼容的本地实现,不是 clawhub 上 klingai-dev/klingai 官方包。
 * 用户跑 `openclaw doctor --fix` + `openclaw skills install klingai-dev/klingai`
 * 后,可用官方版本无缝替换本目录(SKILL.md / scripts/kling.mjs)。
 */

import { createHmac } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import process from 'node:process';

// 国内可灵开放平台(Kuaishou)= api-beijing.klingai.com — 默认值。
// 国际版 = api.klingai.com,用 env KLING_API_BASE 覆盖即可。AK/SK 跟 region 绑定,跨区会 401(code 1002 Auth failed)。
const API_BASE = process.env.KLING_API_BASE || 'https://api-beijing.klingai.com';
const POLL_INTERVAL_MS = 10_000;   // upstream 是 ~10s
const POLL_MAX_ITERATIONS = 60;    // 10 分钟 hard cap

// ─── canonical model 列表(来自 clawhub 官方 doc;别名 omni3/o3 等仅在 routing layer 内部展开)
const MODELS = {
  't2v_default': 'kling-v3',
  'i2v_default': 'kling-v3',
  'omni_default': 'kling-v3-omni',
  'allowed': ['kling-v3', 'kling-v3-omni', 'kling-v2-6', 'kling-video-o1', 'kling-image-o1'],
};

// ─────────────────────────────────────────────────────────────
// utils
// ─────────────────────────────────────────────────────────────

function emit(obj, exitCode = 0) {
  process.stdout.write(JSON.stringify(obj) + '\n');
  process.exit(exitCode);
}

function parseArgs(argv) {
  const out = { _: [], flags: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) {
        out.flags[key] = true;
      } else {
        out.flags[key] = next;
        i++;
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

function b64url(buf) {
  return Buffer.from(buf).toString('base64')
    .replace(/=+$/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function makeJwt(accessKey, secretKey, ttlSec = 1800) {
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const now = Math.floor(Date.now() / 1000);
  const payload = b64url(JSON.stringify({ iss: accessKey, exp: now + ttlSec, nbf: now - 5 }));
  const sig = b64url(createHmac('sha256', secretKey).update(`${header}.${payload}`).digest());
  return `${header}.${payload}.${sig}`;
}

// ─────────────────────────────────────────────────────────────
// credentials
// ─────────────────────────────────────────────────────────────

function credentialsPath() {
  const root = process.env.KLING_STORAGE_ROOT || join(homedir(), '.config', 'kling');
  return join(root, 'credentials.json');
}

function loadCredentials() {
  // 优先 env(KLING_TOKEN > AK/SK 直接 inject > stored file)
  if (process.env.KLING_TOKEN) {
    return { source: 'env_token', token: process.env.KLING_TOKEN };
  }
  if (process.env.KLING_ACCESS_KEY_ID && process.env.KLING_SECRET_ACCESS_KEY) {
    return {
      source: 'env_ak_sk',
      access_key_id: process.env.KLING_ACCESS_KEY_ID,
      secret_access_key: process.env.KLING_SECRET_ACCESS_KEY,
    };
  }
  const path = credentialsPath();
  if (existsSync(path)) {
    try {
      const j = JSON.parse(readFileSync(path, 'utf8'));
      if (j.access_key_id && j.secret_access_key) {
        return { source: 'file', ...j };
      }
    } catch { /* fall through */ }
  }
  return null;
}

function saveCredentials(ak, sk) {
  const path = credentialsPath();
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  writeFileSync(path, JSON.stringify({ access_key_id: ak, secret_access_key: sk }, null, 2),
    { mode: 0o600 });
  return path;
}

function tokenFromCredentials(creds) {
  if (creds.source === 'env_token') return creds.token;
  return makeJwt(creds.access_key_id, creds.secret_access_key);
}

// ─────────────────────────────────────────────────────────────
// http
// ─────────────────────────────────────────────────────────────

async function httpJson(method, url, token, body, opts = {}) {
  // 加重试 — 走 fake-IP 代理时 TCP 握手偶尔会失败,3 次 backoff(0.5/1.5/3s)
  const maxAttempts = opts.maxAttempts || 3;
  const timeoutMs = opts.timeoutMs || 15000;
  let lastErr = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const ctl = new AbortController();
    const tid = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        method,
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctl.signal,
      });
      clearTimeout(tid);
      let payload = {};
      try { payload = await res.json(); } catch { payload = { raw: '<unparseable>' }; }
      return { status: res.status, payload, attempts: attempt };
    } catch (e) {
      clearTimeout(tid);
      lastErr = e;
      const isTransient = e?.name === 'AbortError' || /timeout|ECONNRESET|ETIMEDOUT|ENETUNREACH|fetch failed/i.test(String(e));
      if (attempt < maxAttempts && isTransient) {
        await sleep(500 * (2 ** (attempt - 1)));   // 0.5s, 1.5s
        continue;
      }
      throw e;
    }
  }
  throw lastErr;
}

async function downloadTo(url, outPath) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, buf);
  return buf.length;
}

// ─────────────────────────────────────────────────────────────
// video
// ─────────────────────────────────────────────────────────────

function pickVideoEndpoint(flags) {
  if (flags.image || flags.image_tail) return '/v1/videos/image2video';
  return '/v1/videos/text2video';
}

function pickVideoModel(flags) {
  if (flags.model) {
    if (!MODELS.allowed.includes(flags.model)) {
      emit({ ok: false, error: 'invalid_model', model: flags.model, allowed: MODELS.allowed }, 1);
    }
    return flags.model;
  }
  // omni 触发条件:多图(--image 含逗号) / --element_ids / --video / --aspect_ratio + Omni-only flag
  if (flags.element_ids || flags.video || (flags.image && String(flags.image).includes(','))) {
    return MODELS.omni_default;
  }
  return flags.image ? MODELS.i2v_default : MODELS.t2v_default;
}

function buildVideoBody(flags) {
  const model = pickVideoModel(flags);
  const body = {
    model_name: model,
    prompt: flags.prompt,
    mode: flags.mode || 'pro',
    duration: String(flags.duration || 5),
    aspect_ratio: flags.aspect_ratio || '16:9',
  };
  if (flags.cfg_scale !== undefined) body.cfg_scale = Number(flags.cfg_scale);
  if (flags.negative_prompt) body.negative_prompt = flags.negative_prompt;
  if (flags.sound) body.sound = flags.sound;
  if (flags.image) body.image = flags.image;
  if (flags.image_tail) body.image_tail = flags.image_tail;
  if (flags.element_ids) body.element_ids = flags.element_ids;
  if (flags.video) body.video = flags.video;
  if (flags.video_refer_type) body.video_refer_type = flags.video_refer_type;
  return { body, model };
}

// server 401/403 时把 body 里 code/message/request_id 拼到顶级 hint,方便排查
function _hintFromAuthError(status, payload) {
  if (status !== 401 && status !== 403) return null;
  const code = payload?.code;
  const msg = payload?.message || payload?.msg;
  const rid = payload?.request_id;
  const parts = [];
  if (code !== undefined) parts.push(`code=${code}`);
  if (msg) parts.push(`message="${msg}"`);
  if (rid) parts.push(`request_id=${rid}`);
  let next = '';
  if (code === 1002 || /auth/i.test(String(msg))) {
    next = ' — 1002/Auth failed 通常意味着:(1) AK/SK 拼错,(2) 账号未开通 API 服务(去 console > API 管理 申请),(3) AK/SK 在错 region(国际/国内账号)。带 request_id 去 console 工单可定位';
  }
  return `kling auth ${status}: ${parts.join(', ')}${next}`;
}

async function videoSubmit(token, endpoint, body) {
  const { status, payload } = await httpJson('POST', `${API_BASE}${endpoint}`, token, body);
  if (status !== 200) {
    const hint = _hintFromAuthError(status, payload);
    return { ok: false, error: `http_${status}`, hint, detail: payload };
  }
  if (payload.code !== 0 && payload.code !== '0') {
    return {
      ok: false, error: 'kling_submit',
      code: payload.code, message: payload.message,
      request_id: payload.request_id,
    };
  }
  const data = payload.data || {};
  const taskId = data.task_id || data.id;
  if (!taskId) return { ok: false, error: 'no_task_id', payload };
  return { ok: true, task_id: taskId };
}

async function videoQuery(token, taskId, endpoint) {
  const queryUrl = `${API_BASE}${endpoint}/${encodeURIComponent(taskId)}`;
  const { status, payload } = await httpJson('GET', queryUrl, token);
  if (status !== 200) {
    const hint = _hintFromAuthError(status, payload);
    return { ok: false, error: `http_${status}`, hint, detail: payload };
  }
  if (payload.code !== 0 && payload.code !== '0') {
    return { ok: false, error: 'kling_query', code: payload.code, message: payload.message,
             request_id: payload.request_id };
  }
  return { ok: true, data: payload.data || {} };
}

async function videoPollAndDownload(token, taskId, endpoint, outDir) {
  for (let i = 0; i < POLL_MAX_ITERATIONS; i++) {
    await sleep(POLL_INTERVAL_MS);
    const q = await videoQuery(token, taskId, endpoint);
    if (!q.ok) continue;
    const status = q.data.task_status;
    if (status === 'succeed') {
      const videos = (q.data.task_result || {}).videos || [];
      if (videos.length === 0) {
        return { ok: false, error: 'no_video_in_result', task_id: taskId };
      }
      const url = videos[0].url;
      if (!url) return { ok: false, error: 'no_video_url', task_id: taskId };
      const outFile = join(outDir, `${taskId}.mp4`);
      try {
        const bytes = await downloadTo(url, outFile);
        return {
          ok: true, task_id: taskId, path: outFile, bytes,
          duration: q.data.task_result?.videos?.[0]?.duration,
        };
      } catch (e) {
        return { ok: false, error: 'download_failed', detail: String(e), task_id: taskId };
      }
    }
    if (status === 'failed') {
      return {
        ok: false, error: 'kling_fail', task_id: taskId,
        task_status_msg: q.data.task_status_msg,
      };
    }
    // submitted / processing → keep polling
  }
  return { ok: false, error: 'timeout', task_id: taskId };
}

async function cmdVideo(flags) {
  const creds = loadCredentials();
  if (!creds) {
    return emit({
      ok: false, error: 'no_credentials',
      hint: 'export KLING_ACCESS_KEY_ID and KLING_SECRET_ACCESS_KEY, or run: node kling.mjs account --import-env',
    }, 2);
  }
  const token = tokenFromCredentials(creds);
  const outDir = resolve(String(flags.output_dir || './output'));
  mkdirSync(outDir, { recursive: true });

  // 查询模式 — --task_id 且不传 prompt/image/video
  if (flags.task_id) {
    const endpoint = pickVideoEndpoint(flags);
    if (flags.download) {
      const r = await videoPollAndDownload(token, flags.task_id, endpoint, outDir);
      return emit(r, r.ok ? 0 : 1);
    }
    const r = await videoQuery(token, flags.task_id, endpoint);
    if (!r.ok) return emit(r, 1);
    return emit({ ok: true, ...r.data }, 0);
  }

  if (!flags.prompt && !flags.image && !flags.video) {
    return emit({ ok: false, error: 'missing_prompt_or_image' }, 1);
  }

  const endpoint = pickVideoEndpoint(flags);
  const { body, model } = buildVideoBody(flags);
  const sub = await videoSubmit(token, endpoint, body);
  if (!sub.ok) return emit({ ...sub, model, endpoint }, sub.error === 'quota_exhausted' ? 3 : 1);

  if (flags.no_wait) {
    return emit({ ok: true, task_id: sub.task_id, model, endpoint, wait: false }, 0);
  }

  const r = await videoPollAndDownload(token, sub.task_id, endpoint, outDir);
  emit({ ...r, model, endpoint }, r.ok ? 0 : (r.error === 'timeout' ? 4 : 1));
}

// ─────────────────────────────────────────────────────────────
// account
// ─────────────────────────────────────────────────────────────

async function cmdAccount(flags) {
  if (flags['import-env']) {
    const ak = process.env.KLING_ACCESS_KEY_ID;
    const sk = process.env.KLING_SECRET_ACCESS_KEY;
    if (!ak || !sk) {
      return emit({
        ok: false, error: 'env_missing',
        missing: [!ak && 'KLING_ACCESS_KEY_ID', !sk && 'KLING_SECRET_ACCESS_KEY'].filter(Boolean),
      }, 2);
    }
    const path = saveCredentials(ak, sk);
    return emit({ ok: true, source: 'env', path }, 0);
  }
  if (flags['import-credentials']) {
    const ak = flags.access_key_id;
    const sk = flags.secret_access_key;
    if (!ak || !sk) return emit({ ok: false, error: 'need_ak_sk_flags' }, 1);
    const path = saveCredentials(String(ak), String(sk));
    return emit({ ok: true, source: 'flag', path }, 0);
  }
  if (flags.check || flags.status) {
    const creds = loadCredentials();
    if (!creds) {
      return emit({
        ok: false, source: null, credentials_path: credentialsPath(),
        has_ak: false,
        hint: 'no credentials — run: kling account --import-env (with KLING_ACCESS_KEY_ID + KLING_SECRET_ACCESS_KEY)',
      }, 2);
    }
    // 真 ping API — 查询历史任务 list(read-only,不消耗 quota)
    let token;
    try { token = tokenFromCredentials(creds); }
    catch (e) {
      return emit({ ok: false, source: creds.source, error: 'jwt_sign_failed',
                    detail: String(e?.message || e) }, 2);
    }
    const probeUrl = `${API_BASE}/v1/videos/text2video?pageNum=1&pageSize=1`;
    try {
      const { status, payload, attempts } = await httpJson('GET', probeUrl, token, undefined,
        { maxAttempts: 3, timeoutMs: 10000 });
      const out = {
        ok: status === 200 && (payload?.code === 0 || payload?.code === '0'),
        source: creds.source,
        api_base: API_BASE,
        http_status: status,
        server_code: payload?.code,
        server_message: payload?.message || payload?.msg,
        request_id: payload?.request_id,
        attempts,
      };
      if (status === 401 || status === 403) {
        out.hint = _hintFromAuthError(status, payload);
      } else if (status === 200 && (payload?.code === 0 || payload?.code === '0')) {
        out.hint = 'kling API auth ok · 账号已开通 API 服务';
        out.task_count_visible = (payload?.data?.length) || 0;
      } else if (status === 200) {
        out.hint = `server returned 200 但 code=${payload?.code}: ${payload?.message || '?'}`;
      }
      return emit(out, out.ok ? 0 : 2);
    } catch (e) {
      return emit({
        ok: false, source: creds.source, api_base: API_BASE,
        error: 'network_unreachable',
        hint: 'TCP/HTTP 完全打不到 Kling 服务,看是不是代理(域名解析到 198.18.x.x 但握手失败)/ DNS / firewall',
        detail: String(e?.message || e),
      }, 4);
    }
  }
  // bind-url / configure 未实现 — 提示用户走 env 路径
  return emit({
    ok: false, error: 'subcommand_not_implemented',
    hint: 'this local equivalent supports only --import-env / --import-credentials / --check. For bind URL flow, install the official package: openclaw skills install klingai-dev/klingai',
  }, 1);
}

// ─────────────────────────────────────────────────────────────
// main
// ─────────────────────────────────────────────────────────────

async function main() {
  const [sub, ...rest] = process.argv.slice(2);
  const { flags } = parseArgs(rest);

  if (!sub || sub === '--help' || sub === '-h') {
    process.stdout.write([
      'kling.mjs — Kling AI CLI (clawhub klingai-dev/klingai ABI-compatible local equivalent)',
      'Usage:',
      '  node kling.mjs video   --prompt "<text>" [--image <path>] [--duration 5] [--model kling-v3] [--mode pro] [--output_dir ./output]',
      '  node kling.mjs video   --task_id <id> --download',
      '  node kling.mjs account --import-env',
      '  node kling.mjs account --check',
      '',
      'Env:  KLING_ACCESS_KEY_ID  KLING_SECRET_ACCESS_KEY  [KLING_TOKEN]  [KLING_STORAGE_ROOT]  [KLING_API_BASE]',
    ].join('\n') + '\n');
    process.exit(0);
  }

  try {
    if (sub === 'video')   return await cmdVideo(flags);
    if (sub === 'account') return await cmdAccount(flags);
    if (sub === 'image' || sub === 'element') {
      return emit({
        ok: false, error: 'subcommand_not_implemented', subcommand: sub,
        hint: 'this local equivalent currently implements `video` and `account`. Install official package: openclaw skills install klingai-dev/klingai',
      }, 1);
    }
    return emit({ ok: false, error: 'unknown_subcommand', subcommand: sub }, 1);
  } catch (e) {
    return emit({ ok: false, error: 'unexpected', detail: String(e?.stack || e) }, 1);
  }
}

main();
