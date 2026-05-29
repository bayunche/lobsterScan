# Windows 环境下真实 LLM 管线无法跑通(subprocess + sh-only wrapper)

> 本文件是 GitHub issue 草稿。`gh` CLI 未安装,无法自动提交。
> 请复制以下内容到 https://github.com/bayunche/lobsterScan/issues/new

---

**Labels**: `bug`, `platform:windows`, `dev-env`

## 概要

在 Windows 开发机上,`pnpm dev` 起的 web-backend **从未成功跑过一个真实 LLM 任务** ——
所有 v1/v2 报告任务都在第一个 step(material_parsing)就 `failed`,8 个 step 全部
`agent.failed`,最终 task `failed`。日志累计 15+ 个 task POST,`grep agent.done` 0 命中。

P2(`specs/002-worker-subscription/`)的 T040 quickstart §3 v2 demo curl 验证时暴露。
**这不是 P2 回归** —— v1 默认路径同样失败,与环境相关。

## 根因(两个独立的 Windows 障碍)

### 障碍 1 — uvicorn `--reload` 强制 SelectorEventLoop,subprocess 不可用

- `apps/web-backend/app/orchestrator/agent_backend.py:241` 用
  `asyncio.create_subprocess_exec(...)` fork `openclaw agent` CLI。
- Windows 上 `asyncio.SelectorEventLoop` **不支持 subprocess**,会抛
  `NotImplementedError`(`asyncio/base_events.py:528 _make_subprocess_transport`)。
- uvicorn 在 `--reload` 模式下令 `use_subprocess=True`
  (`uvicorn/config.py:366`),而 `uvicorn/loops/asyncio.py:9` 在
  `use_subprocess=True` 时**强制返回 `SelectorEventLoop`**,覆盖应用层的
  `set_event_loop_policy`。
- `scripts/dev.sh:47-48` 用 `uvicorn app.main:app ... --reload` 起 web-backend
  → Windows 上 worker 永远拿不到 ProactorEventLoop。

**已部分缓解**(commit `c249064`):`apps/web-backend/app/main.py` 在模块导入期装
`WindowsProactorEventLoopPolicy`。这能救 plain `uvicorn` / 其他 ASGI runner / pytest,
但 **救不了 `--reload`**(uvicorn 在子进程里重设 loop)。

### 障碍 2 — `node_modules/.bin/openclaw` 是 sh-only wrapper,Windows CreateProcess 不识别

- `node_modules/.bin/openclaw` 第一行是 `#!/bin/sh`(npm 生成的 POSIX shim)。
- 即便 loop 修好(用 plain uvicorn),`create_subprocess_exec(["...openclaw", ...])`
  在 Windows 走 `CreateProcess`,不解析 shebang → `OSError: [WinError 193] %1 不是有效的
  Win32 应用程序`(实测,见 P2 T040 日志)。
- `OPENCLAW_BIN` 指向 `.cmd` wrapper 时又会触发 `[WinError 2] 系统找不到指定的文件`
  (pnpm 没生成 `openclaw.cmd`,只有 sh shim)。
- 真正的入口是 `node_modules/openclaw/openclaw.mjs`(`#!/usr/bin/env node`)。

## 建议修复(2 处,互相独立但都需要)

### Fix A — `agent_backend.py` Windows 下走 `node openclaw.mjs`

`OpenClawSubprocessBackend.run_turn` 构造 `cmd` 时(`agent_backend.py:212-218`),
在 Windows 平台把 `self._bin`(sh shim)替换为 `[node_exe, <openclaw.mjs 绝对路径>]`:

```python
import shutil, sys
# self._bin 优先级不变;但 Windows 上 sh-shim 不可执行 → 改用 node + .mjs
if sys.platform == "win32" and self._bin.endswith(("openclaw", "openclaw.sh")):
    mjs = Path(self._bin).resolve().parent.parent / "openclaw" / "openclaw.mjs"
    # 或从 node_modules/openclaw/package.json 的 bin 字段解析
    if mjs.is_file():
        node = shutil.which("node") or "node"
        cmd = [node, str(mjs), "--profile", profile, ...]  # 其余参数不变
```

更稳的做法:在 `__init__`(`agent_backend.py:163`)解析时就探测平台,
缓存一个 `self._argv_prefix: list[str]`(`[bin]` 或 `[node, mjs]`),`run_turn` 用它拼 cmd。

### Fix B — `scripts/dev.sh` Windows 下去掉 web-backend 的 `--reload`

`scripts/dev.sh:47-48`:

```bash
# 现状
run web-backend uv run --directory apps/web-backend uvicorn app.main:app \
  --host "$WEB_BACKEND_HOST" --port "$WEB_BACKEND_PORT" --reload

# 建议:Windows 下去 --reload(代价:改代码要手动 restart;但 subprocess 能跑)
RELOAD_FLAG="--reload"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" || "$(uname -s)" == MINGW* ]]; then
  RELOAD_FLAG=""
  echo "▸ Windows 检测到:web-backend 去掉 --reload(否则 SelectorEventLoop 让 subprocess 挂)"
fi
run web-backend uv run --directory apps/web-backend uvicorn app.main:app \
  --host "$WEB_BACKEND_HOST" --port "$WEB_BACKEND_PORT" $RELOAD_FLAG
```

(配合 commit `c249064` 的 main.py policy,plain uvicorn 即可拿到 ProactorEventLoop。)

## 验收

- [ ] Windows 上 `pnpm dev` 后,提交一个常规 3 分钟项目进度汇报任务 → task 跑到 `done`/`partial`(非 `failed`)
- [ ] `data/outputs/<task_id>/events.jsonl` 出现 `agent.done` 事件
- [ ] 顺带能跑通 P2 spec 的 **T038**(v1 baseline diff:main vs 002 分支 5 demo 字段级一致)
      与 **T040**(v2 demo:mentions → 自动响应链路在真任务里 emit)
- [ ] 非 Windows 平台(Linux/WSL2/macOS)行为不回归

## 关联

- P2 spec: `specs/002-worker-subscription/`(T038/T040 因本问题 deferred)
- 已落地缓解: commit `c249064`(main.py ProactorEventLoop policy,只解 plain uvicorn 场景)
- 宪章原则 III(降级而非崩溃):注意 step 失败时**任务不应直接 failed**,当前 8/8
  全 failed 也暴露了兜底链在"全员 subprocess 不可用"极端场景下没有降级到 partial ——
  可考虑单独跟进。
