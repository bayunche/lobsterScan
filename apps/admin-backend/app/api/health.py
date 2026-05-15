"""管台 · 集群健康"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/health", tags=["health"])

_cache: dict = {"ts": 0, "data": None}
_TTL = 5


def _gateway_ping(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _agents_status() -> list[dict]:
    out = []
    for ws in sorted(settings.workspaces_root.iterdir() if settings.workspaces_root.exists() else []):
        if not ws.is_dir():
            continue
        soul = (ws / "SOUL.md").exists()
        agents_md = (ws / "AGENTS.md").exists()
        skill_dir = ws / ".agents" / "skills"
        skills = [p.name for p in skill_dir.iterdir()] if skill_dir.exists() else []
        agent_dir = settings.home_openclaw / "agents" / ws.name
        out.append({
            "id": ws.name,
            "workspace_ready": soul and agents_md,
            "skills": skills,
            "agent_dir_exists": agent_dir.exists(),
        })
    return out


def _openclaw_cli() -> dict:
    bin_ok = shutil.which("openclaw") is not None
    version = None
    if bin_ok:
        try:
            r = subprocess.run(
                ["openclaw", "--version"], capture_output=True, text=True, timeout=2
            )
            version = (r.stdout or r.stderr).strip()
        except Exception:
            version = None
    return {"installed": bin_ok, "version": version}


def _detect_gateway_port() -> int:
    """OpenClaw 在 BOOTSTRAP 时会写出当前监听端口；先读 openclaw.json，再扫常见端口."""
    try:
        import json as _j
        oc = _j.loads((settings.openclaw_json).read_text(encoding="utf-8"))
        p = oc.get("gateway", {}).get("port")
        if p:
            return int(p)
    except Exception:
        pass
    return 7800


def _detect_running_gateway() -> tuple[str, int, bool]:
    host = "127.0.0.1"
    # 1. 先用配置端口
    p = _detect_gateway_port()
    if _gateway_ping(host, p):
        return host, p, True
    # 2. 扫描常见端口（OpenClaw 默认可能写到 7800 / 18789 / 随机）
    for candidate in (7800, 18789, 18888, 8788):
        if _gateway_ping(host, candidate):
            return host, candidate, True
    return host, p, False


@router.get("")
async def health() -> dict:
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    g_host, g_port, g_online = _detect_running_gateway()
    data = {
        "gateway": {
            "host": g_host,
            "port": g_port,
            "online": g_online,
        },
        "cli": _openclaw_cli(),
        "agents": _agents_status(),
        "providers": [],
        "queues": [],
    }
    _cache["ts"] = now
    _cache["data"] = data
    return data
