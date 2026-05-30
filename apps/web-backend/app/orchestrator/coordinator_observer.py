"""v2 Coordinator observer + gatekeeper watchdog（P3, spec 003-coordinator-transform）

模块定位（详 specs/003-coordinator-transform/research.md §4）:
- P3 阶段 v2 路径把"驱动真实 step"从 Coordinator chain 转移到 subscription;
  Coordinator 退为 observer + gatekeeper,本模块承载其退场后的三件职责:
  · stagnation（确定性规则,liveness 命脉）— quiescence 检测 + 激活就绪静默 worker
  · drift（可注入 LLM,minimal context）— 跑题判断 + 复诵原始目标
  · gatekeeper（确定性规则）— 收尾校验 4 核心 artifact 完整性 → done/partial

设计要点:
- 一个 watchdog（asyncio 周期 task）统一承载三职责（避免重复 quiescence 检测）
- drift 走可注入 `DriftJudge` 抽象;默认 `NoDriftJudge`（mock-first,不调 LLM,不违宪）
- 全部 try/except 降级（FR-024）;v1 路径根本不构造本模块（零开销）

宪章合规（v1.1.0）:drift 的受限 LLM 例外已由宪章原则 IV 放宽;路由/兜底/收尾/stagnation
仍纯规则。详 `.specify/memory/constitution.md` 原则 IV「drift 判断的受限 LLM 例外」。

配置（env var override,详 research §4/data-model §配置常量）:
  OBSERVER_TICK_SEC    默认 0.5  · watchdog 轮询周期（秒）
  DRIFT_EVERY_N_TICK   默认 10   · 每几拍跑一次 drift 判断
  DRIFT_RECENT_K       默认 5    · drift 喂最近几条 speak
  STAGNATION_MAX_RETRY 默认 3    · stagnation 无解兜底上限 → partial 收尾
"""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .harness import AgentWorker, HarnessState

log = logging.getLogger("orchestrator.coordinator_observer")

__all__ = [
    "OBSERVER_TICK_SEC",
    "DRIFT_EVERY_N_TICK",
    "DRIFT_RECENT_K",
    "STAGNATION_MAX_RETRY",
    "DriftVerdict",
    "DriftJudge",
    "NoDriftJudge",
    "get_default_drift_judge",
    "set_default_drift_judge",
    "GateResult",
    "ArtifactGate",
    "CoordinatorObserver",
    "AGENT_PRODUCES",
]


# ────────────────────────────── 配置常量 ──────────────────────────────
# T003 · 默认值,可 env var 覆盖（研发/演示用）

OBSERVER_TICK_SEC: float = float(os.environ.get("OBSERVER_TICK_SEC", "0.5"))
DRIFT_EVERY_N_TICK: int = int(os.environ.get("DRIFT_EVERY_N_TICK", "10"))
DRIFT_RECENT_K: int = int(os.environ.get("DRIFT_RECENT_K", "5"))
STAGNATION_MAX_RETRY: int = int(os.environ.get("STAGNATION_MAX_RETRY", "3"))


# 各 agent 产出的核心 artifact（agent_id → artifact_id）。
# 与 pipeline._emit_v2_step_overlay 的 artifact_extract 对齐。
AGENT_PRODUCES: dict[str, str] = {
    "material": "MaterialPool",
    "point-extractor": "ReportCore",
    "structure": "Outline",
    "copywriter": "Script",
}
ARTIFACT_PRODUCER: dict[str, str] = {v: k for k, v in AGENT_PRODUCES.items()}


# ────────────────────────────── DriftJudge（T004） ──────────────────────────────


@dataclass(frozen=True)
class DriftVerdict:
    """drift 判断结果（FR-013/014）。"""
    drifted: bool
    restate_text: str = ""  # drifted=True 时复诵原始目标的文案（业务化中文）


class DriftJudge(ABC):
    """跑题判断抽象。可注入（测试注入 mock）。"""

    @abstractmethod
    async def judge(self, *, goal: str, recent_speaks: list[str]) -> DriftVerdict:
        """判断 recent_speaks 是否偏离 goal。纯只读,无副作用。"""
        raise NotImplementedError


class NoDriftJudge(DriftJudge):
    """默认实现（mock-first）:永远未跑题。不调 LLM,不违宪（FR-016/017）。

    真 LLM 判断（LLMDriftJudge）受 Phase 0 宪章 + Windows issue 双前置,本期不交付。
    """

    async def judge(self, *, goal: str, recent_speaks: list[str]) -> DriftVerdict:
        return DriftVerdict(drifted=False)


# 注入点（同 agent_backend.get/set_default_backend 模式）
_default_drift_judge: DriftJudge = NoDriftJudge()


def get_default_drift_judge() -> DriftJudge:
    return _default_drift_judge


def set_default_drift_judge(judge: DriftJudge) -> None:
    """测试注入 mock;生产默认 NoDriftJudge。"""
    global _default_drift_judge
    _default_drift_judge = judge


# ────────────────────────────── ArtifactGate（T005） ──────────────────────────────


@dataclass(frozen=True)
class GateResult:
    passed: bool
    missing: tuple[str, ...] = ()


class ArtifactGate:
    """4 核心 artifact 完整性校验（FR-018/019）。"""

    CORE: tuple[str, ...] = ("MaterialPool", "ReportCore", "Outline", "Script")

    def check(self, task_id: str) -> GateResult:
        """4 核心 artifact latest 版本都 ≥1 → passed;否则列 missing。

        复用 artifacts_v2.next_version（latest = next_version - 1）。
        降级（FR-024）:探测异常算缺失,不抛。
        """
        from .artifacts_v2 import next_version

        missing: list[str] = []
        for art in self.CORE:
            try:
                latest = next_version(task_id, art) - 1
            except Exception:  # noqa: BLE001 — FR-024
                latest = 0
            if latest < 1:
                missing.append(art)
        return GateResult(passed=not missing, missing=tuple(missing))


# ────────────────────────────── CoordinatorObserver（T006/T007/T031/T038/T045） ──────────────────────────────


@dataclass
class CoordinatorObserver:
    """v2 路径的 observer + gatekeeper watchdog。is_v2 时由 run_harness 构造启动。

    每拍（OBSERVER_TICK_SEC）:
      1. quiescence → gatekeeper（齐 done / 无解 partial）+ stagnation（激活就绪静默 worker）
      2. 每 DRIFT_EVERY_N_TICK 拍 → drift 判断（默认 NoDrift,不 intervene）
    """

    state: "HarnessState"
    workers: dict[str, "AgentWorker"]
    goal: str = ""
    gate: ArtifactGate = field(default_factory=ArtifactGate)
    drift_judge: DriftJudge | None = None  # None → 运行时读 get_default_drift_judge()
    _task: asyncio.Task | None = None
    _tick: int = 0
    _stagnation_retries: int = 0
    _recent: list[str] = field(default_factory=list)  # 最近 agent.speak.text（drift 用）

    # ── 生命周期 ──

    async def start(self) -> None:
        if self._task is None:
            # 订阅 agent.speak 收集 recent speaks（emit_v2 不进 events_log,故走 bus）
            self.state.bus.on("agent.speak", self._collect_speak)
            self._task = asyncio.create_task(self._loop())

    async def _collect_speak(self, event) -> None:
        try:
            text = (getattr(event, "payload", None) or {}).get("text", "")
            if text:
                self._recent.append(text)
                cap = DRIFT_RECENT_K * 4
                if len(self._recent) > cap:
                    self._recent = self._recent[-cap:]
        except Exception:  # noqa: BLE001 — FR-024
            pass

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(OBSERVER_TICK_SEC)
                self._tick += 1
                try:
                    if self._is_quiescent():
                        await self._on_quiescence()
                    if self._tick % DRIFT_EVERY_N_TICK == 0:
                        await self._check_drift()
                except Exception as e:  # noqa: BLE001 — FR-024 降级
                    log.warning("observer tick %d crashed: %s", self._tick, e)
        except asyncio.CancelledError:
            return

    # ── quiescence 检测（T032） ──

    def _is_quiescent(self) -> bool:
        """quiescence = bootstrapped + inflight==0 + inbox 全空 + 未完成（research §5）。"""
        st = self.state
        if not st.bootstrapped:
            return False
        if st.inflight_steps != 0:
            return False
        if st.done is None or st.done.done():
            return False
        for w in self.workers.values():
            if w.inbox is not None and not w.inbox.empty():
                return False
        return True

    # ── quiescence 处理:gatekeeper + stagnation（T031 + T038） ──

    async def _on_quiescence(self) -> None:
        result = self.gate.check(self._task_id())
        if result.passed:
            await self._emit_intervene("gate_pass", "全部产物都齐了,我来收尾。")
            self._set_done("done")
            return

        # 不齐:先 stagnation 激活"依赖就绪却静默"的 worker
        activated = await self._activate_ready_silent_workers()
        if activated:
            self._stagnation_retries = 0
            await self._emit_intervene("stagnation", "流程好像卡住了,我来推进一下。")
            return

        # 无可激活 → 累计无解,达上限转 partial 收尾
        self._stagnation_retries += 1
        if self._stagnation_retries >= STAGNATION_MAX_RETRY:
            miss = "、".join(
                self._display(ARTIFACT_PRODUCER.get(m, m)) for m in result.missing
            )
            await self._emit_intervene(
                "gate_reject", f"还差 {miss} 的部分,先按现有内容交付。"
            )
            self._set_done("partial")

    def _available_artifacts(self) -> set[str]:
        from .artifacts_v2 import next_version

        avail: set[str] = set()
        for art in self.gate.CORE:
            try:
                if next_version(self._task_id(), art) - 1 >= 1:
                    avail.add(art)
            except Exception:  # noqa: BLE001 — FR-024
                pass
        return avail

    def _ready_silent_workers(self) -> list["AgentWorker"]:
        """找 requires 全满足但本任务未产出自己 artifact 的 worker（FR-011）。"""
        from .subscription import WORKER_PROFILE

        available = self._available_artifacts()
        result: list["AgentWorker"] = []
        for agent_id, produced in AGENT_PRODUCES.items():
            if produced in available:
                continue  # 已产出,无需激活
            worker = self.workers.get(agent_id)
            if worker is None:
                continue
            profile = WORKER_PROFILE.get(agent_id)
            if profile is None:
                continue
            if all(r in available for r in profile.requires):
                result.append(worker)
        return result

    async def _activate_ready_silent_workers(self) -> bool:
        """激活就绪静默 worker;不指定 next-speaker（FR-011）。

        激活前同步 inflight_steps += 1（占位,防 watchdog 下一拍重复激活）;
        worker.force_run_v2 的 finally 统一 -1。
        """
        workers = self._ready_silent_workers()
        if not workers:
            return False
        for w in workers:
            self.state.inflight_steps += 1
            asyncio.create_task(w.force_run_v2())
        return True

    # ── drift（T045,依赖宪章 v1.1.0） ──

    async def _check_drift(self) -> None:
        """周期 drift 判断（FR-013/014/016/017）。默认 NoDrift → 不 intervene。"""
        try:
            recent = self._recent_speaks(DRIFT_RECENT_K)
            if not recent:
                return
            judge = self.drift_judge or get_default_drift_judge()
            verdict = await judge.judge(goal=self.goal, recent_speaks=recent)
            if verdict.drifted:
                text = verdict.restate_text or f"我们回到主题:{self.goal[:120]}"
                await self._emit_intervene("drift", text)
        except Exception as e:  # noqa: BLE001 — FR-017 降级
            log.warning("drift judge crashed: %s", e)

    # ── helpers（T007） ──

    def _task_id(self) -> str:
        return getattr(self.state.run, "task_id", "") or ""

    def _set_done(self, reason: str) -> None:
        if self.state.done is not None and not self.state.done.done():
            self.state.done.set_result(reason)

    async def _emit_intervene(self, kind: str, text: str) -> None:
        """emit CoordinatorIntervene（脱敏中文,FR-023）;降级（FR-024）。"""
        from .events_v2 import CoordinatorIntervene

        try:
            await self.state.emit_v2(CoordinatorIntervene(
                task_id=self._task_id(), kind=kind, text=text[:200], hint_agent=None,
            ))
        except Exception as e:  # noqa: BLE001 — FR-024
            log.warning("observer emit intervene(%s) crashed: %s", kind, e)

    def _recent_speaks(self, k: int) -> list[str]:
        """最近 k 条 agent.speak.text（由 _collect_speak 经 bus 收集）。"""
        return self._recent[-k:]

    def _display(self, agent_id: str) -> str:
        """agent_id → 中文显示名;缺映射 fallback「同事」,不暴露 raw id（FR-023）。"""
        try:
            from .pipeline import AGENT_DISPLAY
            return AGENT_DISPLAY.get(agent_id) or "同事"
        except Exception:  # noqa: BLE001
            return "同事"
