"""管理平台 · Skill 市场与挂载（docs/API接口规范.md §3.2）"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter(prefix="/skills", tags=["skills"])

SKILLS_ROOT = settings.skills_root
WS_ROOT = settings.workspaces_root

CATEGORY = {
    # 名 → 分类标签
    "kb-retriever": "检索",
    "web-design-engineer": "视觉",
    "web-video-presentation": "视频",
    "gpt-image-2": "视觉",
    "humanizer": "质量",
    "heygen": "视频",
    "klingai": "视频",
    "elevenlabs": "视频",
    "playwright-recording": "视频",
    "ffmpeg": "视频",
    "moviepy": "视频",
    "material-parser": "检索",
    "point-extractor": "内容生成",
    "report-structure": "内容生成",
    "upward-translator": "内容生成",
    "copywriter": "内容生成",
    "report-reviewer": "质量",
}


def _find_skill(name: str) -> Path | None:
    candidates = [
        SKILLS_ROOT / "custom" / name,
        SKILLS_ROOT / "third-party" / "garden-skills" / "skills" / name,
        SKILLS_ROOT / "third-party" / "humanizer",
        SKILLS_ROOT / "third-party" / "heygen-skills" / name,
        SKILLS_ROOT / "third-party" / "claude-code-video-toolkit" / "skills" / name,
    ]
    for c in candidates:
        if (c / "SKILL.md").exists():
            return c
    return None


@router.get("/catalog")
async def catalog() -> list[dict]:
    out = []
    for name, cat in CATEGORY.items():
        src = _find_skill(name)
        installed_in = []
        for ws in WS_ROOT.iterdir():
            try:
                if (ws / ".agents" / "skills" / name).exists():
                    installed_in.append(ws.name)
            except OSError:
                # Windows 上损坏/不可达 symlink 会让 .exists() 抛 OSError(WinError 1920)
                # 而非返回 False — 当作未安装,不让单个坏 symlink crash 整个 catalog。
                pass
        out.append({
            "name": name,
            "category": cat,
            "source_available": src is not None,
            "installed_in": installed_in,
        })
    return out


@router.get("/{name}")
async def get_skill(name: str) -> dict:
    src = _find_skill(name)
    if not src:
        raise HTTPException(status_code=404)
    skill_md = (src / "SKILL.md").read_text(encoding="utf-8")
    readme = (src / "README.md").read_text(encoding="utf-8") if (src / "README.md").exists() else ""
    return {"name": name, "path": str(src), "skill_md": skill_md, "readme": readme}


class InstallBody(BaseModel):
    name: str
    target_agents: list[str]
    version: str | None = None


@router.post("/install")
async def install(body: InstallBody) -> dict:
    from ..db import audit
    src = _find_skill(body.name)
    if not src:
        raise HTTPException(status_code=404, detail={"error": {"biz_message": "Skill 源未找到，请先 submodule update"}})
    for aid in body.target_agents:
        dst = WS_ROOT / aid / ".agents" / "skills" / body.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            if dst.is_symlink():
                dst.unlink()
            elif dst.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
        try:
            dst.symlink_to(src.resolve(), target_is_directory=True)
        except OSError:
            # WSL/Windows 文件系统下 symlink 可能失败 → 退回 copytree
            shutil.copytree(src, dst)
    audit("skill.install", target=body.name, detail={"agents": body.target_agents})
    return {"ok": True, "installed_in": body.target_agents}


@router.delete("/{name}")
async def uninstall(name: str, target_agent: str) -> dict:
    dst = WS_ROOT / target_agent / ".agents" / "skills" / name
    if dst.exists() or dst.is_symlink():
        dst.unlink() if dst.is_symlink() else shutil.rmtree(dst)
        from ..db import audit
        audit("skill.uninstall", target=name, detail={"agent": target_agent})
        return {"ok": True}
    raise HTTPException(status_code=404)


class BulkInstallBody(BaseModel):
    items: list[InstallBody]


@router.post("/install/bulk")
async def install_bulk(body: BulkInstallBody) -> dict:
    """批量挂载：一次性把多个 (skill, agents[]) 落到位."""
    from ..db import audit
    results = []
    for it in body.items:
        try:
            r = await install(it)
            results.append({"name": it.name, "ok": True, "installed_in": r.get("installed_in", [])})
        except HTTPException as e:
            results.append({"name": it.name, "ok": False, "error": e.detail})
    audit("skill.install_bulk", detail={"count": len(body.items)})
    return {"results": results}
