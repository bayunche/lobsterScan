"""4 核心 artifact 版本化写入（v2 路径专用）

详 specs/001-v2-chat-protocol-state/data-model.md & research.md §4。

文件命名：`<name>_v<N>.<ext>` 带 __meta__；同时复写 `<name>.<ext>` latest 副本（**不带 __meta__**，
让 v1 reader 完全不感知）。

JSON artifact（MaterialPool / ReportCore / Outline）的 __meta__ 是顶层 `__meta__` 键；
Markdown artifact（Script）的 __meta__ 是 `<!--__meta__: {...}-->` HTML 注释头。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .events_v2 import CORE_ARTIFACTS, ArtifactUpdate
from .ids import new_message_id

if TYPE_CHECKING:
    from .harness import HarnessState

__all__ = [
    "ARTIFACT_FILENAME",
    "ARTIFACT_EXT",
    "write_versioned",
    "next_version",
    "read_versioned",
]


# artifact_id → 文件名 stem
ARTIFACT_FILENAME: dict[str, str] = {
    "MaterialPool": "material_pool",
    "ReportCore":   "report_core",
    "Outline":      "outline",
    "Script":       "script",
}

# artifact_id → 文件扩展名
ARTIFACT_EXT: dict[str, str] = {
    "MaterialPool": "json",
    "ReportCore":   "json",
    "Outline":      "json",
    "Script":       "md",
}

_VERSION_FILE_RE = re.compile(r"^(?P<stem>[^_]+(?:_[^_v]+)*)_v(?P<v>\d+)\.(?P<ext>json|md)$")
_MD_META_RE = re.compile(r"^<!--__meta__:\s*(?P<json>.+?)\s*-->\s*\n?", re.DOTALL)


def _task_output_dir(task_id: str) -> Path:
    """data/outputs/<task_id>/ 的解析（与 pipeline.py 现有路径约定一致）。"""
    return Path("data/outputs") / task_id


def _list_existing_versions(task_dir: Path, artifact_id: str) -> list[int]:
    """扫描目录，找出某 artifact 现存所有版本号。"""
    stem = ARTIFACT_FILENAME[artifact_id]
    ext = ARTIFACT_EXT[artifact_id]
    pattern = re.compile(rf"^{re.escape(stem)}_v(\d+)\.{re.escape(ext)}$")
    versions: list[int] = []
    if not task_dir.is_dir():
        return versions
    for p in task_dir.iterdir():
        m = pattern.match(p.name)
        if m:
            versions.append(int(m.group(1)))
    return sorted(versions)


def next_version(task_id: str, artifact_id: str) -> int:
    """该 artifact 的下一个版本号（从 1 起）。"""
    if artifact_id not in CORE_ARTIFACTS:
        raise ValueError(f"artifact_id {artifact_id!r} 不在 4 核心 artifact 列表内")
    existing = _list_existing_versions(_task_output_dir(task_id), artifact_id)
    return (max(existing) + 1) if existing else 1


def _make_meta(
    *,
    artifact_id: str,
    version: int,
    producer: str,
    base_version: int | None,
    delta_summary: str,
    message_id: str,
) -> dict[str, Any]:
    return {
        "id": artifact_id,
        "version": version,
        "producer": producer,
        "base_version": base_version,
        "delta_summary": delta_summary,
        "created_at": time.time(),
        "message_id": message_id,
    }


def _write_json_versioned(task_dir: Path, stem: str, version: int,
                          payload: dict[str, Any], meta: dict[str, Any]) -> Path:
    """JSON artifact：版本文件含 __meta__ 顶层键；latest 副本不含。"""
    versioned = task_dir / f"{stem}_v{version}.json"
    latest = task_dir / f"{stem}.json"

    versioned_payload = {"__meta__": meta, **payload}
    versioned.write_text(
        json.dumps(versioned_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    latest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return versioned


def _write_md_versioned(task_dir: Path, stem: str, version: int,
                        payload: str, meta: dict[str, Any]) -> Path:
    """Markdown artifact：版本文件顶部嵌入 <!--__meta__: ...--> 注释；latest 副本不嵌入。"""
    versioned = task_dir / f"{stem}_v{version}.md"
    latest = task_dir / f"{stem}.md"

    meta_line = f"<!--__meta__: {json.dumps(meta, ensure_ascii=False)}-->\n\n"
    versioned.write_text(meta_line + payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return versioned


async def write_versioned(
    *,
    state: "HarnessState",
    artifact_id: str,
    payload: dict[str, Any] | str,
    producer: str,
    base_version: int | None = None,
    delta_summary: str = "",
) -> int:
    """v2 路径下：写带版本号的 artifact + 同步覆写 latest 副本 + emit artifact.update 事件。

    参数：
      - state: HarnessState 实例（用于 emit_v2 + 取 task_id）
      - artifact_id: 4 核心之一（MaterialPool / ReportCore / Outline / Script）
      - payload: JSON artifact 传 dict；Script 传 str
      - producer: agent_id（写入者）
      - base_version: 基于哪个版本（首次为 None）
      - delta_summary: ≤60 字差异概述

    返回：new_version（写完的新版本号）

    抛错：
      - artifact_id 不在 4 核心 → ValueError
      - payload 类型与 artifact_id 不匹配（如 Script 给 dict）→ ValueError
    """
    if artifact_id not in CORE_ARTIFACTS:
        raise ValueError(f"artifact_id {artifact_id!r} 不在 4 核心 artifact 列表内")

    ext = ARTIFACT_EXT[artifact_id]
    stem = ARTIFACT_FILENAME[artifact_id]
    task_id = state.run.task_id
    task_dir = _task_output_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    new_v = next_version(task_id, artifact_id)
    message_id = new_message_id()
    meta = _make_meta(
        artifact_id=artifact_id,
        version=new_v,
        producer=producer,
        base_version=base_version,
        delta_summary=delta_summary,
        message_id=message_id,
    )

    if ext == "json":
        if not isinstance(payload, dict):
            raise ValueError(f"{artifact_id} payload 必须是 dict，收到 {type(payload).__name__}")
        versioned = _write_json_versioned(task_dir, stem, new_v, payload, meta)
    elif ext == "md":
        if not isinstance(payload, str):
            raise ValueError(f"{artifact_id} payload 必须是 str，收到 {type(payload).__name__}")
        versioned = _write_md_versioned(task_dir, stem, new_v, payload, meta)
    else:
        raise ValueError(f"unsupported ext {ext}")

    # 路径 ref：相对仓根（match contracts/artifact.update.schema.json 的 pattern）
    ref = f"data/outputs/{task_id}/{versioned.name}"
    await state.emit_v2(ArtifactUpdate(
        message_id=message_id,
        task_id=task_id,
        id=artifact_id,
        version=new_v,
        base_version=base_version,
        producer=producer,
        delta_summary=delta_summary,
        ref=ref,
    ))
    return new_v


def read_versioned(task_id: str, artifact_id: str, version: int) -> tuple[dict[str, Any] | str, dict[str, Any]]:
    """读取某 artifact 的某个版本，返回 (payload, meta)。

    JSON: payload 是 dict（已去除 __meta__）；meta 是 __meta__ dict
    MD:   payload 是 str（已去除 meta line）；meta 是从注释解析的 dict
    """
    if artifact_id not in CORE_ARTIFACTS:
        raise ValueError(f"artifact_id {artifact_id!r} 不在 4 核心 artifact 列表内")
    stem = ARTIFACT_FILENAME[artifact_id]
    ext = ARTIFACT_EXT[artifact_id]
    path = _task_output_dir(task_id) / f"{stem}_v{version}.{ext}"
    if not path.is_file():
        raise FileNotFoundError(f"{path} 不存在")

    if ext == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.pop("__meta__", {})
        return data, meta
    else:  # md
        text = path.read_text(encoding="utf-8")
        m = _MD_META_RE.match(text)
        if m:
            meta = json.loads(m.group("json"))
            payload = text[m.end():]
        else:
            meta = {}
            payload = text
        return payload, meta
