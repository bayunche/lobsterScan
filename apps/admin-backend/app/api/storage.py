"""管台 · 存储管理：浏览 data/outputs，按任务清理"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..db import audit

router = APIRouter(prefix="/storage", tags=["storage"])

OUTPUTS = settings.project_root / "data" / "outputs"
UPLOADS = settings.project_root / "data" / "uploads"


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


@router.get("/overview")
async def overview() -> dict:
    return {
        "outputs_bytes": _dir_size(OUTPUTS),
        "uploads_bytes": _dir_size(UPLOADS),
        "task_count": sum(1 for _ in OUTPUTS.iterdir()) if OUTPUTS.exists() else 0,
    }


@router.get("/tasks")
async def list_task_outputs() -> list[dict]:
    if not OUTPUTS.exists():
        return []
    items = []
    for d in sorted(OUTPUTS.iterdir(), reverse=True):
        if d.is_dir():
            items.append({
                "task_id": d.name,
                "size_bytes": _dir_size(d),
                "has_video": (d / "video" / "final.mp4").exists(),
                "has_html": (d / "web-presentation" / "dist").exists(),
            })
    return items


@router.delete("/tasks/{task_id}")
async def delete_task_output(task_id: str) -> dict:
    d = OUTPUTS / task_id
    if not d.exists():
        raise HTTPException(status_code=404)
    shutil.rmtree(d)
    audit("storage.delete_task", target=task_id)
    return {"ok": True}
