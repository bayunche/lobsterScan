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
        # MINIMAX_API_KEY 走双通道:由 _resolve_minimax_key 根据当前 video/tts model
        # 自动选 _TOKENPLAN / _PAYG / 通用 fallback,所以这里不直接列 MINIMAX_API_KEY。
        ("MINIMAX_GROUP_ID", "MINIMAX_GROUP_ID"),
        ("HEYGEN_API_KEY", "HEYGEN_API_KEY"),
        ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
    ],
    "copywriter": [],
    # html-designer 的 OpenAI 凭据走 secrets,base_url/model 走 openclaw.json image.* 节点
    # (那些是配置不是 secret,管台 Config 页编辑)— inject 时在 _inject_image_config 合并
    "html-designer": [
        ("OPENAI_API_KEY", "OPENAI_API_KEY"),
    ],
}


# gpt-image-2 skill Mode A 触发开关 — OPENAI_API_KEY 存在时强制打开。
# 不在 KNOWN_KEYS 里(它不是 secret 而是行为开关),由后端自动推断。
GARDEN_IMAGEGEN_FLAG = "ENABLE_GARDEN_IMAGEGEN"


def _read_oc_section(section: str) -> dict:
    """读 openclaw.json 的某个顶级 section(image / music / providers 等)."""
    from pathlib import Path
    import json as _json
    from ..config import settings
    try:
        oc = Path(settings.project_root) / "openclaw" / "openclaw.json"
        if oc.exists():
            j = _json.loads(oc.read_text(encoding="utf-8"))
            return j.get(section) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("read openclaw.json[%s] failed: %s", section, e)
    return {}


def _inject_image_config(out: dict[str, str]) -> None:
    """给 html-designer 注入 image 配置(base_url / model)— 从 openclaw.json image 节点读。

    只在 provider 是 openai-* 时填 OPENAI_BASE_URL / OPENAI_IMAGE_MODEL(gpt-image-2 skill 用)。
    """
    image = _read_oc_section("image")
    provider = image.get("provider") or ""
    if provider.startswith("openai"):
        if image.get("base_url"):
            out["OPENAI_BASE_URL"] = image["base_url"]
        if image.get("model"):
            out["OPENAI_IMAGE_MODEL"] = image["model"]
    # minimax-image / none 不需要 inject 额外 env(走 mmx 或跳过)


def _resolve_minimax_key(all_secrets: dict[str, str]) -> tuple[str | None, str]:
    """根据当前 openclaw.json 的 video.model / tts.model,挑出该用哪把 MiniMax key。

    返回 (key_value, channel_label)。channel_label 用于日志,便于排查 quota 问题。

    fallback 链(从严到宽):
      1. 当前 channel 的专用 key(_TOKENPLAN / _PAYG)
      2. 另一渠道的 key(交叉 fallback — 用户常常只填一把账户 key,不区分渠道)
      3. 通用 MINIMAX_API_KEY(老配置)
      4. None(没填任何 key)
    """
    from pathlib import Path
    import json as _json
    from ..config import settings
    from ..video.providers import classify_minimax_channel

    video_model = tts_model = None
    try:
        oc = Path(settings.project_root) / "openclaw" / "openclaw.json"
        if oc.exists():
            j = _json.loads(oc.read_text(encoding="utf-8"))
            video_model = (j.get("video") or {}).get("model")
            tts_model = (j.get("tts") or {}).get("model")
    except Exception as e:  # noqa: BLE001
        log.warning("read openclaw.json for minimax channel failed: %s", e)

    channel = classify_minimax_channel(video_model, tts_model)
    primary = "MINIMAX_API_KEY_TOKENPLAN" if channel == "tokenplan" else "MINIMAX_API_KEY_PAYG"
    cross  = "MINIMAX_API_KEY_PAYG" if channel == "tokenplan" else "MINIMAX_API_KEY_TOKENPLAN"

    for slot, label in [(primary, primary), (cross, cross + " (cross-fallback)"),
                        ("MINIMAX_API_KEY", "MINIMAX_API_KEY (legacy)")]:
        v = all_secrets.get(slot)
        if v:
            return v, f"{channel}:{label}"
    return None, f"{channel}:(none)"


async def env_for_agent(agent_id: str) -> dict[str, str]:
    """返回该 agent subprocess 应该注入的环境变量字典."""
    mapping = AGENT_ENV_MAP.get(agent_id, [])
    # video-producer 即使 mapping 为空(因 MINIMAX_API_KEY 走动态路由)也要进
    if not mapping and agent_id != "video-producer":
        return {}
    all_secrets = await fetch_all()
    out: dict[str, str] = {}
    for secret_key, env_name in mapping:
        v = all_secrets.get(secret_key)
        if v:
            out[env_name] = v
    # video-producer 走双通道 MiniMax key 路由
    if agent_id == "video-producer":
        key, label = _resolve_minimax_key(all_secrets)
        if key:
            out["MINIMAX_API_KEY"] = key
            # 用 warning 而不是 info — uvicorn 默认吞 application logger 的 INFO,
            # 但 channel 路由是排查 quota 问题的关键诊断信息,应该一直可见
            log.warning("minimax channel for %s: %s", agent_id, label)
        else:
            log.warning("no MiniMax key available for %s (label=%s)", agent_id, label)
    # html-designer:
    #  1. image 配置(base_url / model)从 openclaw.json 读后注入
    #  2. 有 OPENAI_API_KEY 就把 gpt-image-2 skill 切到 Mode A(真出图)
    if agent_id == "html-designer":
        _inject_image_config(out)
        if out.get("OPENAI_API_KEY"):
            out[GARDEN_IMAGEGEN_FLAG] = "true"
            log.warning("gpt-image-2 Mode A enabled for html-designer (base=%s, model=%s)",
                        out.get("OPENAI_BASE_URL") or "default",
                        out.get("OPENAI_IMAGE_MODEL") or "gpt-image-2")
    return out
