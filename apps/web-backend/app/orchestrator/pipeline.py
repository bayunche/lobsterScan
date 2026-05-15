"""8-step Pipeline · 用 OpenClaw 真跑 9 个 Agent 协作

V0：业务后端编排，每个 step 调用对应 agent 一次。
- 上一步的 JSON 输出，作为下一步的输入
- 每一步推送 task.step 事件到 SSE 订阅者
- 每一步把 token 用量上报管台
- 每一步成功后把产物写到 data/outputs/<task_id>/
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..openclaw.client import TurnResult, extract_json, run_agent_turn
from ..openclaw.secrets import env_for_agent
from ..openclaw.tokens import report_usage

log = logging.getLogger("orchestrator")

# (step_key, agent_id, display_name, depends_on_step)
STEPS: list[tuple[str, str, str, str | None]] = [
    ("material_parsing",    "material",        "资料员 · 整理材料",      None),
    ("point_extraction",    "point-extractor", "分析师 · 提炼重点",      "material_parsing"),
    ("structure_building",  "structure",       "结构师 · 选大纲",        "point_extraction"),
    ("upward_optimization", "upward-opt",      "表达教练 · 优化向上汇报", "point_extraction"),
    ("copywriting",         "copywriter",      "文书 · 生成讲稿与页面",  "upward_optimization"),
    ("html_design",         "html-designer",   "设计师 · 生成 HTML 页面", "copywriting"),
    ("video_production",    "video-producer",  "视频制作 · 数字人视频",  "copywriting"),
    ("review",              "reviewer",        "质量检查员 · 审校",      "copywriting"),
]

OUTPUTS_ROOT = settings.outputs_root

# Agent 在群里的"开场白"与"完成宣告"
AGENT_DISPLAY: dict[str, str] = {
    "coordinator": "汇报总控", "material": "资料员", "point-extractor": "分析师",
    "structure": "结构师", "upward-opt": "表达教练", "copywriter": "文书",
    "html-designer": "设计师", "video-producer": "视频制作", "reviewer": "质量检查员",
}
AGENT_AVATAR: dict[str, str] = {
    "coordinator": "🎯", "material": "📋", "point-extractor": "🔍",
    "structure": "🗂️", "upward-opt": "💬", "copywriter": "✍️",
    "html-designer": "🎨", "video-producer": "🎬", "reviewer": "✅",
}
STEP_INTRO: dict[str, str] = {
    "material_parsing":    "我来整理这次的素材。",
    "point_extraction":    "拿到素材池了，我来提炼几条重点。",
    "structure_building":  "我按汇报类型选个合适的大纲。",
    "upward_optimization": "我把表达改成更适合向上汇报的口吻。",
    "copywriting":         "我写讲稿和每页文案，按时长卡字数。",
    "html_design":         "我搭 HTML 演示工程骨架。",
    "video_production":    "我用 MiniMax TTS 给每段讲稿配音。",
    "review":              "最后我做质量检查，给一些可执行建议。",
}


def _summarize_output(step: str, output_json: dict | None, output_text: str) -> str:
    """把 agent 输出包装成"群里发言"，给前端展示."""
    j = output_json or {}
    if step == "material_parsing":
        payload = j.get("payload", j)
        parts = []
        if payload.get("completed"):
            parts.append(f"完成 {len(payload['completed'])} 项工作")
        if payload.get("risks"):
            parts.append(f"识别 {len(payload['risks'])} 条风险")
        if payload.get("next_steps"):
            parts.append(f"{len(payload['next_steps'])} 项下一步")
        return "✓ 素材池就绪：" + "、".join(parts) if parts else "✓ 素材池就绪"
    if step == "point_extraction":
        if j.get("summary"):
            return f"✓ 一句话总结：{j['summary']}"
    if step == "structure_building":
        if j.get("chapters"):
            return f"✓ 选定 {len(j['chapters'])} 章结构"
    if step == "upward_optimization":
        return "✓ 已改写成领导视角"
    if step == "copywriting":
        script = j.get("script_md") or ""
        slides = j.get("slides") or []
        return f"✓ 讲稿 {len(script)} 字 · {len(slides)} 页 slides"
    if step == "html_design":
        return "✓ HTML 工程骨架就绪"
    if step == "video_production":
        segs = j.get("audio_segments") or []
        intro = j.get("intro_video") or {}
        parts = []
        if segs:
            parts.append(f"{len(segs)} 段配音")
        if intro.get("ok"):
            parts.append(f"{intro.get('duration', '?')}s 数字人开场")
        if j.get("degraded"):
            tail = f"（{j.get('degrade_reason', 'partial')}）"
            return f"⚠ 部分降级 — {'、'.join(parts) or '已交付讲稿与字幕'}{tail}"
        return "✓ " + ('、'.join(parts) if parts else "视频物料就绪")
    if step == "review":
        suggestions = j.get("suggestions") or []
        score = j.get("ai_signal_score")
        return f"✓ {len(suggestions)} 条审校建议 · AI 套话指数 {score if score is not None else '-'}"
    # 没结构化 json，把文本头一段截出来
    head = (output_text or "").strip().splitlines()[0:1]
    return head[0][:80] if head else "（无文本）"


# ---- JSON 输出硬约束（所有 agent prompt 末尾追加这段） ----
JSON_RULE = """

# 输出格式（强制）
你必须只输出一段 ```json ... ``` 代码块，不要任何 markdown 表格、说明文字、校验报告、emoji。
该 JSON 必须能被 json.loads() 直接解析。如果信息不足以填某字段，填 [] 或 null，不要省略字段。
"""


@dataclass
class StepState:
    step: str
    label: str
    agent: str
    status: str = "pending"             # pending|running|success|failed|skipped
    started_at: float | None = None
    ended_at: float | None = None
    output_text: str = ""
    output_json: dict[str, Any] | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider: str | None = None
    model: str | None = None


@dataclass
class TaskRun:
    task_id: str
    title: str
    report_type: str
    audience: str
    duration: str
    style: str
    raw_text: str
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    steps: list[StepState] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "report_type": self.report_type,
            "audience": self.audience,
            "duration": self.duration,
            "style": self.style,
            "status": self.status,
            "created_at": _iso(self.created_at),
            "steps": [
                {
                    "step": s.step, "label": s.label, "agent": s.agent,
                    "status": s.status,
                    "started_at": _iso(s.started_at) if s.started_at else None,
                    "ended_at": _iso(s.ended_at) if s.ended_at else None,
                    "duration_ms": int((s.ended_at - s.started_at) * 1000) if s.started_at and s.ended_at else None,
                    "provider": s.provider, "model": s.model,
                    "tokens": s.prompt_tokens + s.completion_tokens,
                    "has_json": s.output_json is not None,
                    "error": s.error,
                }
                for s in self.steps
            ],
            "artifacts": self.list_artifacts(),
        }

    def step_detail(self, step_key: str) -> dict | None:
        for s in self.steps:
            if s.step == step_key:
                return {
                    "step": s.step, "label": s.label, "agent": s.agent, "status": s.status,
                    "output_text": s.output_text, "output_json": s.output_json,
                    "provider": s.provider, "model": s.model,
                    "prompt_tokens": s.prompt_tokens, "completion_tokens": s.completion_tokens,
                    "error": s.error,
                }
        return None

    def output_dir(self) -> Path:
        return OUTPUTS_ROOT / self.task_id

    def list_artifacts(self) -> dict[str, dict]:
        d = self.output_dir()
        if not d.exists():
            return {}
        out: dict[str, dict] = {}
        for f in sorted(d.iterdir()):
            if f.is_file():
                out[f.name] = {
                    "url": f"/api/tasks/{self.task_id}/exports/{f.name}",
                    "size": f.stat().st_size,
                }
        return out


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---- 全局 task store ----
_runs: dict[str, TaskRun] = {}


def get_run(task_id: str) -> TaskRun | None:
    return _runs.get(task_id)


def list_runs(limit: int = 20) -> list[TaskRun]:
    return sorted(_runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]


# ---- 事件订阅 ----
async def subscribe(task_id: str):
    run = _runs.get(task_id)
    if not run:
        return
    q: asyncio.Queue = asyncio.Queue()
    run.subscribers.append(q)
    try:
        # reconnect 友好：把当前已知 step 状态 + chat 历史喷一次
        for s in run.steps:
            await q.put(("task.step", _step_event(s)))
        for msg in getattr(run, "_chat_log", []):
            await q.put(("chat.message", msg))
        if run.status != "running":
            await q.put(("task.done", {"status": run.status}))
            return
        while True:
            evt = await q.get()
            if evt is None:
                return
            yield evt
    finally:
        try:
            run.subscribers.remove(q)
        except ValueError:
            pass


def _step_event(s: StepState) -> dict:
    return {
        "step": s.step, "label": s.label, "agent": s.agent,
        "status": s.status,
        "tokens": s.prompt_tokens + s.completion_tokens,
        "duration_ms": int((s.ended_at - s.started_at) * 1000) if s.started_at and s.ended_at else None,
    }


async def _broadcast(run: TaskRun, event: str, data: dict) -> None:
    for q in list(run.subscribers):
        await q.put((event, data))


def _chat_msg(agent: str, text: str, kind: str = "result") -> dict:
    """构造一条 chat.message 事件 payload."""
    import uuid as _uuid
    return {
        "id": _uuid.uuid4().hex[:12],
        "agent": agent,
        "display_name": AGENT_DISPLAY.get(agent, agent),
        "avatar": AGENT_AVATAR.get(agent, "🤖"),
        "ts": time.time(),
        "kind": kind,                          # intro | result | error | user | system
        "text": text,
    }


# 保存全任务的群聊历史（用于 reconnect / 历史回放）
def _persist_chat(run: TaskRun, msg: dict) -> None:
    run_chat = getattr(run, "_chat_log", None)
    if run_chat is None:
        run_chat = []
        run._chat_log = run_chat
    run_chat.append(msg)


def get_chat(task_id: str) -> list[dict]:
    run = _runs.get(task_id)
    if not run:
        return []
    return list(getattr(run, "_chat_log", []))


# ---- Pipeline 主流程 ----
def _step_prompt(step: str, run: TaskRun, prev: dict[str, StepState]) -> str:
    ctx = (
        f"report_type: {run.report_type}\n"
        f"audience: {run.audience}\n"
        f"duration: {run.duration}\n"
        f"style: {run.style}"
    )

    if step == "material_parsing":
        body = f"""请把以下原始工作内容解析为 MaterialPool。

# 上下文
{ctx}

# 原始内容
{run.raw_text}

# MaterialPool schema
{{
  "msg_type": "task.step.result",
  "step": "material_parsing",
  "payload": {{
    "time_range": "本周",
    "completed": ["..."],
    "in_progress": ["..."],
    "key_data": [{{"name":"...","value":"...","note":""}}],
    "risks": ["..."],
    "support_needed": ["..."],
    "next_steps": ["..."],
    "people": [],
    "data_gaps": []
  }}
}}"""

    elif step == "point_extraction":
        pool = (prev["material_parsing"].output_json or {}).get("payload") or prev["material_parsing"].output_json or {}
        body = f"""请从 MaterialPool 提炼 ReportCore。

# 上下文
{ctx}

# MaterialPool
```json
{json.dumps(pool, ensure_ascii=False)}
```

# ReportCore schema
{{
  "summary": "≤80 字一句话总结",
  "key_points": ["重点1","重点2","重点3"],
  "progress_status": "正常|有轻微风险|有风险|已延期",
  "risks": [{{"item":"...","impact":"..."}}],
  "support_needed": [{{"item":"...","owner":"领导|同事|客户"}}],
  "next_steps": ["..."]
}}"""

    elif step == "structure_building":
        body = f"""按 report_type={run.report_type} 选合适的大纲结构。

# Outline schema
{{
  "report_type": "{run.report_type}",
  "chapters": [
    {{"chapter_no":1,"title":"封面","type":"cover","data_keys":[]}},
    {{"chapter_no":2,"title":"本周概览","type":"summary","data_keys":["summary","progress_status"]}}
  ]
}}

chapters 数量 5-7，必须包含 cover / summary / next_steps 三种类型。"""

    elif step == "upward_optimization":
        core = prev["point_extraction"].output_json or {}
        body = f"""把这份 ReportCore 改写成更适合向上汇报：结论前置 / 风险显化 / 诉求具体化。

```json
{json.dumps(core, ensure_ascii=False)}
```

输出同 ReportCore schema 的 JSON，内容改写但 schema 不变。"""

    elif step == "copywriting":
        core = prev["upward_optimization"].output_json or prev["point_extraction"].output_json or {}
        outline = prev["structure_building"].output_json or {}
        word_count = {"1分钟": "200-300", "3分钟": "500-750", "5分钟": "900-1200"}[run.duration]
        body = f"""根据 ReportCore + Outline，生成讲稿和页面文案。

# ReportCore
```json
{json.dumps(core, ensure_ascii=False)}
```

# Outline
```json
{json.dumps(outline, ensure_ascii=False)}
```

# 输出 schema
{{
  "script_md": "口播稿 {word_count} 字，每段以动词开头，句长≤25 字，中文标点，无 emoji",
  "slides": [
    {{"page_no":1,"title":"...","type":"cover","content":[]}},
    {{"page_no":2,"title":"...","type":"summary","content":["...","..."]}}
  ],
  "narrations": [
    {{"chapter":1,"step":1,"text":"..."}}
  ]
}}

约束：每页 title ≤30 字 · 每条 content ≤15 字 · narrations.length 等于 chapters 的总 step 数。"""

    elif step == "html_design":
        slides = (prev["copywriting"].output_json or {}).get("slides") or []
        body = f"""根据 slides 设计 HTML 演示工程目录骨架。

# Slides
```json
{json.dumps(slides, ensure_ascii=False)}
```

# 输出 schema
{{
  "project_path": "data/outputs/{run.task_id}/web-presentation/",
  "dist_path": "data/outputs/{run.task_id}/web-presentation/dist/",
  "theme_token_id": "default",
  "page_index": [
    {{"page_no":1,"anchor":"chapter-1-step-1"}}
  ]
}}"""

    elif step == "video_production":
        copy_out = prev["copywriting"].output_json or {}
        script = copy_out.get("script_md") or ""
        slides = copy_out.get("slides") or []
        narrations = copy_out.get("narrations") or []
        body = f"""请用挂载的 minimax-tts 和 minimax-video 两个 skill 制作汇报视频物料。

# 当前任务
task_id: {run.task_id}
audio_dir: data/outputs/{run.task_id}/audio/
video_dir: data/outputs/{run.task_id}/video/
duration:  {run.duration}
audience:  {run.audience}
style:     {run.style}

# 讲稿（script_md）
{script[:1200]}

# Slides（{len(slides)} 页）
```json
{json.dumps(slides, ensure_ascii=False)[:800]}
```

# Narrations（上游已给 {len(narrations)} 段）
```json
{json.dumps(narrations, ensure_ascii=False)[:600]}
```

# 你要做的 — 两条腿并行
## (A) TTS 配音 · minimax-tts
按 narrations / slides 切讲稿，逐段调 `.agents/skills/minimax-tts/scripts/synthesize.py` 生成 mp3。
- 每段 mp3 → `data/outputs/{run.task_id}/audio/<两位序号>.mp3`
- 元数据写 jsonl，调 build_srt.py 生成 subtitles.srt

## (B) 数字人开场镜头 · minimax-video
调一次 `.agents/skills/minimax-video/scripts/generate.py` 生成 6 秒数字人开场，用于嵌入 HTML 汇报页封面。

prompt 写法（结合本次汇报）：
- 主体：与 audience={run.audience}/style={run.style} 匹配的职场人（如「中国职场女性，简洁正装」「中年管理者，深色西装」）
- 动作：自然对镜头自信讲解开场
- 场景：现代会议室 / 屏幕前 / 自然光中景
- 加摄影词：「中景」「专业摄影」「45 度角」

```bash
python3 .agents/skills/minimax-video/scripts/generate.py \\
  --prompt "<你定制的 prompt>" \\
  --duration 6 \\
  --output data/outputs/{run.task_id}/video/intro.mp4
```

# 失败处理
- `MINIMAX_API_KEY` 缺失 → degraded=true / no_api_key
- API quota_exhausted → 已生成部分保留，degraded=true / quota_exhausted
- 视频失败但 TTS 成功 → 保留 audio_segments，intro_video=null，不阻塞
- 两个都失败 → degraded=true 让 pipeline 走降级

# 输出 schema（最终一段 ```json``` 代码块）
{{
  "audio_segments": [
    {{"index": 1, "text": "...", "voice": "male-qn-qingse",
      "path": "data/outputs/{run.task_id}/audio/01.mp3",
      "duration_estimate_sec": 6.4, "ok": true}}
  ],
  "subtitle_path": "data/outputs/{run.task_id}/audio/subtitles.srt",
  "intro_video": {{
    "path": "data/outputs/{run.task_id}/video/intro.mp4",
    "duration": 6,
    "prompt": "...",
    "ok": true
  }},
  "voice_style": "{run.style}",
  "tts_provider": "minimax",
  "tts_model": "speech-02-hd",
  "video_provider": "minimax-hailuo",
  "video_model": "MiniMax-Hailuo-02",
  "degraded": false,
  "degrade_reason": null
}}

# 重要：最终回复硬约束
不管 skill 调用结果如何（成功 / quota 耗尽 / 网络失败），你的最终回复**必须**以一段 ```json``` 代码块结尾，包含上面 schema 的所有字段。失败的段把 ok=false、degraded=true、degrade_reason 填准即可。**不要**把 generate.py 的 stdout 直接当成回复贴出来，要自己整合后写 JSON。"""

    elif step == "review":
        core = prev["upward_optimization"].output_json or prev["point_extraction"].output_json or {}
        script = (prev["copywriting"].output_json or {}).get("script_md") or ""
        body = f"""检查这份汇报材料质量，输出 ReviewSuggestion。

# ReportCore
```json
{json.dumps(core, ensure_ascii=False)}
```

# 讲稿前 300 字
{script[:300]}

# 输出 schema
{{
  "suggestions": ["≥3 条可执行建议，每条以动词开头"],
  "quick_actions": {{
    "shorter": false, "more_problem": false,
    "more_formal": false, "more_result": false
  }},
  "estimated_duration": "2分20秒",
  "ai_signal_score": 0.0,
  "checks": {{
    "has_summary": true, "key_points_ok": true,
    "has_risks": true, "risks_have_impact": true,
    "has_next_steps": true, "support_clear": true,
    "length_ok": true, "audience_fit": true
  }}
}}"""

    else:
        body = "请输出 JSON 代码块。"

    return body + JSON_RULE


def _persist_step(run: TaskRun, s: StepState) -> None:
    """把 step 产物落盘到 data/outputs/<task_id>/."""
    d = run.output_dir()
    d.mkdir(parents=True, exist_ok=True)
    # 原始文本
    (d / f"{s.step}.txt").write_text(s.output_text, encoding="utf-8")
    # 结构化 JSON
    if s.output_json is not None:
        (d / f"{s.step}.json").write_text(
            json.dumps(s.output_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    # 特殊命名：讲稿直接产 .md
    if s.step == "copywriting" and s.output_json:
        script = s.output_json.get("script_md")
        if script:
            (d / "script.md").write_text(script, encoding="utf-8")


def _persist_final(run: TaskRun) -> None:
    """整体 task summary 落盘."""
    d = run.output_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(
        json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _run_step(s: StepState, run: TaskRun, prev: dict[str, StepState]) -> None:
    s.status = "running"
    s.started_at = time.time()
    await _broadcast(run, "task.step", _step_event(s))

    # 群聊开场白：让 agent 先在群里说一句"我准备做 X"
    intro = STEP_INTRO.get(s.step) or f"我开始处理 {s.label}"
    intro_msg = _chat_msg(s.agent, intro, kind="intro")
    _persist_chat(run, intro_msg)
    await _broadcast(run, "chat.message", intro_msg)

    prompt = _step_prompt(s.step, run, prev)
    try:
        timeout = 240 if s.step in ("copywriting", "html_design", "review") else 180
        # video-producer 等需要调外部 TTS / 数字人 Skill 的 agent，给更宽超时
        if s.step == "video_production":
            timeout = 360
        extra_env = await env_for_agent(s.agent)
        res: TurnResult = await run_agent_turn(
            agent_id=s.agent, message=prompt, timeout_sec=timeout,
            extra_env=extra_env,
        )
        s.output_text = res.text
        s.output_json = extract_json(res.text)
        s.provider = res.provider
        s.model = res.model
        s.prompt_tokens = res.prompt_tokens
        s.completion_tokens = res.completion_tokens
        s.status = "success"

        await report_usage(
            agent_id=s.agent, task_id=run.task_id,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )
        # 落盘
        _persist_step(run, s)
    except Exception as e:  # noqa: BLE001
        s.error = str(e)[:500]
        s.status = "failed"
        log.exception("step %s failed", s.step)
    finally:
        s.ended_at = time.time()
        await _broadcast(run, "task.step", _step_event(s))
        # 群聊收尾：把这位 agent 的产出包成一条 message
        if s.status == "success":
            summary = _summarize_output(s.step, s.output_json, s.output_text)
            result_msg = _chat_msg(s.agent, summary, kind="result")
            _persist_chat(run, result_msg)
            await _broadcast(run, "chat.message", result_msg)
            await _broadcast(run, "task.artifact", {
                "step": s.step, "kind": s.step, "ready": True,
                "has_json": s.output_json is not None,
                "url_text": f"/api/tasks/{run.task_id}/exports/{s.step}.txt",
                "url_json": f"/api/tasks/{run.task_id}/exports/{s.step}.json" if s.output_json else None,
            })
        elif s.status == "failed":
            err_msg = _chat_msg(s.agent, f"❌ 我这边失败了：{(s.error or '')[:150]}", kind="error")
            _persist_chat(run, err_msg)
            await _broadcast(run, "chat.message", err_msg)


# 哪些 step 在产出后过"快速评审"质量门
REVIEW_GATES = {"material_parsing", "point_extraction", "upward_optimization", "copywriting"}


QUICK_REVIEW_PROMPT = """\
你是「会汇报」集群的质量检查员。{display} 刚完成了 {label}。请你**快速评审**他这一步的产物，给出是否接受。

# 该 step 产物（output_json 摘要）
{output_summary}

# 评审要点
- material_parsing: 字段齐全？数字未编造？data_gaps 注明？
- point_extraction: 一句话总结？≤3 重点？风险有 impact？诉求具体？
- upward_optimization: 结论前置？风险显化？诉求可执行？
- copywriting: 字数与 duration={duration} 匹配？句长≤25 字？每段动词开头？

# 必须输出格式（严格 JSON 代码块）
```json
{{
  "comment": "≤30 字的简评，专业克制，可以是夸奖也可以是建议",
  "accept": true,
  "reason_if_reject": null
}}
```
- accept=false 时 reason_if_reject 必须给一句**具体的可执行重做指令**
- 不要过度严格：除非真的有明显缺陷再 reject，正常质量就 accept
"""


async def _quick_review(s: StepState, run: TaskRun) -> dict:
    """让 reviewer 快速点评一个 step 的产物。返回 {accept, comment, reason_if_reject}."""
    display = AGENT_DISPLAY.get(s.agent, s.agent)
    output = s.output_json or {}
    payload = output.get("payload") if isinstance(output, dict) else None
    summary_obj = payload if payload else output
    output_summary = json.dumps(summary_obj, ensure_ascii=False)[:1200]

    prompt = QUICK_REVIEW_PROMPT.format(
        display=display, label=s.label,
        output_summary=output_summary,
        duration=run.duration,
    )
    try:
        extra_env = await env_for_agent("reviewer")
        res = await run_agent_turn(
            agent_id="reviewer", message=prompt,
            timeout_sec=90, extra_env=extra_env,
        )
        await report_usage(
            agent_id="reviewer", task_id=run.task_id,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )
        j = extract_json(res.text) or {}
        return {
            "accept":  bool(j.get("accept", True)),
            "comment": (j.get("comment") or "看上去 OK").strip()[:80],
            "reason":  (j.get("reason_if_reject") or "").strip()[:200] or None,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("quick review failed: %s", e)
        return {"accept": True, "comment": "（评审通道暂时不可用，先放行）", "reason": None}


async def _gate_review(s: StepState, run: TaskRun, prev: dict[str, StepState]) -> None:
    """在指定 step success 后跑快速评审；若被否决，自动重做 1 次。"""
    if s.step not in REVIEW_GATES or s.status != "success":
        return

    verdict = await _quick_review(s, run)

    # 反馈进群（哪怕通过也展示，让讨论感更强）
    if verdict["accept"]:
        msg = _chat_msg("reviewer", f"✓ 评审：{verdict['comment']}", kind="result")
        _persist_chat(run, msg)
        await _broadcast(run, "chat.message", msg)
        return

    # 否决：群里 warn + 让原 agent 带反馈重做（最多 1 次）
    warn = _chat_msg(
        "reviewer",
        f"⚠ 评审：{verdict['comment']}。{('建议：' + verdict['reason']) if verdict['reason'] else ''}",
        kind="error",
    )
    _persist_chat(run, warn)
    await _broadcast(run, "chat.message", warn)

    instruction = verdict["reason"] or "请重新优化该步骤"
    # 重做（已经 success 状态，需要先重置）
    s.status = "pending"
    s.output_text = ""
    s.output_json = None
    s.started_at = s.ended_at = None
    await _run_step_with_instruction(s, run, prev, instruction)


def create_task(
    *, task_id: str, title: str, report_type: str, audience: str,
    duration: str, style: str, raw_text: str,
) -> TaskRun:
    run = TaskRun(
        task_id=task_id, title=title, report_type=report_type,
        audience=audience, duration=duration, style=style, raw_text=raw_text,
        steps=[StepState(step=k, label=l, agent=a) for k, a, l, _ in STEPS],
    )
    _runs[task_id] = run
    return run


async def execute(run: TaskRun) -> None:
    # coordinator 开场白
    intro = _chat_msg(
        "coordinator",
        f"收到「{run.title}」。我把它拆给团队 8 位同事。关键节点产物会先经过质量检查员评审，通过才进下一步。",
        kind="intro",
    )
    _persist_chat(run, intro)
    await _broadcast(run, "chat.message", intro)

    prev: dict[str, StepState] = {}
    by_key = {s.step: s for s in run.steps}
    for step_key, agent, label, depends in STEPS:
        s = by_key[step_key]
        if depends and prev.get(depends, StepState("", "", "")).status != "success":
            s.status = "skipped"
            s.started_at = s.ended_at = time.time()
            await _broadcast(run, "task.step", _step_event(s))
            continue
        await _run_step(s, run, prev)
        # 关键 step 过质量门：reviewer 即时点评，否决则带反馈重做 1 次
        if s.status == "success":
            await _gate_review(s, run, prev)
        prev[step_key] = s

    has_failed = any(s.status == "failed" for s in run.steps)
    has_skipped = any(s.status == "skipped" for s in run.steps)
    has_success = any(s.status == "success" for s in run.steps)
    run.status = (
        "done" if not has_failed and not has_skipped
        else "partial" if has_success
        else "failed"
    )
    _persist_final(run)
    # coordinator 收尾发言
    closing_text = {
        "done":   "✅ 初稿全部就绪。您看看摘要、讲稿、HTML、视频和审校建议，需要哪里调整随时告诉我。",
        "partial":"⚠️ 主要内容已就绪，但有几步未跑完。您可以先看可用部分。",
        "failed": "❌ 这次跑不通，请检查管台错误并重新提交。",
    }.get(run.status, "已完成。")
    closing = _chat_msg("coordinator", closing_text, kind="result")
    _persist_chat(run, closing)
    await _broadcast(run, "chat.message", closing)
    await _broadcast(run, "task.done", {"status": run.status})
    for q in list(run.subscribers):
        await q.put(None)

    # 推到管台 pipelines
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as cli:
            await cli.post("http://127.0.0.1:8100/admin/api/pipelines/ingest",
                           json=run.to_dict())
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 用户反馈触发 refine
#
# user message → coordinator 决策（重跑哪些 step + 用什么约束）
# → 顺序重跑这些 step（带新约束 + 原产物作上下文）
# → 每步重跑都广播到群聊 + 新产物覆盖落盘
# ─────────────────────────────────────────────────────────────


REFINE_PLAN_PROMPT = """\
你是「会汇报」集群的总控。用户对当前汇报材料给了反馈，请你决定让哪几位同事重做。

# 当前任务
title: {title}
report_type: {report_type}
audience: {audience}
duration: {duration}
style: {style}

# 当前各 step 状态
{step_states}

# 各位同事的职责简介
- material        资料员：解析原始材料 → MaterialPool
- point-extractor 分析师：从 MaterialPool 提炼一句话 + 重点 + 风险 + 诉求 + 计划
- structure       结构师：按 report_type 选大纲
- upward-opt      表达教练：把表达改写为领导视角（结论前置、风险显化、诉求具体）
- copywriter      文书：写讲稿 + 每页文案
- html-designer   设计师：HTML 演示工程
- video-producer  视频制作：MiniMax TTS 配音
- reviewer        质量检查员：审校建议

# 用户反馈
{feedback}

# 你的任务
判断这条反馈需要让哪些 step 重做（可能多个、可能 0 个）。常见模式：
- "再短一点 / 再精炼" → copywriter
- "更突出问题 / 风险讲透" → upward-opt + copywriter（视角与文字都要重写）
- "重点不对 / 漏了某事" → point-extractor + 后续所有 step
- "标题不好" → 不重做 step，由你直接给新 title
- "讲稿哪一段太啰嗦" → copywriter
- 单纯赞美 / 没诉求 → 空 refine

# 必须输出格式（严格 JSON 代码块，不要其它文字）
```json
{{
  "explanation": "一句话告诉用户你打算怎么办（≤40 字）",
  "refine": [
    {{"step": "upward_optimization", "agent": "upward-opt",
      "instruction": "把每条 risk 的 impact 讲透，加一句对业务的具体冲击"}}
  ],
  "new_title": null
}}
```
- step 用英文 key（material_parsing/point_extraction/structure_building/upward_optimization/copywriting/html_design/video_production/review）
- instruction 是给那位同事的具体指令，要可执行
- 如果只需要改标题，refine 留空数组，给 new_title 字符串
"""


def _step_states_summary(run: TaskRun) -> str:
    lines = []
    for s in run.steps:
        line = f"- {s.step} ({s.agent}): {s.status}"
        if s.output_json:
            # 取个简短摘要
            j = s.output_json
            if isinstance(j, dict):
                if "summary" in j:
                    line += f" — {str(j['summary'])[:80]}"
                elif "payload" in j and isinstance(j["payload"], dict):
                    line += f" — {len(j['payload'])} 字段"
                else:
                    line += f" — {len(j)} 字段"
        lines.append(line)
    return "\n".join(lines)


async def handle_user_feedback(run: TaskRun, feedback: str) -> None:
    """用户反馈 → coordinator 决策 → 重跑指定 step."""
    # 1. 让 coordinator 给计划
    typing = _chat_msg("coordinator", "…", "intro")
    _persist_chat(run, typing)
    await _broadcast(run, "chat.message", typing)

    prompt = REFINE_PLAN_PROMPT.format(
        title=run.title, report_type=run.report_type,
        audience=run.audience, duration=run.duration, style=run.style,
        step_states=_step_states_summary(run),
        feedback=feedback,
    )

    extra_env = await env_for_agent("coordinator")

    try:
        res = await run_agent_turn(
            agent_id="coordinator", message=prompt,
            timeout_sec=120, extra_env=extra_env,
        )
        await report_usage(
            agent_id="coordinator", task_id=run.task_id,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )
        plan = extract_json(res.text) or {}
        explanation = plan.get("explanation") or "好，我安排团队按你说的重做。"
        refine_items = plan.get("refine") or []
        new_title = plan.get("new_title")

        # 更新打字消息为 coordinator 的解释
        typing["text"] = explanation
        typing["kind"] = "result"
        typing["tokens"] = res.prompt_tokens + res.completion_tokens
        await _broadcast(run, "chat.message.update", typing)
    except Exception as e:  # noqa: BLE001
        typing["text"] = f"我这边出了点状况：{str(e)[:120]}"
        typing["kind"] = "result"
        await _broadcast(run, "chat.message.update", typing)
        return

    # 直接改标题
    if new_title and isinstance(new_title, str):
        old = run.title
        run.title = new_title.strip()[:80]
        ack = _chat_msg(
            "coordinator",
            f"📝 标题已更新：「{old}」→「{run.title}」",
            kind="result",
        )
        _persist_chat(run, ack)
        await _broadcast(run, "chat.message", ack)

    # 顺序重跑各 step
    if refine_items:
        # 重置后续步骤为 pending（重跑某个 step 的话，依赖它的下游也需要刷新）
        impacted = _expand_impacted_steps([it["step"] for it in refine_items if "step" in it])
        for s in run.steps:
            if s.step in impacted:
                s.status = "pending"
                s.output_text = ""
                s.output_json = None
                s.started_at = s.ended_at = None
                s.error = None
        run.status = "running"
        await _broadcast(run, "task.done", {"status": "running"})  # 让前端切回进行中

        prev: dict[str, StepState] = {s.step: s for s in run.steps if s.status == "success"}
        for it in refine_items:
            step_key = it.get("step")
            instruction = it.get("instruction") or ""
            target = next((s for s in run.steps if s.step == step_key), None)
            if not target:
                continue
            await _run_step_with_instruction(target, run, prev, instruction)
            if target.status == "success":
                prev[step_key] = target

        # 也跑被影响但未在 refine 列表里的下游
        for s in run.steps:
            if s.status == "pending":
                await _run_step_with_instruction(s, run, prev, "")
                if s.status == "success":
                    prev[s.step] = s

        # 收尾
        has_failed = any(s.status == "failed" for s in run.steps)
        run.status = "partial" if has_failed else "done"
        _persist_final(run)
        closing = _chat_msg("coordinator", "✅ 已按你的反馈更新，您再看看。", kind="result")
        _persist_chat(run, closing)
        await _broadcast(run, "chat.message", closing)
        await _broadcast(run, "task.done", {"status": run.status})


def _expand_impacted_steps(steps: list[str]) -> set[str]:
    """给定一组需要重跑的 step，返回它们 + 所有下游 step 集合."""
    order = [k for k, _, _, _ in STEPS]
    if not steps:
        return set()
    earliest = min((order.index(s) for s in steps if s in order), default=len(order))
    return set(order[earliest:])


async def _run_step_with_instruction(
    s: StepState, run: TaskRun, prev: dict[str, StepState], instruction: str,
) -> None:
    """重跑某个 step，在 prompt 末尾追加用户的具体 instruction."""
    s.status = "running"
    s.started_at = time.time()
    await _broadcast(run, "task.step", _step_event(s))

    intro = _chat_msg(
        s.agent,
        (f"收到反馈，我来重做 — {instruction[:60]}" if instruction
         else f"上游变了，我同步重做一遍 {s.label}"),
        kind="intro",
    )
    _persist_chat(run, intro)
    await _broadcast(run, "chat.message", intro)

    prompt = _step_prompt(s.step, run, prev)
    if instruction:
        prompt += f"\n\n# 用户最新反馈（请按这个调整）\n{instruction}\n"

    try:
        timeout = 240 if s.step in ("copywriting", "html_design", "review") else 180
        if s.step == "video_production":
            timeout = 360
        extra_env = await env_for_agent(s.agent)
        res = await run_agent_turn(
            agent_id=s.agent, message=prompt, timeout_sec=timeout,
            extra_env=extra_env,
        )
        s.output_text = res.text
        s.output_json = extract_json(res.text)
        s.provider = res.provider
        s.model = res.model
        s.prompt_tokens = res.prompt_tokens
        s.completion_tokens = res.completion_tokens
        s.status = "success"
        await report_usage(
            agent_id=s.agent, task_id=run.task_id,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )
        _persist_step(run, s)
    except Exception as e:  # noqa: BLE001
        s.error = str(e)[:500]
        s.status = "failed"
        log.exception("refine step %s failed", s.step)
    finally:
        s.ended_at = time.time()
        await _broadcast(run, "task.step", _step_event(s))
        if s.status == "success":
            summary = _summarize_output(s.step, s.output_json, s.output_text)
            msg = _chat_msg(s.agent, summary, kind="result")
            _persist_chat(run, msg)
            await _broadcast(run, "chat.message", msg)
            await _broadcast(run, "task.artifact", {
                "step": s.step, "kind": s.step, "ready": True,
                "has_json": s.output_json is not None,
                "url_text": f"/api/tasks/{run.task_id}/exports/{s.step}.txt",
                "url_json": f"/api/tasks/{run.task_id}/exports/{s.step}.json" if s.output_json else None,
            })
        elif s.status == "failed":
            err = _chat_msg(s.agent, f"❌ 重做失败：{(s.error or '')[:140]}", kind="error")
            _persist_chat(run, err)
            await _broadcast(run, "chat.message", err)
