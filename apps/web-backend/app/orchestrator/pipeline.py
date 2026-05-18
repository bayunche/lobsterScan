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
from ..render.html_builder import build_presentation
from ..video.providers import (
    VideoProvider,
    build_prompt_for_provider,
    resolve_from_openclaw_json,
    stub_response_payload,
)

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

# 输出格式（强制 · 必须两段）
你必须按这个顺序输出**两段内容**:

## 第 1 段:思考过程(自然语言,80-300 字)
告诉用户你是怎么读这道题的、抓住了哪些关键点、怎么决策的、为什么选这种结构。
**不要列条目堆砌**,要像跟同事说话一样讲清楚思路。**不要写 emoji,不要 markdown 表格**。

## 第 2 段:结构化结果(JSON 代码块)
紧接着上面,输出**一段** ```json ... ``` 代码块,内部 JSON 必须能被 json.loads() 直接解析。
如果信息不足以填某字段,填 [] 或 null,不要省略字段。**不要在 JSON 里写说明 / 校验报告 / emoji。**

只允许这两段,顺序固定:先「思考过程」自然语言,再「JSON 代码块」。
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
        # 由 html_builder 产出的自包含投屏页,提一个常驻 alias 进 artifacts 列表
        index_html = d / "web-presentation" / "index.html"
        if index_html.exists():
            out["web_presentation.html"] = {
                "url": f"/api/tasks/{self.task_id}/exports/web-presentation/index.html",
                "size": index_html.stat().st_size,
            }
        return out


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---- video provider (按需读管台 openclaw.json,决定走哪条数字人源) ----
def _current_video_provider() -> VideoProvider:
    return resolve_from_openclaw_json(settings.project_root / "openclaw" / "openclaw.json")


# ---- 全局 task store ----
_runs: dict[str, TaskRun] = {}


def get_run(task_id: str) -> TaskRun | None:
    """先看内存,内存没有就 lazy-load 从 disk 恢复(uvicorn --reload 后能找回 done 的 task)"""
    run = _runs.get(task_id)
    if run is not None:
        return run
    return _load_run_from_disk(task_id)


def list_runs(limit: int = 20) -> list[TaskRun]:
    """合并内存里的活动 task + 磁盘上的历史 task,按 created_at 降序"""
    seen: dict[str, TaskRun] = dict(_runs)
    # 扫描 data/outputs/ 把没在内存里的历史 task lazy-load 进来
    try:
        for d in OUTPUTS_ROOT.iterdir():
            if not d.is_dir():
                continue
            tid = d.name
            if tid in seen:
                continue
            r = _load_run_from_disk(tid)
            if r:
                seen[tid] = r
    except (FileNotFoundError, OSError):
        pass
    return sorted(seen.values(), key=lambda r: r.created_at, reverse=True)[:limit]


def _load_run_from_disk(task_id: str) -> TaskRun | None:
    """从 data/outputs/<task_id>/task.json + chat.jsonl 恢复一个 TaskRun(只用于查询,
    不参与新事件 dispatch — subscribers 是空的,但 history replay 能拿到 chat)。"""
    from datetime import datetime as _dt
    task_dir = OUTPUTS_ROOT / task_id
    task_json = task_dir / "task.json"
    if not task_json.exists():
        return None
    try:
        j = json.loads(task_json.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("parse task.json for %s failed: %s", task_id, e)
        return None

    # task.json 没保留 raw_text,这里给空字符串(已 done 的 task 不会再 re-run material)
    run = TaskRun(
        task_id=j.get("task_id", task_id),
        title=j.get("title", ""),
        report_type=j.get("report_type", "project_progress"),
        audience=j.get("audience", ""),
        duration=j.get("duration", ""),
        style=j.get("style", ""),
        raw_text="",
        status=j.get("status", "done"),
    )
    try:
        run.created_at = _dt.fromisoformat(j["created_at"]).timestamp()
    except Exception:  # noqa: BLE001
        pass
    # 复原 steps(状态 + 时间 + tokens,LLM 文本细节不恢复)
    for sd in j.get("steps", []):
        st = StepState(step=sd["step"], label=sd["label"], agent=sd["agent"],
                       status=sd.get("status", "pending"))
        for k in ("provider", "model", "error"):
            setattr(st, k, sd.get(k))
        if isinstance(sd.get("tokens"), int):
            st.prompt_tokens = sd["tokens"]  # 粗略归到 prompt
        run.steps.append(st)
    # 加载 chat history(jsonl 每行一条 msg)
    chat_jsonl = task_dir / "chat.jsonl"
    if chat_jsonl.exists():
        msgs: list[dict] = []
        for line in chat_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                msgs.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
        run._chat_log = msgs  # type: ignore[attr-defined]
    # 缓存到 _runs,后续访问直接命中
    _runs[task_id] = run
    return run


# ---- 事件订阅 ----
async def subscribe(task_id: str):
    """SSE 长连接:任务期间 + 任务结束后都保持监听,允许后续 refine 推流."""
    run = _runs.get(task_id)
    if not run:
        return
    q: asyncio.Queue = asyncio.Queue()
    # 先把已知状态预填到队列(reconnect 友好),再注册到广播池,确保历史与新事件不会错序
    for s in run.steps:
        q.put_nowait(("task.step", _step_event(s)))
    for msg in getattr(run, "_chat_log", []):
        q.put_nowait(("chat.message", msg))
    q.put_nowait(("task.done", {"status": run.status}))
    run.subscribers.append(q)
    try:
        while True:
            evt = await q.get()
            if evt is None:
                # 留个兜底 sentinel,正常情况下 execute / handle_user_feedback 不会 put None
                break
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


def _extract_analysis(text: str | None) -> str:
    """从 agent LLM 输出里抽出"思考过程" — 去掉所有 ```...``` 代码块(主要是 ```json),
    剩下的解释性文本(agent 怎么读题 / 怎么决策的)就是给用户看的 reasoning。

    限长 1200 字符,过长截断;过短(< 30 字)返回 "" 让前端不渲染。
    """
    if not text:
        return ""
    import re
    cleaned = re.sub(r"```[a-zA-Z]*\n.*?\n\s*```", "", text, flags=re.S).strip()
    # 多余空行收一下
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) < 30:
        return ""
    if len(cleaned) > 1200:
        cleaned = cleaned[:1200].rstrip() + "…"
    return cleaned


def _step_artifacts(task_id: str, step: str, has_json: bool, has_text: bool = True) -> list[dict]:
    """根据 step 名拼出该步产物在 /api/tasks/{id}/exports/ 下的访问 URL 列表。

    返回结构供前端 Bubble 渲染:[{label, url, kind, open_in_new?}]
    - kind 用于前端按 MIME 决定 open / download(json/md → 打开,mp3/mp4 → 浏览器播放,
      未知 → download)
    - open_in_new = True 时强制新 tab 打开(投屏 HTML 这种)
    """
    base = f"/api/tasks/{task_id}/exports"
    items: list[dict] = []
    if has_text:
        items.append({"label": "文本", "url": f"{base}/{step}.txt", "kind": "txt"})
    if has_json:
        items.append({"label": "JSON", "url": f"{base}/{step}.json", "kind": "json"})
    # step 特殊产物 hint:让 Bubble 列出 agent 真正产出的可视化文件
    extras: dict[str, list[dict]] = {
        "copywriting": [
            {"label": "讲稿 (md)", "url": f"{base}/script.md", "kind": "md"},
        ],
        "html_design": [
            {"label": "▶ 投屏 HTML", "url": f"{base}/web-presentation/index.html",
             "kind": "html", "open_in_new": True},
        ],
        "video_production": [
            # 这些路径只在 minimax skill 真跑通时存在;前端拿到 404 自己降级
            {"label": "数字人开场 mp4", "url": f"{base}/video/intro.mp4", "kind": "mp4", "probe": True},
            {"label": "SRT 字幕", "url": f"{base}/audio/subtitles.srt", "kind": "srt", "probe": True},
        ],
    }
    items.extend(extras.get(step, []))
    return items


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
    """append 到内存 + jsonl 落盘 — uvicorn --reload 不丢消息"""
    run_chat = getattr(run, "_chat_log", None)
    if run_chat is None:
        run_chat = []
        run._chat_log = run_chat
    run_chat.append(msg)
    # 同步落盘:每条 message 一行 JSON,追加写
    try:
        d = run.output_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "chat.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("persist chat to disk failed: %s", e)


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
        provider = _current_video_provider()
        body = build_prompt_for_provider(
            provider=provider,
            task_id=run.task_id,
            duration=run.duration,
            audience=run.audience,
            style=run.style,
            script=script,
            slides_json=json.dumps(slides, ensure_ascii=False)[:800],
            narrations_json=json.dumps(narrations, ensure_ascii=False)[:600],
            narrations_count=len(narrations),
        )

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


def _write_srt_from_narrations(run: TaskRun, narrations: list[dict]) -> str | None:
    """none 模式专用:从 narrations 直接生成 SRT 字幕落盘,不依赖 agent."""
    if not narrations:
        return None
    audio_dir = run.output_dir() / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    srt_path = audio_dir / "subtitles.srt"

    def _fmt(ts: float) -> str:
        h = int(ts // 3600); m = int((ts % 3600) // 60)
        s = int(ts % 60); ms = int((ts - int(ts)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    t = 0.0
    for i, n in enumerate(narrations, 1):
        text = (n.get("text") or "").strip() if isinstance(n, dict) else str(n).strip()
        if not text:
            continue
        # 简单按字符数估时长,中文每字 0.18s,最少 2s
        dur = max(2.0, len(text) * 0.18)
        lines.append(f"{i}\n{_fmt(t)} --> {_fmt(t + dur)}\n{text}\n")
        t += dur
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(srt_path.relative_to(run.output_dir().parent.parent))


def _build_html_artifact(
    run: TaskRun, s: StepState, prev: dict[str, StepState],
) -> None:
    """html_design step success 后,后端真正渲染 self-contained 投屏页."""
    copy_step = prev.get("copywriting")
    if not copy_step or not copy_step.output_json:
        return
    review_step = next((x for x in run.steps if x.step == "review" and x.output_json), None)
    video_step  = next((x for x in run.steps if x.step == "video_production" and x.output_json), None)
    try:
        result = build_presentation(
            task_id=run.task_id,
            title=run.title,
            audience=run.audience,
            duration=run.duration,
            style=run.style,
            report_type=run.report_type,
            copywriting=copy_step.output_json,
            review=review_step.output_json if review_step else None,
            video_meta=video_step.output_json if video_step else None,
            output_root=run.output_dir(),
        )
        # 把 builder 结果合并进 html_design.output_json,前端 / 管台能直接看到
        merged = dict(s.output_json or {})
        merged["builder"] = result
        merged["index_path"] = result.get("rel_path")
        s.output_json = merged
        _persist_step(run, s)
        log.info(
            "html-builder: %s pages=%s bytes=%s",
            result.get("rel_path"), result.get("pages"), result.get("bytes"),
        )
    except Exception as e:  # noqa: BLE001
        log.exception("html-builder failed for %s: %s", run.task_id, e)


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
    intro_text = STEP_INTRO.get(s.step) or f"我开始处理 {s.label}"
    # video_production 的开场白根据 provider 自适应
    if s.step == "video_production":
        provider = _current_video_provider()
        if provider.id == "minimax":
            intro_text = "我用 MiniMax TTS + Hailuo 数字人给每段讲稿配音。"
        elif provider.id == "none":
            intro_text = "当前是「仅字幕」模式,我只生成 SRT。"
        else:
            intro_text = f"当前数字人源 = {provider.display_name},暂未接好,我走降级。"
    intro_msg = _chat_msg(s.agent, intro_text, kind="intro")
    _persist_chat(run, intro_msg)
    await _broadcast(run, "chat.message", intro_msg)

    # video_production 短路:provider 未实现 或 none 模式 → 不调 agent,后端直接出降级 JSON
    if s.step == "video_production":
        provider = _current_video_provider()
        # 走短路的两种情况:
        #   1) 占位 provider(heygen / sadtalker 等)→ 纯 stub
        #   2) none 模式 → 后端直接生成 SRT 字幕,不去喊 agent
        if (not provider.implemented) or provider.id == "none":
            narrations = (prev["copywriting"].output_json or {}).get("narrations") or []
            payload = stub_response_payload(
                provider, narrations_count=len(narrations), style=run.style,
            )
            if provider.id == "none":
                srt_path = _write_srt_from_narrations(run, narrations)
                if srt_path:
                    payload["subtitle_path"] = srt_path
                    payload["degrade_reason"] = "subtitle_only_mode"
            s.output_json = payload
            s.output_text = json.dumps(s.output_json, ensure_ascii=False, indent=2)
            s.provider = "stub"
            s.model = provider.video_model or "n/a"
            s.status = "success"
            _persist_step(run, s)
            s.ended_at = time.time()
            await _broadcast(run, "task.step", _step_event(s))
            if provider.id == "none":
                summary = f"✓ 仅字幕模式,SRT 已生成({len(narrations)} 段)"
            else:
                summary = f"⚠ 数字人源 {provider.display_name} 未接入,已写入降级元数据"
            msg = _chat_msg(s.agent, summary, kind="result")
            msg["artifacts"] = _step_artifacts(run.task_id, s.step, has_json=True)
            _persist_chat(run, msg)
            await _broadcast(run, "chat.message", msg)
            await _broadcast(run, "task.artifact", {
                "step": s.step, "kind": s.step, "ready": True,
                "has_json": True,
                "url_text": f"/api/tasks/{run.task_id}/exports/{s.step}.txt",
                "url_json": f"/api/tasks/{run.task_id}/exports/{s.step}.json",
            })
            return

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
        # html_design 成功后,后端实际渲染 self-contained HTML 投屏页
        if s.step == "html_design":
            _build_html_artifact(run, s, prev)
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
            # 把该 step 产物 URL 嵌进 message,前端 Bubble 直接渲染成可点击 chip
            result_msg["artifacts"] = _step_artifacts(
                run.task_id, s.step, has_json=s.output_json is not None,
            )
            # agent 的"思考过程"(LLM 文本去掉 JSON 块剩下的解释性内容)
            analysis = _extract_analysis(s.output_text)
            if analysis:
                result_msg["analysis"] = analysis
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
    # 不再 put(None) 强制断流 — 让 SSE 保持长连接,用户随时可以发反馈触发 refine

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
        if s.step == "html_design":
            _build_html_artifact(run, s, prev)
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
            msg["artifacts"] = _step_artifacts(
                run.task_id, s.step, has_json=s.output_json is not None,
            )
            analysis = _extract_analysis(s.output_text)
            if analysis:
                msg["analysis"] = analysis
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
