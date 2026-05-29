"""Multi-agent harness · event-driven runtime

设计思想:
- Pipeline = EventBus + AgentWorkers + Coordinator(三层)
- Backend 不再当 "导演" 串 STEPS,只提供 transport(chat / SSE / 持久化)
- Agent 跑完后通过 handoff 字段声明"把活交给谁",Coordinator 路由
- 失败 / needs_help / needs_retry 都是事件,Coordinator 决策(retry / 路由上游 / 兜底)
- 全 trace 落 events.jsonl,可重放

事件 kinds(命名收敛):
  task.start       任务开始
  task.end         任务结束(DONE / aborted)
  agent.start      worker 开干
  agent.done       worker 跑完产物 ok
  agent.failed     worker 跑炸
  agent.handoff    worker 声明交棒(payload.to)
  agent.needs_help worker 声明上游缺数据(payload.from + payload.what)
  agent.needs_retry worker 自检不过(payload.hint)
  chain.skip       Coordinator 因依赖不足跳过某 step
  chain.revisit_limit  某 step 被访问过 N 次,熔断
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .ids import MessageIdRegistry

if TYPE_CHECKING:
    from .events_v2 import V2EventBase
    from .subscription import SubscriptionRegistry

log = logging.getLogger("orchestrator.harness")

Handler = Callable[["AgentEvent"], Awaitable[None]]


# ───────────────────────────── 事件 ─────────────────────────────

@dataclass
class AgentEvent:
    kind: str
    agent_id: str
    step_key: str | None = None
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "agent_id": self.agent_id,
            "step_key": self.step_key,
            "payload": self.payload,
            "ts": self.ts,
        }


class EventBus:
    """In-memory async pub/sub。handlers 必须是 async。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._wild: list[Handler] = []

    def on(self, kind: str, handler: Handler) -> None:
        self._subs[kind].append(handler)

    def on_any(self, handler: Handler) -> None:
        self._wild.append(handler)

    async def emit(self, event: AgentEvent) -> None:
        # 串行 dispatch(确保 visited / route 顺序确定;handler 自己应避免阻塞)
        for h in list(self._wild):
            try:
                await h(event)
            except Exception as e:  # noqa: BLE001
                log.exception("wildcard handler crashed on %s: %s", event.kind, e)
        for h in list(self._subs.get(event.kind, [])):
            try:
                await h(event)
            except Exception as e:  # noqa: BLE001
                log.exception("handler crashed on %s: %s", event.kind, e)


# ───────────────────────────── 任务状态 ─────────────────────────────

@dataclass
class HarnessState:
    """共享给所有 worker / coordinator 的 task-level 状态。"""

    run: Any                        # TaskRun(避免循环 import,用 Any)
    prev: dict[str, Any]            # step_key → StepState
    by_key: dict[str, Any]          # step_key → StepState(同 prev,只是 alias)
    bus: EventBus
    visited: dict[str, int] = field(default_factory=dict)
    events_log: list[AgentEvent] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)
    done: asyncio.Future | None = None
    events_jsonl_path: Path | None = None
    # v2 群聊 harness 字段(P1, spec 001-v2-chat-protocol-state)。默认 False, v1 路径行为不变。
    is_v2: bool = False
    message_id_registry: MessageIdRegistry = field(default_factory=MessageIdRegistry)
    # P2 字段(spec 002-worker-subscription, T010)。
    # 守红线:v1 路径下 subscriptions 永远 None;agent_locks 空 dict(get_agent_lock 不会被调)。
    # is_v2=True 时由 run_harness 构造 SubscriptionRegistry 并注册 worker(Phase 4 T028)。
    subscriptions: "SubscriptionRegistry | None" = None
    agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        """lazy-init per-agent Lock(spec FR-008;详 research.md §3)。

        必须在**运行中的 event loop** 内调用(asyncio.Lock() 绑定当前 loop)。
        任务级隔离:同 task 内 agent_id 相同 → 同一把锁;跨 task 不串扰
        (每个 HarnessState 实例独立 agent_locks dict)。
        """
        if agent_id not in self.agent_locks:
            self.agent_locks[agent_id] = asyncio.Lock()
        return self.agent_locks[agent_id]

    async def emit(self, kind: str, agent_id: str,
                   step_key: str | None = None,
                   payload: dict | None = None) -> None:
        ev = AgentEvent(kind=kind, agent_id=agent_id, step_key=step_key,
                        payload=payload or {})
        self.events_log.append(ev)
        # 写一行到 events.jsonl(异步追加,容错)
        if self.events_jsonl_path:
            try:
                self.events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with self.events_jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            except Exception as e:  # noqa: BLE001
                log.warning("events.jsonl write failed: %s", e)
        await self.bus.emit(ev)

    async def emit_v2(self, event: "V2EventBase") -> None:
        """v2 群聊事件 emit(P1, spec 001-v2-chat-protocol-state)。

        关键不变量:
          - self.is_v2=False(v1 路径)时立即 return,零开销
          - message_id 重复 → 降级写一条 agent.failed(FR-005 / FR-020),不抛
          - schema 由 Pydantic 在构造 event 时已校验,这里不再二次校验
          - 写入 events.jsonl 用 model_dump(mode="json")(扁平 dict,与 v1 行格式兼容)
        """
        if not self.is_v2:
            return  # v1 路径短路 — 不动 events.jsonl 也不发总线

        # message_id 去重
        if not self.message_id_registry.add_or_reject(event.message_id):
            log.warning("duplicate v2 message_id rejected: %s (msg_type=%s)",
                        event.message_id, event.msg_type)
            await self.emit("agent.failed", "coordinator", None, {
                "error": f"duplicate v2 message_id: {event.message_id}",
                "msg_type": event.msg_type,
            })
            return

        row = event.model_dump(mode="json", by_alias=True)
        # 写 events.jsonl(v1 行带 kind,v2 行带 msg_type,replay 工具靠这两个字段区分)
        if self.events_jsonl_path:
            try:
                self.events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with self.events_jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception as e:  # noqa: BLE001
                # 降级:写入失败仅 warn,不影响任务(FR-020)
                log.warning("events.jsonl v2 write failed: %s", e)

        # 投到 EventBus(让 wildcard / kind 订阅都能看到;kind 用 msg_type 命名空间)
        agent_id = ""
        if hasattr(event, "from_"):
            agent_id = getattr(event, "from_") or ""
        elif hasattr(event, "producer"):
            agent_id = getattr(event, "producer") or ""
        await self.bus.emit(AgentEvent(
            kind=event.msg_type, agent_id=agent_id, step_key=None, payload=row,
        ))


# ───────────────────────────── Worker ─────────────────────────────

class AgentWorker:
    """每个 agent 一个 worker。订阅 agent.handoff(to=自己)→ 跑 step → 发后续事件。

    单元职责很窄:
      - 收到 handoff(targeted at me)
      - 调 _run_step 跑 LLM
      - 解析 output_json.handoff → emit "agent.handoff" 或终止
      - 失败 → emit "agent.failed"
      - output_json.needs_help → emit "agent.needs_help"
      - output_json.needs_retry → emit "agent.needs_retry"
    """

    def __init__(
        self,
        *,
        agent_id: str,
        step_key: str,
        state: HarnessState,
        run_step_fn: Callable[..., Awaitable[None]],
        gate_review_fn: Callable[..., Awaitable[None]] | None,
    ) -> None:
        self.agent_id = agent_id
        self.step_key = step_key
        self.state = state
        self._run_step = run_step_fn
        self._gate_review = gate_review_fn
        # P2 字段占位(spec 002-worker-subscription, T011)。
        # is_v2=True 且 agent 有 interests 时,Phase 4 T025 的 start_v2_consumer()
        # 会构造 inbox = asyncio.Queue(maxsize=V2_INBOX_MAX) 并起 _consume_loop task。
        # v1 路径下永远保持 None,零开销(FR-003)。
        self.inbox: asyncio.Queue["V2EventBase"] | None = None
        self._consume_task: asyncio.Task | None = None

    async def run(self) -> None:
        """被 Coordinator dispatch — 跑一次 step。"""
        st = self.state
        s = st.by_key.get(self.step_key)
        if s is None:
            log.warning("worker %s: step %s not found", self.agent_id, self.step_key)
            await st.emit("agent.failed", self.agent_id, self.step_key,
                          {"error": f"step {self.step_key} not in by_key"})
            return

        st.visited[self.step_key] = st.visited.get(self.step_key, 0) + 1
        st.chain.append(self.step_key)
        await st.emit("agent.start", self.agent_id, self.step_key,
                      {"visit_no": st.visited[self.step_key]})

        try:
            await self._run_step(s, st.run, st.prev)
        except Exception as e:  # noqa: BLE001
            log.exception("worker %s · _run_step crashed: %s", self.agent_id, e)
            await st.emit("agent.failed", self.agent_id, self.step_key,
                          {"error": str(e)[:300]})
            return

        # 即时质量门(reviewer 介入)
        if s.status == "success" and self._gate_review is not None:
            try:
                await self._gate_review(s, st.run, st.prev)
            except Exception as e:  # noqa: BLE001
                log.warning("gate_review crashed on %s: %s", self.step_key, e)

        st.prev[self.step_key] = s

        if s.status != "success":
            await st.emit("agent.failed", self.agent_id, self.step_key,
                          {"error": (s.error or "unknown")[:300]})
            return

        out = s.output_json or {}

        # 信号事件:needs_help / needs_retry(Coordinator 自己消费)
        nh = out.get("needs_help")
        if isinstance(nh, dict):
            await st.emit("agent.needs_help", self.agent_id, self.step_key, nh)

        nr = out.get("needs_retry")
        if isinstance(nr, dict):
            await st.emit("agent.needs_retry", self.agent_id, self.step_key, nr)

        await st.emit("agent.done", self.agent_id, self.step_key,
                      {"tokens": getattr(s, "total_tokens", 0)})

        # handoff
        handoff = out.get("handoff") or {}
        if not isinstance(handoff, dict):
            handoff = {}
        await st.emit("agent.handoff", self.agent_id, self.step_key, {
            "to": (handoff.get("to") or "").strip(),
            "reason": (handoff.get("reason") or "").strip(),
            "payload_hint": (handoff.get("payload_hint") or "").strip(),
        })


# ───────────────────────────── Coordinator ─────────────────────────────

class Coordinator:
    """运行时调度器。听总线,按规则路由。**这不是 LLM agent**,是规则引擎。"""

    def __init__(
        self,
        *,
        state: HarnessState,
        workers: dict[str, AgentWorker],            # agent_id → worker
        agent_to_step: dict[str, str],
        step_to_agent: dict[str, str],
        default_next_step: dict[str, str],
        agent_display: dict[str, str] | None = None,  # agent_id → 中文显示名(用户可见文案)
        max_revisits: int = 2,
        max_hops: int = 16,
        chat_publisher: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.state = state
        self.workers = workers
        self.agent_to_step = agent_to_step
        self.step_to_agent = step_to_agent
        self.default_next_step = default_next_step
        self.agent_display = agent_display or {}
        self.max_revisits = max_revisits
        self.max_hops = max_hops
        self._chat = chat_publisher  # 用于在 chat 里出 "→ 转交给 X" 气泡

        state.bus.on("agent.handoff", self.on_handoff)
        state.bus.on("agent.failed", self.on_failed)
        state.bus.on("agent.needs_help", self.on_needs_help)
        state.bus.on("agent.needs_retry", self.on_needs_retry)

    def _disp(self, agent_id: str) -> str:
        """agent_id → 中文显示名;缺映射时 fallback 到通用业务化称呼,**绝不暴露 raw id**。"""
        return self.agent_display.get(agent_id) or "同事"

    # ── 终止 ──
    def _end(self, reason: str) -> None:
        if self.state.done and not self.state.done.done():
            self.state.done.set_result(reason)

    # ── 路由解析 ──
    def _resolve_target(self, to: str) -> str | None:
        if not to:
            return None
        if to in ("DONE", "coordinator"):
            return "DONE"
        if to in self.agent_to_step:
            return self.agent_to_step[to]
        if to in self.step_to_agent:  # 直接传 step_key
            return to
        return None

    # ── 事件处理 ──
    async def _announce(self, text: str) -> None:
        """coordinator 在群里说话(导演引导消息,不是控制指令)。"""
        if self._chat is not None:
            try:
                await self._chat("coordinator", text, "supervise")
            except Exception:  # noqa: BLE001
                pass

    async def on_handoff(self, event: AgentEvent) -> None:
        if len(self.state.chain) >= self.max_hops:
            await self._announce(f"⚙ 已转交 {self.max_hops} 次,我先收尾防止打转。")
            await self.state.emit("chain.revisit_limit", "coordinator", None,
                                  {"chain": self.state.chain})
            self._end("max_hops")
            return

        to_raw = (event.payload or {}).get("to") or ""
        from_step = event.step_key or ""
        from_agent_id = self.step_to_agent.get(from_step, "") or ""
        from_agent_disp = self._disp(from_agent_id) if from_agent_id else "上一位"
        target = self._resolve_target(to_raw)

        # 导演引导 1:agent 没有有效 handoff → 推荐默认下一棒(劝告语气,**不暴露 to_raw**)
        if target is None and from_step:
            target = self.default_next_step.get(from_step, "DONE")
            recommended = self._disp(self.step_to_agent.get(target, "")) if target != "DONE" else None
            await self._announce(
                f"💡 {from_agent_disp} 没明说接给谁,"
                + (f"我看任务到这一步比较顺,建议下一棒给 {recommended}。"
                   if recommended else "我先收尾,把已有的部分先呈上。")
            )
        elif target is None:
            target = "DONE"

        if target == "DONE":
            await self.state.emit("task.end", "coordinator", None, {"chain": self.state.chain})
            self._end("done")
            return

        # 死循环防护 — 引导式(**用显示名,不暴露 agent_id**)
        if self.state.visited.get(target, 0) >= self.max_revisits:
            target2 = self.default_next_step.get(from_step, "DONE")
            stuck_disp = self._disp(self.step_to_agent.get(target, ""))
            next_disp = self._disp(self.step_to_agent.get(target2, "")) if target2 != "DONE" else None
            await self._announce(
                f"⚠ {stuck_disp} 已经跑过 {self.max_revisits} 次,这条路打转了。"
                + (f"换 {next_disp} 试试。" if next_disp else "我先收尾。")
            )
            await self.state.emit("chain.revisit_limit", "coordinator", target,
                                  {"visited": self.state.visited.get(target)})
            if target2 == "DONE" or self.state.visited.get(target2, 0) >= self.max_revisits:
                self._end("revisit_limit")
                return
            target = target2

        # 必经步骤保护:agent 不能跳过 default_next_step 链上的关键步骤
        if from_step and target != "DONE":
            expected = self.default_next_step.get(from_step)
            if expected and expected != target and self.state.visited.get(expected, 0) == 0:
                skipped_disp = self._disp(self.step_to_agent.get(expected, ""))
                target_disp = self._disp(self.step_to_agent.get(target, ""))
                await self._announce(
                    f"💡 {from_agent_disp} 想跳到 {target_disp},但 {skipped_disp} 还没跑过,不能跳。"
                    f"按流程先给 {skipped_disp}。"
                )
                target = expected

        # 转交气泡 — 由"上一位"发声(用显示名,不暴露 agent_id)
        if self._chat is not None:
            from_agent = self.step_to_agent.get(from_step or "", "coordinator")
            to_agent_id = self.step_to_agent.get(target, target)
            to_disp = self._disp(to_agent_id)
            reason = (event.payload or {}).get("reason", "")
            payload_hint = (event.payload or {}).get("payload_hint", "")
            bubble = f"→ 接力 {to_disp}"
            if reason:
                bubble += f" · {reason[:60]}"
            if payload_hint:
                bubble += f" · 看 {payload_hint[:60]}"
            try:
                await self._chat(from_agent, bubble, "handoff")
            except Exception:  # noqa: BLE001
                pass

        # dispatch
        next_agent = self.step_to_agent.get(target, "")
        worker = self.workers.get(next_agent)
        if worker is None:
            await self._announce(f"⚠ 找不到 {next_agent or target} 这个 agent,我先收尾。")
            self._end("no_worker")
            return

        asyncio.create_task(worker.run())

    async def on_failed(self, event: AgentEvent) -> None:
        from_step = event.step_key or ""
        from_agent = event.agent_id
        from_disp = self._disp(from_agent)
        next_step = self.default_next_step.get(from_step, "DONE")
        next_agent_id = self.step_to_agent.get(next_step, "") if next_step != "DONE" else ""
        recommended_disp = self._disp(next_agent_id) if next_agent_id else None
        # 导演在群里发声:谁卡住了(**不暴露原始错误**,err 已落到 events.jsonl 留 trace)
        await self._announce(
            f"⚠ {from_disp} 这边偶发卡顿。"
            + (f"我让 {recommended_disp} 接着往下走,看能不能用现有产物。"
               if recommended_disp else "我先收尾,把已有的部分先呈上。")
        )
        # 仍然 emit handoff 让流水继续(否则卡死),内部 reason 用 agent_id 留 trace 用
        await self.state.emit("agent.handoff", "coordinator", from_step,
                              {"to": self.step_to_agent.get(next_step, "DONE"),
                               "reason": f"{from_agent} fail,导演兜底",
                               "payload_hint": ""})

    async def on_needs_help(self, event: AgentEvent) -> None:
        # agent 声明缺数据 → 导演在群里 @ 上游,并真的把活送回上游让 ta 补
        from_agent = event.agent_id
        from_step = event.step_key or ""
        who = (event.payload or {}).get("from", "").strip()
        what = (event.payload or {}).get("what", "未说明") or "未说明"
        # 把 "from" 名字翻成 agent_id
        target_step: str | None = None
        if who in self.agent_to_step:
            target_step = self.agent_to_step[who]
        elif who in self.step_to_agent:
            target_step = who

        if target_step and self.state.visited.get(target_step, 0) < self.max_revisits:
            target_disp = self.step_to_agent.get(target_step, target_step)
            await self._announce(
                f"📣 {from_agent} 求助:需要 {target_disp} 补「{what[:80]}」。我让 {target_disp} 补一手。"
            )
            await self.state.emit("agent.handoff", "coordinator", from_step,
                                  {"to": target_disp,
                                   "reason": f"为 {from_agent} 补数据:{what[:60]}",
                                   "payload_hint": what[:80]})
        else:
            # 没有具体上游可叫,只在群里通报
            await self._chat(from_agent, f"⚠ 需要帮助 · {who or '上游'}: {what[:120]}", "result") if self._chat else None
            await self._announce(
                f"📣 {from_agent} 提了一个求助({what[:60]}),但没指明合适的上游或上游已用满次数。"
                f"我先让流程继续,稍后人工补。"
            )

    async def on_needs_retry(self, event: AgentEvent) -> None:
        # needs_retry 由 _run_step 内部已经处理一次(它有自己的 _retried flag)
        hint = (event.payload or {}).get("hint", "")[:120]
        if hint:
            await self._announce(f"🔁 {event.agent_id} 自己声明需要重做(原因:{hint[:60]})。")


# ───────────────────────────── 顶层 run ─────────────────────────────

async def run_harness(
    *,
    run: Any,
    prev: dict[str, Any],
    by_key: dict[str, Any],
    steps_meta: list[tuple[str, str, str, str | None]],   # (step_key, agent_id, label, depends)
    default_next_step: dict[str, str],
    run_step_fn: Callable[..., Awaitable[None]],
    gate_review_fn: Callable[..., Awaitable[None]] | None,
    chat_publisher: Callable[[str, str, str], Awaitable[None]] | None,
    events_jsonl_path: Path | None,
    agent_display: dict[str, str] | None = None,  # agent_id → 显示名(pipeline.AGENT_DISPLAY)
    timeout_sec: int = 3600 * 2,
    is_v2: bool = False,  # v2 群聊 harness flag(P1); 默认 False 保 v1 路径行为不变
) -> dict:
    """跑一次完整 task 的 harness 主入口。返回最终 stats / chain / events 数量。

    调用方:
      - pipeline.execute() 在 coordinator_briefing 之后调本函数
      - 起点:第一个 step 的 agent
    """
    bus = EventBus()
    state = HarnessState(
        run=run, prev=prev, by_key=by_key, bus=bus,
        events_jsonl_path=events_jsonl_path,
        is_v2=is_v2,  # v2 群聊 harness flag(P1)
    )
    state.done = asyncio.get_running_loop().create_future()

    agent_to_step = {agent: step for step, agent, *_ in steps_meta}
    step_to_agent = {step: agent for step, agent, *_ in steps_meta}

    workers: dict[str, AgentWorker] = {}
    for step_key, agent_id, _label, _depends in steps_meta:
        workers[agent_id] = AgentWorker(
            agent_id=agent_id, step_key=step_key, state=state,
            run_step_fn=run_step_fn, gate_review_fn=gate_review_fn,
        )

    Coordinator(
        state=state, workers=workers,
        agent_to_step=agent_to_step, step_to_agent=step_to_agent,
        default_next_step=default_next_step,
        agent_display=agent_display,
        chat_publisher=chat_publisher,
    )

    await state.emit("task.start", "coordinator", None,
                     {"steps": [k for k, *_ in steps_meta]})

    # 起点 — 发起第一个 handoff,目标 = steps_meta[0]
    first_agent = steps_meta[0][1]
    await state.emit("agent.handoff", "coordinator", None, {
        "to": first_agent,
        "reason": "起点,从资料员开始",
        "payload_hint": "原始材料 + supplement + agent_briefs",
    })

    try:
        result_reason = await asyncio.wait_for(state.done, timeout=timeout_sec)
    except asyncio.TimeoutError:
        log.warning("harness · overall timeout after %ds", timeout_sec)
        result_reason = "overall_timeout"

    return {
        "reason": result_reason,
        "chain": state.chain,
        "visited": state.visited,
        "events_count": len(state.events_log),
        # P1 v2(spec 001-v2-chat-protocol-state): 暴露 state 给 pipeline.execute()
        # 做收尾期 v2 emit / artifact 版本化(参见 _emit_v2_finalization)。
        # 仅内部使用,不要在 admin/API response 中外露。
        "_state": state,
    }
