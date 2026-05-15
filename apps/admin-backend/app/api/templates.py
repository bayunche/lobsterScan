"""管台 · 模板与素材：报告大纲 / HTML 主题 / 数字人形象

模板按文件落盘：
  skills/custom/report-structure/assets/report-structures/<id>.yaml
  skills/custom/copywriter/assets/html-themes/<id>.json
  openclaw/workspaces/video-producer/avatars/<id>.md   (HeyGen AVATAR-*.md)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import audit

router = APIRouter(prefix="/templates", tags=["templates"])

ROOTS = {
    "report-structures": (settings.skills_root / "custom" / "report-structure" / "assets" / "report-structures", ".yaml"),
    "html-themes":       (settings.skills_root / "custom" / "copywriter" / "assets" / "html-themes",            ".json"),
    "avatars":           (settings.workspaces_root / "video-producer" / "avatars",                                ".md"),
}


def _resolve(kind: str, name: str | None = None):
    if kind not in ROOTS:
        raise HTTPException(status_code=404, detail={"error": {"biz_message": "未知模板类型"}})
    root, ext = ROOTS[kind]
    if name is None:
        return root, ext, None
    # 防穿越
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail={"error": {"biz_message": "非法模板名"}})
    if not name.endswith(ext):
        name = f"{name}{ext}"
    return root, ext, root / name


class TemplateBody(BaseModel):
    content: str


@router.get("/{kind}")
async def list_templates(kind: str) -> dict:
    root, ext, _ = _resolve(kind)
    if not root.exists():
        return {"items": []}
    items = [p.name for p in sorted(root.iterdir()) if p.is_file() and p.name.endswith(ext)]
    return {"items": items, "root": str(root.relative_to(settings.project_root))}


@router.get("/{kind}/{name}")
async def read_template(kind: str, name: str) -> dict:
    root, ext, fp = _resolve(kind, name)
    if not fp.exists():
        raise HTTPException(status_code=404)
    return {"name": fp.name, "content": fp.read_text(encoding="utf-8")}


@router.put("/{kind}/{name}")
async def write_template(kind: str, name: str, body: TemplateBody) -> dict:
    root, ext, fp = _resolve(kind, name)
    root.mkdir(parents=True, exist_ok=True)
    fp.write_text(body.content, encoding="utf-8")
    audit("template.write", target=f"{kind}/{fp.name}")
    return {"ok": True, "name": fp.name}


@router.delete("/{kind}/{name}")
async def delete_template(kind: str, name: str) -> dict:
    root, ext, fp = _resolve(kind, name)
    if not fp.exists():
        raise HTTPException(status_code=404)
    fp.unlink()
    audit("template.delete", target=f"{kind}/{fp.name}")
    return {"ok": True}
