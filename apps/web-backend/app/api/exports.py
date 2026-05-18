"""GET /api/tasks/{task_id}/exports[/subpath]
   下载 task 产物（支持子路径 video/intro.mp4 这种）。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import settings

router = APIRouter(tags=["exports"])

OUTPUT_ROOT = settings.outputs_root

# 安全的 task_id 字符 + 路径 segment 检查
_SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _safe_path(task_id: str, subpath: str) -> Path:
    if not _SAFE.match(task_id):
        raise HTTPException(status_code=400)
    # 拆解路径段并校验
    segments = [seg for seg in subpath.split("/") if seg]
    for seg in segments:
        if seg.startswith(".") or seg in {"..", ""} or not re.match(r"^[一-龥A-Za-z0-9_.\-]+$", seg):
            raise HTTPException(status_code=400)
    fp = OUTPUT_ROOT / task_id
    for seg in segments:
        fp = fp / seg
    # 防止符号链接逃逸
    fp_resolved = fp.resolve()
    root_resolved = (OUTPUT_ROOT / task_id).resolve()
    if not str(fp_resolved).startswith(str(root_resolved)):
        raise HTTPException(status_code=400)
    return fp


@router.get("/tasks/{task_id}/exports")
async def list_exports(task_id: str) -> dict:
    d = OUTPUT_ROOT / task_id
    if not d.exists():
        return {"items": []}
    items = []
    for f in sorted(d.rglob("*")):
        if f.is_file():
            rel = f.relative_to(d).as_posix()
            items.append({
                "name": rel,
                "size": f.stat().st_size,
                "url": f"/api/tasks/{task_id}/exports/{rel}",
            })
    return {"items": items}


@router.api_route("/tasks/{task_id}/exports/{subpath:path}", methods=["GET", "HEAD"])
async def export(task_id: str, subpath: str):
    """GET 拿文件;HEAD 仅探测存在性(前端 ArtifactChip probe 用,不能 405)"""
    fp = _safe_path(task_id, subpath)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail={"error": {"biz_message": "产物尚未生成"}})
    media, _ = mimetypes.guess_type(fp.name)
    return FileResponse(fp, filename=fp.name, media_type=media or "application/octet-stream")
