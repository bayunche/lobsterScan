"""龙虾集群对话主入口

不像 /api/tasks 那样有固定 8-step pipeline，这里是用户跟集群的自由对话：
- 默认 coordinator 接收并回应
- coordinator 在 system prompt 里被告知：发现「生成汇报材料」意图时返回 intent=create_report
- 业务后端拿到 intent 后真触发 pipeline，并回送 task_id 让前端跳转
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..openclaw.client import extract_json, run_agent_turn
from ..openclaw.secrets import env_for_agent
from ..openclaw.tokens import report_usage

router = APIRouter(prefix="/cluster", tags=["cluster"])


# session_id (浏览器侧持久) → 历史 + 订阅
_SESSIONS: dict[str, dict[str, Any]] = {}


def _get_session(sid: str) -> dict[str, Any]:
    if sid not in _SESSIONS:
        _SESSIONS[sid] = {
            "id": sid,
            "messages": [],            # list[dict]
            "subscribers": [],         # asyncio.Queue
            "created_at": time.time(),
        }
    return _SESSIONS[sid]


def _make_msg(agent: str, text: str, kind: str, **extra) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "agent": agent,
        "display_name": _DISPLAY.get(agent, agent),
        "seal": _SEAL_CHAR.get(agent, "·"),
        "ts": time.time(),
        "kind": kind,             # user | intro | result | system
        "text": text,
        **extra,
    }


_DISPLAY = {
    "user": "你",
    "coordinator": "汇报总控", "material": "资料员",
    "point-extractor": "分析师", "structure": "结构师",
    "upward-opt": "表达教练", "copywriter": "文书",
    "html-designer": "设计师", "video-producer": "视频制作",
    "reviewer": "质量检查员",
}
_SEAL_CHAR = {
    "user": "阅",
    "coordinator": "调", "material": "料", "point-extractor": "析",
    "structure": "纲", "upward-opt": "译", "copywriter": "文",
    "html-designer": "设", "video-producer": "影", "reviewer": "校",
}


# 跟 coordinator 自由对话的 system 指令片段
COORDINATOR_FREE_PROMPT = """\
现在你是「龙虾集群」与用户的接待入口。请按下面的规则回应：

1. 用户消息可能是任意话题：你简短自然地回应；不要每次都强行问"是否生成汇报"。
2. 如果用户消息含有"生成 / 整理 / 帮我做 / 我想要 ... 汇报 / 周报 / 述职 / 演讲 / PPT"等明显意图，
   在最后一段以 ```json``` 输出 intent，例如：
   {"intent": "create_report", "report_type": "project_progress",
    "title": "本周项目进度汇报", "duration": "3分钟", "raw_text_hint": "用户提供的材料"}
3. 如果用户已经粘了一大段工作记录、任务清单等"原材料"，直接给 intent=create_report，raw_text_hint 用用户原文。
4. 没有 intent 时不要输出 JSON 代码块。
5. 回应保持中文、专业、克制。

用户的本轮消息：
"""


class ChatRequest(BaseModel):
    text: str
    session_id: str | None = None


class FeatReportRequest(BaseModel):
    session_id: str
    raw_text: str
    title: str | None = None
    report_type: str = "project_progress"
    audience: str = "直属领导"
    duration: str = "3分钟"
    style: str = "简洁正式"
    file_ids: list[str] = []


@router.post("/chat")
async def chat(req: ChatRequest) -> dict:
    sid = req.session_id or uuid.uuid4().hex[:16]
    session = _get_session(sid)

    user_msg = _make_msg("user", req.text, "user")
    session["messages"].append(user_msg)
    await _broadcast(session, "chat.message", user_msg)

    # 检测 @ 路由：用户消息里 @某位中文名 / @agent_id → 直接给那几位
    mentions = _detect_mentions(req.text)
    if mentions:
        for aid in mentions:
            asyncio.create_task(_handle_agent_reply(session, aid, req.text))
    else:
        asyncio.create_task(_handle_coordinator(session, req.text))

    return {"session_id": sid, "message_id": user_msg["id"], "mentions": mentions}


_NAME_TO_ID = {v: k for k, v in _DISPLAY.items()}


def _detect_mentions(text: str) -> list[str]:
    """从消息里抠 @中文名 / @agent_id."""
    import re as _re
    out: list[str] = []
    for m in _re.finditer(r"@([一-龥A-Za-z\-_]+)", text):
        tag = m.group(1).strip()
        if tag in _NAME_TO_ID and _NAME_TO_ID[tag] != "user":
            out.append(_NAME_TO_ID[tag])
        elif tag in _DISPLAY and tag != "user":
            out.append(tag)
    seen, uniq = set(), []
    for a in out:
        if a not in seen:
            seen.add(a); uniq.append(a)
    return uniq


def _agent_prompt(agent_id: str, user_text: str) -> str:
    return f"""你是「龙虾集群」里的一位成员（{_DISPLAY.get(agent_id)}）。
用户在群里 @ 了你，请用你这个角色应有的口吻回应。

- 短答（≤3 段），中文，专业克制
- 不要输出 JSON 代码块，不要表格
- 如果请求超出你的职责，简短说明并 @ 一位更合适的同事

用户消息：
{user_text}
"""


async def _handle_agent_reply(session: dict, agent_id: str, user_text: str) -> None:
    typing = _make_msg(agent_id, "…", "intro")
    session["messages"].append(typing)
    await _broadcast(session, "chat.message", typing)

    try:
        extra_env = await env_for_agent(agent_id)
        res = await run_agent_turn(
            agent_id=agent_id,
            message=_agent_prompt(agent_id, user_text),
            timeout_sec=120, extra_env=extra_env,
        )
        await report_usage(
            agent_id=agent_id, task_id=None,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )
        typing["text"] = (res.text or "").strip() or "（无回应）"
        typing["kind"] = "result"
        typing["tokens"] = res.prompt_tokens + res.completion_tokens
        await _broadcast(session, "chat.message.update", typing)
    except Exception as e:  # noqa: BLE001
        typing["text"] = f"我这边出了点状况：{str(e)[:120]}"
        typing["kind"] = "result"
        await _broadcast(session, "chat.message.update", typing)


@router.get("/chat/{session_id}/messages")
async def get_messages(session_id: str) -> dict:
    session = _SESSIONS.get(session_id)
    if not session:
        return {"messages": [], "session_id": session_id}
    return {"messages": session["messages"], "session_id": session_id}


@router.get("/chat/{session_id}/events")
async def events(session_id: str):
    """SSE 订阅：用户消息 / coordinator 回应 / intent 触发 / 跳转指令."""
    session = _get_session(session_id)

    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        session["subscribers"].append(q)
        try:
            for m in session["messages"]:
                yield {"event": "chat.message", "data": json.dumps(m, ensure_ascii=False)}
            while True:
                evt = await q.get()
                if evt is None:
                    return
                event, data = evt
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
        finally:
            try:
                session["subscribers"].remove(q)
            except ValueError:
                pass

    return EventSourceResponse(gen(), ping=15)


async def _broadcast(session: dict, event: str, data: dict) -> None:
    for q in list(session["subscribers"]):
        await q.put((event, data))


async def _handle_coordinator(session: dict, user_text: str) -> None:
    """让 coordinator 自由回应；解析 intent；必要时启动 pipeline."""
    # 显示打字中状态
    typing_msg = _make_msg("coordinator", "…", "intro")
    session["messages"].append(typing_msg)
    await _broadcast(session, "chat.message", typing_msg)

    history_summary = ""
    recent_user = [m for m in session["messages"][-10:] if m["agent"] == "user"]
    if len(recent_user) > 1:
        history_summary = "\n# 最近用户上下文\n" + "\n".join(
            f"- {m['text'][:200]}" for m in recent_user[:-1]
        )

    prompt = COORDINATOR_FREE_PROMPT + user_text + history_summary

    try:
        extra_env = await env_for_agent("coordinator")
        res = await run_agent_turn(
            agent_id="coordinator", message=prompt,
            timeout_sec=120, extra_env=extra_env,
        )
        text = (res.text or "").strip()
        intent_json = extract_json(text)
        await report_usage(
            agent_id="coordinator", task_id=None,
            provider=res.provider, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )

        # 把回复主体（去掉 JSON 代码块）做为消息文本
        display_text = _strip_json_block(text)
        # 替换 typing 占位
        typing_msg["text"] = display_text or "（无回应）"
        typing_msg["kind"] = "result"
        typing_msg["tokens"] = res.prompt_tokens + res.completion_tokens
        await _broadcast(session, "chat.message.update", typing_msg)

        # 如果识别到意图，触发 pipeline 并广播 task.created
        if isinstance(intent_json, dict) and intent_json.get("intent") == "create_report":
            from ..orchestrator import pipeline
            task_id = f"tsk_{uuid.uuid4().hex[:12]}"
            raw_text = intent_json.get("raw_text_hint") or user_text
            run = pipeline.create_task(
                task_id=task_id,
                title=intent_json.get("title") or "新汇报",
                report_type=intent_json.get("report_type") or "daily",
                audience=intent_json.get("audience") or "直属领导",
                duration=intent_json.get("duration") or "3分钟",
                style=intent_json.get("style") or "简洁正式",
                raw_text=raw_text,
            )
            await _broadcast(session, "task.created", {
                "task_id": task_id,
                "title": run.title,
                "report_type": run.report_type,
            })
            # 后台跑
            asyncio.create_task(pipeline.execute(run))

    except Exception as e:  # noqa: BLE001
        typing_msg["text"] = f"我这边出了点状况：{str(e)[:120]}"
        typing_msg["kind"] = "result"
        await _broadcast(session, "chat.message.update", typing_msg)


def _strip_json_block(text: str) -> str:
    import re as _re
    return _re.sub(r"```(?:json)?\s*\n.*?\n\s*```", "", text, flags=_re.S).strip()


# ─────────────────────────────────────────────────────────────
# 功能：汇报材料生成（feat/report）
#
# 用户在 /feat/report 上传材料 + 选选项后调用：
#   - 创建 pipeline task
#   - 在 cluster session 注入一条系统消息
#   - 把 pipeline 的群聊事件桥接到 cluster session（前端首页能看到 8 agent 讨论）
#   - 返回 task_id 给前端跳转
# ─────────────────────────────────────────────────────────────


@router.post("/feat/report")
async def feat_report(req: FeatReportRequest) -> dict:
    from ..orchestrator import pipeline
    from .files import lookup_many_with_paths
    from ..extract import extract_attachment_text
    session = _get_session(req.session_id)

    # 解出上传的文件 + 真磁盘 path(给抽取器用)
    items = lookup_many_with_paths(req.file_ids)
    attachments = [meta for meta, _ in items]   # 给前端 message 用,不带 path

    task_id = f"tsk_{uuid.uuid4().hex[:12]}"
    # 把 raw_text + 每个附件**真实抽出的内容**喂给 material agent
    # 之前只拼了文件名/大小,agent 完全看不到内容 → 报"素材整理失败"
    raw_text = req.raw_text or ""
    if items:
        sections: list[str] = []
        unparsed: list[str] = []
        for meta, p in items:
            r = extract_attachment_text(p, meta["filename"], meta["mime"])
            head = f"## 附件:{meta['filename']}({_human_size(meta['size'])}, {meta['mime']})"
            if r.ok:
                body = r.text + (f"\n\n_(注:{r.note})_" if r.truncated else "")
                sections.append(f"{head}\n\n{body}")
            else:
                unparsed.append(f"- {meta['filename']}:{r.note}")
                sections.append(f"{head}\n\n_未抽取_({r.note})")
        raw_text = (raw_text + "\n\n" if raw_text else "") + "\n\n---\n\n".join(sections)
        if unparsed:
            raw_text += (
                "\n\n# 解析失败的附件(请在 data_gaps 注明,提示用户改文本框输入)\n"
                + "\n".join(unparsed)
            )

    run = pipeline.create_task(
        task_id=task_id,
        title=req.title or "汇报材料",
        report_type=req.report_type,
        audience=req.audience,
        duration=req.duration,
        style=req.style,
        raw_text=raw_text,
    )

    # 用户系统消息：带附件 + 任务元信息
    text = f"📂 启动 [汇报材料生成] · {req.title or '汇报材料'} · {req.duration}"
    if not req.raw_text and attachments:
        text += f" · {len(attachments)} 份附件"
    elif req.raw_text and attachments:
        text += f" · 含 {len(attachments)} 份附件"

    intro = _make_msg("user", text, kind="system")
    intro["display_name"] = "你"
    intro["seal"] = "阅"
    intro["task_id"] = task_id
    intro["attachments"] = attachments
    intro["report_meta"] = {
        "title":       req.title or "汇报材料",
        "audience":    req.audience,
        "duration":    req.duration,
        "style":       req.style,
        "report_type": req.report_type,
        "preview":     (req.raw_text or "")[:200],
    }
    session["messages"].append(intro)
    await _broadcast(session, "chat.message", intro)

    # 后台：跑 pipeline + 桥接它的 chat 事件到 cluster session
    asyncio.create_task(_run_and_bridge(session, run))

    return {
        "ok": True, "task_id": task_id, "session_id": req.session_id,
        "attachments": attachments,
    }


def _human_size(n: int) -> str:
    if n < 1024:           return f"{n} B"
    if n < 1024 * 1024:    return f"{n/1024:.1f} KB"
    if n < 1024**3:        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024**3:.1f} GB"


async def _run_and_bridge(session: dict, run) -> None:
    """同时跑 pipeline 并把它的 chat 事件转到 cluster session."""
    from ..orchestrator import pipeline

    # 用一个独立 task 订阅 pipeline 群聊，转到 cluster session
    async def forwarder():
        async for event, data in pipeline.subscribe(run.task_id):
            if event == "chat.message":
                # 桥接 pipeline 群聊 → cluster session（append + broadcast）
                # 把 'avatar' 字段映射成 'seal' 字段，复用前端模板
                msg = dict(data)
                msg.setdefault("seal", data.get("avatar", "·"))
                session["messages"].append(msg)
                await _broadcast(session, "chat.message", msg)
            elif event == "task.done":
                done_note = _make_msg(
                    "coordinator",
                    f"✅ 整套材料已就绪。打开任务详情看完整产物 → /tasks/{run.task_id}",
                    kind="result",
                )
                done_note["link"] = f"/tasks/{run.task_id}"
                session["messages"].append(done_note)
                await _broadcast(session, "chat.message", done_note)

    forward_task = asyncio.create_task(forwarder())
    await asyncio.sleep(0)        # 让 forwarder 先注册 subscriber
    try:
        await pipeline.execute(run)
    finally:
        try:
            await asyncio.wait_for(forward_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            forward_task.cancel()
