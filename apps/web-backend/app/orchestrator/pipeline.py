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

# agent_id → step_key 反查(handoff 时 agent 报 agent-id,backend 翻译成 step_key)
AGENT_TO_STEP: dict[str, str] = {agent: step for step, agent, *_ in STEPS}
STEP_TO_AGENT: dict[str, str] = {step: agent for step, agent, *_ in STEPS}

# 漏报 handoff 时的默认顺序(回退用 · 与 STEPS 顺序一致)
DEFAULT_NEXT_STEP: dict[str, str] = {
    "material_parsing":    "point_extraction",
    "point_extraction":    "structure_building",
    "structure_building":  "upward_optimization",
    "upward_optimization": "copywriting",
    "copywriting":         "html_design",
    "html_design":         "video_production",
    "video_production":    "review",
    "review":              "DONE",
}

# step → 它在 pipeline handler 里**实际通过 prev[...] 读取的上游 step state** 完整依赖。
# 与 DEFAULT_NEXT_STEP(线性下一棒)不同:这是"真跑前哪些上游 step 必须已 success"。
# 用途:v2 work-driver 的 step 级依赖门控(harness._deps_ready)。
# 背景:subscription 闸门只看 4 个 CORE_ARTIFACT 版本(MaterialPool/ReportCore/Outline/
# Script),无法表达"copywriting 读 upward_optimization step state"这类依赖
# (upward-opt 不产出独立 artifact)→ 曾导致 copywriter 在 upward_opt 完成前被提前触发,
# prev["upward_optimization"] KeyError。STEP_DEPENDS 让门控按真实 step 依赖判定。
STEP_DEPENDS: dict[str, tuple[str, ...]] = {
    "material_parsing":    (),
    "point_extraction":    ("material_parsing",),
    "structure_building":  ("point_extraction",),
    "upward_optimization": ("point_extraction",),
    "copywriting":         ("upward_optimization", "structure_building"),
    "html_design":         ("copywriting",),
    "video_production":     ("copywriting",),
    "review":              ("copywriting",),
}


def _prev_json(prev: dict, *keys: str) -> dict:
    """按顺序取第一个存在且有 output_json 的上游 step state 的 output_json;全缺返回 {}。

    v2 work-driver 下 step 可能乱序触发,上游 step state 未必已写入 prev —— 防御访问避免
    KeyError(与 harness 的 step 依赖门控构成双保险:门控保质量、本函数保不崩)。
    多 key 表示"优先用前者,缺失回退后者"(如 upward_optimization 缺则回退 point_extraction)。
    """
    for k in keys:
        s = prev.get(k)
        if s is not None and getattr(s, "output_json", None):
            return s.output_json
    return {}


# step → agent-id 列表(handoff.to 接受 agent-id;DONE / coordinator 表示终止)
HANDOFF_VALID_TARGETS: list[str] = list(AGENT_TO_STEP.keys()) + ["coordinator", "DONE"]

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
    supplement: str = ""              # 用户补充说明,跨 step 全局共享
    # coordinator 给 8 个 step 的动态 briefing(本次任务特有的侧重 / 强调 / 硬约束)
    # 由 coordinator 在 pipeline 启动前的"任务理解"阶段生成,_agent_brief_block 注入到 ctx。
    agent_briefs: dict[str, str] = field(default_factory=dict)
    coordinator_brief_summary: str = ""   # 给前端展示用的整体 briefing 摘要
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    steps: list[StepState] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    # v2 群聊化 harness 路径 flag(P1, spec 001-v2-chat-protocol-state)。
    # 默认 v1(行为与改造前完全一致);非 "v1"/"v2" 一律降级为 "v1"(FR-002 防御)。
    # 仅决定本任务是否启用 v2 事件 emit + artifact 版本化,worker/coordinator 行为不变(P2-P5)。
    harness_version: str = "v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "report_type": self.report_type,
            "audience": self.audience,
            "duration": self.duration,
            "style": self.style,
            "status": self.status,
            "harness_version": self.harness_version,  # v2: 仅 admin / log 用,不外露用户层(原则 I)
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


# ────────────────────────────────────────────────────────────────────
# 汇报时长 → 总分结构 single source of truth
# ────────────────────────────────────────────────────────────────────
# 用户选的 duration 决定材料的组织节奏:
#   1分钟  → 总-分 精简:只讲 1-2 个核心成果,无展开
#   3分钟  → 总-分-总 标准:3 个分点 + 收束
#   5分钟  → 总-分-分-总 展开:每个分点可以带数据/案例
#
# 所有 step(structure / upward / copywriter / html / review)读这张表。
DURATION_PROFILE: dict[str, dict] = {
    "1分钟": {
        "label":         "1 分钟精要",
        "structure":     "总-分",
        "chapters_n":    "5-6",                 # 章节总数(对齐 PRD:HTML 投屏 ≥ 5 页)
        "narrations_n":  "4-5",                 # narration 段数(cover 无 narration,可少于章节)
        "narration_len": "40-60",               # 单段字数(4-5 段 × 40-60 ≈ 200-300 总)
        "script_words":  "200-300",
        "structure_template": (
            "1. cover(封面 · 标题 + 一句话定位)\n"
            "2. summary(总览 · 核心一句话 + 1 个量化数据)\n"
            "3. completed(分1 · 已落地最关键 1 点 + 量化结果)\n"
            "4. risks(分2 · 最关键风险 + 影响;无风险时写'暂无显著风险待办')\n"
            "5. next_steps(收束 · 1-2 条最关键动作 + owner)\n"
            "6. closing(可选 · 一句话承诺或会议安排)"
        ),
        "writing_principle": (
            "极致克制 — 一个观点 + 一个数字 + 一个动作。"
            "**不要展开**,**不要案例**,**不要细节**。"
            "用户没时间听铺垫,30 秒进入核心数据。"
            "5-6 章节是为了投屏页数下限,不是为了讲更多 — 每页字更少、留白更多。"
        ),
    },
    "3分钟": {
        "label":         "3 分钟标准",
        "structure":     "总-分-总",
        "chapters_n":    "5-6",
        "narrations_n":  "7-9",
        "narration_len": "55-90",
        "script_words":  "500-750",
        "structure_template": (
            "1. cover(封面)\n"
            "2. summary(总览 · 一句话 + 进展状态)\n"
            "3. completed(分1 · 已落地能力的业务价值)\n"
            "4. risks(分2 · 风险与应对)\n"
            "5. next_steps(分3→总收束 · 下一步 + 诉求)\n"
            "6. closing(可选 · 一句话呼吁/承诺)"
        ),
        "writing_principle": (
            "标准向上汇报节奏 — 总开篇定基调,3 个分点各有侧重(成果/风险/诉求),"
            "结尾收束到决策点。每个分点 1-2 段 narration,带 1 个量化数据。"
        ),
    },
    "5分钟": {
        "label":         "5 分钟展开",
        "structure":     "总-分-分-总",
        "chapters_n":    "7-8",
        "narrations_n":  "12-15",
        "narration_len": "70-100",
        "script_words":  "900-1200",
        "structure_template": (
            "1. cover(封面)\n"
            "2. summary(总览)\n"
            "3. completed(分1.1 · 已落地能力,可分 2 段)\n"
            "4. key_data(分1.2 · 业务数据深挖)\n"
            "5. in_progress(分2.1 · 进行中工作)\n"
            "6. risks(分2.2 · 风险与应对)\n"
            "7. support_needed(分3 · 具体诉求)\n"
            "8. next_steps(总收束 · 时间表)"
        ),
        "writing_principle": (
            "有空间展开 — 每个分点可带 1 个具体案例或数据细节。"
            "依然保持 总→分→总 整体节奏,不要变成流水账。"
            "前 30 秒讲完整体,后 4.5 分钟分点深入,最后 30 秒收束。"
        ),
    },
}


def _duration_block(duration: str) -> str:
    """渲染给 prompt 的"总分结构"约束段."""
    p = DURATION_PROFILE.get(duration) or DURATION_PROFILE["3分钟"]
    return (
        f"# 汇报时长 · {p['label']} · 结构 {p['structure']}\n"
        f"- 章节总数:**{p['chapters_n']}**\n"
        f"- narration 段数:**{p['narrations_n']}**\n"
        f"- 单段字数:**{p['narration_len']}** 字\n"
        f"- script_md 总字数:**{p['script_words']}** 字\n\n"
        f"## 章节模板\n{p['structure_template']}\n\n"
        f"## 写作原则\n{p['writing_principle']}"
    )


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
def _build_global_ctx(run: TaskRun) -> str:
    """构造全 step 共享的上下文块。

    supplement(用户补充说明)是**最高优先级业务指令** — 跟 audience/style 冲突时按 supplement。
    所有 agent 在生成内容前必须先读 supplement,并在自检环节回看是否落实。

    如果 coordinator 已经为当前 step 生成了 agent_brief,会自动附加到 ctx 末尾。
    """
    base = (
        f"- report_type: {run.report_type}\n"
        f"- audience:    {run.audience}\n"
        f"- duration:    {run.duration}\n"
        f"- style:       {run.style}"
    )
    if run.supplement and run.supplement.strip():
        supp = run.supplement.strip()
        base += (
            f"\n\n# 用户补充说明(最高优先级 — 遇冲突以此为准)\n"
            f"{supp}\n\n"
            f"**自检**:在输出 JSON 前,必须确认上述补充说明在你的产物中已经落实;"
            f"如某条说明无法落实(如缺数据),在 data_gaps / notes 字段明确记录原因,不要忽略。"
        )
    return base


# step → 你必须用的挂载 skill 清单(install-skills.sh 同步真值)
# 每条 {name, focus} — focus 是「为啥要读它」的一句话,逼 agent 真去看 SKILL.md
_REQUIRED_SKILLS_MAP: dict[str, list[dict[str, str]]] = {
    "material_parsing": [
        {"name": "material-parser",
         "focus": "附件解析方法 — xlsx/docx/pdf 抽 facts、人物、时间、风险的标准流程"},
        {"name": "kb-retriever",
         "focus": "知识检索方法 — 上游对接 RAG/向量库时的查询模板"},
    ],
    "point_extraction": [
        {"name": "point-extractor",
         "focus": "重点提炼方法论 — Phase1 候选清单 + Phase2 收敛 top3 的标准流程 / 评分维度 / 反过度提炼"},
    ],
    "structure_building": [
        {"name": "report-structure",
         "focus": "汇报结构方法论 — 总分/总分总/总分分总 的章节分布、转场词、role 标注规则"},
    ],
    "upward_optimization": [
        {"name": "upward-translator",
         "focus": "向上汇报口吻 — 数据前置、风险显化但不升级、客观语态的具体替换表"},
        {"name": "humanizer",
         "focus": "去 AI 味 — 禁用词清单(说白了/底层逻辑/本质上...)+ 句长节奏 + 自然停顿"},
    ],
    "copywriting": [
        {"name": "copywriter",
         "focus": "讲稿撰写方法论 — 信息池→钩子→narrations 双阶段、facts_used 溯源、info_retention ≥60%"},
        {"name": "web-video-presentation",
         "focus": "口播稿方法 — 详略策略、反流水账、商务汇报 vs B 站风格的对照表"},
    ],
    "html_design": [
        {"name": "web-design-engineer",
         "focus": "投屏 HTML 设计语汇 — 印刷工艺/印章/配准/数字突出/stagger reveal,反 AI slop 清单"},
        {"name": "web-video-presentation",
         "focus": "页面节奏对应口播节奏 — 一页 = 一个信息单元的对齐准则"},
        {"name": "gpt-image-2",
         "focus": "插画生成 — 需要图时调用,接 OpenAI 接口;不需要可以跳过"},
    ],
    "video_production": [
        {"name": "web-video-presentation",
         "focus": "口播 + 画面拼接的总指挥 — script 切段、配音节奏、字幕对齐"},
        {"name": "minimax-tts",
         "focus": "TTS 主链 — 走 mmx CLI 优先(MiMo-V2.5 plan 配额);失败再切其他"},
        {"name": "minimax-video",
         "focus": "MiniMax 数字人视频 — Hailuo-2.3 model,plan 配额可能不通,失败时 backend 兜底"},
        {"name": "klingai",
         "focus": "可灵 AI 数字人 — AK/SK 走 admin.db,401 时直接降级,不要 retry 死循环"},
        {"name": "heygen",
         "focus": "HeyGen V2 — avatar/voice/talking-photo 三维 ID,余额不足时降级"},
        {"name": "elevenlabs",
         "focus": "英文 TTS 兜底 — 中文场景一般用不到"},
        {"name": "ffmpeg",
         "focus": "视频拼接合成 — 数字人 + 配音 mux,慎用 -y 防覆盖"},
        {"name": "moviepy",
         "focus": "Python 端视频合成 — 兜底不可用 ffmpeg 时用"},
        {"name": "playwright-recording",
         "focus": "录屏方法 — 用 web-presentation 做底图录制时用"},
        {"name": "minimax-music",
         "focus": "背景音乐 — 一般保守汇报不加 BGM,除非用户明示"},
    ],
    "review": [
        {"name": "report-reviewer",
         "focus": "审校规则 — supplement 对账 / facts 溯源 / AI 信号词扫描 / suggestions 写法"},
        {"name": "humanizer",
         "focus": "AI 套话扫描 — 与 report-reviewer 配合,给 ai_signal_score 打分"},
    ],
}


def _required_skills_block(step: str) -> str:
    """声明本 step 必须用的挂载 skill — 强制 agent 先读 SKILL.md 再开干。"""
    skills = _REQUIRED_SKILLS_MAP.get(step) or []
    if not skills:
        return ""
    rows = "\n".join(
        f"  {i+1}. **{s['name']}** — {s['focus']}"
        for i, s in enumerate(skills)
    )
    return f"""

# 必读 · 本步骤挂载的 skill(SKILL.md 是单一真值)
你这次的角色专属能力**都封装在挂载的 skill 里**,**不是「可选参考」而是「必读起点」**。
开干之前,**先做这三件事**:

1. **定位 skill** — 你的 agent 工作区下有 `.agents/skills/<name>/SKILL.md`。
   不确定时用 `find ~/.openclaw* -type f -name "SKILL.md" -path "*<name>*"` 找到具体路径。
2. **完整读完 SKILL.md** — 不是扫一眼。重点看「方法论 / Schema / 反模式 / 自检清单」几个 section。
3. **照 SKILL.md 干,而不是凭直觉** — SKILL.md 写了哪几步、什么 schema、禁用什么词,你就照着出。

本步骤你必须用的 skill:
{rows}

# 强制声明
- 在最终 JSON 末尾加 `"skills_used": ["<name1>", "<name2>", ...]`,**声明你真读了哪些 SKILL.md 并照做**。
- 在思考过程里,**至少引用一条 SKILL.md 的具体规则**(原文片段或 section 名)证明你看了。
- 如果某个 skill 不适用本次输入(比如 minimax-music 但本次没要 BGM),**在 skills_used 用 `"<name>:skipped"` 标注**,别假装用过。
- **未声明 skills_used 一律视为偷懒**,reviewer 会直接打回。
"""


_HANDOFF_BLOCK_TEMPLATE = """

# 探索目标 · 你是有判断力的 agent,不是流水线工人

**总目标**:把用户的原始材料 + supplement,变成符合 audience/duration/style 三方约束的高质量汇报材料。
**你的角色**:看上面 SOUL.md / brief / skills,这是你的专长;但**怎么走、谁来走、走多深,你自己想清楚**。

总控(coordinator)在群里只做三件事:**(1) 任务开始时给每位 agent 一份 brief;(2) 谁卡住时在群里提醒;
(3) 兜底防死循环**。它**不会硬性规定你必须把活交给谁** — 这条 pipeline 不是固定流水线,是一群 agent
彼此协作完成共同目标。

# 转交 · 你跑完之后把活交给谁(由你判断 · 必填)
你必须在最终 JSON 末尾加这个字段:

```json
"handoff": {
  "to": "<agent-id>",         // 必填,见下面合法值
  "reason": "<≤40 字,为啥交给 ta>",
  "payload_hint": "<≤60 字,提示下一位重点看你产物里的哪几个字段>"
}
```

合法的 to 值(agent-id):
- material / point-extractor / structure / upward-opt / copywriter / html-designer / video-producer / reviewer
- coordinator(退给总控,声明任务无法继续或要重排)
- DONE(你判断整条 pipeline 已完成,reviewer 也审过了,可以收尾)

# 怎么想 handoff?(不是机械,是策略)
跑完你的活,先停下来问自己三个问题:
1. **我的产物质量过关吗?** 如果自检 fail(facts 编造 / 字数不够 / supplement 没落实)→
   `handoff.to = self`(交给同一个 agent 重做)+ `needs_retry` 字段。**不要为了走流程硬交给下一棒**。
2. **我的产物足够下一棒用吗?** 不够的话,**退给上游**(`to: material / point-extractor` 等)
   声明你缺什么数据;coordinator 会在群里告诉那位同事。
3. **谁最该接这棒?** 标准链是:material → point → structure → upward → copywriter → html-designer +
   video-producer → reviewer → DONE。但这是默认,**不是强制**。如果你判断:
   - HTML 和视频可以并行 → 你可以选择把 html-designer 和 video-producer 各发一棒(虽然每次只能 to 一个,
     但用 reason 告诉 coordinator "建议 X 并行后再合并")
   - 数据已经够好,跳过中间润色 → `to: reviewer` 直接审
   - 你就是最后一棒(reviewer 审完了)→ `to: DONE`
   按你的判断写,coordinator 不会硬塞你换。

本步骤的**默认下一棒**(无特殊情况就这么交):**{default_next}**
但如果你判断不该这么交,**就别这么交**;在 reason 里说明你的理由。
"""


_AUTONOMY_BLOCK = """

# 自主能力授权(所有 agent 共享 · 复杂问题时使用)
当你发现这次问题特别复杂(数据多 / 维度乱 / 上游缺信息 / 自检不通过)时,**你不必硬塞进一轮**。
你有 4 种合法的「自主拆解」方式,按优先级选:

1. **内部拆解**(思考过程里)— 在第 1 段思考过程里**显式列出**你的拆解步骤:
   `步骤 1: 先 X · 步骤 2: 再 Y · 步骤 3: 校验 Z`
   然后逐步推进,每步在思考过程留下推理痕迹。**别一句话糊弄过去**。

2. **调用挂载的 skill**(.agents/skills/ 下的 SKILL.md + scripts)— **见上面「必读 · skill」段落,
   这一节不再是「可选」而是「强制」**。SKILL.md 里写了的流程你必须照做,scripts 能跑就用 Bash 调。
   skill 的 stdout 一般是 JSON,你拿到后整合到自己的最终 JSON。

3. **声明 needs_help**(在最终 JSON 末尾)— 如果上游给你的数据缺关键字段,
   你可以在 JSON 加 `"needs_help": {"from": "material|point-extractor|user", "what": "..."}`,
   coordinator 会把这条诉求带回去重跑上游或问用户。
   **但不要逃避** — 能自己用思考过程补齐就别请求 needs_help。

4. **声明 needs_retry**(自检不过时)— 如果你跑完发现自己的产物自检不过(facts 编造 / 字数不达标 / supplement 没落实 / skills_used 没声明 / SKILL.md 规则没遵守),
   在 JSON 加 `"needs_retry": {"reason": "...", "hint": "<下一轮该怎么改>"}`,
   coordinator 会把这个 hint 加到下一轮 prompt 里让你重做。**不要假装通过**。

你的判断力 > 流水线机械。复杂时拆,简单时一刀切;但 SKILL.md 一定要读完。
"""


def _autonomy_block() -> str:
    """所有 agent 共享的「自主拆解」授权 — 复杂问题时不要硬来,自己拆/找帮手。"""
    return _AUTONOMY_BLOCK


def _handoff_block(step: str) -> str:
    """每个 step 跑完必须声明把活交给谁。"""
    next_step = DEFAULT_NEXT_STEP.get(step, "DONE")
    if next_step == "DONE":
        default_next = "DONE(整条 pipeline 完成)"
    else:
        default_next = f"{STEP_TO_AGENT.get(next_step, next_step)}({next_step})"
    return _HANDOFF_BLOCK_TEMPLATE.replace("{default_next}", default_next)


def _agent_brief_block(run: TaskRun, step: str) -> str:
    """coordinator 给本 step 的动态 briefing(若已生成)。

    coordinator 在 pipeline 启动前会跑一次"任务理解 + 上下文规划",
    为 8 个 step 各生成一个 agent_brief(本任务的侧重点 / 数据强调 / 硬约束 / 适配建议)。
    SOUL.md 定通用角色,brief 定本次任务的具体指令 — 不固化角色,而是动态适配。
    """
    briefs = getattr(run, "agent_briefs", None) or {}
    brief = briefs.get(step) or ""
    if not brief or not brief.strip():
        return ""
    return (
        f"\n\n# 本次任务 · 总控对你的额外指引(coordinator briefing)\n"
        f"{brief.strip()}\n"
        f"(以上是 coordinator 针对本次输入材料 + supplement 做的动态指引,跟 SOUL.md 通用角色互补。)"
    )


def _point_phase1_prompt(run: TaskRun, pool: dict) -> str:
    """Phase 1: 候选清单 5-10 条带分,不做收敛。"""
    ctx = (_build_global_ctx(run)
           + _agent_brief_block(run, "point_extraction")
           + _required_skills_block("point_extraction")
           + _autonomy_block()
           + _handoff_block("point_extraction"))
    return f"""你是【分析师】。现在是 Phase 1:**生成候选清单**,不要收敛。
从 MaterialPool 抓 5-10 条 candidate points,每条带 importance(1-5) + audience-relevance(1-5) 评分。

# 上下文
{ctx}

# MaterialPool(完整)
```json
{json.dumps(pool, ensure_ascii=False)}
```

# 工作要点
1. **数量 5-10 条** — 宽松、可重叠、可大可小
2. **每条评 2 个分**:
   - `importance` (1-5): 业务影响力(完成的事影响多大 / 风险多严重 / 数字多关键)
   - `audience_relevance` (1-5): 这 audience({run.audience}) 会有多在意
3. **不要总结** — 这是候选筛选,Phase 2 才挑 top 3
4. **supplement 优先** — supplement 提到的关键词,相应候选 audience_relevance 至少 +1
5. **数据/数字优先** — 含具体数字的候选 importance 至少 +1
6. **可以重复同一信息的不同角度** — Phase 2 会去重

# 候选清单 schema
```json
{{
  "msg_type": "task.step.result.phase1",
  "step": "point_extraction",
  "payload": {{
    "candidates": [
      {{
        "point": "<≤30 字>",
        "importance": <1-5>,
        "audience_relevance": <1-5>,
        "evidence_ref": "<MaterialPool 字段名,如 'completed[0]' / 'key_data[2]'>",
        "rationale": "<≤40 字 为什么给这个分>"
      }}
    ]
  }}
}}
```

不要超 10 条;最终输出严格 JSON,不要 markdown 表格。"""


def _material_phase1_prompt(run: TaskRun) -> str:
    """Phase 1: 文档地形侦查 — 不抽 MaterialPool 字段,只描述资料里"有什么"."""
    ctx = (_build_global_ctx(run)
           + _agent_brief_block(run, "material_parsing")
           + _required_skills_block("material_parsing")
           + _autonomy_block()
           + _handoff_block("material_parsing"))
    return f"""你是【资料员】。现在是 Phase 1:**地形侦查**。
不要抽 MaterialPool 字段,先把这份资料做一次全局扫描,告诉下一阶段"这里面有什么"。

# 上下文
{ctx}

# 原始内容
{run.raw_text}

# 你要输出的 doc_structure schema
```json
{{
  "msg_type": "task.step.result.phase1",
  "step": "material_parsing",
  "payload": {{
    "doc_structure": {{
      "sections": [
        {{"title":"<章节或表头标题>","summary":"<≤40 字描述这一段讲什么>","line_range":"<起止行>"}}
      ],
      "tables": [
        {{"name":"<表名或附件名>","row_count":<n>,"col_headers":["..."],"sample_numbers":["<带单位>"]}}
      ],
      "key_numbers": [
        {{"value":"<带单位>","context":"<上下文 ≤30 字>","section":"<出现位置>"}}
      ],
      "time_anchors": [
        {{"date":"<原文里出现的日期/时间表述>","what_happened":"<≤30 字>"}}
      ],
      "people": [
        {{"name":"<姓名/团队>","role":"<负责人|提出人|客户|上级|...>"}}
      ],
      "organizations": ["<提到的部门 / 公司 / 项目>"],
      "supplement_anchors": [
        {{"keyword":"<supplement 中提到的词>","where":"<原文哪一段触到了>"}}
      ]
    }}
  }}
}}
```

# 工作要点
1. **逐节扫**:章节 / 段落 / 附件表格,每个给一个一行摘要
2. **数字优先**:遇到数字(项数/金额/百分比/日期/时长)就抓到 key_numbers
3. **xlsx 表格**:每个表写到 tables[],至少抽 3 个 sample_numbers
4. **supplement 锚点**:对照 supplement,在原文找触到的位置 — 这是 Phase 2 的导航图
5. **不要总结**:这是侦查,不下判断;不写"完成 X"那样的事实,只写"section 3 描述了 X"
6. **不要省略**:即使看起来无关的数字也抓,Phase 2 才决定是否用

输出严格 JSON,不要 markdown 表格。"""


def _copywriting_phase1_prompt(run: TaskRun, core: dict, outline: dict) -> str:
    """Phase 1 · 信息池 + 钩子草稿 — 像 web-video-presentation 的双源原则。

    输出:
      - facts_pool:从 ReportCore 抽出的可用 facts,按 chapter 分组(数字 / 案例 / 引用 / 状态)
      - hook_drafts:3-5 个备选开篇句(数据前置型 / 进展状态型 / 行动驱动型)
      - script_skeleton:每章 1-2 句的骨架(还不是 narration,只是节奏点)
    """
    ctx = (_build_global_ctx(run)
           + _agent_brief_block(run, "copywriting")
           + _required_skills_block("copywriting")
           + _autonomy_block()
           + _handoff_block("copywriting"))
    duration_block = _duration_block(run.duration)
    return f"""你是【文书】。Phase 1:**抽信息池 + 拟钩子草稿**,不要写最终讲稿。

# 上下文
{ctx}

{duration_block}

# ReportCore(信息池源)
```json
{json.dumps(core, ensure_ascii=False)}
```

# Outline(章节顺序的真理)
```json
{json.dumps(outline, ensure_ascii=False)}
```

# 你要做的 — 4 步

## 1. 抽信息池(per chapter)
对 Outline 里每个 chapter,从 ReportCore 抽出该章可用的"facts"。
**facts 分类**:`数字`(带单位的量化结果)/ `状态`(进展定性)/ `案例`(具体场景或事件)/ `诉求`(向上要的动作)/ `时间`(deadline)
**每个 fact 必须能在 ReportCore 找到出处**(写到 source_ref,如 "key_points[0].evidence_ref")。

## 2. 钩子草稿(3-5 句备选)
开篇第一句(给 chapter=summary 的第一段 narration 用)。**禁止**自我介绍("大家好"/"本期")或 PPT 感("本汇报将...")。
- 数据前置型:"识别异常报价 87 万元,4 项 AI 能力本周全部正式运行。"
- 进展状态型:"整体进展受控,有一项资源配额需领导决策。"
- 行动驱动型:"建议本周完成 token 配额追加,保障 7 月灰度。"

## 3. script 骨架(每章 1-2 句)
按 Outline.chapters 顺序,每章给 1-2 句**骨架句**(还不是 narration,只是节奏点)。
这是后续 Phase 2 拆 narration 的依据。骨架句要保留**所有关键 facts**(不要丢数字)。

## 4. 信息保留度自查
计算: 你的 facts_pool 覆盖了 ReportCore 多少 facts? 至少 60% 才算合格。
**禁止编造**:任何不能在 ReportCore 找到的 fact 都不能进 facts_pool。

# 输出 schema(Phase 1)
```json
{{
  "msg_type": "task.step.result.phase1",
  "step": "copywriting",
  "payload": {{
    "facts_pool_by_chapter": [
      {{
        "chapter_no": 1,
        "chapter_role": "总|分|收束",
        "facts": [
          {{"kind":"数字","content":"识别异常报价 87 万元","source_ref":"key_points[0]"}},
          {{"kind":"状态","content":"进展正常,有轻微风险","source_ref":"progress_status"}},
          {{"kind":"诉求","content":"追加 token 配额 200 万","source_ref":"support_needed[0]"}}
        ]
      }}
    ],
    "hook_drafts": [
      {{"kind":"数据前置","text":"<≤30 字>"}},
      {{"kind":"进展状态","text":"<≤30 字>"}},
      {{"kind":"行动驱动","text":"<≤30 字>"}}
    ],
    "script_skeleton": [
      {{"chapter_no":1,"sketch":"<1-2 句节奏点,保留关键 facts>"}}
    ],
    "info_retention_check": {{
      "report_core_facts_count": <ReportCore 总 facts 数>,
      "facts_pool_count":        <你池里 facts 数>,
      "retention_pct":           <0-100>,
      "ok":                      true,
      "notes":                   "<若 <60% 解释为何>"
    }}
  }}
}}
```

不要写最终讲稿。Phase 2 才写。"""


def _step_prompt(step: str, run: TaskRun, prev: dict[str, StepState]) -> str:
    # ctx = 全局 ctx(supplement) + coordinator brief + 本 step 必读 skill + 自主拆解授权
    ctx = (_build_global_ctx(run)
           + _agent_brief_block(run, step)
           + _required_skills_block(step)
           + _autonomy_block()
           + _handoff_block(step))

    if step == "material_parsing":
        # material_parsing 在 _run_step 内部拆成 Phase1 (doc_structure 侦查) + Phase2 (字段抽取)
        # 该函数返回的是 Phase2 prompt;Phase1 由 _material_phase1_prompt() 单独构造
        # prev["__phase1__"] 是个虚拟 key,_run_step 把 Phase1 结果暂存进来后再调本函数渲染 Phase2
        phase1_out = (prev.get("__phase1__") or StepState("","","")).output_json or {}
        doc_structure_json = json.dumps(phase1_out, ensure_ascii=False, indent=2) if phase1_out else "(无 Phase1 输出,直接看原文)"
        body = f"""你是【资料员】,刚刚已经在 Phase 1 把这份资料做了「地形侦查」。现在 Phase 2:基于侦查结果,抽出结构化 MaterialPool。

# 上下文
{ctx}

# Phase 1 的 doc_structure(地形侦查产物)
```json
{doc_structure_json}
```

# 原始内容(再看一遍,做字段抽取)
{run.raw_text}

# 关于附件
原始内容如果出现 `## 附件:<filename>` 标题,那是**后端已经抽取好的附件纯文本**(.docx / .xlsx / .pdf 用 python-docx / openpyxl / pdfminer 抽过),内容已在标题下方,**不要**写"附件解析失败"。仅当出现 `_未抽取_(...)` 才是真的抽取失败。

# 抽取规则(严格)
1. **每条事实必须能在原文找到证据**,写到 `evidence_snippets.<字段>[]`(≤30 字原文摘抄)
2. **数字必须保留单位 + 出处**:`"value": "4 项"` 不是 `"4"`;`"value": "800 万元"` 不是 `"800"`
3. **completed / in_progress 用原文动词**:不抽象化(原文是"正式运行"就保留,不要改"已落地")
4. **key_data 是"带单位的数字事实"**,不是岗位描述
5. **空字段必须解释**:
   - 原文没相关线索 → `data_gaps`: "未发现 X 相关表述"
   - 原文有但拿不准 → `data_gaps`: "弱信号:'...'(无法判定是否构成 X)"
   - **绝不允许 `[]` + 无解释**
6. **xlsx 表格**:逐行扫描,数字行优先,不要因"像参数"就跳过

# 自检
在写 JSON 前默问一遍:
- supplement 的关键词在我的产物里都有体现吗?
- 每条 completed 我能在原文哪里找到?
- risks 真的空吗?还是我漏看了?

# MaterialPool schema(payload 内字段)
```json
{{
  "msg_type": "task.step.result",
  "step": "material_parsing",
  "payload": {{
    "time_range": "本周",
    "completed": ["原文动词起头的具体事实"],
    "in_progress": ["..."],
    "key_data": [{{"name":"...","value":"<数字+单位>","note":"<出处>"}}],
    "risks": ["X 可能 Y,导致 Z" 形式;或弱信号写到 data_gaps],
    "support_needed": [{{"item":"...","owner":"领导|同事|客户"}}],
    "next_steps": ["..."],
    "people": [{{"name":"...","role":"..."}}],
    "data_gaps": ["未发现 X / 弱信号 Y"],
    "evidence_snippets": {{
      "completed":      ["<原文 ≤30 字>"],
      "in_progress":    ["..."],
      "key_data":       ["..."],
      "risks":          ["..."]
    }},
    "doc_structure_ref": "Phase 1 的侦查结果作为元数据,不在 payload 重复"
  }}
}}
```

完整保留 Phase 1 的 doc_structure 字段在最终 JSON 的 `payload.doc_structure` 里(便于下游 trace),内容可以原样复制。"""

    elif step == "point_extraction":
        pool = (prev["material_parsing"].output_json or {}).get("payload") or prev["material_parsing"].output_json or {}
        # point_extraction 也走双阶段:Phase1 候选清单(5-10 条带分) → Phase2 收敛 top3 + why_matters
        # Phase1 结果存在 prev["__candidates__"] 喂 Phase2 prompt 用
        candidates = (prev.get("__candidates__") or StepState("","","")).output_json or {}
        candidates_block = json.dumps(candidates, ensure_ascii=False, indent=2) if candidates else "(Phase1 候选未生成,本次直接做收敛)"
        body = f"""你是【分析师】。Phase 1 已经从 MaterialPool 里抓了 5-10 条候选 points 并打了分。
现在 Phase 2:收敛到 top 3 + 给每条配 why_matters + 写 summary + 填风险/诉求/进展状态。

# 上下文
{ctx}

# Phase 1 的候选清单(已打 importance + audience-relevance 分)
```json
{candidates_block}
```

# MaterialPool(完整素材池,供回查 evidence)
```json
{json.dumps(pool, ensure_ascii=False)}
```

# 工作规则(严格)
1. **从 Phase 1 候选里挑 importance×audience-relevance 总分最高的 top 3**(允许微调评分,但说明理由)
2. **不要复读 MaterialPool 的 completed/in_progress** — 每条 point 必须升华
3. **每条 key_point 包含**:
   - `point`: 主语 + 动词 + 对象 + (结果数据 or 影响),≤30 字
   - `why_matters`: 对 {{audience}} 意味着什么,**不能是"非常重要/有价值"这种空话**
   - `evidence_ref`: MaterialPool 里证据所在字段名(如 "completed[0]" / "key_data[2]")
4. **summary 50-80 字**,**必须含 1 个量化数据**(从 MaterialPool 抓最有冲击力那个)
5. **progress_status**:从 ["正常","有轻微风险","有风险","已延期"] 选 1,**必须配 progress_status_reason**(为什么是这个不是那个)
6. **risks 不为空时,每条有 impact** — 没 impact 就丢回 candidates 不收;空 risks 时写 `data_gaps` 解释
7. **support_needed 每条有 owner + 动作**

# 自检
- supplement 关键词在 summary / 某条 key_point 出现了?
- summary 有具体数字?
- 每条 why_matters 都不是空话?
- progress_status_reason 给了具体理由?

# ReportCore schema
```json
{{
  "summary": "<50-80 字,含 1 个量化数据>",
  "key_points": [
    {{"point":"<≤30 字>","why_matters":"<对 audience 意味着什么>","evidence_ref":"<MaterialPool 字段名>"}}
  ],
  "progress_status": "正常|有轻微风险|有风险|已延期",
  "progress_status_reason": "<≤40 字 — 为什么是这个状态>",
  "risks": [{{"item":"...","impact":"...","evidence_ref":"..."}}],
  "support_needed": [{{"item":"...","owner":"领导|同事|客户","action":"<具体可执行动作>"}}],
  "next_steps": ["..."],
  "data_gaps": ["<空字段的原因>"]
}}
```"""

    elif step == "structure_building":
        core = prev["point_extraction"].output_json or {}
        core_block = json.dumps(core, ensure_ascii=False, indent=2)[:1200]
        duration_block = _duration_block(run.duration)
        body = f"""你是【结构设计师】。把 ReportCore 套进合适的大纲模板。
你不写文字,只决定章节顺序、每章承载哪些字段。

# 上下文
{ctx}

{duration_block}

# ReportCore(参考,挑哪些字段在哪一章)
```json
{core_block}
```

# 工作规则
1. **章节数严格匹配上方"汇报时长"约束** — 不要少也不要多
2. **总分结构严格匹配** — 1 分钟走"总-分",3 分钟走"总-分-总",5 分钟走"总-分-分-总"
3. **supplement 优先** — supplement 提"先讲风险",哪怕 report_type=daily 默认 completed 在前,也要把 risks 章节前置到 summary 之后
4. **必须含 cover / summary / next_steps 三类**(1 分钟可以把 next_steps 合并到 summary 章节,但语义要保留)
5. **结论先行** — summary 永远在 cover 之后第一条
6. **data_keys 必须能在 ReportCore 找到对应字段**(否则文书无米下锅)
7. **progress_status=有风险/已延期** → risks 章节前置到 summary 之后

# Outline schema
```json
{{
  "report_type": "{run.report_type}",
  "duration": "{run.duration}",
  "structure_pattern": "总-分 | 总-分-总 | 总-分-分-总",
  "chapters": [
    {{"chapter_no":1,"title":"封面","type":"cover","role":"总|分|收束","data_keys":[]}},
    {{"chapter_no":2,"title":"本周概览","type":"summary","role":"总","data_keys":["summary","progress_status","progress_status_reason"]}}
  ]
}}
```

# 自检
- 章节数在 prompt 头部"章节总数"区间内?
- chapters[].role 全部填了"总"/"分"/"收束"?
- 整体节奏匹配 structure_pattern?
- supplement 关键词体现为某个章节或某个 data_key 了吗?
- 每个 data_keys 在 ReportCore 都能找到?"""

    elif step == "upward_optimization":
        core = prev["point_extraction"].output_json or {}
        body = f"""你是【表达教练】。把这份 ReportCore 改写成更适合向上汇报的版本:结论前置 / 风险显化 / 诉求具体化。
你不是 humanizer — humanizer 去 AI 套话,你转视角。

# 上下文
{ctx}

# ReportCore(输入)
```json
{json.dumps(core, ensure_ascii=False)}
```

# 改写规则
1. **supplement 是改写指南** — supplement 要求"稳健保守",把"突破性进展"改成"按计划推进"
2. **summary 强化**:"X 项工作" → "X 项工作 + 完成度 + 对业务的具体影响"(仍 ≤80 字,含 1 个量化数据)
3. **key_points 升级**:每条 `point` 改更主动的动词,`why_matters` 必须是对 audience 的具体意味(不能"非常重要"这种空话)
4. **risks 强化**:没 impact 的 risk → 自己补"X 可能 Y,导致 Z";否则丢
5. **support_needed 强化**:没 owner 直接删;没动作补"建议 X 协助/拍板/对接"
6. **不夸大,不虚构数据** — 只改表达,绝不补凭空数字
7. **保持 schema** — key_points 仍是对象数组 [{{point,why_matters,evidence_ref}}],别退化成字符串

# 自检
- supplement 每条要求都已落实?
- summary 含量化数字 + 影响表述?
- 每条 key_point 的 why_matters 都不空泛?
- schema 字段没丢?

输出同 ReportCore schema 的 JSON,内容改写但 schema 不变。"""

    elif step == "copywriting":
        core = _prev_json(prev, "upward_optimization", "point_extraction")
        outline = _prev_json(prev, "structure_building")
        profile = DURATION_PROFILE.get(run.duration) or DURATION_PROFILE["3分钟"]
        word_count = profile["script_words"]
        per_narration = profile["narration_len"]
        narrations_n = profile["narrations_n"]
        structure_pattern = profile["structure"]
        duration_block = _duration_block(run.duration)
        # Phase 1 信息池 + 钩子草稿(由 _run_step 在 Phase 2 之前喂进 prev["__copy_phase1__"])
        phase1 = (prev.get("__copy_phase1__") or StepState("","","")).output_json or {}
        phase1_block = (
            f"# Phase 1 信息池 + 钩子草稿(刚才已经抽好,Phase 2 必须基于此)\n```json\n"
            f"{json.dumps(phase1, ensure_ascii=False, indent=2)}\n```\n\n"
            if phase1 else
            "# Phase 1 信息池\n(Phase 1 未生成,本次直接从 ReportCore 抽)\n\n"
        )
        body = f"""你是【文书】。Phase 2:根据 ReportCore + Outline + Phase 1 信息池,生成可朗读的讲稿 + 投屏页面文案 + TTS narrations。

# 上下文
{ctx}

{duration_block}

{phase1_block}# ReportCore(key_points 是 [{{point,why_matters,evidence_ref}}] 对象数组)
```json
{json.dumps(core, ensure_ascii=False)}
```

# Outline(章节顺序的真理)
```json
{json.dumps(outline, ensure_ascii=False)}
```

# 写作方法论 · 信息保留 + 双源 + 详略得当

## 最高原则:信息保留度 ≥ 60%
**这一条优先于下面所有规则。** 讲稿是 ReportCore 的"换说法",不是"摘要"。
- 关键事实(数字 / 名称 / 时间 / 进展状态 / 风险项 / 诉求)**逐项对得上**,不能整段消失
- 论证链(完成 → 影响 / 风险 → 后果 → 应对)必须保留,可以换说法不能跳步
- **允许大幅压缩**:套话开场 / 重复总结 / 客套结语
- **禁止压缩**:核心数据 / 具体案例 / 反方观点(token 配额风险这种关键限制最容易被"一刀切")

如果时长不够装下 60% 信息(如 1 分钟 vs 12 个核心事实),**优先保留量级最大、最影响决策的事实**,
其余写到 notes 字段,但 script.md 主线必须含 top-3 数据点。

## 双源原则:script.md 定节拍 / Outline 定信息密度
- **script.md 按段分行 → 每段就是一个 narration**(节拍源)
- **Outline.chapters[].data_keys 就是"信息池"**(画面密度源) — slides.content 从这里取
- narration 顺序 = chapters 顺序,中间不准插原 outline 没有的章节

## 步骤 1 · 拆 narration(讲稿原子段)
narration 是讲稿的"原子段",一个 narration ≈ 一页 slide 念出的口播 ≈ 一个完整论点。
- **数量**:严格 **{narrations_n}** 段,匹配 {structure_pattern} 节奏
- **每条字数**:**{per_narration} 字**,**多不超 10 字 / 少不少 10 字**(详略得当的边界)
- **每条 5-10 秒朗读时长**(中文约 4 字/秒)
- **总分节奏映射**(每段在 {structure_pattern} 中扮演的角色,写到 narration.role 字段):
  - "总":开篇定调 / 一句话核心 / 收束总结 — 一段话装下整个汇报骨架
  - "分":单点深入 / 数据展开 / 案例支撑 — 一段一个论点,带 1 个量化数据
  - "收束":总结 + 诉求 / 下一步动作
- **结构模板**:`<动词起头的主句> + <数据支撑> + <so what / 影响 / 动作>`
  - ✓ "完成 4 项能力正式运行,识别异常报价 87 万元,直接降低资金风险。"
  - ✗ "我们这次取得了不错的成绩,主要包括四个方面。"(空话,无数据,无影响)
  - ✗ "AI 能力运行。识别 87 万。降低风险。"(过简,语焉不详,缺主语和宾语)
- **数字保留具体值**(商务汇报不翻译成"几乎快一倍",领导要原始数字)
- **句长 ≤ 25 字**;一条 narration 可以 1-3 句

## 步骤 2 · 详略得当 · 分时长策略
**1 分钟 · 极致压缩**:
- 只讲"什么完成了 + 进展正常吗 + 一个最大的风险/诉求"
- 每个分点 1 段 narration,**禁止案例展开**
- 数据保留最有冲击力的 1-2 个(如 "识别异常报价 87 万元")

**3 分钟 · 标准展开**:
- 每个分点 1-2 段 narration,主段讲数据 + 影响
- 可以有 1 个"举个例子"式具体场景(≤ 一句话)
- 风险段必须含 impact + 应对动作

**5 分钟 · 案例化展开**:
- 每个分点 2-3 段 narration,可以"先讲数据 → 再展开具体场景 → 再回到影响"
- 1 个分点可以带 1 个完整案例(2-3 句)
- 但**禁止流水账** — 整体仍是 总 → 分 → 收束,不要变成"工作日志"

## 步骤 3 · 拼 script_md(完整口播稿)
- script_md = narrations 按序拼起来,**按段分行**(每条 narration 一个自然段)
- 段间用**空行**分隔(代替 web-video-presentation 的 `---`)
- 总字数严格 **{word_count}** 字,**多 50 字就砍 / 少 50 字就补具体数据**
- 每段动词开头(完成 / 推进 / 识别 / 建议),不要"我们 / 本周 / 项目组" 重复开头
- 全程**中文标点**,无 emoji,无英文括号,无 markdown 表格

## 步骤 4 · 写 slides(投屏文案)
- slides 数 == narrations 数(一页配一条 narration)
- 每页 title ≤14 字,动宾结构,**不要标点结尾**
- 每页 content 是 2-4 条短 bullet,每条 ≤12 字
- bullet 必须是**数据/事实/动作**,不是修辞句
  - ✓ ["识别异常报价 87 万元", "效率提升 68%", "处理 320 份文件"]
  - ✗ ["亮眼的成绩单", "全方位的提升", "取得了重大进展"]

## 步骤 5 · 双向校验
- 拼回 narrations 的总字数 == script_md 总字数(误差 ±5%)
- 每页 slide.content 的 bullet 在对应 narration 文本中**能找到对应数据**
- **信息保留度自查**:ReportCore 的 key_points + risks + support_needed 加起来的事实点,
  script.md 中**至少有 60% 在出现**(可用同义词,但事实不变)
- 反 cliché 扫描 · **禁止词**:赋能 / 重塑 / 全方位 / 打通 / 深度 / 卓越 / 突破 / 颠覆 / 革命 / 取得了
- 反 AI 味 · **商务版禁句**:"说白了" / "本质上" / "底层逻辑" / "一句话总结" / "归根结底" / "首先...其次...最后"

# supplement 落实
- 用户说"稳健保守"→ 避免"突破/突出/重大"等夸张词,用"按计划推进 / 已运行 / 已落实"
- 用户说"成果突出"→ 前置数据,让用户第一句就听到关键数字
- supplement 关键词在 script 中**至少出现 1 次**

# 自检 checklist
- [ ] narrations 数 == {narrations_n}(在区间内)
- [ ] 总字数严格在 {word_count} 区间
- [ ] 每条 narration 在 {per_narration} 字区间
- [ ] narrations.length == slides.length
- [ ] 总分节奏 {structure_pattern} 在 narration.role 字段体现
- [ ] 每页 title ≤14 字,每条 content ≤12 字
- [ ] 没 emoji / 英文括号 / 表格 / 禁止词
- [ ] supplement 关键词在 script 至少出现 1 次
- [ ] 拼回的 narrations 总字数 ≈ script 字数(误差 ±5%)

# 输出 schema
```json
{{
  "script_md": "口播稿 {word_count} 字,按段分行",
  "duration": "{run.duration}",
  "structure_pattern": "{structure_pattern}",
  "hook_chosen": "<从 Phase 1 hook_drafts 选的开篇句,或自己写更好的>",
  "info_retention": {{
    "facts_in_pool":  <Phase 1 facts_pool 总数>,
    "facts_used":     <你的 narrations 中实际引用的 facts 数>,
    "coverage_pct":   <facts_used / facts_in_pool · 应 ≥ 60>
  }},
  "slides": [
    {{"page_no":1,"title":"...","type":"cover","content":[]}},
    {{"page_no":2,"title":"...","type":"summary","content":["...","..."]}}
  ],
  "narrations": [
    {{
      "chapter": 1,
      "step": 1,
      "role": "总|分|收束",
      "text": "<{per_narration} 字>",
      "facts_used": ["<引用的 fact content,如 '识别异常报价 87 万元'>"]
    }}
  ]
}}
```

**facts_used 必填**:每条 narration 列出从 Phase 1 facts_pool 引用的 facts(可重叠,数量不限)。
这是信息溯源,后续 review 会用它对账。"""

    elif step == "html_design":
        slides = (prev["copywriting"].output_json or {}).get("slides") or []
        body = f"""你是【设计师】。**这是整个 pipeline 里视觉质量的最后一道关 — 设计水平决定汇报材料能不能被领导认真看**。

# 两份产物 · 都必须高质量
后端会基于你 design_notes / page_index / theme_token 同时渲染两份 self-contained HTML:
- **presenter / index.html** — 讲者视图,带 narration / review block / controls / 键盘 hints,
  指导用户怎么讲;评审会跑 audit 评分(必须 ≥ 80)。
- **broadcast / broadcast.html** — 录屏视图,锁 1920×1080 全屏,自动翻页 + inline audio 串联,
  **无任何 narration / review / controls / kbd** 干扰元素,纯画面;评审同样 audit ≥ 80。

**两份用同一份 slides + 同一份 design 决策渲染**,但目标用途不同 — 你的 design_notes / type 标注
要兼顾两边:type 差异化让录屏有节奏,数字 hero 让录屏有视觉冲击,narration 字段写好让 presenter
有讲解但不影响 broadcast。

至少要达到挂载 skill(web-design-engineer + web-video-presentation)规定的质量门槛 —
两个 SKILL.md 都要读、对照执行,不能凭直觉随便出。

# 上下文
{ctx}

# Slides
```json
{json.dumps(slides, ensure_ascii=False)}
```

# 设计哲学(不可妥协 · 必须在思考过程逐条对照)
**目标:做出一份不像 AI 出的、像专业设计师做的汇报投屏页。**

## 视觉方向(你必须挑一个并解释为啥)
后端模板默认走「档案室 / 公文校对 / 印刷工艺」方向 — 米黄棉纸 + 朱砂印章 + 配准十字 + 罗马章节编号 +
ruler 进度尺 + tick 印章替代圆点 + 数字 count-up + 章节段落感。
你的工作是**评估这个方向是否适合本次任务**(audience / report_type / supplement 调性)。
如果不适配,在 design_notes 写清楚换哪个方向(可选:**编辑室校样 / 蓝图工程图 / 索引卡 / 木纹年报**)。

## 五条死线(违反一条整页打回重做)
1. **数字必须是主角** — bullet 里只要带数字,就要被 hero 化(大字号 display serif + count-up)。
   反例:把 "320 份" 当普通文字塞进 li,失格。
2. **章节段落感** — 8 页要有清晰节奏:开篇(总) → 论据(分,2-4 页) → 风险/转折 → 落点(收束 + 诉求)。
   每页都要有 type 字段({{cover|summary|completed|progress|risk|next_steps|review|closing}}),
   后端按 type 上不同印章字 + 颜色。**slide type 不能全是 detail**。
3. **节奏 = 视觉 + 口播** — slide.content 字数与 narration 长度匹配:数字多的页面 bullet 短、
   narration 解释长;转折页 bullet 少、narration 给情绪。**信息密度按页面 type 设计,不要平均铺**。
4. **反 AI slop 五件套** — 紫粉渐变 / glassmorphism / 圆角彩色卡片 / emoji-as-icon /
   Title Case 西文标题 / 居中对齐一切 / 卡片 box-shadow=0 5px 20px rgba(0,0,0,.1)。
   **看到任何一项,你的设计就废了**。
5. **数据有源头,视觉有出处** — 每页关键数据要能在 ReportCore / copywriting.narrations.facts_used
   里对得上,不要为了排版编新数字。

## 视觉细节准则(评估后端模板时用)
- **字体**:display 用衬线(Source Han Serif / STSong / 宋体类),body 用 PingFang / Noto Sans SC,
  数字配 IBM Plex Mono 突出"机器精确"感。**禁止 Inter / Roboto / Arial**。
- **配色**:主色 ≤ 3(ink + paper + 1 accent),accent 永远只在「数字 / 印章 / 章节标记」三处出现,
  不要染 body 文字。完成态可加 olive,风险态可加 vermilion 强度。
- **留白**:左 88px gutter + 右 112px filetag + 中央 940-1080px 内容,宽屏一定要有"边距压住"。
- **动画**:stagger reveal(40-700ms 递增 delay)+ 数字 count-up + tick 印章一个一个盖下,
  **每个动画 ≤ 800ms,合并播完 ≤ 1.2s,不要让领导等**。
- **过渡**:翻页用 paper-shift(blur + 微上移),不要 fade-only,不要 3D 翻转。

# 输出 schema
```json
{{
  "project_path": "data/outputs/{run.task_id}/web-presentation/",
  "dist_path": "data/outputs/{run.task_id}/web-presentation/dist/",
  "theme_token_id": "archive-paper | editorial-proof | blueprint | index-card",
  "design_notes": "<一段 ≤120 字的设计决策说明:为啥选这个方向、跟 supplement 关系、特殊处理>",
  "page_index": [
    {{"page_no":1, "type":"cover", "anchor":"chapter-1-step-1",
      "headline_metric": "320 份|null", "headline_unit":"份|null",
      "design_role": "总|分|收束"}}
  ],
  "anti_slop_check": {{
    "no_purple_pink_gradient": true,
    "no_glassmorphism": true,
    "no_emoji_as_icon": true,
    "no_title_case_en": true,
    "all_chinese_punctuation": true
  }}
}}
```

# 自检(在产出 JSON 前你必须逐条对答,不通过就 needs_retry)
- supplement 关键词在某页有体现?(列出具体页号)
- theme_token 跟 audience/style/supplement 一致?(给一句理由)
- 五条死线(数字主角 / 章节段落感 / 节奏对齐口播 / 反 AI slop 五件套 / 数据有源头)逐条 ✓/✗?
- page_index 长度 == slides 长度?type 分布有差异化(不是全 detail)?
- design_notes 写清楚为啥这次选这个方向?
- anti_slop_check 5 项全 true 才算合格?
- web-design-engineer + web-video-presentation 这两个 SKILL.md 你真读了哪些章节?(在 thinking 里引用原文)"""

    elif step == "video_production":
        copy_out = prev["copywriting"].output_json or {}
        script = copy_out.get("script_md") or ""
        slides = copy_out.get("slides") or []
        narrations = copy_out.get("narrations") or []
        provider = _current_video_provider()
        provider_body = build_prompt_for_provider(
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
        body = f"""# 你的任务:生成数字人视频

本次汇报需要数字人出镜。你的**核心交付**是用下面的 provider 生成数字人视频片段。

录屏（broadcast.html → mp4）和 TTS 配音由 backend 自动完成,**不需要你操心**。
你只需要专注一件事:**调用数字人 provider 生成视频片段**。

{provider_body}

# 降级规则
如果数字人 provider 调用失败(凭据缺失 / 配额耗尽 / 超时),你**不需要自己录屏或做兜底**。
只需如实在输出 JSON 中标记 `degraded: true` + `degrade_reason`,backend 会自动走录屏兜底。"""

    elif step == "review":
        core = _prev_json(prev, "upward_optimization", "point_extraction")
        script = _prev_json(prev, "copywriting").get("script_md") or ""
        html_out = (prev.get("html_design") or StepState("","","")).output_json or {}
        presenter_audit = html_out.get("audit") or {}
        broadcast_audit = html_out.get("broadcast_audit") or {}
        html_overview_str = json.dumps({
            "presenter": {
                "summary": presenter_audit.get("summary") or {},
                "findings": presenter_audit.get("findings") or [],
            },
            "broadcast": {
                "summary": broadcast_audit.get("summary") or {},
                "findings": broadcast_audit.get("findings") or [],
            },
        }, ensure_ascii=False, indent=2)
        html_pages_str = json.dumps({
            "presenter_pages": presenter_audit.get("pages") or [],
            "broadcast_pages": broadcast_audit.get("pages") or [],
        }, ensure_ascii=False, indent=2)[:3000]
        body = f"""你是【质量检查员】。检测产物质量,输出 ≥3 条可执行建议 + 4 个快捷指令矩阵 + AI 信号分 + HTML 视觉评分。
你只检测,不重写。

# 上下文
{ctx}

# ReportCore(经表达教练优化后)
```json
{json.dumps(core, ensure_ascii=False)}
```

# 讲稿(供逐字扫 AI 套话 + 句长 + 节奏)
{script[:2000]}

# HTML 投屏页 · 双视图诊断摘要(由 html_builder 后端产出)
**注意**:html_design 现在产两份 HTML:
- **presenter**(index.html)— 讲者视图,带 narration / review / controls
- **broadcast**(broadcast.html)— 录屏视图,1920×1080 全屏 / 无控件 / 自动翻页 / inline audio

两份都必须达到 web-design-engineer + web-video-presentation skill 规定的质量,
**任何一份分数 < 80 都要在 suggestions 写明哪一份哪一页要改**。

```json
{html_overview_str}
```

# 双视图逐页诊断
```json
{html_pages_str}
```

# 数据来源 · 强制信任规则
**上面的「双视图诊断摘要 + 逐页诊断」JSON 是 backend 真实跑出来的 audit,这是事实真相**。
你的 html_review.overall_visual_score 必须直接用上面 summary.overall_visual_score 数值,
不是你自己拍脑袋打分,也不是你 ls 找不到文件就判 0 分。

如果你想 Bash 自查文件,**必须用下面的绝对路径**(profile cwd 隔离,相对路径找不到):
- presenter:`/mnt/c/code/lobsterScan/data/outputs/{run.task_id}/web-presentation/index.html`
- broadcast:`/mnt/c/code/lobsterScan/data/outputs/{run.task_id}/web-presentation/broadcast.html`
- audit:`/mnt/c/code/lobsterScan/data/outputs/{run.task_id}/web-presentation/audit.json`
- broadcast_audit:`/mnt/c/code/lobsterScan/data/outputs/{run.task_id}/web-presentation/broadcast_audit.json`

**ls 找不到不代表文件不存在**(你看的是 profile 隔离的工作区,不是 repo 真路径)— 直接信 prompt 里的 audit 数据。

# 验证 video_production 真实性(强制)
看 video_production.output_json 末尾的 `_recording_verifications` 节(backend 验证过的真实性):
- `cdp_recording.verified=true / false`?
- `intro_video.verified=true / false`?
- `recording_method` 字段是 backend 已经筛选过的真实方法,跟 `recording_method_claimed` 对照:
  - 若两者不同 → agent 在 `_claimed` 里假声明了一些方法,你要在 suggestions 写「video-producer 假声明 X」

# 检查规则
1. **supplement 对账(首要)** — 用户每条补充说明你都要逐一对照:落实在哪一段?没落实直接写到 suggestions 首条
2. **信息保留度对账** — 看 copywriting.info_retention.coverage_pct,如果 < 60% 写到 suggestions
3. **facts 溯源对账** — 抽 3 条 narration 看其 facts_used,验证每个 fact 真在 ReportCore 找得到(防编造)
4. **建议必须可执行** — 用动词,指向具体段落,告诉作者改什么 / 改哪段 / 为什么
5. **不输出技术化错误** — "模型超时" 这种不写;只写业务化("建议为'80% 完成度'补一个数据支撑")
6. **三轴检查**:
   - 结构:章节齐 / 数字撑 / 风险显 / 总分节奏(structure_pattern 跟时长匹配?)
   - 表达:句长 ≤25 / 动词起头 / 无 emoji / 无 AI 套话
   - 语气:符合 supplement + audience + style 三方一致性
7. **AI 信号分 0-100** — 扫"赋能/重塑/全方位/打通/深度/说白了/本质上/底层逻辑"等 AI 套话密度,频率高分高
8. **skills_used 对账(强制)** — 检查每个 step 的 output_json 末尾有没有 `skills_used` 字段:
   - 缺失 → suggestions 写「{{step}} 未声明 skills_used,SKILL.md 可能没读」,扣分
   - 列了但思考过程里**完全没引用 SKILL.md 任何规则** → suggestions 写「{{step}} 假声明 skills_used」
   - 列对应 skill 但产物明显违反 SKILL.md 规则(比如 copywriter 没双阶段 / structure 没填 role) → 同样打回

9. **HTML 视觉审计 · 两份都必须达标(图文并茂 · 详实 · 详略得当 · 结构优秀 · 不花)** —
   **直接读上面 prompt 里的 audit JSON,不要再 ls/cat 重新核查**。**两份都要审**:presenter ≥ 80 + broadcast ≥ 80。
   两份目标用途不同,审 broadcast 时注意它**不带 narration / review / controls** 是设计意图,不是缺失。
   **每份都挑出最弱 3 页写到 suggestions(用 [presenter] / [broadcast] 前缀区分)**。
   你输出的 `html_review.{{presenter|broadcast}}.overall_visual_score` 必须 = audit.summary.overall_visual_score(不是 0,不是你重评)。
   - **图文并茂** — `has_num_hero`/`has_media_video`/`has_media_audio` 至少一项为 true 的页面占比应 ≥ 60%,
     纯文字 bullet 多的页面要点名(写到 suggestions:页 X 应把「Y」抽成数字 hero)。
   - **详实** — 每页 narration_chars 应 ≥ 40(< 40 视为信息单薄);bullet_total 0 或 ≥ 6 都是异常。
   - **详略得当** — `density_std / density_avg` 应 ≥ 0.18,否则每页信息量太平均(wall-of-text)。
     1分钟禁案例展开,3分钟主页应有 1 个 case 短场景,5分钟可允 1 个分点完整案例。
   - **结构优秀** — `type_distribution` 至少 2 种 type 且 cover_idx=0;
     中间页面 type 不能全是 detail(应有 summary/completed/risk/next_steps 差异化)。
   - **样式不花** — `anti_slop_hits` 任何一项 true 直接 -12 分,suggestions 第一条要求换设计方向。
   你必须输出 **两份**(presenter + broadcast 各一):
   ```json
   "html_review": {{
     "presenter": {{
       "overall_visual_score": <0-100,直接用 audit.summary.overall_visual_score 或你自己评>,
       "best_page": <page_no + 一句理由>,
       "worst_pages": [<page_no + 一句问题>, ...],
       "page_notes": [{{ "page_no":1, "verdict":"优|良|差", "note":"<≤25 字>"}}, ...],
       "must_fix": [<必须修的具体 issue,指向 page_no + 改法,≥1 条>]
     }},
     "broadcast": {{
       "overall_visual_score": <0-100>,
       "best_page": <page_no + 一句理由>,
       "worst_pages": [...],
       "page_notes": [...],
       "must_fix": [...]
     }},
     "verdict": "两份都达 ≥80 / 仅 presenter 达标 / 仅 broadcast 达标 / 两份都不达标"
   }}
   ```

# 自检
- supplement 每条要求都在 suggestions 或 quick_actions 里有回应?
- suggestions 数 ≥ 3?每条有 what/where/why?
- 没说"模型超时 / token 不够" 等技术话?

# 输出 schema
```json
{{
  "supplement_check": [
    {{"requirement":"<原文 supplement 中某条>","landed":"<具体段落或字段;若没落实写 NO>","note":""}}
  ],
  "suggestions": [
    {{"what":"<改什么>","where":"<具体段落 / 字段名>","why":"<为什么不达标>"}}
  ],
  "quick_actions": {{
    "shorter": false, "more_data": false,
    "softer_tone": false, "add_risk": false
  }},
  "estimated_duration": "2分20秒",
  "ai_signal_score": 12.5,
  "ai_signal_words": ["扫到的 AI 套话词"],
  "checks": {{
    "has_summary": true, "key_points_have_why_matters": true,
    "has_risks": true, "risks_have_impact": true,
    "has_next_steps": true, "support_clear": true,
    "length_ok": true, "audience_fit": true,
    "supplement_landed": true
  }}
}}
```"""

    else:
        body = "请输出 JSON 代码块。"

    if step == "video_production":
        return body + """

# 输出格式（强制）
**你必须先用 Bash 工具真正执行 skill 脚本**,拿到真实的执行结果后,再输出最终 JSON。
不要跳过 Bash 执行直接编写 JSON — backend 会验证文件是否存在,伪造的路径会被标记 verified=false。

执行完成后,把所有结果汇总到一段 ```json ... ``` 代码块中输出。
"""
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


async def _emit_v2_step_overlay(
    state: Any,
    run: "TaskRun",
    step: Any,
    handoff: dict,
    producer_agent_id: str,
) -> None:
    """T029 · spec 002-worker-subscription · per-step v2 chat overlay。

    每个 step success 后由 AgentWorker.run() 调用(harness 已守 is_v2=True 红线)。
    职责:让 subscription 链路在任务进行中有事件可订阅(详 research.md §F1)。

    流程:
      1) 若 step 对应 4 核心 artifact(material_parsing/point_extraction/structure_building/
         copywriting),write_versioned 写一份带版本号副本 + emit ArtifactUpdate
         → 下游订阅 artifact_id_in 的 worker 被唤醒
      2) emit AgentSpeak(text=步骤摘要, mentions=[handoff.to], intent="propose")
         → 下游订阅 mention_includes 的 worker 被唤醒

    宪章 III(降级):任何 emit / 写入异常仅 log warn,不影响 step.success 流程。
    """
    if step.status != "success":
        return

    from .artifacts_v2 import write_versioned
    from .events_v2 import AgentSpeak

    # 4 核心 artifact 抽取:step → (artifact_id, producer, json→payload)
    artifact_extract = {
        "material_parsing":    ("MaterialPool", "material",        lambda j: j),
        "point_extraction":    ("ReportCore",   "point-extractor", lambda j: j),
        "structure_building":  ("Outline",      "structure",       lambda j: j),
        "copywriting":         ("Script",       "copywriter",      lambda j: j.get("script_md") or ""),
    }

    # ① 若 step 对应核心 artifact → write_versioned(内部已 emit ArtifactUpdate)
    if step.step in artifact_extract:
        artifact_id, producer, extractor = artifact_extract[step.step]
        try:
            payload = extractor(step.output_json or {})
            if payload:
                await write_versioned(
                    state=state, artifact_id=artifact_id, payload=payload,
                    producer=producer, base_version=None,
                    delta_summary=f"{step.step} 产物 by {producer}",
                )
        except Exception as e:  # noqa: BLE001 — FR-020 降级
            log.warning("v2 per-step write_versioned %s failed: %s", artifact_id, e)

    # ② emit AgentSpeak — 让 mention 链路驱动订阅
    try:
        # mentions = handoff.to(若它是已知的 agent;否则按默认链 fallback)
        to_raw = (handoff.get("to") or "").strip()
        mentions: list[str] = []
        if to_raw and to_raw not in ("DONE", "coordinator"):
            mentions = [to_raw]
        elif step.step in DEFAULT_NEXT_STEP:
            next_step = DEFAULT_NEXT_STEP[step.step]
            if next_step != "DONE":
                next_agent = STEP_TO_AGENT.get(next_step, "")
                if next_agent:
                    mentions = [next_agent]

        # text: 复用 _summarize_output;若空降级为 step 显示名
        text = _summarize_output(
            step.step, step.output_json, step.output_text or "",
        ) or f"{STEP_TO_AGENT.get(step.step, producer_agent_id)} 完成"
        # 任何 step 都是 propose 语气(进行中接力),与收尾期的 confirm 区分
        await state.emit_v2(AgentSpeak(
            task_id=run.task_id, **{"from": producer_agent_id},
            text=text[:4000],
            mentions=mentions, cc=[], reply_to=None,
            intent="propose",
            artifact_updates=[],
        ))
    except Exception as e:  # noqa: BLE001 — FR-020 降级
        log.warning(
            "v2 per-step AgentSpeak failed for %s/%s: %s",
            producer_agent_id, step.step, e,
        )


async def _emit_v2_finalization(state: Any, run: TaskRun) -> None:
    """P1 v2(spec 001-v2-chat-protocol-state)收尾期 v2 emit + 4 核心 artifact 版本化。

    P2-P5 阶段每个 agent 会在自己 step 里 emit 真业务 speak/silent;P1 仅在收尾时 emit
    一组示例 + 给 4 核心 artifact 写带版本号副本,证明 v2 协议栈端到端跑通
    (spec FR-004 / FR-010 / SC-002 / SC-003)。

    宪章原则 III(降级而非崩溃):任何 v2 emit / 写入失败都仅 log warn,不影响任务状态。
    """
    from .artifacts_v2 import write_versioned
    from .events_v2 import (
        AgentSilent, AgentSpeak, ArtifactRef, CoordinatorIntervene, ReviewerVerdict,
    )

    # ① 4 核心 artifact 版本化(基于已成功的 step.output_json 派生)
    artifact_map = [
        ("material_parsing",    "material",        "MaterialPool", lambda j: j),
        ("point_extraction",    "point-extractor", "ReportCore",   lambda j: j),
        ("structure_building",  "structure",       "Outline",      lambda j: j),
        ("copywriting",         "copywriter",      "Script",       lambda j: j.get("script_md") or ""),
    ]
    for step_key, producer, artifact_id, extractor in artifact_map:
        s = next((x for x in run.steps if x.step == step_key), None)
        if s is None or s.status != "success" or not s.output_json:
            continue
        try:
            payload = extractor(s.output_json)
            if not payload:
                continue
            await write_versioned(
                state=state, artifact_id=artifact_id, payload=payload,
                producer=producer, base_version=None,
                delta_summary=f"{step_key} 首版产物",
            )
        except Exception as e:  # noqa: BLE001 — FR-020 降级
            log.warning("v2 write_versioned %s failed: %s", artifact_id, e)

    # ② 收尾期 5 类示例 v2 事件(P5 之后由 prompt 真实驱动)
    try:
        # agent.speak — 资料员发言 + @ 分析师
        material_step = next(
            (x for x in run.steps if x.step == "material_parsing" and x.status == "success"),
            None,
        )
        if material_step:
            await state.emit_v2(AgentSpeak(
                task_id=run.task_id, **{"from": "material"},
                text="素材池已整理完毕,后面交给分析师。",
                mentions=["point-extractor"], cc=[], reply_to=None,
                intent="propose", artifact_updates=[],
            ))
        # agent.silent — 设计师听完没补充
        await state.emit_v2(AgentSilent(
            task_id=run.task_id, **{"from": "html-designer"},
            reply_to=None, reason="设计已按文书讲稿落地,无需补充",
        ))
        # coordinator.intervene — 输出门通过 / 拒绝(纯发声,不路由)
        gate_kind = "gate_pass" if run.status == "done" else "gate_reject"
        gate_text = {
            "gate_pass":   "全部 artifact 齐了,task 收尾。",
            "gate_reject": "有 step 未跑完,先按 partial 交付,稍后可补。",
        }.get(gate_kind, "task 收尾。")
        await state.emit_v2(CoordinatorIntervene(
            task_id=run.task_id, kind=gate_kind, text=gate_text, hint_agent=None,
        ))
        # reviewer.verdict — pass / fail 双轨结论(示例;P4 才落真双轨)
        await state.emit_v2(ReviewerVerdict(
            task_id=run.task_id,
            verdict="pass" if run.status == "done" else "fail",
            dimension="quality", findings=[], suggested_fix_agent=None,
            suggestions=[
                "建议把核心数据前置到讲稿第二段",
                "建议给风险段配明确的补救计划 owner",
                "建议在结尾用一句话再次强调下一步动作",
            ],
        ))
    except Exception as e:  # noqa: BLE001 — FR-020
        log.warning("v2 finalization emit failed: %s", e)


def _verify_agent_recording_claims(run: TaskRun, out: dict) -> dict:
    """验证 agent 声明的 cdp_recording / playwright_recording / intro_video 文件**真实存在 + 大小达标**。
    虚假声明的字段标 verified=False,recording_method 数组重新筛选只保留 verified 的。
    返回:补充字段 dict(verification 节)。"""
    verifications: dict[str, dict] = {}
    repo_root = OUTPUTS_ROOT.parent.parent
    MIN_BYTES = 100 * 1024   # 100KB 以下视为无效产物

    for field in ("cdp_recording", "playwright_recording", "intro_video", "outro_video", "intro_video_avatar"):
        rec = out.get(field)
        if not isinstance(rec, dict):
            continue
        path = (rec.get("path") or "").strip()
        if not path:
            verifications[field] = {"verified": False, "why": "no path"}
            rec["verified"] = False
            continue
        pp = Path(path)
        if not pp.is_absolute():
            pp = (repo_root / pp).resolve()
        if not pp.exists():
            verifications[field] = {"verified": False, "why": f"file not found: {pp}"}
            rec["verified"] = False
            log.warning("agent 假声明 %s.path=%r,文件不存在", field, path)
            continue
        sz = pp.stat().st_size
        if sz < MIN_BYTES:
            verifications[field] = {"verified": False, "why": f"file too small ({sz}B)"}
            rec["verified"] = False
            continue
        verifications[field] = {"verified": True, "size_bytes": sz, "abs_path": str(pp)}
        rec["verified"] = True

    # recording_method 数组只保留 verified 的方式
    rm_in_raw = out.get("recording_method") or []
    if isinstance(rm_in_raw, list):
        # agent 可能返回 str 列表或 dict 列表，归一化为 str
        rm_in: list[str] = []
        for m in rm_in_raw:
            if isinstance(m, str):
                rm_in.append(m)
            elif isinstance(m, dict):
                rm_in.append(str(m.get("method") or ""))
        rm_in = [m for m in rm_in if m]
        rm_keep: list[str] = []
        for m in rm_in:
            field = {"cdp": "cdp_recording", "playwright": "playwright_recording",
                     "slideshow": "intro_video"}.get(m)
            if field and verifications.get(field, {}).get("verified"):
                rm_keep.append(m)
        if set(rm_in) != set(rm_keep):
            log.warning("recording_method 假声明 %s → 真实 %s", rm_in, rm_keep)
        out["recording_method"] = rm_keep
        out["recording_method_claimed"] = rm_in_raw
    out["_recording_verifications"] = verifications
    return verifications


def _ensure_audio_segments(
    run: TaskRun, s: StepState, prev: dict[str, StepState],
) -> None:
    """video_production 之后:确保中间页 narrations 有对应 audio_segments(mp3).

    HeyGen 模式下 agent 只交付 intro/outro 数字人视频,不交付 audio_segments,
    必须由 backend 用 MiniMax-TTS(或 edge-tts fallback)兜底,broadcast.html
    才能在重渲时把 narration 配音 inline 进去。

    minimax 模式下 agent 通常已交付 audio_segments,本函数 no-op 跳过。
    """
    out = s.output_json or {}
    copy_out = (prev.get("copywriting") or StepState("", "", "")).output_json or {}
    narrations = copy_out.get("narrations") or []
    if not narrations:
        return

    audio_dir = run.output_dir() / "audio"
    audio_segments = out.get("audio_segments") or []

    # agent 报告的 path 可能是 relative(从 repo root 算),归一化到 abs
    for seg in audio_segments:
        p = seg.get("path")
        if not p:
            continue
        pp = Path(p)
        if not pp.is_absolute():
            candidate = audio_dir / pp.name
            seg["path"] = str(candidate) if candidate.exists() else str((OUTPUTS_ROOT.parent.parent / pp).resolve())

    has_real_audio = any(
        seg.get("ok") and seg.get("path") and Path(seg["path"]).exists()
        for seg in audio_segments
    )
    if has_real_audio:
        return  # agent 给的 audio_segments 真实可用,不重复跑

    try:
        from ..render.tts_fallback import build_audio_segments, synthesize_one
        if not has_real_audio:
            log.warning("backend TTS fallback kicking in (narrations=%d)", len(narrations))
            audio_segments = build_audio_segments(
                narrations=narrations,
                output_dir=audio_dir,
                prefer_voice_kind="male",
            )
            out["audio_segments"] = audio_segments
            out["audio_via"] = list({sg.get("via") for sg in audio_segments if sg.get("via")})

        # 给 avatar intro/outro 视频也跑 TTS(Kling 视频是无音的,需要外接配音)
        for field in ("intro_video", "outro_video"):
            av = out.get(field) or {}
            if not isinstance(av, dict) or not av.get("ok"):
                continue
            text = (av.get("text") or "").strip()
            if not text:
                continue
            existing = av.get("audio_path")
            if existing and Path(existing).exists() and Path(existing).stat().st_size > 0:
                continue
            mp3 = audio_dir / f"{field}.mp3"
            r = synthesize_one(text, mp3, prefer_voice_kind="male")
            if r.get("ok"):
                av["audio_path"] = str(mp3)
                av["audio_duration"] = r.get("duration") or 0
                av["audio_via"] = r.get("via")
                out[field] = av
                log.info("backend TTS for %s ok: via=%s dur=%s", field, r.get("via"), r.get("duration"))
            else:
                log.warning("backend TTS for %s failed: %s", field, r.get("error"))

        s.output_json = out
        s.output_text = json.dumps(out, ensure_ascii=False, indent=2)
        _persist_step(run, s)
    except Exception as e:  # noqa: BLE001
        log.exception("backend TTS fallback failed: %s", e)


def _maybe_build_slideshow_fallback(
    run: TaskRun, s: StepState, prev: dict[str, StepState],
) -> None:
    """video_production 收尾:用 playwright 录 broadcast.html → 终交付 recording.mp4。

    broadcast.html 现在嵌入 intro_video(首页)+ outro_video(末页)+ 中间页 audio(file:// URL),
    录屏自然带数字人开场结尾 + 中间旁白。

    流程:
      1. 验证 agent 声明的 intro/outro/cdp/playwright recording 真实存在
      2. 主路径:playwright 录 broadcast.html(同时存在数字人镜头 + slides + audio)
      3. 兜底:广播录屏失败 → build_slideshow(slides + audio → mp4 无数字人)
    """
    out = s.output_json or {}
    # 验证 agent 声明的 recording 是否真存在(intro/outro 已被纳入 _verify... 列表)
    _verify_agent_recording_claims(run, out)

    # agent 自己录的 cdp/playwright recording 如果验证通过,promote 到 backend_recording 但**不 return**
    # —— 现在 broadcast.html 录屏是终交付,intro/outro 是封面镜头(可能两者都存在)
    for fld in ("cdp_recording", "playwright_recording"):
        rec = out.get(fld) or {}
        if rec and rec.get("verified") and rec.get("path"):
            out["backend_recording"] = {**rec, "ok": True, "kind": fld}

    # ─── Backend 主路径:用 playwright 录 broadcast.html(已带 intro/outro + audio file://)
    broadcast_html = run.output_dir() / "web-presentation" / "broadcast.html"
    if broadcast_html.exists() and broadcast_html.stat().st_size > 20_000:
        try:
            from ..render.broadcast_recorder import record_broadcast
            recording_out = run.output_dir() / "video" / "recording.mp4"
            log.info("backend playwright recording broadcast.html → %s", recording_out)
            # 拼完整音轨给 mux:[intro audio] + 中间页 audio_segments + [outro audio]
            # 数字人镜头里 video 是无音的,intro/outro 配音 mp3 必须 mux,否则首末页静音
            full_segs: list[dict] = []
            iv_av = (out.get("intro_video") or {}).get("audio_path")
            iv_dur = (out.get("intro_video") or {}).get("audio_duration") or 5.0
            if iv_av and Path(iv_av).exists():
                full_segs.append({"index": 0, "path": iv_av, "duration_estimate_sec": iv_dur, "ok": True})
            full_segs.extend(out.get("audio_segments") or [])
            ov_av = (out.get("outro_video") or {}).get("audio_path")
            ov_dur = (out.get("outro_video") or {}).get("audio_duration") or 5.0
            if ov_av and Path(ov_av).exists():
                full_segs.append({"index": 999, "path": ov_av, "duration_estimate_sec": ov_dur, "ok": True})
            rec = record_broadcast(broadcast_html, recording_out, audio_segments=full_segs)
            if rec.get("ok"):
                # broadcast 录屏 mp4 = 终交付,写到独立的 `recording` 字段
                # intro_video / outro_video 保留 agent 给的数字人镜头(已嵌在 broadcast.html 里)
                out["recording"] = {
                    "path": rec["path"],
                    "duration": rec.get("duration", 0),
                    "width": rec.get("width", 1920),
                    "height": rec.get("height", 1080),
                    "has_audio": rec.get("has_audio", False),
                    "ok": True,
                    "kind": "playwright_recording",
                    "verified": True,
                    "size_bytes": rec.get("size_bytes", 0),
                }
                out["recording_method"] = list(set((out.get("recording_method") or []) + ["playwright"]))
                out["backend_recording"] = rec
                s.output_json = out
                s.output_text = json.dumps(out, ensure_ascii=False, indent=2)
                _persist_step(run, s)
                log.warning("backend playwright recording ok: %s (%.1fs, audio=%s)",
                            rec["path"], rec.get("duration", 0), rec.get("has_audio"))
                return
            else:
                log.warning("backend playwright recording failed: %s", rec.get("error"))
                out["backend_recording"] = rec
        except Exception as e:  # noqa: BLE001
            log.exception("backend playwright recording exception: %s", e)
            out["backend_recording"] = {"ok": False, "error": str(e)[:300]}

    # ─── 兜底:broadcast 录屏失败 → slides + audio_segments 拼无数字人 mp4
    copy_out = (prev.get("copywriting") or StepState("", "", "")).output_json or {}
    slides = copy_out.get("slides") or []
    if not slides:
        return
    audio_segments = out.get("audio_segments") or []
    output_dir = run.output_dir() / "video"

    try:
        from ..render.slideshow_video import build_slideshow
        result = build_slideshow(
            task_id=run.task_id,
            slides=slides,
            audio_segments=audio_segments,
            output_dir=output_dir,
        )
        if not result.get("ok"):
            log.warning("slideshow fallback failed: %s", result.get("error"))
            out["slideshow_fallback"] = result
            s.output_json = out
            _persist_step(run, s)
            return

        original_reason = out.get("degrade_reason") or "no_recording"
        # 同样写到 recording 字段(非数字人 mp4 也是终交付),并标 kind
        out["recording"] = {
            "path": result["path"],
            "duration": result.get("duration", 0),
            "pages": result.get("pages", 0),
            "has_audio": result.get("has_audio", False),
            "ok": True,
            "kind": "slideshow_fallback",
        }
        out["degraded"] = True
        out["degrade_reason"] = f"{original_reason}_fallback_slideshow"
        out["slideshow_fallback"] = {"ok": True, **result}
        s.output_json = out
        s.output_text = json.dumps(out, ensure_ascii=False, indent=2)
        _persist_step(run, s)
        log.warning(
            "slideshow fallback ok: %s (%.1fs, %d pages, audio=%s)",
            result["path"], result.get("duration", 0),
            result.get("pages", 0), result.get("has_audio"),
        )
    except Exception as e:  # noqa: BLE001
        log.exception("slideshow fallback exception: %s", e)
        out["slideshow_fallback"] = {"ok": False, "error": str(e)[:200]}
        s.output_json = out
        _persist_step(run, s)


async def _run_step(s: StepState, run: TaskRun, prev: dict[str, StepState]) -> None:
    s.status = "running"
    s.started_at = time.time()
    await _broadcast(run, "task.step", _step_event(s))

    # 群聊开场白：让 agent 先在群里说一句"我准备做 X"
    intro_text = STEP_INTRO.get(s.step) or f"我开始处理 {s.label}"
    # video_production 的开场白根据 provider 自适应
    if s.step == "video_production":
        provider = _current_video_provider()
        if provider.id == "none":
            intro_text = "当前是「仅字幕」模式,我只生成 SRT。"
        elif not provider.implemented:
            intro_text = f"当前数字人源 {provider.display_name} 暂未接入,我走降级。"
        else:
            intro_text = f"我用 {provider.display_name} 生成数字人视频。"
    intro_msg = _chat_msg(s.agent, intro_text, kind="intro")
    _persist_chat(run, intro_msg)
    await _broadcast(run, "chat.message", intro_msg)

    # video_production 短路:provider 未实现 或 none 模式 → 不调 agent,后端直接出降级 JSON
    if s.step == "video_production":
        provider = _current_video_provider()
        # 走短路的两种情况:
        #   1) 占位 provider(sadtalker 等 implemented=False)→ 纯 stub
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
            # 即使是 none / stub,只要有 slides 就拼无音频 slides mp4,保证用户拿到"视频"
            _maybe_build_slideshow_fallback(run, s, prev)
            s.ended_at = time.time()
            await _broadcast(run, "task.step", _step_event(s))
            has_fallback_video = bool(((s.output_json or {}).get("intro_video") or {}).get("path"))
            if has_fallback_video:
                summary = f"⚠ 数字人未启用,已生成 slides 翻页视频({len(narrations)} 段)"
            elif provider.id == "none":
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

    # ── 双阶段抽取通用机制:material_parsing / point_extraction 两步都先跑 Phase1 ──
    # Phase1 结果存进 prev 的虚拟 key(`__phase1__` / `__candidates__`)给 Phase2 prompt 渲染
    if s.step == "point_extraction":
        phase1_msg = _chat_msg(s.agent, "Phase 1 · 先列 5-10 条候选,带分,Phase 2 再收敛 top 3", kind="intro")
        _persist_chat(run, phase1_msg)
        await _broadcast(run, "chat.message", phase1_msg)
        extra_env = await env_for_agent(s.agent)
        try:
            pool = (prev["material_parsing"].output_json or {}).get("payload") or prev["material_parsing"].output_json or {}
            p1_res: TurnResult = await run_agent_turn(
                agent_id=s.agent, message=_point_phase1_prompt(run, pool),
                timeout_sec=1500, extra_env=extra_env,
            )
            p1_json = extract_json(p1_res.text) or {}
            ph1_state = StepState(step="__candidates__", label="phase1", agent=s.agent)
            ph1_state.output_json = (p1_json.get("payload") if isinstance(p1_json.get("payload"), dict) else p1_json)
            prev["__candidates__"] = ph1_state
            s.prompt_tokens += p1_res.prompt_tokens
            s.completion_tokens += p1_res.completion_tokens
            await report_usage(
                agent_id=s.agent, task_id=run.task_id,
                provider=p1_res.provider, model=p1_res.model,
                prompt_tokens=p1_res.prompt_tokens,
                completion_tokens=p1_res.completion_tokens,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("point phase1 failed, fallback to single-shot: %s", e)
            ph1_state = StepState(step="__candidates__", label="phase1", agent=s.agent)
            ph1_state.output_json = {}
            prev["__candidates__"] = ph1_state

    # copywriting 走两阶段:Phase 1 抽信息池 + 钩子草稿 → Phase 2 写 narrations + script
    if s.step == "copywriting":
        phase1_msg = _chat_msg(s.agent, "Phase 1 · 先抽信息池 + 拟钩子,Phase 2 再写讲稿", kind="intro")
        _persist_chat(run, phase1_msg)
        await _broadcast(run, "chat.message", phase1_msg)
        extra_env = await env_for_agent(s.agent)
        try:
            core = _prev_json(prev, "upward_optimization", "point_extraction")
            outline = prev["structure_building"].output_json or {}
            p1_res: TurnResult = await run_agent_turn(
                agent_id=s.agent, message=_copywriting_phase1_prompt(run, core, outline),
                timeout_sec=1500, extra_env=extra_env,
            )
            p1_json = extract_json(p1_res.text) or {}
            ph1_state = StepState(step="__copy_phase1__", label="phase1", agent=s.agent)
            ph1_state.output_json = (p1_json.get("payload") if isinstance(p1_json.get("payload"), dict) else p1_json)
            prev["__copy_phase1__"] = ph1_state
            s.prompt_tokens += p1_res.prompt_tokens
            s.completion_tokens += p1_res.completion_tokens
            await report_usage(
                agent_id=s.agent, task_id=run.task_id,
                provider=p1_res.provider, model=p1_res.model,
                prompt_tokens=p1_res.prompt_tokens,
                completion_tokens=p1_res.completion_tokens,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("copywriting phase1 failed, fallback to single-shot: %s", e)
            ph1_state = StepState(step="__copy_phase1__", label="phase1", agent=s.agent)
            ph1_state.output_json = {}
            prev["__copy_phase1__"] = ph1_state

    # material_parsing 走两阶段:Phase 1 地形侦查 → Phase 2 字段抽取
    # 两次 LLM 调用,token 累加,Phase 1 产物落到 prev["__phase1__"] 喂 Phase 2 prompt
    if s.step == "material_parsing":
        phase1_msg = _chat_msg(s.agent, "Phase 1 · 先做地形侦查,看看资料里有什么", kind="intro")
        _persist_chat(run, phase1_msg)
        await _broadcast(run, "chat.message", phase1_msg)
        extra_env = await env_for_agent(s.agent)
        try:
            p1_res: TurnResult = await run_agent_turn(
                agent_id=s.agent, message=_material_phase1_prompt(run),
                timeout_sec=1500, extra_env=extra_env,
            )
            p1_json = extract_json(p1_res.text) or {}
            # 把 Phase 1 结果存进 prev 一个虚拟 key 给 _step_prompt 渲染 Phase 2 用
            ph1_state = StepState(step="__phase1__", label="phase1", agent=s.agent)
            ph1_state.output_json = (p1_json.get("payload") if isinstance(p1_json.get("payload"), dict) else p1_json)
            prev["__phase1__"] = ph1_state
            # token 累计(Phase 1 token 计入本 step,user 在管台看到的是合并)
            s.prompt_tokens += p1_res.prompt_tokens
            s.completion_tokens += p1_res.completion_tokens
            await report_usage(
                agent_id=s.agent, task_id=run.task_id,
                provider=p1_res.provider, model=p1_res.model,
                prompt_tokens=p1_res.prompt_tokens,
                completion_tokens=p1_res.completion_tokens,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("material phase1 failed, fallback to single-shot: %s", e)
            # Phase 1 失败时给 Phase 2 一个空 doc_structure,继续单 shot 不阻塞
            ph1_state = StepState(step="__phase1__", label="phase1", agent=s.agent)
            ph1_state.output_json = {}
            prev["__phase1__"] = ph1_state

    prompt = _step_prompt(s.step, run, prev)
    try:
        # thinking=medium + 大 prompt + 双阶段 — 每 step 都给宽超时,deepseek-flash 慢
        # 老的 180s/240s 太短,structure/upward 这种中间 step 同样长 ctx;900s 也偶尔会卡
        timeout = 1800
        # video_production 还要等子进程 TTS / 数字人 skill(Kling 两段 polling 各 ~1.5 min)
        if s.step == "video_production":
            timeout = 2400
        extra_env = await env_for_agent(s.agent)
        res: TurnResult = await run_agent_turn(
            agent_id=s.agent, message=prompt, timeout_sec=timeout,
            extra_env=extra_env,
        )
        s.output_text = res.text
        s.output_json = extract_json(res.text)
        s.provider = res.provider
        s.model = res.model
        s.prompt_tokens += res.prompt_tokens
        s.completion_tokens += res.completion_tokens
        s.status = "success"

        await report_usage(
            agent_id=s.agent, task_id=run.task_id,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )

        # ── agent 自主信号处理:needs_retry / needs_help ──
        # agent 在 JSON 里可以声明 "needs_retry":{"reason":...,"hint":...} 让自己再来一轮
        # backend 把 hint 加到 prompt 末尾,重跑 1 次(最多 1 次,防死循环)
        payload = (s.output_json or {}).get("payload") or s.output_json or {}
        needs_retry = payload.get("needs_retry") or (s.output_json or {}).get("needs_retry")
        if needs_retry and isinstance(needs_retry, dict) and not getattr(s, "_retried", False):
            reason = str(needs_retry.get("reason") or "")[:120]
            hint = str(needs_retry.get("hint") or "")[:300]
            log.warning("agent %s declared needs_retry: %s | hint=%s", s.agent, reason, hint)
            retry_msg = _chat_msg(
                s.agent, f"我自检发现:{reason} — 重做一次",
                kind="intro",
            )
            _persist_chat(run, retry_msg)
            await _broadcast(run, "chat.message", retry_msg)
            retry_prompt = prompt + (
                f"\n\n# 上一轮你自己声明 needs_retry · reason={reason}\n"
                f"上轮的 hint:{hint}\n"
                f"这一轮请按 hint 修正,**不要再声明 needs_retry**。"
            )
            try:
                res2: TurnResult = await run_agent_turn(
                    agent_id=s.agent, message=retry_prompt, timeout_sec=timeout,
                    extra_env=extra_env,
                )
                s.output_text = res2.text
                s.output_json = extract_json(res2.text)
                s.prompt_tokens += res2.prompt_tokens
                s.completion_tokens += res2.completion_tokens
                await report_usage(
                    agent_id=s.agent, task_id=run.task_id,
                    provider=res2.provider, model=res2.model,
                    prompt_tokens=res2.prompt_tokens, completion_tokens=res2.completion_tokens,
                )
                setattr(s, "_retried", True)
            except Exception as e:  # noqa: BLE001
                log.warning("retry for %s failed: %s — keep first attempt", s.agent, e)

        # needs_help:agent 觉得上游缺数据,记到群聊给 coordinator 兜底
        needs_help = payload.get("needs_help") or (s.output_json or {}).get("needs_help")
        if needs_help and isinstance(needs_help, dict):
            help_msg = _chat_msg(
                s.agent,
                f"我需要帮忙 · 找 {needs_help.get('from','?')} 要 {needs_help.get('what','?')}",
                kind="result",
            )
            _persist_chat(run, help_msg)
            await _broadcast(run, "chat.message", help_msg)

        # 落盘
        _persist_step(run, s)
        # html_design 成功后,后端实际渲染 self-contained HTML 投屏页
        if s.step == "html_design":
            _build_html_artifact(run, s, prev)
        # video_production 完成后处理(顺序敏感):
        # 1. 中间页 narrations 没 audio_segments → backend 跑 MiniMax TTS 兜底(HeyGen 模式必须)
        # 2. 重渲 broadcast.html(audio file:// + 数字人 intro/outro mp4 嵌进去)
        # 3. playwright 录 broadcast.html → recording.mp4(终交付)
        if s.step == "video_production":
            try:
                _ensure_audio_segments(run, s, prev)
            except Exception as e:  # noqa: BLE001
                log.warning("ensure_audio_segments failed: %s", e)
            try:
                html_state = prev.get("html_design")
                if html_state and html_state.status == "success":
                    _build_html_artifact(run, html_state, prev)
                    log.info("broadcast.html re-rendered after video_production (audio + intro/outro)")
            except Exception as e:  # noqa: BLE001
                log.warning("broadcast.html re-render failed: %s", e)
            _maybe_build_slideshow_fallback(run, s, prev)
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


def _coordinator_briefing_prompt(run: TaskRun) -> str:
    """coordinator 启动 brief 调用 — 让总控读完原始材料和 supplement,为 8 个 step 各写一段侧重指引。"""
    raw_head = (run.raw_text or "")[:3500]
    return f"""你是【汇报总控】(coordinator)。pipeline 即将开始,这是你的**首次任务理解**。

你**不写任何最终产物**,只做两件事:
1. **理解本次输入的独特点**:这份材料有什么不一样?数据密度?是否有附件?有无敏感信息?语气基调?
2. **为 8 个下游 agent 各写一段「本任务侧重」指引** — 让通用角色(SOUL.md)针对本次任务做适配。
   每段 ≤80 字,告诉对应 agent "**本次任务**需要你特别注意什么 / 强调什么 / 跳过什么"。

# 输入信息
- title:      {run.title}
- report_type:{run.report_type}
- audience:   {run.audience}
- duration:   {run.duration}
- style:      {run.style}
- supplement: {run.supplement or "(无)"}

# 原始材料(前 3500 字)
```
{raw_head}
```

# 你要给 8 个 agent 写的 brief(都是动态指引,不要重复 SOUL.md 的通用内容)
- **material**(资料员):材料类型 / 数据密度 / 抽取重点 / 易漏字段。例如:"这份材料含大量百分比数字,key_data 优先抓百分比;附件 xlsx 第 3 列疑似进度,逐行扫"
- **point-extractor**(分析师):本次最该突出的 1-2 个 point 主题 / 哪些是配角 / 风险该不该升级。例如:"客户合同金额是本次第一重点,其它 4 项能力可压成 1 条 key_point"
- **structure**(结构师):本次大纲建议偏向哪种结构(总分总 / 时间线 / 主次)、需不需要把 risks 章节前置。例如:"supplement 提到风险显化,大纲第 2 章必须是 risks 不是 completed"
- **upward-opt**(表达教练):本次最需要改写的关键句的方向。例如:"原材料语气太技术化,改成业务影响优先;'token 配额耗尽' 改成 '7 月发布节奏的关键决策点'"
- **copywriter**(文书):本次讲稿的钩子方向(数据前置 / 进展状态 / 行动驱动)、信息池中哪些 fact 必须保留。例如:"开篇必须出 87 万异常报价数字;'数字员工'不要替换成'AI 助手'"
- **html-designer**(设计师):本次视觉调性 / 色彩克制度。例如:"audience 是市领导,克制商务灰蓝;不要 sparkle / 紫粉渐变"
- **video-producer**(视频):本次配音语气 / 是否需要数字人。例如:"稳健保守 → 选 male-qn-qingse 沉稳男声,语速 0.95"
- **reviewer**(质检):本次特别要扫的禁忌(比如某些用户提到的禁用词)、supplement 关键词列表(对账用)。例如:"对账 supplement 4 条,核心数据 87 万 / 68% / 1240 次必须在讲稿出现"

# 输出 schema(严格 JSON)
```json
{{
  "msg_type": "task.coordinator.brief",
  "task_understanding": "<≤120 字 — 你对本次任务的整体理解 + 关键判断>",
  "agent_briefs": {{
    "material":         "<≤80 字 · 本任务对材料员的侧重指引>",
    "point-extractor":  "<≤80 字 · 对分析师的侧重>",
    "structure":        "<≤80 字 · 对结构师的侧重>",
    "upward-opt":       "<≤80 字 · 对表达教练的侧重>",
    "copywriter":       "<≤80 字 · 对文书的侧重>",
    "html-designer":    "<≤80 字 · 对设计师的侧重>",
    "video-producer":   "<≤80 字 · 对视频制作的侧重>",
    "reviewer":         "<≤80 字 · 对质检员的侧重>"
  }}
}}
```

**禁止**:重复 SOUL.md 通用规则、堆砌"要写得好/要严格"等空话、超过 80 字。
每条 brief 必须给出**本次任务特有**的具体指令。"""


async def _coordinator_briefing(run: TaskRun) -> None:
    """调 coordinator agent 跑一次任务理解 → 8 个 agent_brief 写入 run.agent_briefs."""
    extra_env = await env_for_agent("coordinator")
    res: TurnResult = await run_agent_turn(
        agent_id="coordinator",
        message=_coordinator_briefing_prompt(run),
        timeout_sec=1500,
        extra_env=extra_env,
    )
    data = extract_json(res.text) or {}
    briefs = data.get("agent_briefs") or {}
    if not isinstance(briefs, dict):
        log.warning("coordinator briefing: invalid agent_briefs shape, skipping")
        return
    # 只保留 STEPS 里实际存在的 agent
    valid_agents = {a for _, a, _, _ in STEPS}
    run.agent_briefs = {a: str(b).strip() for a, b in briefs.items()
                        if a in valid_agents and b and str(b).strip()}
    # 把 brief 也按 step_key 映射一份(_agent_brief_block 按 step 查)
    step_to_agent = {k: a for k, a, _, _ in STEPS}
    run.agent_briefs = {
        **{a: v for a, v in run.agent_briefs.items()},
        **{k: run.agent_briefs[a] for k, a in step_to_agent.items() if a in run.agent_briefs},
    }
    run.coordinator_brief_summary = (data.get("task_understanding") or "").strip()
    await report_usage(
        agent_id="coordinator", task_id=run.task_id,
        provider=res.provider, model=res.model,
        prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
    )
    log.warning("coordinator briefing ok: %d agent_briefs, summary=%s",
                len(briefs), run.coordinator_brief_summary[:60])


def create_task(
    *, task_id: str, title: str, report_type: str, audience: str,
    duration: str, style: str, raw_text: str, supplement: str = "",
    harness_version: str | None = None,
) -> TaskRun:
    # v2 群聊 harness flag(P1)。非 "v1"/"v2" 一律降级为 "v1"(FR-002)。
    hv = harness_version if harness_version in ("v1", "v2") else "v1"
    run = TaskRun(
        task_id=task_id, title=title, report_type=report_type,
        audience=audience, duration=duration, style=style, raw_text=raw_text,
        supplement=supplement,
        steps=[StepState(step=k, label=l, agent=a) for k, a, l, _ in STEPS],
        harness_version=hv,
    )
    _runs[task_id] = run
    return run


async def execute(run: TaskRun) -> None:
    # coordinator 开场白 — 明确角色:导演 ≠ 控制器
    intro = _chat_msg(
        "coordinator",
        f"收到「{run.title}」。我先做一次任务理解,把这次输入的特点和侧重告诉团队。"
        f"之后每位同事自己跑、自己交棒,我在群里盯着;谁卡住或需要帮忙直接喊我,我来调度。",
        kind="intro",
    )
    _persist_chat(run, intro)
    await _broadcast(run, "chat.message", intro)

    # ── coordinator 先做一次"任务理解 + 上下文规划",为 8 个 step 生成 agent_brief ──
    try:
        await _coordinator_briefing(run)
        if run.coordinator_brief_summary:
            brief_msg = _chat_msg(
                "coordinator",
                f"任务侧重已分派 — {run.coordinator_brief_summary[:120]}",
                kind="result",
            )
            _persist_chat(run, brief_msg)
            await _broadcast(run, "chat.message", brief_msg)
    except Exception as e:  # noqa: BLE001
        log.warning("coordinator briefing failed (non-blocking): %s", e)

    # ── 用 harness 跑(event-driven) ──
    prev: dict[str, StepState] = {}
    by_key = {s.step: s for s in run.steps}

    async def _chat_publisher(from_agent: str, text: str, kind: str) -> None:
        msg = _chat_msg(from_agent, text, kind=kind)
        _persist_chat(run, msg)
        try:
            await _broadcast(run, "chat.message", msg)
        except Exception:  # noqa: BLE001
            pass

    from .harness import run_harness

    events_jsonl_path = run.output_dir() / "events.jsonl"
    is_v2 = run.harness_version == "v2"
    harness_result = await run_harness(
        run=run,
        prev=prev,
        by_key=by_key,
        steps_meta=STEPS,
        default_next_step=DEFAULT_NEXT_STEP,
        run_step_fn=_run_step,
        gate_review_fn=_gate_review,
        chat_publisher=_chat_publisher,
        events_jsonl_path=events_jsonl_path,
        agent_display=AGENT_DISPLAY,  # 用户可见 chat 文案显示名 ↔ agent_id 映射
        is_v2=is_v2,                  # v2 群聊 harness flag(P1,默认 False;True 时启用 v2 事件 emit)
    )
    log.info("harness done · reason=%s · chain=%s · visited=%s · events=%d",
             harness_result.get("reason"),
             " → ".join(harness_result.get("chain") or []),
             harness_result.get("visited"),
             harness_result.get("events_count"))

    # 处理未到达的 step → 标 skipped
    for step_key2, _ag, _lab, _dep in STEPS:
        s2 = by_key[step_key2]
        if s2.status == "pending":
            s2.status = "skipped"
            s2.started_at = s2.ended_at = time.time()
            try:
                await _broadcast(run, "task.step", _step_event(s2))
            except Exception:  # noqa: BLE001
                pass

    has_failed = any(s.status == "failed" for s in run.steps)
    has_skipped = any(s.status == "skipped" for s in run.steps)
    has_success = any(s.status == "success" for s in run.steps)
    run.status = (
        "done" if not has_failed and not has_skipped
        else "partial" if has_success
        else "failed"
    )

    # P3 (T025, spec 003):v2 路径收尾已由 observer gatekeeper 接管 —— emit
    # gate_pass/gate_reject + set done/partial(coordinator_observer._on_quiescence);
    # 4 核心 artifact 版本化由 _emit_v2_step_overlay 在每个 step 完成时做。
    # 因此**不再**调 _emit_v2_finalization(避免双 gate 事件 + 重复 artifact 版本,research F3)。
    # _emit_v2_finalization 函数保留,供 P1 test_v2_integration 直接单测 v2 事件 schema。

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


async def refine_with_action(
    *,
    run: TaskRun,
    action: str,
    plan: dict,
    segment_id: str | None = None,
) -> None:
    """快捷按钮触发的 refine — 不走 coordinator LLM 解析,直接按 plan 重跑。

    与 handle_user_feedback 的区别:
      - 自由文本走 handle_user_feedback(coordinator LLM 决策走哪些 step)
      - 5 个 PRD 快捷按钮走本函数(action → plan 是硬映射,无 LLM 中间层,快 + 稳)
    plan 来源:api/tasks.py REFINE_ACTION_MAP[action]
    """
    target_step = plan["step"]
    instruction = plan["instruction"]
    user_note = plan["user_note"]
    if segment_id and action == "regenerate_segment":
        instruction = f"{instruction}\n\n**特别指定**:重写 segment_id={segment_id} 对应的 narration。"

    # 1. coordinator 在 chat 里承接用户意图(已脱敏:不提 action 英文 enum)
    ack = _chat_msg("coordinator", f"📝 {user_note}", kind="result")
    _persist_chat(run, ack)
    await _broadcast(run, "chat.message", ack)

    # 2. 把 target_step + 下游全部 reset 为 pending,准备重跑
    impacted = _expand_impacted_steps([target_step])
    for s in run.steps:
        if s.step in impacted:
            s.status = "pending"
            s.output_text = ""
            s.output_json = None
            s.started_at = s.ended_at = None
            s.error = None
    run.status = "running"
    await _broadcast(run, "task.done", {"status": "running"})  # 前端切回"进行中"

    # 3. 顺序重跑:target_step 带 instruction,下游 step 不带(让它们按上游新结果自然产)
    prev: dict[str, StepState] = {s.step: s for s in run.steps if s.status == "success"}
    for s in run.steps:
        if s.status != "pending":
            continue
        instr_for_step = instruction if s.step == target_step else ""
        await _run_step_with_instruction(s, run, prev, instr_for_step)
        if s.status == "success":
            prev[s.step] = s

    # 4. 收尾
    has_failed = any(s.status == "failed" for s in run.steps)
    run.status = "partial" if has_failed else "done"
    _persist_final(run)
    closing = _chat_msg(
        "coordinator",
        "✅ 已按你的意见更新,请再看看。如果还要调整,可以继续点按钮或直接说。",
        kind="result",
    )
    _persist_chat(run, closing)
    await _broadcast(run, "chat.message", closing)
    await _broadcast(run, "task.done", {"status": run.status})


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
            timeout = 2400
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
        if s.step == "video_production":
            try:
                _ensure_audio_segments(run, s, prev)
            except Exception as e:  # noqa: BLE001
                log.warning("refine ensure_audio_segments failed: %s", e)
            try:
                html_state = prev.get("html_design")
                if html_state and html_state.status == "success":
                    _build_html_artifact(run, html_state, prev)
            except Exception as e:  # noqa: BLE001
                log.warning("refine broadcast re-render failed: %s", e)
            _maybe_build_slideshow_fallback(run, s, prev)
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
