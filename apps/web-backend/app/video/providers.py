"""Video / 数字人 Provider 注册表 · 可切换源

每个 Provider 描述:
- 给 video-producer agent 的 prompt 应该让它调哪个 skill
- 失败时 pipeline 该怎么降级
- 是否真的"接好了"(implemented=False 直接走降级,不再喊 agent)

后端读管台 openclaw.json 的 video.provider 字段,挑出对应 Provider 注入
到 pipeline。新接入一个数字人源,只需在这里加一个条目。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VideoProvider:
    id: str
    display_name: str
    implemented: bool                          # False = 占位,pipeline 走降级
    tts_skill: str | None                      # 用于 prompt 中告诉 agent 调哪个 skill
    video_skill: str | None
    tts_provider_label: str                    # 写到 video_production.json 的元数据
    tts_model: str
    video_provider_label: str
    video_model: str                           # 默认 video model id(可被管台 video.model 覆盖)
    required_env: list[str] = field(default_factory=list)
    notes: str = ""
    available_video_models: list[str] = field(default_factory=list)  # 给管台 UI 出下拉
    available_tts_models: list[str] = field(default_factory=list)

    def env_ok(self) -> tuple[bool, list[str]]:
        missing = [v for v in self.required_env if not os.environ.get(v)]
        return (len(missing) == 0, missing)


REGISTRY: dict[str, VideoProvider] = {
    "minimax": VideoProvider(
        id="minimax",
        display_name="MiniMax(Hailuo + speech)",
        implemented=True,
        tts_skill="minimax-tts",
        video_skill="minimax-video",
        tts_provider_label="minimax",
        tts_model="speech-02-hd",
        video_provider_label="minimax-hailuo",
        # 默认走 TokenPlan 周配额列表里能跑的型号(Hailuo-02 不在 plan 覆盖 → quota_exhausted)
        video_model="MiniMax-Hailuo-2.3-Fast-6s-768p",
        required_env=["MINIMAX_API_KEY"],
        notes="V0 默认。video 模型见 TokenPlan 配额面板,不在 plan 的会 quota_exhausted",
        # 这些选项在管台 Config 出下拉,按 plan 覆盖 + 常用 PAYG 排序
        available_video_models=[
            "MiniMax-Hailuo-2.3-Fast-6s-768p",
            "MiniMax-Hailuo-2.3-6s-768p",
            "MiniMax-Hailuo-02",
            "T2V-01-Director",
        ],
        available_tts_models=[
            "speech-02-hd", "speech-02-turbo",
            "speech-01-hd", "speech-01-turbo",
        ],
    ),
    "heygen": VideoProvider(
        id="heygen",
        display_name="HeyGen Avatar(占位)",
        implemented=False,
        tts_skill="heygen-tts",
        video_skill="heygen-avatar",
        tts_provider_label="heygen-builtin",
        tts_model="heygen-default",
        video_provider_label="heygen",
        video_model="heygen-avatar-v3",
        required_env=["HEYGEN_API_KEY"],
        notes="待接入 skills/third-party/heygen-skills",
    ),
    "self-hosted-sadtalker": VideoProvider(
        id="self-hosted-sadtalker",
        display_name="自托管 SadTalker(占位)",
        implemented=False,
        tts_skill="local-tts",
        video_skill="sadtalker",
        tts_provider_label="huihuibao",
        tts_model="self-hosted",
        video_provider_label="self-hosted",
        video_model="sadtalker",
        required_env=[],
        notes="待接入 claude-code-video-toolkit + SadTalker GPU 节点",
    ),
    "none": VideoProvider(
        id="none",
        display_name="仅字幕(不生成音视频)",
        implemented=True,
        tts_skill=None,
        video_skill=None,
        tts_provider_label="none",
        tts_model="",
        video_provider_label="none",
        video_model="",
        required_env=[],
        notes="只产 SRT 字幕,适合本地预览或外网不可达",
    ),
}

DEFAULT_PROVIDER_ID = "minimax"


# ─────────────────────────────────────────────────────────────
# MiniMax 双通道 API key 路由
# ─────────────────────────────────────────────────────────────
# TokenPlan 周配额覆盖的模型集合 — 不在此集合的型号属于 PAYG 渠道。
# 业务后端用它决定给 skill 注入 _TOKENPLAN / _PAYG / 通用 fallback 哪一把 key。
# 与管台 Config 页 video model 下拉列表保持一致(参见 admin config_api.py)。
MINIMAX_TOKENPLAN_VIDEO_MODELS: set[str] = {
    "MiniMax-Hailuo-2.3-Fast-6s-768p",
    "MiniMax-Hailuo-2.3-6s-768p",
}

MINIMAX_TOKENPLAN_TTS_MODELS: set[str] = {
    "speech-02-hd", "speech-02-turbo",
    "speech-01-hd", "speech-01-turbo",
}


def classify_minimax_channel(video_model: str | None, tts_model: str | None) -> str:
    """根据当前选用的 model 判断走 TokenPlan 还是 PAYG 渠道。

    优先级:任一 model 不在 plan 集合 → 整体走 PAYG(两个 skill 在同一 subprocess
    里只能注一把 key,从严)。两者都在或都没指定 → TokenPlan。
    """
    v_ok = (not video_model) or video_model in MINIMAX_TOKENPLAN_VIDEO_MODELS
    t_ok = (not tts_model)   or tts_model   in MINIMAX_TOKENPLAN_TTS_MODELS
    return "tokenplan" if (v_ok and t_ok) else "payg"


def list_providers() -> list[dict]:
    out = []
    for p in REGISTRY.values():
        ok, missing = p.env_ok()
        out.append({
            "id": p.id,
            "display_name": p.display_name,
            "implemented": p.implemented,
            "env_ok": ok,
            "missing_env": missing,
            "notes": p.notes,
        })
    return out


def get_provider(provider_id: str | None) -> VideoProvider:
    if provider_id and provider_id in REGISTRY:
        return REGISTRY[provider_id]
    return REGISTRY[DEFAULT_PROVIDER_ID]


def resolve_from_openclaw_json(openclaw_json: Path) -> VideoProvider:
    """读管台已落盘的 openclaw.json,挑出当前 video provider(model 也按用户配置覆盖)."""
    try:
        if openclaw_json.exists():
            j = json.loads(openclaw_json.read_text(encoding="utf-8"))
            v = j.get("video") or {}
            provider = get_provider(v.get("provider"))
            # 如果 openclaw.json 给了 video.model,覆盖 provider 的默认 — 让用户在管台改了立即生效
            user_model = v.get("model")
            if user_model and provider.id == "minimax":
                # 用 dataclass replace 而不是原地改,避免污染 REGISTRY 单例
                from dataclasses import replace
                provider = replace(provider, video_model=user_model)
            return provider
    except Exception:  # noqa: BLE001
        pass
    return get_provider(None)


# ─────────────────────────────────────────────────────────────
# 给 video-producer agent 的 prompt 模板:按 provider 选不同 skill
# ─────────────────────────────────────────────────────────────


def build_prompt_for_provider(
    *,
    provider: VideoProvider,
    task_id: str,
    duration: str,
    audience: str,
    style: str,
    script: str,
    slides_json: str,
    narrations_json: str,
    narrations_count: int,
) -> str:
    """根据 provider 渲染 video_production step 的 prompt."""
    if provider.id == "none":
        # 不调任何外部 API,只让 agent 写一份 srt
        return _NONE_TEMPLATE.format(
            task_id=task_id, duration=duration, audience=audience, style=style,
            script_head=script[:1200], narrations_json=narrations_json,
            narrations_count=narrations_count,
            provider_label=provider.video_provider_label,
            tts_label=provider.tts_provider_label,
        )
    if provider.id == "minimax":
        return _MINIMAX_TEMPLATE.format(
            task_id=task_id, duration=duration, audience=audience, style=style,
            script_head=script[:1200], slides_json=slides_json,
            narrations_json=narrations_json, narrations_count=narrations_count,
            video_model=provider.video_model,        # 用户在管台选的 / 默认 Hailuo-2.3-Fast
            tts_model=provider.tts_model,
            video_provider_label=provider.video_provider_label,
        )
    # 其它已声明但未接好的 provider — 让 agent 直接产降级 JSON,不去尝试调 skill
    return _STUB_TEMPLATE.format(
        task_id=task_id, audience=audience, style=style,
        narrations_count=narrations_count,
        provider_label=provider.video_provider_label,
        provider_display=provider.display_name,
        video_skill=provider.video_skill or "(none)",
        tts_skill=provider.tts_skill or "(none)",
        tts_label=provider.tts_provider_label,
        tts_model=provider.tts_model,
        video_model=provider.video_model,
    )


_MINIMAX_TEMPLATE = """请用挂载的 minimax-tts 和 minimax-video 两个 skill 制作汇报视频物料。

# 当前任务
task_id: {task_id}
audio_dir: data/outputs/{task_id}/audio/
video_dir: data/outputs/{task_id}/video/
duration:  {duration}
audience:  {audience}
style:     {style}

# 讲稿（script_md）
{script_head}

# Slides
```json
{slides_json}
```

# Narrations（上游已给 {narrations_count} 段）
```json
{narrations_json}
```

# 你要做的 — 两条腿并行
## (A) TTS 配音 · minimax-tts
按 narrations / slides 切讲稿,逐段调 `.agents/skills/minimax-tts/scripts/synthesize.py` 生成 mp3。
- 每段 mp3 → `data/outputs/{task_id}/audio/<两位序号>.mp3`
- 元数据写 jsonl,调 build_srt.py 生成 subtitles.srt

## (B) 数字人开场镜头 · minimax-video
调一次 `.agents/skills/minimax-video/scripts/generate.py` 生成 6 秒数字人开场。

**模型必须用 `{video_model}`(TokenPlan 周配额覆盖的型号),不要用 Hailuo-02 否则 quota_exhausted。**

prompt 写法:
- 主体:与 audience={audience}/style={style} 匹配的职场人
- 动作:自然对镜头自信讲解开场
- 场景:现代会议室 / 屏幕前 / 自然光中景

```bash
python3 .agents/skills/minimax-video/scripts/generate.py \\
  --prompt "<你定制的 prompt>" \\
  --duration 6 \\
  --model "{video_model}" \\
  --output data/outputs/{task_id}/video/intro.mp4
```

# 失败处理
- `MINIMAX_API_KEY` 缺失 → degraded=true / no_api_key
- API quota_exhausted → 已生成部分保留,degraded=true / quota_exhausted
- 视频失败但 TTS 成功 → 保留 audio_segments,intro_video=null,不阻塞
- 两个都失败 → degraded=true 让 pipeline 走降级

# 输出 schema(最终一段 ```json``` 代码块)
{{
  "audio_segments": [{{"index": 1, "text": "...", "voice": "male-qn-qingse",
    "path": "data/outputs/{task_id}/audio/01.mp3",
    "duration_estimate_sec": 6.4, "ok": true}}],
  "subtitle_path": "data/outputs/{task_id}/audio/subtitles.srt",
  "intro_video": {{"path": "data/outputs/{task_id}/video/intro.mp4",
    "duration": 6, "prompt": "...", "ok": true}},
  "voice_style": "{style}",
  "tts_provider": "minimax",
  "tts_model": "{tts_model}",
  "video_provider": "{video_provider_label}",
  "video_model": "{video_model}",
  "degraded": false,
  "degrade_reason": null
}}

# 重要:最终回复硬约束
不管 skill 调用结果如何(成功 / quota 耗尽 / 网络失败),你的最终回复**必须**以一段 ```json``` 代码块结尾,包含上面 schema 的所有字段。失败的段把 ok=false、degraded=true、degrade_reason 填准即可。不要把脚本 stdout 当成回复贴出来,自己整合后写 JSON。"""


_NONE_TEMPLATE = """当前 video provider 已切换到「仅字幕」模式 — 不要调用任何外部 TTS / 视频 API。

# 当前任务
task_id: {task_id}
duration:  {duration}
audience:  {audience}
style:     {style}

# 讲稿
{script_head}

# Narrations(上游已给 {narrations_count} 段)
```json
{narrations_json}
```

# 你要做的
按 narrations 顺序拼一份 SRT 字幕。每段预估 4 秒(可按句长简单微调),写到:
`data/outputs/{task_id}/audio/subtitles.srt`

可用 Bash + Write 工具直接生成。无需调任何 minimax/heygen skill。

# 输出 schema(最终 ```json``` 代码块)
{{
  "audio_segments": [],
  "subtitle_path": "data/outputs/{task_id}/audio/subtitles.srt",
  "intro_video": null,
  "voice_style": "{style}",
  "tts_provider": "{tts_label}",
  "tts_model": "",
  "video_provider": "{provider_label}",
  "video_model": "",
  "degraded": true,
  "degrade_reason": "subtitle_only_mode"
}}
"""


_STUB_TEMPLATE = """当前 video provider = {provider_display},尚未接入。请直接产出降级 JSON,不要尝试调 skill。

# 应当出现但未接的 skill
- 数字人:{video_skill}
- TTS:{tts_skill}

# 输出 schema(最终 ```json``` 代码块)
{{
  "audio_segments": [],
  "subtitle_path": null,
  "intro_video": null,
  "voice_style": "{style}",
  "tts_provider": "{tts_label}",
  "tts_model": "{tts_model}",
  "video_provider": "{provider_label}",
  "video_model": "{video_model}",
  "degraded": true,
  "degrade_reason": "provider_not_implemented"
}}

(narrations_count={narrations_count}, audience={audience} — 信息已记录到 metadata,后续接入此 provider 时会自动启用)"""


def stub_response_payload(provider: VideoProvider, narrations_count: int, style: str) -> dict[str, Any]:
    """provider 没接好时,直接返回这个,完全跳过 agent."""
    return {
        "audio_segments": [],
        "subtitle_path": None,
        "intro_video": None,
        "voice_style": style,
        "tts_provider": provider.tts_provider_label,
        "tts_model": provider.tts_model,
        "video_provider": provider.video_provider_label,
        "video_model": provider.video_model,         # 现在反映用户在管台选的 model
        "degraded": True,
        "degrade_reason": "provider_not_implemented",
        "narrations_count": narrations_count,
        "provider_id": provider.id,
    }
