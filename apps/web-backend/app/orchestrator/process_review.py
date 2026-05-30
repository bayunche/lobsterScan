"""v2 流程逻辑轨 —— Reviewer 的流程逻辑校验（P4, spec 004-reviewer-dual-track）

模块定位(详 specs/004-reviewer-dual-track/research.md §5):
- Reviewer 双轨之"流程逻辑轨"：收尾时对整个任务做确定性规则校验,**不调 LLM**。
- 3 项纯规则:① 版本一致 ② 依赖图 ③ 参与度。
- observer 在 quiescence 时调 ProcessReviewer.check → emit ReviewerVerdict(process_logic)。

宪章合规:纯规则(原则 IV 流程逻辑验证本职);findings 业务化中文;异常降级(FR-019)。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from .events_v2 import Finding, ReviewerVerdict

log = logging.getLogger("orchestrator.process_review")

__all__ = ["ProcessReviewResult", "ProcessReviewer", "_process_verdict"]

# 4 核心 artifact 的依赖序(产出顺序应符合)
CORE_ORDER: tuple[str, ...] = ("MaterialPool", "ReportCore", "Outline", "Script")
# artifact_id → producer agent_id(修复定位 + finding 文案)
ARTIFACT_PRODUCER: dict[str, str] = {
    "MaterialPool": "material",
    "ReportCore": "point-extractor",
    "Outline": "structure",
    "Script": "copywriter",
}


@dataclass(frozen=True)
class ProcessReviewResult:
    passed: bool
    findings: tuple[Finding, ...] = ()
    fix_targets: tuple[str, ...] = ()   # 违例 artifact 的 producer agent_id


class ProcessReviewer:
    """流程逻辑轨:3 项确定性规则。输入 artifact_log(observer 经 bus 收集) + task_id。"""

    def check(self, task_id: str, artifact_log: list[dict]) -> ProcessReviewResult:
        """artifact_log: [{id, version, producer}, ...](按 emit 时序)。"""
        findings: list[Finding] = []
        fix: set[str] = set()
        try:
            self._check_version_consistency(artifact_log, findings, fix)
        except Exception as e:  # noqa: BLE001 — FR-019
            log.warning("process review · version rule crashed: %s", e)
        try:
            self._check_dependency_order(artifact_log, findings, fix)
        except Exception as e:  # noqa: BLE001
            log.warning("process review · dependency rule crashed: %s", e)
        try:
            self._check_participation(task_id, findings, fix)
        except Exception as e:  # noqa: BLE001
            log.warning("process review · participation rule crashed: %s", e)
        return ProcessReviewResult(
            passed=not findings, findings=tuple(findings), fix_targets=tuple(fix),
        )

    # ── 规则①:版本一致(每个 artifact 的 version 序列从 1 递增无跳号) ──
    def _check_version_consistency(self, log_: list[dict], findings, fix) -> None:
        versions: dict[str, list[int]] = defaultdict(list)
        for e in log_:
            aid = e.get("id")
            v = e.get("version")
            if aid and isinstance(v, int):
                versions[aid].append(v)
        for aid, vs in versions.items():
            expected = list(range(1, max(vs) + 1))
            if sorted(set(vs)) != expected:
                findings.append(Finding(severity="med", what=f"{aid} 的版本号不连续"))
                fix.add(ARTIFACT_PRODUCER.get(aid, aid))

    # ── 规则②:依赖图(每个 artifact 首次出现顺序符合 CORE_ORDER) ──
    def _check_dependency_order(self, log_: list[dict], findings, fix) -> None:
        first_seen: dict[str, int] = {}
        for idx, e in enumerate(log_):
            aid = e.get("id")
            if aid and aid not in first_seen:
                first_seen[aid] = idx
        present = [a for a in CORE_ORDER if a in first_seen]
        for i in range(len(present) - 1):
            a, b = present[i], present[i + 1]
            if first_seen[a] > first_seen[b]:
                findings.append(Finding(severity="med", what=f"{b} 早于其依赖 {a} 出现"))
                fix.add(ARTIFACT_PRODUCER.get(b, b))

    # ── 规则③:参与度(每个核心 artifact 都产出) ──
    def _check_participation(self, task_id: str, findings, fix) -> None:
        from .artifacts_v2 import next_version
        for aid in CORE_ORDER:
            try:
                latest = next_version(task_id, aid) - 1
            except Exception:  # noqa: BLE001
                latest = 0
            if latest < 1:
                findings.append(Finding(severity="high", what=f"{aid} 始终未产出"))
                fix.add(ARTIFACT_PRODUCER.get(aid, aid))


def _process_verdict(result: ProcessReviewResult, task_id: str) -> ReviewerVerdict:
    """ProcessReviewResult → ReviewerVerdict(process_logic)。suggestions 凑 ≥3。"""
    from .harness import _pad_suggestions
    return ReviewerVerdict(
        task_id=task_id,
        verdict="pass" if result.passed else "fail",
        dimension="process_logic",
        findings=list(result.findings),
        suggested_fix_agent=(result.fix_targets[0] if result.fix_targets else None),
        suggestions=_pad_suggestions([f.what for f in result.findings]),
    )
