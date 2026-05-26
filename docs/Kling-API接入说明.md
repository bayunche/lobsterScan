# Kling AI 接入说明

> 配套：`docs/数字人接入.md` §四
> 目标读者：维护 `skills/custom/klingai/scripts/kling.mjs` 或排查 Kling 视频生成失败的工程师
> 关键文件：`skills/custom/klingai/scripts/kling.mjs`

> ⚠️ 本目录是 clawhub `klingai-dev/klingai` 包的**本地 ABI 兼容实现**。用户后续可以跑 `openclaw doctor --fix` + `openclaw skills install klingai-dev/klingai` 用官方包无缝覆盖本目录。

---

## 一、国内 vs 国际域名（最大坑点）

```js
// kling.mjs:30
const API_BASE = process.env.KLING_API_BASE || 'https://api-beijing.klingai.com';
```

| 域名 | 区域 | 账号 |
| --- | --- | --- |
| `https://api-beijing.klingai.com` | **国内可灵开放平台（快手）** | 国内账号 |
| `https://api.klingai.com` | 国际版 | 国际账号 |

**AK/SK 跟 region 绑定，跨区会 401 `code 1002 Auth failed`**。

> ⚠️ 早期版本默认走国际域名，国内 AK/SK 一直 401。fix 是把默认 API_BASE 改成国内 + 通过 `KLING_API_BASE` env 覆盖来切国际。account check 真 ping 后才能发现这种错。

---

## 二、Auth：JWT HS256（无第三方依赖）

```js
function makeJwt(accessKey, secretKey, ttlSec = 1800) {
  const header  = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const now     = Math.floor(Date.now() / 1000);
  const payload = b64url(JSON.stringify({
    iss: accessKey,
    exp: now + ttlSec,    // 30 分钟
    nbf: now - 5,         // 容时偏移 5s
  }));
  const sig = b64url(createHmac('sha256', secretKey).update(`${header}.${payload}`).digest());
  return `${header}.${payload}.${sig}`;
}
```

- 算法：HS256（HMAC-SHA256）— 用 SK 当 HMAC key 签 `${header}.${payload}`。
- payload 三字段：`iss` = AK，`exp` = now+1800，`nbf` = now-5（**容时偏移**，避开 server 时钟漂移 5s 内的拒签）。
- HTTP header：`Authorization: Bearer <jwt>`。
- 每个请求都重新签 — TTL 30min 够长，但 retry 链中重签开销可忽略。

### Credentials 优先级（loadCredentials）

```
1. env KLING_TOKEN              · session-only，最高优先级（CI 注入预签 JWT 用）
2. env KLING_ACCESS_KEY_ID + KLING_SECRET_ACCESS_KEY  · 常态路径
3. file KLING_STORAGE_ROOT/credentials.json (默认 ~/.config/kling/credentials.json)
4. 都没 → exit 2 'no_credentials'
```

`account --import-env` 把 env AK/SK 落到 credentials.json（mode 0600），适合本地开发；CI 推荐直接 env 注入不落盘。

---

## 三、CLI subcommand

只实现了 `video` 和 `account`，其它（`image` / `element`）走"not_implemented"提示用户装官方包。

### video 子命令

```bash
# 提交并等结果（默认）
node kling.mjs video \
  --prompt "<text>" \
  [--image <url|path>] [--image_tail <url|path>] [--element_ids <ids>] \
  [--duration 5] [--mode pro] [--aspect_ratio 16:9] \
  [--model kling-v3] [--cfg_scale 0.5] [--negative_prompt "<text>"] \
  [--sound true] [--output_dir ./output] \
  [--no_wait]

# 仅查询已提交任务
node kling.mjs video --task_id <id> [--download]
```

模型选择逻辑（`pickVideoModel`）：

```
flags.model 指定 → 必须在 allowed 列表 (kling-v3 / kling-v3-omni / kling-v2-6 / kling-video-o1 / kling-image-o1)
否则 →
  element_ids / video / 多图 (image 含逗号) → kling-v3-omni
  flags.image                                 → i2v_default = kling-v3
  纯文本                                       → t2v_default = kling-v3
```

endpoint：`POST /v1/videos/text2video` 或 `/v1/videos/image2video`（按 image/image_tail flag）。

### account 子命令

```bash
node kling.mjs account --import-env                       # env → credentials.json
node kling.mjs account --import-credentials \
                       --access_key_id AK --secret_access_key SK
node kling.mjs account --check                            # 真 ping API
```

`--check` 不是空查 credentials，而是**真发 GET `/v1/videos/text2video?pageNum=1&pageSize=1`**（read-only，不消耗配额）。output 字段：

```json
{
  "ok": true,
  "source": "env_ak_sk",
  "api_base": "https://api-beijing.klingai.com",
  "http_status": 200,
  "server_code": 0,
  "server_message": "SUCCESS",
  "request_id": "Cgo...",
  "attempts": 1,
  "hint": "kling API auth ok · 账号已开通 API 服务",
  "task_count_visible": 1
}
```

`hint` 字段非常关键 — 见下文错误码节。

---

## 四、HTTP retry + timeout

```js
// kling.mjs:134
async function httpJson(method, url, token, body, opts = {}) {
  const maxAttempts = opts.maxAttempts || 3;
  const timeoutMs   = opts.timeoutMs   || 15000;   // 单次 15s
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const ctl = new AbortController();
    const tid = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { method, headers: {...}, body, signal: ctl.signal });
      clearTimeout(tid);
      return { status, payload, attempts: attempt };
    } catch (e) {
      const isTransient = e?.name === 'AbortError'
        || /timeout|ECONNRESET|ETIMEDOUT|ENETUNREACH|fetch failed/i.test(String(e));
      if (attempt < maxAttempts && isTransient) {
        await sleep(500 * (2 ** (attempt - 1)));   // 0.5s / 1.5s 指数退避
        continue;
      }
      throw e;
    }
  }
}
```

为啥要 retry：用户机器走 fake-IP 代理时 TCP 握手偶尔会失败（域名解析到 198.18.x.x 但握手 timeout）— 1 次失败就 abort 用户体验很差。3 次 backoff 通常够。

POST 也会重试（语义上 Kling submit 不幂等，但失败前没产生 task_id，重试只会得到独立的新 task — 不会导致重复消耗配额）。

---

## 五、错误码 hint

`_hintFromAuthError(status, payload)` 把 401/403 错误格式化成人话：

```js
// kling.mjs:223
if (code === 1002 || /auth/i.test(String(msg))) {
  next = ' — 1002/Auth failed 通常意味着：' +
         '(1) AK/SK 拼错，' +
         '(2) 账号未开通 API 服务（去 console > API 管理 申请），' +
         '(3) AK/SK 在错 region（国际/国内账号）。' +
         '带 request_id 去 console 工单可定位';
}
```

常见 server code：

| code | 含义 | 怎么处理 |
| --- | --- | --- |
| 0 / "0" | 成功 | — |
| 1002 | Auth failed | 见上 hint 三条 |
| 1102 | account_balance_not_enough | 充值或换 PAYG 账号 |
| 1303 | request resource is overload | retry / 错峰 |
| 5000 | server internal | retry，超 3 次工单 |

每个错误响应里 `request_id` 字段必须保留 — 去 Kling console 提工单这是定位依据。

### CLI exit code 约定

业务后端 `pipeline.py` 按 exit code 判定降级路径：

| exit | 含义 | pipeline 行为 |
| --- | --- | --- |
| 0 | ok | — |
| 1 | 一般失败（network / parse / submit error） | degraded=true |
| 2 | no_credentials / env_missing | degraded=true / degrade_reason=no_credentials |
| 3 | quota_exhausted | degraded=true / degrade_reason=quota_exhausted |
| 4 | timeout | degraded=true / degrade_reason=timeout |

---

## 六、API 提交 body schema 速查

```json
POST /v1/videos/text2video
{
  "model_name":      "kling-v3",
  "prompt":          "<text>",
  "mode":            "pro",          // pro / std
  "duration":        "5",            // 字符串！不是数字
  "aspect_ratio":    "16:9",         // 16:9 / 9:16 / 1:1
  "cfg_scale":       0.5,            // optional
  "negative_prompt": "<text>",       // optional
  "sound":           true,           // optional, omni 模式才有
  "image":           "<url|base64>", // i2v 必填
  "image_tail":      "<url|base64>", // 收尾帧，optional
  "element_ids":     "<ids>",        // omni 多图模式
  "video":           "<url|base64>", // v2v 模式
  "video_refer_type": "..."
}
```

response：

```json
{
  "code": 0,
  "message": "SUCCESS",
  "request_id": "Cgo...",
  "data": {
    "task_id": "...",
    "task_status": "submitted"
  }
}
```

查询：`GET /v1/videos/text2video/{task_id}` →

```json
{
  "code": 0,
  "data": {
    "task_status": "succeed",     // submitted / processing / succeed / failed
    "task_status_msg": "...",
    "task_result": {
      "videos": [{ "url": "https://...", "duration": "5" }]
    }
  }
}
```

### Polling

```js
const POLL_INTERVAL_MS = 10_000;   // 10s 一次
const POLL_MAX_ITERATIONS = 60;    // 10 分钟 hard cap
```

终态字符串：`succeed` / `failed` / `partial`（早期 poll 脚本只匹配 success/failed/done/error，漏掉 `partial` 会一直 poll 到 timeout — fix 已加上）。

---

## 七、调试 checklist

Kling 出问题先按这个顺序：

1. **`account --check` 能不能 ok**：返回 `http_status=200 + server_code=0` 才算真通。`http_status=200 + server_code=非零` 是 API 业务错（账号未开通常见）。
2. **region 对没对**：`api_base` 字段对比账号注册区。国内账号必须 `api-beijing`，国际账号必须 `api.klingai.com`。
3. **request_id 留没留**：不管成功失败都打 log；工单全靠它。
4. **网络通不通**：error=`network_unreachable` 时 hint 会提示「域名解析到 198.18.x.x 但握手失败」— 这是代理拦截，看 DNS / firewall / 代理白名单。
5. **JWT 时钟漂移**：`nbf: now - 5` 是兜底，但 server-client 时钟差超 5s 时还是会 401。`date` 看下系统时间是不是飘了。
6. **AK/SK 别打 log**：`account --check` 输出已经回避了，但自己加 debug log 时记得不要 echo 原 key。

---

## 八、相关 memory

- 无独立 memory，本文档即权威来源（Kling 是 2026-05 才接入的）。
