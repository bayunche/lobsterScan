"""管台 · 流水线监控

V0：业务后端未接入时使用 mock 任务推进，让 Gantt 真的动起来。
接入后改成从 web-backend 的 TaskRun 表读。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

SEED_FILE = settings.project_root / "data" / "pipelines.seed.json"

STEPS = [
    ("material_parsing",    "正在整理材料",         "material"),
    ("point_extraction",    "正在提炼工作重点",     "point-extractor"),
    ("structure_building",  "选大纲",               "structure"),
    ("upward_optimization", "正在优化向上汇报表达", "upward-opt"),
    ("copywriting",         "正在生成汇报稿",       "copywriter"),
    ("html_design",         "正在生成 HTML 页面",   "html-designer"),
    ("video_production",    "正在生成配音视频",     "video-producer"),
    ("review",              "正在检查汇报质量",     "reviewer"),
]

# ---- in-memory task store (mock + 业务后端上报混合) ----
_runs: dict[str, dict[str, Any]] = {}


def _build_seed() -> dict[str, Any]:
    base = datetime.utcnow() - timedelta(minutes=12)
    steps = []
    for i, (key, label, agent) in enumerate(STEPS):
        start = base + timedelta(seconds=i * 90)
        if i < 6:
            steps.append({
                "step": key, "label": label, "agent": agent,
                "status": "success",
                "started_at": start.isoformat(), "ended_at": (start + timedelta(seconds=80)).isoformat(),
                "duration_ms": 80_000,
            })
        elif i == 6:
            steps.append({
                "step": key, "label": label, "agent": agent,
                "status": "running",
                "started_at": start.isoformat(), "ended_at": None, "duration_ms": None,
            })
        else:
            steps.append({
                "step": key, "label": label, "agent": agent,
                "status": "pending",
                "started_at": None, "ended_at": None, "duration_ms": None,
            })
    return {
        "task_id": "tsk_demo_seed",
        "title": "本周项目进度汇报",
        "report_type": "project_progress",
        "status": "running",
        "created_at": base.isoformat(),
        "steps": steps,
    }


# 启动时把 seed 灌进 store
_runs["tsk_demo_seed"] = _build_seed()


@router.get("")
async def list_pipelines(limit: int = 50) -> dict:
    # 也允许从 SEED_FILE 覆盖（业务后端持久化的场景）
    if SEED_FILE.exists():
        try:
            for item in json.loads(SEED_FILE.read_text(encoding="utf-8")):
                _runs[item["task_id"]] = item
        except Exception:
            pass
    items = sorted(_runs.values(), key=lambda r: r.get("created_at", ""), reverse=True)[:limit]
    return {"items": items, "total": len(_runs)}


@router.get("/{task_id}")
async def get_pipeline(task_id: str) -> dict:
    if task_id in _runs:
        return _runs[task_id]
    return {"task_id": task_id, "steps": [], "status": "unknown"}


@router.post("/mock")
async def create_mock(title: str | None = None, report_type: str = "project_progress") -> dict:
    """创建一个 mock 任务，并在后台逐步推进 8 个 step，每步 ~3s."""
    task_id = f"tsk_{uuid.uuid4().hex[:10]}"
    now = datetime.utcnow()
    run = {
        "task_id": task_id,
        "title": title or "Mock 演示任务",
        "report_type": report_type,
        "status": "running",
        "created_at": now.isoformat(),
        "steps": [
            {
                "step": k, "label": l, "agent": a,
                "status": "pending",
                "started_at": None, "ended_at": None, "duration_ms": None,
            }
            for k, l, a in STEPS
        ],
    }
    _runs[task_id] = run
    asyncio.create_task(_advance_mock(task_id))
    from ..db import audit
    audit("pipeline.mock_create", target=task_id)
    return {"task_id": task_id}


async def _advance_mock(task_id: str) -> None:
    run = _runs.get(task_id)
    if not run:
        return
    for step in run["steps"]:
        step["status"] = "running"
        step["started_at"] = datetime.utcnow().isoformat()
        await asyncio.sleep(3)
        step["ended_at"] = datetime.utcnow().isoformat()
        step["duration_ms"] = 3000
        step["status"] = "success"
    run["status"] = "done"


@router.delete("/{task_id}")
async def delete_pipeline(task_id: str) -> dict:
    if task_id not in _runs:
        raise HTTPException(status_code=404)
    del _runs[task_id]
    from ..db import audit
    audit("pipeline.delete", target=task_id)
    return {"ok": True}


# ---- 业务后端上报接口（后续接入） ----
@router.post("/ingest")
async def ingest(payload: dict) -> dict:
    """业务后端把 TaskRun 推过来，管台直接接入展示."""
    tid = payload.get("task_id")
    if not tid:
        raise HTTPException(status_code=400)
    _runs[tid] = payload
    return {"ok": True}
