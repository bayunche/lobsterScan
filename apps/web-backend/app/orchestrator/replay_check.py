"""events.jsonl 最小回放校验工具（FR-015）

用法：
    uv run --directory apps/web-backend python -m app.orchestrator.replay_check \
        data/outputs/<task_id>/events.jsonl

输出（人可读）：每行一类计数 + schema invalid 数。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from .events_v2 import (
    AgentSilent,
    AgentSpeak,
    ArtifactUpdate,
    CoordinatorIntervene,
    ReviewerVerdict,
)

_V2_MODELS = {
    "agent.speak": AgentSpeak,
    "agent.silent": AgentSilent,
    "coordinator.intervene": CoordinatorIntervene,
    "reviewer.verdict": ReviewerVerdict,
    "artifact.update": ArtifactUpdate,
}


def replay_check(events_jsonl: Path) -> dict:
    """解析一份 events.jsonl，返回统计结果。"""
    if not events_jsonl.is_file():
        raise FileNotFoundError(f"{events_jsonl} 不存在")

    v1_count = 0
    v2_counts: Counter[str] = Counter()
    invalid = 0
    unknown = 0
    total = 0

    with events_jsonl.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue

            msg_type = row.get("msg_type")
            kind = row.get("kind")

            if msg_type and msg_type in _V2_MODELS:
                model = _V2_MODELS[msg_type]
                try:
                    model.model_validate(row)
                    v2_counts[msg_type] += 1
                except ValidationError:
                    invalid += 1
            elif kind:
                v1_count += 1
            else:
                unknown += 1

    return {
        "path": str(events_jsonl),
        "total_lines": total,
        "v1_events": v1_count,
        "v2_events": sum(v2_counts.values()),
        "v2_per_kind": dict(v2_counts),
        "schema_invalid": invalid,
        "unknown_rows": unknown,
    }


def _print_report(report: dict) -> None:
    print(f"events.jsonl summary · {report['path']}")
    print(f"  total lines:       {report['total_lines']}")
    print(f"  v1 events:         {report['v1_events']}")
    print(f"  v2 events:         {report['v2_events']}")
    for k in ("agent.speak", "agent.silent", "coordinator.intervene",
              "reviewer.verdict", "artifact.update"):
        print(f"    {k:<22}: {report['v2_per_kind'].get(k, 0)}")
    print(f"  schema invalid:    {report['schema_invalid']}")
    if report["unknown_rows"]:
        print(f"  unknown rows:      {report['unknown_rows']}")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m app.orchestrator.replay_check <events.jsonl>", file=sys.stderr)
        return 2
    report = replay_check(Path(argv[0]))
    _print_report(report)
    return 0 if report["schema_invalid"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
