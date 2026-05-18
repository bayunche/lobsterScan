"""POST /api/files · 上传任意类型文件
GET  /api/files/{file_id}     · 元数据
GET  /api/files/{file_id}/raw · 二进制内容（前端预览 / 下载）
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings

router = APIRouter(tags=["files"])

FILES_ROOT = settings.uploads_root / "files"

# 单文件大小上限（MB）— 防止误传超大文件
MAX_BYTES = 64 * 1024 * 1024     # 64 MB


def _safe_name(name: str) -> str:
    """文件名安全化（保留中文，去掉路径分隔符）"""
    return name.replace("/", "_").replace("\\", "_").replace("..", "_")[:200]


def _find_path(file_id: str) -> Path | None:
    """根据 file_id 查实际存储路径。约定：<file_id>__<original>."""
    if not file_id or "/" in file_id or ".." in file_id:
        return None
    if not FILES_ROOT.exists():
        return None
    for p in FILES_ROOT.iterdir():
        if p.is_file() and p.name.startswith(f"{file_id}__"):
            return p
    return None


def _meta_from_path(p: Path) -> dict:
    name = p.name.split("__", 1)[1] if "__" in p.name else p.name
    mime, _ = mimetypes.guess_type(name)
    return {
        "file_id":  p.name.split("__", 1)[0],
        "filename": name,
        "size":     p.stat().st_size,
        "mime":     mime or "application/octet-stream",
        "url":      f"/api/files/{p.name.split('__', 1)[0]}/raw",
    }


@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),
    task_id: str | None = Form(None),
) -> dict:
    """上传任意类型文件。返回 {file_id, filename, size, mime, url}."""
    FILES_ROOT.mkdir(parents=True, exist_ok=True)

    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": {
                "code": "FILE_TOO_LARGE",
                "biz_message": f"文件超过 {MAX_BYTES // 1024 // 1024} MB 上限，无法上传",
                "retryable": False,
            }},
        )

    file_id = f"f_{uuid.uuid4().hex[:14]}"
    safe = _safe_name(file.filename or "upload.bin")
    dst = FILES_ROOT / f"{file_id}__{safe}"
    dst.write_bytes(raw)

    mime, _ = mimetypes.guess_type(safe)
    return {
        "file_id": file_id,
        "filename": safe,
        "size": len(raw),
        "mime": mime or file.content_type or "application/octet-stream",
        "url": f"/api/files/{file_id}/raw",
    }


@router.get("/files/{file_id}")
async def file_meta(file_id: str) -> dict:
    p = _find_path(file_id)
    if not p:
        raise HTTPException(status_code=404)
    return _meta_from_path(p)


@router.get("/files/{file_id}/raw")
async def file_raw(file_id: str):
    p = _find_path(file_id)
    if not p:
        raise HTTPException(status_code=404)
    mime, _ = mimetypes.guess_type(p.name)
    name = p.name.split("__", 1)[1] if "__" in p.name else p.name
    return FileResponse(p, media_type=mime or "application/octet-stream", filename=name)


# 给 cluster.py 用：批量查
def lookup_many(file_ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for fid in file_ids or []:
        p = _find_path(fid)
        if p:
            out.append(_meta_from_path(p))
    return out


def lookup_many_with_paths(file_ids: list[str]) -> list[tuple[dict, Path]]:
    """同 lookup_many,但带磁盘 Path — 给 extractor 抽文本用,不返回给前端"""
    out: list[tuple[dict, Path]] = []
    for fid in file_ids or []:
        p = _find_path(fid)
        if p:
            out.append((_meta_from_path(p), p))
    return out
