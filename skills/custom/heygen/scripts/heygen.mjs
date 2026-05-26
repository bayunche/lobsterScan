#!/usr/bin/env node
/**
 * HeyGen Skill — local implementation against HeyGen V2 REST API.
 *
 * Docs: https://docs.heygen.com/reference/create-an-avatar-video-v2
 *
 * Subcommands:
 *   avatars  list|search                    — GET /v2/avatars
 *   voices   list|search                    — GET /v2/voices
 *   video    generate|status|wait           — POST /v2/video/generate · GET /v1/video_status.get
 *   account  check                          — credential probe (uses list-voices as ping)
 *
 * Auth: env HEYGEN_API_KEY → header `x-api-key`
 *
 * Design notes:
 * - HeyGen 不像 Kling 自带本地凭据持久化;key 每次从 env 读,业务后端通过
 *   AGENT_ENV_MAP 注入到 video-producer subprocess。
 * - list endpoint 返回较大数组 (~3000+ avatars / ~1500+ voices),`search`
 *   走本地过滤(--gender / --language / --name),不再调 API。
 * - video_url 7 天过期 → `video wait --download` 直接落盘到 output_dir,
 *   避免后续 ffmpeg 找不到文件。
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import process from 'node:process';

const API_BASE = process.env.HEYGEN_API_BASE || 'https://api.heygen.com';
const POLL_INTERVAL_MS = 8_000;
const POLL_MAX_ITERATIONS = 75;          // 75 * 8s = 10 min hard cap

// ─────────────────────────────────────────────────────────────
// CLI plumbing
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

function apiKey() {
  const k = process.env.HEYGEN_API_KEY;
  if (!k) return null;
  return k;
}

// ─────────────────────────────────────────────────────────────
// http
// ─────────────────────────────────────────────────────────────

async function httpJson(method, path, body) {
  const key = apiKey();
  if (!key) {
    emit({ ok: false, error: 'no_credentials', hint: 'export HEYGEN_API_KEY' }, 2);
  }
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const res = await fetch(url, {
    method,
    headers: {
      'x-api-key': key,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let payload = {};
  const text = await res.text();
  try { payload = text ? JSON.parse(text) : {}; }
  catch { payload = { raw: text.slice(0, 500) }; }
  return { status: res.status, payload };
}

async function downloadTo(url, outPath) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, buf);
  return buf.length;
}

// HeyGen v2 envelope:  { error: null|{}, data: {...} }
// v1 envelope:         { code: "100", data: {...}, message: "..." }
function checkEnvelope(status, payload, opLabel) {
  if (status !== 200) {
    return { ok: false, error: `http_${status}`, op: opLabel, detail: payload };
  }
  if (payload.error && (payload.error.code || payload.error.message)) {
    return { ok: false, error: 'heygen_api', op: opLabel, ...payload.error };
  }
  return { ok: true, data: payload.data || {} };
}

// ─────────────────────────────────────────────────────────────
// avatars / voices  (read-only, no charges)
// ─────────────────────────────────────────────────────────────

async function loadAvatars() {
  const { status, payload } = await httpJson('GET', '/v2/avatars');
  return checkEnvelope(status, payload, 'list_avatars');
}

async function loadVoices() {
  const { status, payload } = await httpJson('GET', '/v2/voices');
  return checkEnvelope(status, payload, 'list_voices');
}

function filterAvatars(avatars, flags) {
  let xs = avatars;
  if (flags.gender)       xs = xs.filter(a => (a.gender || '').toLowerCase() === String(flags.gender).toLowerCase());
  if (flags.name) {
    const needle = String(flags.name).toLowerCase();
    xs = xs.filter(a => (a.avatar_name || '').toLowerCase().includes(needle));
  }
  if (flags.premium === 'true')  xs = xs.filter(a => a.premium === true);
  if (flags.premium === 'false') xs = xs.filter(a => a.premium === false);
  return xs;
}

function filterVoices(voices, flags) {
  let xs = voices;
  if (flags.language) {
    const needle = String(flags.language).toLowerCase();
    xs = xs.filter(v => (v.language || '').toLowerCase().includes(needle));
  }
  if (flags.gender) xs = xs.filter(v => (v.gender || '').toLowerCase() === String(flags.gender).toLowerCase());
  if (flags.name) {
    const needle = String(flags.name).toLowerCase();
    xs = xs.filter(v => (v.name || '').toLowerCase().includes(needle));
  }
  if (flags.emotion === 'true')  xs = xs.filter(v => v.emotion_support === true);
  if (flags.support_pause === 'true')  xs = xs.filter(v => v.support_pause === true);
  return xs;
}

async function cmdAvatars(action, flags) {
  if (action === 'list' || action === 'search') {
    const r = await loadAvatars();
    if (!r.ok) return emit(r, 1);
    const avatars = filterAvatars(r.data.avatars || [], flags);
    const talkingPhotos = r.data.talking_photos || [];
    const limit = Math.max(1, parseInt(flags.limit || '50', 10));
    return emit({
      ok: true,
      count_avatars: avatars.length,
      count_talking_photos: talkingPhotos.length,
      avatars: avatars.slice(0, limit).map(a => ({
        avatar_id: a.avatar_id, name: a.avatar_name,
        gender: a.gender, premium: a.premium,
        preview_image_url: a.preview_image_url,
        default_voice_id: a.default_voice_id,
      })),
      talking_photos: talkingPhotos.slice(0, limit).map(t => ({
        talking_photo_id: t.talking_photo_id, name: t.talking_photo_name,
        preview_image_url: t.preview_image_url,
      })),
    });
  }
  return emit({ ok: false, error: 'unknown_action', subcommand: 'avatars', action }, 1);
}

async function cmdVoices(action, flags) {
  if (action === 'list' || action === 'search') {
    const r = await loadVoices();
    if (!r.ok) return emit(r, 1);
    const voices = filterVoices(r.data.voices || [], flags);
    const limit = Math.max(1, parseInt(flags.limit || '50', 10));
    return emit({
      ok: true,
      count: voices.length,
      voices: voices.slice(0, limit).map(v => ({
        voice_id: v.voice_id, name: v.name,
        language: v.language, gender: v.gender,
        emotion_support: v.emotion_support,
        support_pause: v.support_pause,
        preview_audio: v.preview_audio,
      })),
    });
  }
  return emit({ ok: false, error: 'unknown_action', subcommand: 'voices', action }, 1);
}

// ─────────────────────────────────────────────────────────────
// video
// ─────────────────────────────────────────────────────────────

function buildVideoBody(flags) {
  // 默认场景:单 scene、avatar (or talking_photo) + 文本 TTS + 白底
  const character = flags.talking_photo_id
    ? {
        type: 'talking_photo',
        talking_photo_id: String(flags.talking_photo_id),
        scale: flags.scale !== undefined ? Number(flags.scale) : 1,
        ...(flags.talking_photo_style ? { talking_photo_style: String(flags.talking_photo_style) } : {}),
      }
    : {
        type: 'avatar',
        avatar_id: String(flags.avatar_id),
        avatar_style: String(flags.avatar_style || 'normal'),
        scale: flags.scale !== undefined ? Number(flags.scale) : 1,
      };

  // matting / expression / talking_style / Avatar IV
  if (flags.matting === 'true' || flags.matting === true) character.matting = true;
  if (flags.expression)    character.expression = String(flags.expression);
  if (flags.talking_style) character.talking_style = String(flags.talking_style);
  if (flags.use_avatar_iv === 'true' || flags.use_avatar_iv === true) {
    character.use_avatar_iv_model = true;
    if (flags.motion_prompt) character.prompt = String(flags.motion_prompt);
  }

  // voice — text 模式最常用
  let voice;
  if (flags.audio_url) {
    voice = { type: 'audio', audio_url: String(flags.audio_url) };
  } else if (flags.audio_asset_id) {
    voice = { type: 'audio', audio_asset_id: String(flags.audio_asset_id) };
  } else if (flags.silence_seconds) {
    voice = { type: 'silence', duration: String(flags.silence_seconds) };
  } else {
    voice = {
      type: 'text',
      voice_id: String(flags.voice_id),
      input_text: String(flags.input_text || flags.text || ''),
    };
    if (flags.speed !== undefined)   voice.speed = Number(flags.speed);
    if (flags.pitch !== undefined)   voice.pitch = parseInt(flags.pitch, 10);
    if (flags.emotion)               voice.emotion = String(flags.emotion);
    if (flags.locale)                voice.locale = String(flags.locale);
  }

  // background
  let background;
  if (flags.background_image_url) {
    background = { type: 'image', url: String(flags.background_image_url),
                   fit: String(flags.background_fit || 'cover') };
  } else if (flags.background_video_url) {
    background = { type: 'video', url: String(flags.background_video_url),
                   play_style: String(flags.background_play_style || 'loop') };
  } else {
    background = { type: 'color', value: String(flags.background_color || '#FFFFFF') };
  }

  const body = {
    video_inputs: [{ character, voice, background }],
    dimension: {
      width:  parseInt(flags.width  || '1920', 10),
      height: parseInt(flags.height || '1080', 10),
    },
  };
  if (flags.title)       body.title = String(flags.title);
  if (flags.callback_id) body.callback_id = String(flags.callback_id);
  if (flags.callback_url) body.callback_url = String(flags.callback_url);
  if (flags.caption === 'true' || flags.caption === true) body.caption = true;
  return body;
}

async function videoGenerate(flags) {
  // 输入校验:必须有 (avatar_id 或 talking_photo_id) + (input_text/text 或 audio_*)
  if (!flags.avatar_id && !flags.talking_photo_id) {
    return emit({ ok: false, error: 'missing_avatar_or_talking_photo_id',
                  hint: 'pass --avatar_id or --talking_photo_id (use `avatars list` to discover)' }, 1);
  }
  const hasVoiceInput = flags.input_text || flags.text || flags.audio_url ||
                        flags.audio_asset_id || flags.silence_seconds;
  if (!hasVoiceInput) {
    return emit({ ok: false, error: 'missing_voice_input',
                  hint: 'pass --text "..." with --voice_id, or --audio_url, or --silence_seconds' }, 1);
  }
  if ((flags.input_text || flags.text) && !flags.voice_id) {
    return emit({ ok: false, error: 'missing_voice_id',
                  hint: 'pass --voice_id (use `voices list --language Chinese` to discover)' }, 1);
  }

  const body = buildVideoBody(flags);
  const { status, payload } = await httpJson('POST', '/v2/video/generate', body);
  const env = checkEnvelope(status, payload, 'generate_video');
  if (!env.ok) return env;
  const videoId = env.data.video_id;
  if (!videoId) return { ok: false, error: 'no_video_id', payload };
  return { ok: true, video_id: videoId };
}

async function videoStatus(videoId) {
  // status 是 v1 endpoint(HeyGen 官方混用)
  const { status, payload } = await httpJson('GET',
    `/v1/video_status.get?video_id=${encodeURIComponent(videoId)}`);
  if (status !== 200) return { ok: false, error: `http_${status}`, op: 'video_status', detail: payload };
  // v1 envelope: { code, data, message }
  const data = payload.data || {};
  if (data.error && data.error.code) {
    return { ok: false, error: 'heygen_video', ...data.error, status: data.status };
  }
  return { ok: true, data };
}

async function videoPollAndDownload(videoId, outDir, opts = {}) {
  for (let i = 0; i < POLL_MAX_ITERATIONS; i++) {
    await sleep(POLL_INTERVAL_MS);
    const q = await videoStatus(videoId);
    if (!q.ok) {
      // 404 等错误不重试,直接出
      return q;
    }
    const s = q.data.status;
    if (s === 'completed') {
      if (!opts.download) {
        return {
          ok: true, video_id: videoId, status: s,
          video_url: q.data.video_url,
          thumbnail_url: q.data.thumbnail_url,
          duration: q.data.duration,
        };
      }
      const url = q.data.video_url;
      if (!url) return { ok: false, error: 'no_video_url', video_id: videoId };
      const outFile = join(outDir, `${videoId}.mp4`);
      try {
        const bytes = await downloadTo(url, outFile);
        return {
          ok: true, video_id: videoId, status: s,
          path: outFile, bytes,
          duration: q.data.duration,
          thumbnail_url: q.data.thumbnail_url,
        };
      } catch (e) {
        return { ok: false, error: 'download_failed', detail: String(e), video_id: videoId };
      }
    }
    if (s === 'failed') {
      return {
        ok: false, error: 'heygen_fail', video_id: videoId,
        heygen_error: q.data.error,
      };
    }
    // pending / processing → 继续
  }
  return { ok: false, error: 'timeout', video_id: videoId };
}

async function cmdVideo(action, flags) {
  const outDir = resolve(String(flags.output_dir || './output'));
  if (action === 'generate') {
    const sub = await videoGenerate(flags);
    if (!sub.ok) return emit(sub, sub.error === 'quota_exhausted' ? 3 : 1);
    if (flags.no_wait) {
      return emit({ ok: true, video_id: sub.video_id, wait: false });
    }
    mkdirSync(outDir, { recursive: true });
    const r = await videoPollAndDownload(sub.video_id, outDir,
      { download: !(flags.no_download) });
    return emit(r, r.ok ? 0 : (r.error === 'timeout' ? 4 : 1));
  }
  if (action === 'status') {
    if (!flags.video_id) return emit({ ok: false, error: 'missing_video_id' }, 1);
    const r = await videoStatus(String(flags.video_id));
    return emit(r.ok ? { ok: true, ...r.data } : r, r.ok ? 0 : 1);
  }
  if (action === 'wait') {
    if (!flags.video_id) return emit({ ok: false, error: 'missing_video_id' }, 1);
    mkdirSync(outDir, { recursive: true });
    const r = await videoPollAndDownload(String(flags.video_id), outDir,
      { download: !!(flags.download) });
    return emit(r, r.ok ? 0 : (r.error === 'timeout' ? 4 : 1));
  }
  return emit({ ok: false, error: 'unknown_action', subcommand: 'video', action }, 1);
}

// ─────────────────────────────────────────────────────────────
// account
// ─────────────────────────────────────────────────────────────

async function cmdAccount(action) {
  if (action === 'check' || action === undefined || action === 'status') {
    if (!apiKey()) {
      return emit({ ok: false, error: 'no_credentials', source: null }, 2);
    }
    // 调一次 list-voices 当 ping(轻量,只读)
    const r = await loadVoices();
    if (!r.ok) return emit({ ok: false, error: 'ping_failed', op: 'list_voices', ...r }, 1);
    return emit({ ok: true, source: 'env', voices_count: (r.data.voices || []).length });
  }
  return emit({ ok: false, error: 'unknown_action', subcommand: 'account', action }, 1);
}

// ─────────────────────────────────────────────────────────────
// main
// ─────────────────────────────────────────────────────────────

async function main() {
  const [sub, action, ...rest] = process.argv.slice(2);
  const { flags } = parseArgs(rest);

  if (!sub || sub === '--help' || sub === '-h') {
    process.stdout.write([
      'heygen.mjs — HeyGen V2 REST API skill',
      'Usage:',
      '  node heygen.mjs avatars  list   [--gender female] [--name Anna] [--limit 50]',
      '  node heygen.mjs voices   list   [--language Chinese] [--gender female] [--emotion true]',
      '  node heygen.mjs video    generate --avatar_id <id> --voice_id <id> --text "..." --output_dir ./output',
      '                                   [--talking_photo_id <id>] [--background_color #FFFFFF]',
      '                                   [--width 1920] [--height 1080] [--no_wait] [--no_download]',
      '                                   [--emotion Excited] [--speed 1] [--use_avatar_iv true]',
      '  node heygen.mjs video    status  --video_id <id>',
      '  node heygen.mjs video    wait    --video_id <id> [--download]',
      '  node heygen.mjs account  check',
      '',
      'Env:  HEYGEN_API_KEY  [HEYGEN_API_BASE]',
    ].join('\n') + '\n');
    process.exit(0);
  }

  try {
    if (sub === 'avatars') return await cmdAvatars(action, flags);
    if (sub === 'voices')  return await cmdVoices(action, flags);
    if (sub === 'video')   return await cmdVideo(action, flags);
    if (sub === 'account') return await cmdAccount(action);
    return emit({ ok: false, error: 'unknown_subcommand', subcommand: sub }, 1);
  } catch (e) {
    return emit({ ok: false, error: 'unexpected', detail: String(e?.stack || e) }, 1);
  }
}

main();
