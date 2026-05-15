"""管台 · 配置中心：Provider / TTS / 视频参数

写入 openclaw/openclaw.json:
  providers.default          LLM provider
  providers.<name>.model     该 provider 的当前模型
  video.provider             heygen | self-hosted
  tts.provider               heygen-builtin | minimax | elevenlabs | qwen3 | huihuibao
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings
from ..db import audit

router = APIRouter(prefix="/config", tags=["config"])
OC_JSON = settings.openclaw_json


LLM_PROVIDERS = ["anthropic", "minimax", "qwen", "glm", "openai"]
TTS_PROVIDERS = ["minimax", "heygen-builtin", "elevenlabs", "qwen3", "huihuibao"]
VIDEO_PROVIDERS = ["heygen", "self-hosted"]

MODEL_PRESETS: dict[str, list[str]] = {
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"],
    "minimax":   ["abab7-chat-preview", "abab6.5s-chat", "abab6.5-chat", "abab5.5-chat"],
    "qwen":      ["qwen3-max", "qwen3-plus", "qwen2.5-72b-instruct"],
    "glm":       ["glm-4.5", "glm-4-plus", "glm-4-air"],
    "openai":    ["gpt-4o", "gpt-4o-mini"],
}

TTS_MODEL_PRESETS: dict[str, list[str]] = {
    "minimax":        ["speech-02-hd", "speech-02-turbo", "speech-01-hd", "speech-01-turbo"],
    "heygen-builtin": ["heygen-default"],
    "elevenlabs":     ["eleven_v3", "eleven_multilingual_v2", "eleven_turbo_v2_5"],
    "qwen3":          ["qwen3-tts-flash"],
    "huihuibao":      ["self-hosted"],
}


class ConfigBody(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    video_provider: str | None = None


@router.get("/options")
async def options() -> dict:
    """前端下拉用：LLM / TTS / 视频可选项 + 模型预设"""
    return {
        "llm_providers": LLM_PROVIDERS,
        "tts_providers": TTS_PROVIDERS,
        "video_providers": VIDEO_PROVIDERS,
        "model_presets": MODEL_PRESETS,
        "tts_model_presets": TTS_MODEL_PRESETS,
    }


def _read() -> dict:
    if not OC_JSON.exists():
        return {}
    return json.loads(OC_JSON.read_text(encoding="utf-8"))


def _write(j: dict) -> None:
    OC_JSON.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("")
async def read():
    j = _read()
    return {
        "providers": j.get("providers", {}),
        "tts": j.get("tts", {"provider": "minimax", "model": "speech-02-hd"}),
        "video": j.get("video", {"provider": "heygen"}),
    }


@router.put("")
async def write(body: ConfigBody):
    j = _read()
    providers = j.setdefault("providers", {})
    if body.llm_provider:
        providers["default"] = body.llm_provider
    target_provider = body.llm_provider or providers.get("default", "anthropic")
    if body.llm_model:
        providers.setdefault(target_provider, {})["model"] = body.llm_model

    if body.tts_provider or body.tts_model:
        tts = j.setdefault("tts", {})
        if body.tts_provider:
            tts["provider"] = body.tts_provider
        if body.tts_model:
            tts["model"] = body.tts_model

    if body.video_provider:
        j.setdefault("video", {})["provider"] = body.video_provider

    _write(j)
    audit("config.update", detail=body.model_dump(exclude_none=True))
    return {"ok": True}
