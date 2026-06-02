"""P8 US2 · rolling summary 折叠(spec 008-ops-safety-net)

T013 · FR-013/014/015/016 + SC-001/004

- _transcript_block:off(默认)走 recent[-k:];on 未超阈值原样;on 超阈值折叠为「尾部 K + 1 行摘要」;
  observer 缺失降级空串;T=1/K≥len 边界不崩。
"""

from __future__ import annotations

from app.orchestrator import subscription as sub
from app.orchestrator import pipeline


class _Obs:
    def __init__(self, recent, artifacts=None):
        self._recent = recent
        self._artifact_log = artifacts or []


class _State:
    def __init__(self, observer):
        self.observer = observer


def _recent(n):
    return [f"发言{i}" for i in range(n)]


# ────────────────────────── off(默认)零回归 ──────────────────────────

def test_rolling_off_keeps_tail_k(monkeypatch):
    """off(默认)→ 走 recent_all[-k:],与 P5 一致(SC-001)。"""
    monkeypatch.setattr(sub, "V2_ROLLING_SUMMARY", "off")
    monkeypatch.setattr(sub, "V2_SUMMARY_THRESHOLD", 5)
    out = pipeline._transcript_block(_State(_Obs(_recent(20))), k=5)
    assert "发言19" in out and "发言15" in out
    assert "发言14" not in out
    assert "已折叠" not in out          # off 不折叠


# ────────────────────────── on 未超阈值 ──────────────────────────

def test_rolling_on_below_threshold_no_fold(monkeypatch):
    """on 但条数未超阈值 → 全保留不折叠(FR-014)。"""
    monkeypatch.setattr(sub, "V2_ROLLING_SUMMARY", "on")
    monkeypatch.setattr(sub, "V2_SUMMARY_THRESHOLD", 10)
    out = pipeline._transcript_block(_State(_Obs(_recent(6))), k=8)
    assert "已折叠" not in out
    assert "发言0" in out and "发言5" in out


# ────────────────────────── on 超阈值折叠 ──────────────────────────

def test_rolling_on_over_threshold_folds(monkeypatch):
    """on 且超阈值 → 尾部 K 行逐条 + 恰 1 行折叠摘要(FR-013/SC-004)。"""
    monkeypatch.setattr(sub, "V2_ROLLING_SUMMARY", "on")
    monkeypatch.setattr(sub, "V2_SUMMARY_THRESHOLD", 5)
    out = pipeline._transcript_block(_State(_Obs(_recent(12))), k=3)
    # 尾部 3 条逐条保留
    assert "发言11" in out and "发言10" in out and "发言9" in out
    # 折叠行恰 1 行,含「前 N 条…已折叠」
    fold_lines = [ln for ln in out.splitlines() if "已折叠" in ln]
    assert len(fold_lines) == 1
    assert "前 9 条" in fold_lines[0]     # 12 - 3 = 9 条被折叠
    # 注入发言条数(逐条 "- 发言" 行)≤ k(=3);总上界 k+1(含折叠行)
    bullet_lines = [ln for ln in out.splitlines() if ln.startswith("- 发言")]
    assert len(bullet_lines) == 3


def test_rolling_fold_summary_has_no_tech_id(monkeypatch):
    """折叠摘要只含发言文本,不泄漏技术编号(FR-015/SC-003 同口径)。"""
    monkeypatch.setattr(sub, "V2_ROLLING_SUMMARY", "on")
    monkeypatch.setattr(sub, "V2_SUMMARY_THRESHOLD", 3)
    recent = ["资料员:素材就绪", "分析师:三条重点", "设计师:配色定了", "视频:脚本好了", "讲稿:成稿"]
    out = pipeline._transcript_block(_State(_Obs(recent)), k=2)
    fold = [ln for ln in out.splitlines() if "已折叠" in ln][0]
    for banned in ("tsk_", "agent_id", "task_id", "point-extractor", "html-designer"):
        assert banned not in fold


# ────────────────────────── 降级 / 边界 ──────────────────────────

def test_rolling_observer_none_returns_empty(monkeypatch):
    """observer None → 空串,不报错(FR-016)。"""
    monkeypatch.setattr(sub, "V2_ROLLING_SUMMARY", "on")
    assert pipeline._transcript_block(_State(None), k=8) == ""


def test_rolling_threshold_one_edge_no_crash(monkeypatch):
    """T=1 边界:超阈值但 K≥len 时 folded 空,渲染「前 0 条」不抛错(edge)。"""
    monkeypatch.setattr(sub, "V2_ROLLING_SUMMARY", "on")
    monkeypatch.setattr(sub, "V2_SUMMARY_THRESHOLD", 1)
    out = pipeline._transcript_block(_State(_Obs(_recent(3))), k=8)  # 3>1 进折叠;K=8≥3
    assert "已折叠" in out             # 折叠分支触发不崩
    assert "前 0 条" in out            # folded == []
