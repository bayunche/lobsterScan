"""管台 · 配置中心:Provider / TTS / 视频 / 图像 / 音乐 参数

写入 openclaw/openclaw.json:
  providers.default          LLM provider
  providers.<name>.model     该 provider 的当前模型
  providers.<name>.base_url  自定义 endpoint(走代理 / Azure 兼容 / 第三方网关)
  video.provider/model       数字人/视频源
  tts.provider/model         配音引擎
  image.provider/model       图像生成源(gpt-image-2 / minimax-image)
  image.base_url             OpenAI 兼容网关地址(只对 openai 类 provider 生效)
  music.provider/model       音乐生成源(minimax-music)
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
# 与 web-backend/app/video/providers.py 的 REGISTRY 对齐
VIDEO_PROVIDERS = ["minimax", "heygen", "self-hosted-sadtalker", "none"]

VIDEO_PROVIDER_DETAILS: list[dict] = [
    {"id": "minimax", "display_name": "MiniMax(Hailuo + speech)",
     "implemented": True,  "required_env": ["MINIMAX_API_KEY"],
     "notes": "TokenPlan 周配额按 model 列表分别计费,选 plan 覆盖的型号避免 quota_exhausted",
     # 默认 + 下拉列表(默认走 TokenPlan plan 覆盖范围内的型号)
     "default_video_model": "MiniMax-Hailuo-2.3-Fast-6s-768p",
     "available_video_models": [
         "MiniMax-Hailuo-2.3-Fast-6s-768p",
         "MiniMax-Hailuo-2.3-6s-768p",
         "MiniMax-Hailuo-02",
         "T2V-01-Director",
     ],
     # TokenPlan 周配额覆盖的型号 — 业务后端用同一份判定用哪条 key
     # (web-backend video.providers.MINIMAX_TOKENPLAN_VIDEO_MODELS 是唯一真值,这里给 UI 用)
     "tokenplan_video_models": [
         "MiniMax-Hailuo-2.3-Fast-6s-768p",
         "MiniMax-Hailuo-2.3-6s-768p",
     ]},
    {"id": "heygen", "display_name": "HeyGen Avatar",
     "implemented": False, "required_env": ["HEYGEN_API_KEY"],
     "notes": "待接入 skills/third-party/heygen-skills"},
    {"id": "self-hosted-sadtalker", "display_name": "自托管 SadTalker",
     "implemented": False, "required_env": [],
     "notes": "待接入 claude-code-video-toolkit + SadTalker GPU 节点"},
    {"id": "none", "display_name": "仅字幕(不生成音视频)",
     "implemented": True,  "required_env": [],
     "notes": "只产 SRT 字幕,适合本地预览或外网不可达"},
]

MODEL_PRESETS: dict[str, list[str]] = {
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"],
    "minimax":   ["abab7-chat-preview", "abab6.5s-chat", "abab6.5-chat", "abab5.5-chat"],
    "qwen":      ["qwen3-max", "qwen3-plus", "qwen2.5-72b-instruct"],
    "glm":       ["glm-4.5", "glm-4-plus", "glm-4-air"],
    "openai":    ["gpt-4o", "gpt-4o-mini"],
}

TTS_MODEL_PRESETS: dict[str, list[str]] = {
    # mmx CLI 真实 API 默认:speech-2.8-hd(放第一位)。speech-02-hd 是 plan 面板计量名,
    # mmx 调 API 时会被转换;为了路由 + 配额匹配,推荐用 mmx 接受的真名
    "minimax":        ["speech-2.8-hd", "speech-2.6", "speech-02-hd", "speech-02-turbo",
                       "speech-01-hd", "speech-01-turbo"],
    "heygen-builtin": ["heygen-default"],
    "elevenlabs":     ["eleven_v3", "eleven_multilingual_v2", "eleven_turbo_v2_5"],
    "qwen3":          ["qwen3-tts-flash"],
    "huihuibao":      ["self-hosted"],
}


# ─────────────────────────────────────────────────────────────
# 图像生成(挂到 html-designer agent)
# ─────────────────────────────────────────────────────────────
IMAGE_PROVIDER_DETAILS: list[dict] = [
    {"id": "openai-gpt-image-2",
     "display_name": "OpenAI gpt-image-2(garden-skills)",
     "implemented": True,
     "required_env": ["OPENAI_API_KEY"],
     "supports_base_url": True,           # UI 显示 base_url 输入框
     "default_model": "gpt-image-2",
     "available_models": ["gpt-image-2", "gpt-image-1", "dall-e-3"],
     "notes": "严格 OpenAI 标准接口;base_url 可切到 Azure / 第三方网关(默认 api.openai.com/v1)"},
    {"id": "minimax-image",
     "display_name": "MiniMax image-01",
     "implemented": True,
     "required_env": ["MINIMAX_API_KEY"],
     "supports_base_url": False,
     "default_model": "image-01",
     "available_models": ["image-01", "image-01-live"],
     "notes": "走 mmx CLI(免传 key,plan 周配额按 image-01 计费)"},
    {"id": "none",
     "display_name": "不生成图(占位降级)",
     "implemented": True, "required_env": [], "supports_base_url": False,
     "default_model": "",
     "available_models": [],
     "notes": "html-designer 跳过出图,只渲染纯文字 slide"},
]

# ─────────────────────────────────────────────────────────────
# 音乐生成(挂到 video-producer agent;BGM / 主题曲)
# ─────────────────────────────────────────────────────────────
MUSIC_PROVIDER_DETAILS: list[dict] = [
    {"id": "minimax-music",
     "display_name": "MiniMax music-2.6 / cover",
     "implemented": True,
     "required_env": ["MINIMAX_API_KEY"],
     "supports_base_url": False,
     "default_model": "music-2.6",
     "available_models": ["music-2.6", "music-2.6-free", "music-2.5+", "music-2.5", "music-cover"],
     "notes": "走 mmx CLI;music-2.6-free 需 Max plan,普通 plan 用 music-2.6(100/天)"},
    {"id": "none",
     "display_name": "不生成 BGM",
     "implemented": True, "required_env": [], "supports_base_url": False,
     "default_model": "",
     "available_models": [],
     "notes": "video-producer 不调音乐 skill"},
]


class ConfigBody(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None      # 历史字段:写到 providers[当前 default].base_url
    # 新:每个 LLM provider 独立 base_url(管台 UI 表格式编辑,不需要切到该 provider 才能改)
    # 值 = "" 表示清除该 provider 的 base_url(走官方端点)
    providers_base_urls: dict[str, str] | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    video_provider: str | None = None
    video_model: str | None = None
    image_provider: str | None = None
    image_model: str | None = None
    image_base_url: str | None = None    # OpenAI 兼容 image endpoint(写到 image.base_url)
    music_provider: str | None = None
    music_model: str | None = None


@router.get("/options")
async def options() -> dict:
    """前端下拉用:LLM / TTS / 视频 / 图像 / 音乐 可选项 + 模型预设"""
    return {
        "llm_providers": LLM_PROVIDERS,
        "tts_providers": TTS_PROVIDERS,
        "video_providers": VIDEO_PROVIDERS,
        "video_provider_details": VIDEO_PROVIDER_DETAILS,
        "image_provider_details": IMAGE_PROVIDER_DETAILS,
        "music_provider_details": MUSIC_PROVIDER_DETAILS,
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
        "tts": j.get("tts", {"provider": "minimax", "model": "speech-2.8-hd"}),
        "video": j.get("video", {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"}),
        "image": j.get("image", {"provider": "openai-gpt-image-2", "model": "gpt-image-2"}),
        "music": j.get("music", {"provider": "minimax-music", "model": "music-2.6"}),
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
    if body.llm_base_url is not None:
        providers.setdefault(target_provider, {})["base_url"] = body.llm_base_url

    # 批量写入每个 provider 的 base_url(独立于 default provider)
    if body.providers_base_urls is not None:
        for pid, url in body.providers_base_urls.items():
            entry = providers.setdefault(pid, {})
            if url:
                entry["base_url"] = url
            else:
                entry.pop("base_url", None)

    if body.tts_provider or body.tts_model:
        tts = j.setdefault("tts", {})
        if body.tts_provider:
            tts["provider"] = body.tts_provider
        if body.tts_model:
            tts["model"] = body.tts_model

    if body.video_provider or body.video_model:
        video = j.setdefault("video", {})
        if body.video_provider:
            video["provider"] = body.video_provider
        if body.video_model:
            video["model"] = body.video_model

    if body.image_provider or body.image_model or body.image_base_url is not None:
        image = j.setdefault("image", {})
        if body.image_provider:
            image["provider"] = body.image_provider
        if body.image_model:
            image["model"] = body.image_model
        if body.image_base_url is not None:
            image["base_url"] = body.image_base_url

    if body.music_provider or body.music_model:
        music = j.setdefault("music", {})
        if body.music_provider:
            music["provider"] = body.music_provider
        if body.music_model:
            music["model"] = body.music_model

    _write(j)
    audit("config.update", detail=body.model_dump(exclude_none=True))
    return {"ok": True}
