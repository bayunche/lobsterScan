"""从管台拉取明文 secrets，注入给 Agent subprocess

业务后端不持久化 secret；管台是唯一密钥源。
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("openclaw.secrets")

ADMIN_BASE = "http://127.0.0.1:8100"
_cache: dict[str, str] = {}
_cache_ts: float = 0.0
_TTL = 30  # 秒


async def fetch_all(force: bool = False) -> dict[str, str]:
    """从管台拉一次明文 secrets；30s 缓存。"""
    global _cache_ts
    now = time.time()
    if not force and _cache and (now - _cache_ts) < _TTL:
        return _cache
    try:
        async with httpx.AsyncClient(timeout=3) as cli:
            r = await cli.get(f"{ADMIN_BASE}/admin/api/secrets/internal/values")
            if r.status_code == 200:
                _cache.clear()
                _cache.update(r.json())
                _cache_ts = now
    except Exception as e:  # noqa: BLE001
        log.warning("fetch secrets failed: %s", e)
    return _cache


# 给每个 agent 注入的环境变量映射
# (agent_id, secret_key, env_name)
AGENT_ENV_MAP: dict[str, list[tuple[str, str]]] = {
    "video-producer": [
        ("MINIMAX_API_KEY", "MINIMAX_API_KEY"),
        ("MINIMAX_GROUP_ID", "MINIMAX_GROUP_ID"),
        ("HEYGEN_API_KEY", "HEYGEN_API_KEY"),
        ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
    ],
    "copywriter": [],
    "html-designer": [],
}


async def env_for_agent(agent_id: str) -> dict[str, str]:
    """返回该 agent subprocess 应该注入的环境变量字典."""
    mapping = AGENT_ENV_MAP.get(agent_id, [])
    if not mapping:
        return {}
    all_secrets = await fetch_all()
    out: dict[str, str] = {}
    for secret_key, env_name in mapping:
        v = all_secrets.get(secret_key)
        if v:
            out[env_name] = v
    return out
