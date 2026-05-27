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
    # ── Avatar 类 provider 通用字段(HeyGen / D-ID / 未来 OmniHuman) ──
    # 这些是"内容标识 ID"维度,跟 video_model(技术 model id)正交。
    # provider 自己声明默认值,user 在管台 Config 选 → 写 openclaw.json video.avatar_id 等。
    avatar_id: str | None = None              # avatar_id 或 talking_photo_id(provider 自决)
    voice_id: str | None = None
    talking_photo_id: str | None = None       # HeyGen 把 photo 和 avatar 区分开;其它 provider 忽略

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
        tts_model="speech-2.8-hd",       # mmx CLI 真 API 默认(plan 面板写 speech-hd / speech-02-hd 是计量别名)
        video_provider_label="minimax-hailuo",
        # 默认走 mmx 接受的真 API model id(plan 面板的 -6s-768p 是计费 metric)
        video_model="MiniMax-Hailuo-2.3",
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
            "speech-2.8-hd", "speech-2.6",
            "speech-02-hd", "speech-02-turbo",
            "speech-01-hd", "speech-01-turbo",
        ],
    ),
    "heygen": VideoProvider(
        id="heygen",
        display_name="HeyGen V2(数字人 lip-sync)",
        implemented=True,
        # HeyGen 一个 skill 包揽 video + TTS(声音由 voice_id 选,内置 TTS)
        tts_skill="heygen",          # 同一个 skill,subcommand voices 拿声音
        video_skill="heygen",
        tts_provider_label="heygen-builtin",
        tts_model="",                # voice_id 替代 model 概念
        video_provider_label="heygen",
        video_model="v2",            # API schema 版本,不是 model id
        required_env=["HEYGEN_API_KEY"],
        notes="V2 API · 真人 lip-sync。需先用 `voices list` / `avatars list` 拿 ID。"
              "本账号实测:1281 avatars + 5469 talking_photos + 2414 voices",
        # 默认值:中文女声 Xiaoxin Professional + Veronica talking_photo
        voice_id="de6ad44022104ac0872392d1139e9364",          # Xiaoxin - Professional
        talking_photo_id="6013fc758b5446a2ba17d8c459538bb4",  # Veronica(经典口播形象)
        avatar_id=None,                                       # 留给 user 在管台选
    ),
    "kling": VideoProvider(
        id="kling",
        display_name="Kling AI(可灵)",
        # 已启用:本地等价实现 + 管台 Secrets 已落盘 KLING_ACCESS_KEY_ID/SECRET_ACCESS_KEY(2026-05-19)
        # 后续可选 openclaw doctor --fix + skills install klingai-dev/klingai 用官方包覆盖
        implemented=True,
        # TTS 复用 MiniMax(Kling 平台本身只生视频,不出语音)
        tts_skill="minimax-tts",
        video_skill="klingai",
        tts_provider_label="minimax",
        tts_model="speech-2.8-hd",
        video_provider_label="kling",
        video_model="kling-v3",
        required_env=["KLING_ACCESS_KEY_ID", "KLING_SECRET_ACCESS_KEY"],
        notes="国内可灵 AI · ABI 对齐 clawhub klingai-dev/klingai。TTS 复用 MiniMax(Kling 不出语音)",
        available_video_models=[
            "kling-v3",
            "kling-v3-omni",
            "kling-v2-6",
            "kling-video-o1",
        ],
        available_tts_models=[
            "speech-2.8-hd", "speech-2.6",
            "speech-02-hd", "speech-02-turbo",
        ],
    ),
    "dashscope": VideoProvider(
        id="dashscope",
        display_name="万相 2.7(阿里 DashScope)",
        implemented=True,
        tts_skill="minimax-tts",
        video_skill="dashscope-video",
        tts_provider_label="minimax",
        tts_model="speech-2.8-hd",
        video_provider_label="dashscope",
        video_model="wan2.7-t2v-2026-04-25",
        required_env=["DASHSCOPE_API_KEY"],
        notes="阿里百炼 DashScope · 万相 2.7 文生视频,TTS 复用 MiniMax",
        available_video_models=[
            "wan2.7-t2v-2026-04-25",
        ],
        available_tts_models=[
            "speech-2.8-hd", "speech-2.6",
            "speech-02-hd", "speech-02-turbo",
        ],
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
#
# 注意命名空间:plan 配额面板用「计量名」(含 -6s-768p 后缀),mmx CLI 真 API ID 用基础名。
# 路由集合**同时包含两者**,只要任一形式匹配就归到 TokenPlan,避免 user 在管台改成 API ID
# 后 routing 判定漂移到 PAYG。
MINIMAX_TOKENPLAN_VIDEO_MODELS: set[str] = {
    # plan 计量名(metric 维度)
    "MiniMax-Hailuo-2.3-Fast-6s-768p",
    "MiniMax-Hailuo-2.3-6s-768p",
    # mmx 真 API model id
    "MiniMax-Hailuo-2.3",
    "MiniMax-Hailuo-2.3-Fast",
}

MINIMAX_TOKENPLAN_TTS_MODELS: set[str] = {
    # plan 计量名
    "speech-02-hd", "speech-02-turbo",
    "speech-01-hd", "speech-01-turbo",
    "speech-hd",
    # mmx 真 API model id
    "speech-2.8-hd", "speech-2.6",
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
    """读管台已落盘的 openclaw.json,挑出当前 video provider(model / avatar / voice 都按用户配置覆盖)."""
    try:
        if openclaw_json.exists():
            j = json.loads(openclaw_json.read_text(encoding="utf-8"))
            v = j.get("video") or {}
            provider = get_provider(v.get("provider"))
            # 用 dataclass replace 而不是原地改,避免污染 REGISTRY 单例
            from dataclasses import replace
            overrides: dict[str, str] = {}
            user_model = v.get("model")
            if user_model and provider.id in {"minimax", "kling"}:
                overrides["video_model"] = user_model
            # Avatar 类 provider:HeyGen 等通用字段
            if provider.id in {"heygen"}:
                if v.get("avatar_id"):         overrides["avatar_id"] = v["avatar_id"]
                if v.get("voice_id"):          overrides["voice_id"] = v["voice_id"]
                if v.get("talking_photo_id"):  overrides["talking_photo_id"] = v["talking_photo_id"]
            if overrides:
                provider = replace(provider, **overrides)
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
    if provider.id == "kling":
        # 简单从受众/风格推 prompt 子串,让 agent 写的 prompt 更具体
        audience_role = {
            "直属领导": "女性", "团队内部": "女性",
            "跨部门": "女性", "客户": "女性",
        }.get(audience, "女性")
        style_dress = {
            "简洁正式": "深色现代职业装",
            "成果突出": "白衬衫加西装外套",
            "问题导向": "简约浅灰职业装",
            "述职风":   "正装深色西装",
        }.get(style, "现代职业装")
        return _KLING_TEMPLATE.format(
            task_id=task_id, duration=duration, audience=audience, style=style,
            audience_role=audience_role, style_dress=style_dress,
            script_head=script[:1200],
            narrations_json=narrations_json, narrations_count=narrations_count,
            video_model=provider.video_model,
            tts_model=provider.tts_model,
            video_provider_label=provider.video_provider_label,
        )
    if provider.id == "heygen":
        # HeyGen 一个 skill 把 video + TTS 都接管,prompt 单独处理
        # 优先 talking_photo(本账号 5469 个,主推路径),其次 avatar(1281 个)
        character_arg = (
            f'--talking_photo_id "{provider.talking_photo_id}"'
            if provider.talking_photo_id and not provider.avatar_id
            else f'--avatar_id "{provider.avatar_id}"'
        )
        return _HEYGEN_TEMPLATE.format(
            task_id=task_id, duration=duration, audience=audience, style=style,
            script_head=script[:1200], slides_json=slides_json,
            narrations_json=narrations_json, narrations_count=narrations_count,
            character_arg=character_arg,
            voice_id=provider.voice_id or "",
            video_provider_label=provider.video_provider_label,
        )
    if provider.id == "dashscope":
        audience_role = {
            "直属领导": "女性", "团队内部": "女性",
            "跨部门": "女性", "客户": "女性",
        }.get(audience, "女性")
        style_dress = {
            "简洁正式": "深色现代职业装",
            "成果突出": "白衬衫加西装外套",
            "问题导向": "简约浅灰职业装",
            "述职风":   "正装深色西装",
        }.get(style, "现代职业装")
        return _DASHSCOPE_TEMPLATE.format(
            task_id=task_id, duration=duration, audience=audience, style=style,
            audience_role=audience_role, style_dress=style_dress,
            script_head=script[:1200],
            narrations_json=narrations_json, narrations_count=narrations_count,
            video_model=provider.video_model,
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

# 重要前置 · 必读
本环境必须走 **mmx CLI**(已 `mmx auth login` 配置 API key,见 ~/.config/mmx/config.json)。
- 你**不能**自己判断 "MINIMAX_API_KEY 是否设置",**不能**预判降级 — 必须先真正调脚本。
- 两个 skill scripts 内部都是 **mmx CLI 优先**(独立 auth,不读 MINIMAX_API_KEY)+ HTTP 兜底。
  即使你看到 env 里 MINIMAX_API_KEY 是空,**也要先调脚本** — mmx CLI 走自己的 config.json。
- 脚本 stdout 会有 `"via": "mmx"` 或 `"via": "http"` 字段,告诉你实际走了哪条腿。
- 只有当 `via=mmx` 也 `ok=false`(mmx_quota_exhausted / mmx_nonzero / mmx_timeout)时,才算真的失败。

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
- **必须真调脚本,不要预判降级**。脚本内部走 mmx CLI(独立 auth 不依赖 env)。
- 每段 mp3 → `data/outputs/{task_id}/audio/<两位序号>.mp3`
- 元数据写 jsonl,调 build_srt.py 生成 subtitles.srt
- 留意 stdout 的 `via` 字段:`mmx` 走 plan 配额(speech-hd interval 4000),`http` 走 PAYG。

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
  --output /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4
```

# 失败处理(只有真调脚本拿到 ok=false 才能算失败)
- 脚本输出 `via=mmx, ok=false, error=mmx_quota_exhausted` → degraded=true / quota_exhausted
- 脚本输出 `via=mmx, ok=false, error=mmx_nonzero` → 看 stderr 决定 degrade_reason
- 脚本输出 `via=http, ok=false, error=no_api_key` → 这才是真 no_api_key,degraded=true
- 视频失败但 TTS 成功 → 保留 audio_segments,intro_video=null,不阻塞
- 两个都失败 → degraded=true 让 pipeline 走 slideshow 降级
- **禁止行为**:看到 env 没 key 就直接写 "no_api_key",不调脚本。这是错的 — 必须真调。

# 输出 schema(最终一段 ```json``` 代码块)
{{
  "audio_segments": [{{"index": 1, "text": "...", "voice": "male-qn-qingse",
    "path": "data/outputs/{task_id}/audio/01.mp3",
    "duration_estimate_sec": 6.4, "ok": true}}],
  "subtitle_path": "data/outputs/{task_id}/audio/subtitles.srt",
  "intro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4",
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


_KLING_TEMPLATE = """# 数字人 Provider:Kling AI(可灵)
本次任务已为你挂载 **klingai** skill,你必须用它生成两段数字人短视频。

## 当前任务
task_id: {task_id}
video_dir: data/outputs/{task_id}/video/
audience:  {audience}
style:     {style}

## 讲稿参考
{script_head}

## Narrations({narrations_count} 段)
```json
{narrations_json}
```

## 你必须做的 — 两段 5s 数字人视频

### Step 1: 先验证凭据
```bash
node .agents/skills/klingai/scripts/kling.mjs account --check
```
确认 `ok: true` 再继续。如果失败 → 直接输出 degraded JSON,不要编造视频文件。

### Step 2: 生成开场镜头 (intro_video)
取 narrations[0] 文本,精简到 15-25 字开场白。然后**真正执行**:
```bash
node .agents/skills/klingai/scripts/kling.mjs video \\
  --prompt "中国年轻职场{audience_role},身穿{style_dress},坐在干净明亮的现代会议室中景,自然微笑温暖地讲话,正面对镜头" \\
  --model "{video_model}" --mode pro --aspect_ratio 16:9 --duration 5 \\
  --output_dir /mnt/c/code/lobsterScan/data/outputs/{task_id}/video
```
等待 stdout 返回 JSON(内含 `path` 字段,轮询约 1-3 分钟),然后:
```bash
mv "<stdout 返回的 path>" /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4
```

### Step 3: 生成结尾镜头 (outro_video)
取 narrations[-1] 文本,精简到 15-25 字。同样执行 kling.mjs video(prompt 可稍改,如"站起身平视镜头自信收尾"),拿到后:
```bash
mv "<stdout 返回的 path>" /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/outro.mp4
```

## 关键规则
- **必须真正运行 Bash 命令**。不要跳过执行直接编写输出 JSON — backend 会验证文件是否存在
- 每段 5s(Kling 单段上限),model 用 `{video_model}`,不要换
- 视频无音(数字人嘴动不对口型没关系,用中景避开特写嘴部)
- TTS / 录屏你不需要管,backend 自动处理
- 鉴权已由 backend 注入 env: KLING_ACCESS_KEY_ID / KLING_SECRET_ACCESS_KEY

## 失败处理
kling.mjs 返回 `ok: false` 或非零退出码时:
- exit 2 → degrade_reason: "no_credentials"
- exit 3 → degrade_reason: "quota_exhausted"
- exit 4 → degrade_reason: "timeout"
- intro 成功 outro 失败 → 保留成功的,degraded: true

## 输出 schema(最终一段 ```json``` 代码块)
{{
  "audio_segments": [],
  "subtitle_path": null,
  "intro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4",
    "duration": 5,
    "text": "<15-25 字开场白,backend 用它跑 TTS 配音>",
    "ok": true}},
  "outro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/outro.mp4",
    "duration": 5,
    "text": "<15-25 字结尾总结,backend 用它跑 TTS 配音>",
    "ok": true}},
  "voice_style": "{style}",
  "tts_provider": "minimax",
  "tts_model": "{tts_model}",
  "video_provider": "{video_provider_label}",
  "video_model": "{video_model}",
  "degraded": false,
  "degrade_reason": null
}}

**intro_video.text / outro_video.text 不能省**(backend 拿它跑配音)。
任一段失败 → 该段 ok=false / path=null,整体 degraded=true + degrade_reason 填准。"""


_HEYGEN_TEMPLATE = """请用挂载的 heygen skill 生成**两段独立**数字人视频:开场白 + 结尾总结。

# 当前任务
task_id: {task_id}
video_dir: data/outputs/{task_id}/video/
duration:  {duration}
audience:  {audience}
style:     {style}

# 讲稿(script_md,完整文本)
{script_head}

# Slides(参考语境 · 不直接喂 HeyGen)
```json
{slides_json}
```

# Narrations(上游 narrations · 中间页旁白会由 backend 用 MiniMax-TTS 兜底,**不要你管**)
```json
{narrations_json}
```

# 你要做的 — 两段独立短镜头(各 15-30s)

## (A) 开场白 intro_video
取 narrations[0] 文本(汇报封面的开场话术,如标题 + 主旨)。如果该段超过 200 字,**自行精简到 80-120 字**,保持开场气势。然后:

```bash
node .agents/skills/heygen/scripts/heygen.mjs video generate \\
  {character_arg} \\
  --voice_id "{voice_id}" \\
  --text "<精简后的开场白,80-120 字,以问候/亮明主题开头>" \\
  --emotion "Friendly" \\
  --speed 1.0 \\
  --background_color "#FFFFFF" \\
  --width 1920 --height 1080 \\
  --output_dir /mnt/c/code/lobsterScan/data/outputs/{task_id}/video
```

拿到 stdout 的 `path` 字段后立刻:
```bash
mv "<返回的 path>" /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4
```

## (B) 结尾总结 outro_video
取 narrations[-1] 文本(汇报末页的总结/展望)。同样精简到 80-120 字,然后**再调一次** heygen skill,**output_dir 同上**,拿到后:
```bash
mv "<返回的 path>" /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/outro.mp4
```

# 重要原则
- **两段视频是必需品**,缺一个都不行(intro / outro 任一失败 → degraded=true)
- character_arg 由后端注入(talking_photo / avatar);**不要自己换 ID**
- voice_id 后端给的是中文女声 Xiaoxin(`de6ad44...`);想换在管台 Config 改
- 每段 80-120 字,HeyGen 渲染约 15-25 秒,两段加起来 ≤ 60 秒
- 中间页 narration 由 backend 自动跑 MiniMax-TTS 兜底,**你不需要调 minimax-tts**

# 不要做的事
- 不要把整个讲稿(--text 几千字)塞一段视频里(会卡 quota / 时长超控)
- 不要去调 minimax-tts(中间页 backend 兜)
- 不要打印 HEYGEN_API_KEY
- 不要尝试改 avatar_style / use_avatar_iv,默认行为已经稳

# 失败处理(exit code)
- exit 2(no_credentials)→ degraded=true / degrade_reason=no_credentials
- exit 3(quota_exhausted)→ degraded=true / degrade_reason=quota_exhausted
- exit 4(timeout)→ degraded=true / degrade_reason=timeout
- exit 1 → degraded=true / degrade_reason=heygen_<code>
- intro 成功 outro 失败(或反之)→ 把成功那段写进 schema,degraded=true / degrade_reason=outro_missing(或 intro_missing)

# 输出 schema(最终一段 ```json``` 代码块)
{{
  "audio_segments": [],
  "subtitle_path": null,
  "intro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4",
    "duration": <intro 实际秒数,从 skill 返回的 duration 字段>,
    "text": "<本次喂 intro 的精简稿,完整保留>", "ok": true}},
  "outro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/outro.mp4",
    "duration": <outro 实际秒数>,
    "text": "<本次喂 outro 的精简稿,完整保留>", "ok": true}},
  "voice_style": "{style}",
  "tts_provider": "heygen-builtin",
  "tts_model": "{voice_id}",
  "video_provider": "{video_provider_label}",
  "video_model": "v2",
  "degraded": false,
  "degrade_reason": null
}}

# 重要:最终回复硬约束
最终回复必须以一段 ```json``` 代码块结尾,字段完整。任一段失败:把那段填 ok=false / path=null,degraded=true / degrade_reason 填准。"""


_DASHSCOPE_TEMPLATE = """# 数字人 Provider:万相 2.7(阿里 DashScope)
本次任务已为你挂载 **dashscope-video** skill,你必须用它生成两段数字人短视频。

## 当前任务
task_id: {task_id}
video_dir: data/outputs/{task_id}/video/
audience:  {audience}
style:     {style}

## 讲稿参考
{script_head}

## Narrations({narrations_count} 段)
```json
{narrations_json}
```

## 你必须做的 — 两段 5s 数字人视频

### Step 1: 先验证凭据
```bash
python3 .agents/skills/dashscope-video/scripts/dashscope_video.py --check
```
确认 `ok: true` 再继续。如果失败 → 直接输出 degraded JSON,不要编造视频文件。

### Step 2: 生成开场镜头 (intro_video)
取 narrations[0] 文本,精简到 15-25 字开场白。然后**真正执行**:
```bash
python3 .agents/skills/dashscope-video/scripts/dashscope_video.py \\
  --prompt "中国年轻职场{audience_role},身穿{style_dress},坐在干净明亮的现代会议室中景,自然微笑温暖地讲话,正面对镜头" \\
  --model {video_model} --duration 5 --resolution 720P --ratio 16:9 \\
  --output_dir /mnt/c/code/lobsterScan/data/outputs/{task_id}/video
```
等待 stdout 返回 JSON(内含 `path` 字段,轮询约 1-5 分钟),然后:
```bash
mv "<stdout 返回的 path>" /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4
```

### Step 3: 生成结尾镜头 (outro_video)
取 narrations[-1] 文本,精简到 15-25 字。同样执行脚本(prompt 可稍改,如"站起身平视镜头自信收尾"),拿到后:
```bash
mv "<stdout 返回的 path>" /mnt/c/code/lobsterScan/data/outputs/{task_id}/video/outro.mp4
```

## 关键规则
- **必须真正运行 Bash 命令**。不要跳过执行直接编写输出 JSON — backend 会验证文件是否存在
- 每段 5s,model 用 `{video_model}`,不要换
- 视频无音(数字人嘴动不对口型没关系,用中景避开特写嘴部)
- TTS / 录屏你不需要管,backend 自动处理
- 鉴权已由 backend 注入 env: DASHSCOPE_API_KEY

## 失败处理
脚本返回 `ok: false` 或非零退出码时:
- exit 2 → degrade_reason: "no_credentials"
- exit 4 → degrade_reason: "timeout"
- intro 成功 outro 失败 → 保留成功的,degraded: true

## 输出 schema(最终一段 ```json``` 代码块)
{{
  "audio_segments": [],
  "subtitle_path": null,
  "intro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/intro.mp4",
    "duration": 5,
    "text": "<15-25 字开场白,backend 用它跑 TTS 配音>",
    "ok": true}},
  "outro_video": {{"path": "/mnt/c/code/lobsterScan/data/outputs/{task_id}/video/outro.mp4",
    "duration": 5,
    "text": "<15-25 字结尾总结,backend 用它跑 TTS 配音>",
    "ok": true}},
  "voice_style": "{style}",
  "tts_provider": "minimax",
  "tts_model": "{tts_model}",
  "video_provider": "{video_provider_label}",
  "video_model": "{video_model}",
  "degraded": false,
  "degrade_reason": null
}}

**intro_video.text / outro_video.text 不能省**(backend 拿它跑配音)。
任一段失败 → 该段 ok=false / path=null,整体 degraded=true + degrade_reason 填准。"""


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
