# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**lobsterScan**（展示名「会汇报」）is an OpenClaw multi-agent system that turns a basic
manager's scattered work notes into a report package: one-line summary + key points,
1/3/5-minute spoken scripts, an HTML presentation, a digital-human video, and review
suggestions. Nine agents (`coordinator` + 8 specialists) collaborate to produce it.

Two products ship from one monorepo:
- **会汇报** — the end-user app (`web-frontend` + `web-backend`).
- **OpenClaw 管台** — the internal admin/ops console (`admin-frontend` + `admin-backend`).

## ⚠️ Docs describe the plan, not the current code

`docs/开发文档.md` and `README.md` are the *design spec*. Several things diverge from what
is actually built — **trust the code, not the docs**, for these:

- Agents are **not** invoked over a WebSocket Channel Plugin / persistent Gateway. They run
  as **`openclaw agent --local --json` CLI subprocesses**, one turn per step
  (`app/orchestrator/agent_backend.py`). The Gateway is optional and skipped if its port is busy.
- `packages/ui`, `packages/schemas`, `packages/openclaw-client` are listed in
  `pnpm-workspace.yaml` but contain **no code yet** — frontends are standalone.
- `tests/` is described in docs but **no test files exist**.
- `DATABASE_URL` (web-backend SQLite for `tasks`) is configured but the live task store is
  in-memory + JSON files on disk (see "Outputs"), not the DB.

## Commands

All commands run from the repo root in a **bash** shell (the environment is Windows but uses
Unix shell syntax — forward slashes, `/dev/null`, etc.).

```bash
# First-time setup
pnpm install                                   # installs openclaw@2026.5.5 + frontend deps
uv venv && uv pip install -e apps/web-backend -e apps/admin-backend
cp .env.example .env                           # then fill in at least one LLM provider key
pnpm bootstrap                                 # create 9 agents + apply prompts + mount skills

# Run everything (gateway?, web-backend:8000, admin-backend:8100, web:3000, admin:3100)
pnpm dev          # = bash scripts/dev.sh ; requires .env to exist
pnpm stop         # = bash scripts/stop.sh ; kills PIDs in data/.logs/*.pid

# Logs for each service land in data/.logs/<service>.log

# Run a single backend (with autoreload)
uv run --directory apps/web-backend  uvicorn app.main:app --reload --port 8000
uv run --directory apps/admin-backend uvicorn app.main:app --reload --port 8100

# Run a single frontend
pnpm web          # web-frontend on :3000
pnpm admin        # admin-frontend on :3100
pnpm --filter web-frontend build   # next build
pnpm --filter web-frontend lint    # next lint (admin-frontend has no lint script)

# OpenClaw cluster maintenance
bash scripts/bootstrap-openclaw.sh apply-prompts   # re-sync docs/Agent-Prompts → workspaces
bash scripts/install-skills.sh --all               # re-mount all skill symlinks
bash scripts/install-skills.sh --agent <id>        # re-mount one agent's skills
pnpm oc -- <args>                                  # wrapper around the self-hosted openclaw bin
```

There is no test runner, linter config for Python, or CI wired up yet.

## Architecture

### Service topology

```
web-frontend (:3000) ──REST+SSE──> web-backend (:8000)  ──subprocess──> openclaw agent --local
admin-frontend (:3100) ─REST──────> admin-backend (:8100) ──fs──> openclaw/workspaces, openclaw.json
                                          ▲                          data/admin.db (secrets, tokens, audit)
        web-backend ──HTTP 127.0.0.1:8100─┘  (secrets pull, token-usage report, pipeline ingest)
```

The two backends do **not** share code or a database. They coordinate over **hardcoded
`http://127.0.0.1:8100` HTTP calls** (see `web-backend/app/openclaw/secrets.py`,
`openclaw/tokens.py`, and the `pipelines/ingest` POST at the end of `execute()`).

- **`admin-backend` is the single source of secrets.** web-backend never persists API keys;
  it pulls plaintext from `GET /admin/api/secrets/internal/values` (30s cache) and injects
  per-agent env vars via `secrets.env_for_agent()`. Secrets are Fernet-encrypted at rest in
  `data/admin.db` (key derived from `ADMIN_SECRET_KEY`).
- admin-backend reads/writes `openclaw/workspaces/<agent>/*.md` and `openclaw/openclaw.json`
  **directly on the filesystem**, never through the Gateway.

### The pipeline (web-backend)

`app/orchestrator/pipeline.py` is the core (large file, ~2900 lines). Flow of `execute(run)`:

1. `coordinator` posts an intro to the group chat, then `_coordinator_briefing` runs one LLM
   turn to generate a per-step **`agent_brief`** (task-specific emphasis) injected into prompts.
2. `run_harness(...)` (`app/orchestrator/harness.py`) drives the agents **event-driven**, not
   as a fixed loop:
   - `EventBus` (async pub/sub) + one `AgentWorker` per agent + a `Coordinator` that is a
     **rule engine, not an LLM**.
   - Each agent's JSON output ends with a **`handoff: {to, reason, payload_hint}`** field; the
     Coordinator routes to the next worker. `STEPS` / `DEFAULT_NEXT_STEP` are the *default*
     chain, but agents can hand off anywhere (retry self, go back upstream, jump to reviewer, `DONE`).
   - Loop protection: `max_revisits=2`, `max_hops=16`. `needs_help` / `needs_retry` fields are
     events the Coordinator reacts to (route upstream / re-run).
   - Every event is appended to `data/outputs/<task_id>/events.jsonl` (replayable).
3. Final status is `done` / `partial` / `failed` based on per-step status; results are persisted
   and POSTed to admin-backend `pipelines/ingest`.

The 9 agents and their default order (`STEPS`): `material` → `point-extractor` → `structure` →
`upward-opt` → `copywriter` → `html-designer` + `video-producer` → `reviewer`. `coordinator`
orchestrates and is never a pipeline step.

Several agents run **two LLM phases** internally (`material_parsing`, `point_extraction`,
`copywriting`): a Phase-1 reconnaissance/candidate pass feeding a Phase-2 extraction pass.

### Agent execution backend (swappable)

`app/orchestrator/agent_backend.py` abstracts "run one agent turn" behind `AgentBackend`. The
only implementation is `OpenClawSubprocessBackend`, which forks
`openclaw --profile lobster-<agentId> agent --agent main --local --json --model <provider/model> -m <prompt>`.
Use `get_default_backend()` / `set_default_backend()` (the latter for injecting a mock in tests).
The model string comes from `LLM_PROVIDER` env routing in `_model_from_llm_provider_env()`.

`app/openclaw/client.py` parses the messy stdout: it skips OpenClaw's pre-JSON warnings via
`raw_decode`, then `extract_json()` salvages LLM JSON with `_fix_inner_quotes` (unescaped inner
quotes → 「」), trailing-comma stripping, and loose fallbacks. **Don't "simplify" this parser** —
it exists because LLM JSON output is unreliable.

### Prompt construction

Every step prompt is assembled from composable blocks in `pipeline.py`:
`_build_global_ctx` (report_type/audience/duration/style + **user `supplement` = highest-priority
instruction**) + `_agent_brief_block` (coordinator's per-task brief) + `_required_skills_block`
(forces the agent to read mounted `SKILL.md` and declare `skills_used`) + `_autonomy_block` +
`_handoff_block` + `JSON_RULE` (mandates a two-part output: prose reasoning, then one ```json``` block).

`DURATION_PROFILE` (1/3/5 分钟) is the **single source of truth** for chapter count, narration
segment count, and word budgets — multiple steps read it via `_duration_block`.

### Skills

Source skills live in `skills/custom/<name>` (self-authored) and `skills/third-party/<repo>/...`
(garden-skills, humanizer, heygen, video-toolkit). `scripts/install-skills.sh` **symlinks** them
into `openclaw/workspaces/<agent>/.agents/skills/<name>` per the `MAP` table at the top of that
script. That `MAP` is the real wiring — keep it in sync with `_REQUIRED_SKILLS_MAP` in `pipeline.py`.

### Outputs & persistence

Per task: `data/outputs/<task_id>/` holds `task.json` (status snapshot), `chat.jsonl` (group-chat
history), `events.jsonl` (harness events), per-step `.txt`/`.json`, `script.md`,
`web-presentation/index.html` (self-contained projectable HTML, built by `render/html_builder.py`),
and `video/` artifacts. `TaskRun` is held in an in-memory dict but **lazy-loads from disk**
(`_load_run_from_disk`) so tasks survive `uvicorn --reload`. SSE (`subscribe`) replays known steps
+ chat history on reconnect, then keeps the connection open for `refine` feedback.

### Video generation & fallback

`app/video/providers.py` resolves the active provider from `openclaw.json` (`heygen` / `minimax` /
`klingai` / self-hosted). MiniMax uses **dual-channel key routing** (tokenplan vs payg) in
`secrets._resolve_minimax_key`. When external providers fail/lack quota, `render/` modules provide
local fallbacks: `tts_fallback.py` (edge-tts), `slideshow_video.py` + `broadcast_recorder.py`
(Playwright + imageio/ffmpeg). Video failure must degrade to `partial`, never block the report.

## Conventions & gotchas

- **User-facing text must never leak technical IDs** (`task_id`, `agent_id`, `run_id`, error
  codes). The UI renders agents as named "colleagues" in a group chat (`AGENT_DISPLAY` /
  `AGENT_AVATAR`); raw IDs stay in logs and the admin console only.
- Chinese is the product language — agent display names, chat bubbles, and exported filenames
  (`<标题>_YYYYMMDD.<ext>`) are Chinese. Code identifiers and comments mix Chinese and English.
- CORS allowlists are exact-match and include both `localhost` and `127.0.0.1` — keep both when
  editing origins in either `main.py`.
- `scripts/dev.sh` requires `.env` and **skips the gateway** if `OPENCLAW_GATEWAY_PORT` is already
  bound (pipeline uses `--local`, so the gateway isn't required for report generation).
- Each agent gets an isolated `agentDir` (`~/.openclaw/agents/<id>`) and workspace
  (`openclaw/workspaces/<id>`) — **never share agentDirs** (auth/session crosstalk).

<!-- SPECKIT START -->
Active spec-driven feature: **P4 — Reviewer 双轨(质量 + 流程逻辑)+ verdict.fail 修复闭环** ✅ Implemented
- Plan:       `specs/004-reviewer-dual-track/plan.md`
- Spec:       `specs/004-reviewer-dual-track/spec.md`
- Research:   `specs/004-reviewer-dual-track/research.md` (9 decisions + 4 派生发现)
- Data model: `specs/004-reviewer-dual-track/data-model.md`
- Quickstart: `specs/004-reviewer-dual-track/quickstart.md`
- Tasks:      `specs/004-reviewer-dual-track/tasks.md` (44/45;US1-US5 + 实现 + 测试全绿;T044 v1 baseline + v2 真 LLM 质量审挂 Windows issue 后人工)
- (contracts/ skipped — 复用 P1 ReviewerVerdict schema)
- Constitution: `.specify/memory/constitution.md` (v1.1.0;P4 **无需新宪章修订** — Reviewer 用 LLM 做质量验证是原则 IV 本职)
Code: `apps/web-backend/app/orchestrator/process_review.py` (新模块 ~130 行;`ProcessReviewer` 3 纯规则
      版本一致/依赖图/参与度 + `_process_verdict`)
      + `harness.py` 扩(`AgentWorker._reviewed` 版本去重 + `handle_v2_event` reviewer 特化早期分支 +
      `_reviewer_handle`/`_reviewer_quality_review`(ArtifactUpdate→质量审/否则 silent)+
      `_to_reviewer_verdict`/`_pad_suggestions` 适配)
      + `coordinator_observer.py` 扩(`REVIEW_FIX_MAX_RETRY` + `_fix_retries`/`_process_reviewed`/
      `_artifact_log` + `bus.on(reviewer.verdict)→_on_verdict` 修复闭环 + `_on_quiescence` 双因子改造)
Tests: `apps/web-backend/tests/orchestrator/*` (98 passed;P1 32 + P2 29 + P3 20 + P4 17:US1 2 +
      US2 4 + US3 4 + US4 4 + US5 3)。v1 字段级零回归。
关键转变:Reviewer 不再是链式 work-driver 一环(P3 中跑 review step;P4 改为全程订阅审校者,被
      mention→silent);ReviewerVerdict 从 P3 `_emit_v2_finalization` 示例变为真实双轨 emit。
      质量轨 LLM(report-reviewer)真任务挂 Windows issue,ScriptedBackend/monkeypatch _quick_review 测试级覆盖。

Previously shipped (still authoritative for code):
- **P3 Coordinator 转型 + subscription work-driver** ✅ Implemented — `specs/003-coordinator-transform/`
  Code: `apps/web-backend/app/orchestrator/coordinator_observer.py` (~310 行;`DriftJudge`/`ArtifactGate`/
        `CoordinatorObserver` watchdog:quiescence + stagnation 激活 + gatekeeper gate_pass/reject + drift)
        + `harness.py`(`HarnessState.{inflight_steps,bootstrapped,observer}` + `handle_v2_event` SPEAK→真跑
        `_run_unlocked` + step-success 去重 + `force_run_v2` + Coordinator 4 handler `is_v2` short-circuit +
        bootstrap) + `pipeline.py` execute v2 收尾 observer 接管
  Tests: `apps/web-backend/tests/orchestrator/*` (81 passed;P1 32 + P2 29 + US1 4 + US2 4 + US3/US5 7 + US4 5)
  宪章 1.1.0:原则 IV 新增 drift 受限 LLM 例外。work-driver 下 artifact.update + agent.speak(mention) 双触发,
  靠 step-success 去重只跑一次。
- **P2 worker 订阅化 + decide-to-speak 闸门** ✅ Implemented — `specs/002-worker-subscription/`
  Code: `apps/web-backend/app/orchestrator/subscription.py` (~310 行;9 agent `WORKER_PROFILE` +
        Predicate helpers + `DecisionResult` + `decide_to_speak` + `SubscriptionRegistry`)
        + `harness.py`(`HarnessState.{subscriptions,agent_locks}` + `AgentWorker.{inbox,_consume_loop,
        enqueue_v2,handle_v2_event}` + per-agent lock + `emit_v2` 末尾 dispatch + run_harness v2 分支)
        + `pipeline.py` `_emit_v2_step_overlay` per-step chat overlay
  Tests: `apps/web-backend/tests/orchestrator/*` (61 passed;P1 32 + US1 4 + US2 19 + US3 6)
- **P1 v2 protocol + state model** ✅ Implemented — `specs/001-v2-chat-protocol-state/`
  Code: `apps/web-backend/app/orchestrator/{events_v2,artifacts_v2,ids,replay_check}.py`
        + `TaskRun.harness_version` / `HarnessState.is_v2` / `HarnessState.emit_v2()`
  Tests: `apps/web-backend/tests/orchestrator/*` (29 passed / 3 skipped)

For overall v2 roadmap (P1–P8) see `docs/开发文档.md` §9.4.
<!-- SPECKIT END -->
