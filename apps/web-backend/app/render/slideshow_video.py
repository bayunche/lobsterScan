"""无数字人降级 — 把 slides + TTS audio 拼成 1920x1080 mp4

调用场景:video_production 拿到 degraded=true,但 audio_segments 不为空
(TTS 跑通了,只是数字人 fail)。此时不应该只产 SRT,而是拼一个
"slides 翻页 + 配音" 的 mp4,作为非数字人的视频降级产物。

如果 audio_segments 也是空(纯字幕模式),直接走默认 stub,不进这里。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("slideshow")

W, H = 1920, 1080
FPS = 24
BG = (247, 244, 238)        # 跟 html_builder 主色一致
INK = (26, 26, 31)
INK_SOFT = (90, 90, 102)
ACCENT = (198, 84, 60)
LINE = (230, 225, 214)


def _load_font(size: int):
    """优先 Noto Sans SC,fallback 链覆盖 WSL / Linux / Mac / Win."""
    from PIL import ImageFont
    import os

    home = os.path.expanduser("~")
    candidates = [
        # 用户自己装的字体 (WSL 常见配置)
        f"{home}/.fonts/NotoSansSC-VF.ttf",
        f"{home}/.fonts/msyh.ttc",
        f"{home}/.fonts/simhei.ttf",
        f"{home}/.fonts/simsun.ttc",
        # 系统级 Linux Noto / WenQuanYi
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        # Mac
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        # WSL Win 共享字体 (.ttf 比 .ttc 更稳)
        "/mnt/c/Windows/Fonts/simhei.ttf",
        "/mnt/c/Windows/Fonts/msyh.ttc",
        # 拉丁字符 fallback (没中文支持但至少不崩)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _wrap_lines(draw, text: str, font, max_width: int) -> list[str]:
    """按字符切行 — 中文按字宽度,不切英文单词内部."""
    out: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        w = draw.textbbox((0, 0), trial, font=font)[2]
        if w > max_width and cur:
            out.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def _render_slide(slide: dict, total: int, page_no: int):
    """渲染单页 slide 为 1920x1080 PIL Image."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 顶部 accent 细条
    d.rectangle([(0, 0), (W, 6)], fill=ACCENT)
    # 底部 page indicator
    d.text((W - 200, H - 50), f"{page_no} / {total}", fill=INK_SOFT, font=_load_font(28))

    title = (slide.get("title") or "").strip()
    stype = (slide.get("type") or "").strip()
    content = slide.get("content") or []

    # 标题(大字)
    title_font = _load_font(72)
    title_lines = _wrap_lines(d, title, title_font, W - 240)
    y = 200
    for line in title_lines[:2]:
        d.text((120, y), line, fill=INK, font=title_font)
        y += 90

    # 副线
    d.rectangle([(120, y + 20), (320, y + 24)], fill=ACCENT)
    y += 80

    # content 列表
    body_font = _load_font(44)
    for item in content[:6]:  # 单页最多 6 条
        if not item:
            continue
        lines = _wrap_lines(d, "· " + str(item), body_font, W - 280)
        for line in lines[:2]:
            if y > H - 120:
                break
            d.text((140, y), line, fill=INK, font=body_font)
            y += 64
        y += 16  # 段间距

    # 类型角标
    if stype:
        d.text((120, 140), f"# {stype}", fill=ACCENT, font=_load_font(28))

    return img


def _slide_duration_for_audio(audio_path: str) -> float:
    """读 mp3/wav 实际时长(秒)."""
    try:
        import imageio_ffmpeg
        import subprocess
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        # ffprobe 不在 imageio-ffmpeg,用 ffmpeg -i 解析
        out = subprocess.run(
            [ffmpeg, "-i", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        # ffmpeg 把 Duration 写到 stderr
        for line in out.stderr.splitlines():
            line = line.strip()
            if line.startswith("Duration:"):
                # Duration: 00:00:06.43, start: ...
                t = line.split(",")[0].replace("Duration:", "").strip()
                hh, mm, ss = t.split(":")
                return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception as e:  # noqa: BLE001
        log.warning("probe duration failed for %s: %s", audio_path, e)
    return 6.0  # fallback


def build_slideshow(
    *,
    task_id: str,
    slides: list[dict],
    audio_segments: list[dict],
    output_dir: Path,
) -> dict[str, Any]:
    """把 slides + audio_segments 拼成一个 mp4。

    audio_segments 顺序对应 slides 顺序;数量不一致时取 min;
    每页时长 = 对应音频时长;无音频的 slide 用 4 秒静默占位。

    返回 {"ok": True, "path": <mp4>, "duration": <total_sec>, "pages": n}
    或 {"ok": False, "error": "..."}
    """
    try:
        import imageio
        import numpy as np
        import subprocess
        import imageio_ffmpeg
    except ImportError as e:
        return {"ok": False, "error": f"deps_missing: {e}"}

    if not slides:
        return {"ok": False, "error": "no_slides"}

    output_dir.mkdir(parents=True, exist_ok=True)
    video_only = output_dir / "slideshow_silent.mp4"
    final_mp4 = output_dir / "slideshow.mp4"

    # 1) 渲染每页 PNG
    images = []
    durations = []
    n_pages = len(slides)
    audio_paths: list[str | None] = []
    for i, slide in enumerate(slides):
        img = _render_slide(slide, n_pages, i + 1)
        images.append(np.array(img))

        # 找对应音频
        if i < len(audio_segments):
            ap = audio_segments[i].get("path")
            if ap and Path(ap).exists():
                dur = _slide_duration_for_audio(ap)
                audio_paths.append(ap)
            else:
                dur = 4.0
                audio_paths.append(None)
        else:
            dur = 4.0
            audio_paths.append(None)
        durations.append(dur)

    # 2) 写无声视频(每页持续 N 帧)
    writer = imageio.get_writer(video_only, fps=FPS, codec="libx264", quality=8)
    for img_arr, dur in zip(images, durations):
        n_frames = max(1, int(dur * FPS))
        for _ in range(n_frames):
            writer.append_data(img_arr)
    writer.close()

    # 3) 合并音频:用 ffmpeg concat audio,然后 mux 到视频
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    audio_files = [p for p in audio_paths if p]
    if not audio_files:
        # 没音频 → 直接 rename 无声视频为最终产物
        video_only.rename(final_mp4)
        return {
            "ok": True, "path": str(final_mp4),
            "duration": sum(durations), "pages": n_pages,
            "has_audio": False,
        }

    # ffmpeg concat audio
    concat_list = output_dir / "_audio_list.txt"
    lines = []
    for ap, dur in zip(audio_paths, durations):
        if ap:
            lines.append(f"file '{Path(ap).absolute()}'")
        else:
            # 无音频段:生成静默 wav 用 ffmpeg
            silent_wav = output_dir / f"_silent_{durations.index(dur)}.wav"
            subprocess.run(
                [ffmpeg, "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                 "-t", str(dur), str(silent_wav)],
                capture_output=True, timeout=20,
            )
            lines.append(f"file '{silent_wav.absolute()}'")
    concat_list.write_text("\n".join(lines), encoding="utf-8")

    audio_concat = output_dir / "_audio_concat.aac"
    r1 = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c:a", "aac", "-b:a", "128k", str(audio_concat)],
        capture_output=True, text=True, timeout=120,
    )
    if r1.returncode != 0:
        log.warning("audio concat failed: %s", r1.stderr[-500:])
        # 降级到无声
        final_mp4.write_bytes(video_only.read_bytes())
        return {"ok": True, "path": str(final_mp4),
                "duration": sum(durations), "pages": n_pages,
                "has_audio": False, "warning": "audio_concat_failed"}

    # 4) Mux video + audio
    r2 = subprocess.run(
        [ffmpeg, "-y", "-i", str(video_only), "-i", str(audio_concat),
         "-c:v", "copy", "-c:a", "aac", "-shortest", str(final_mp4)],
        capture_output=True, text=True, timeout=120,
    )
    if r2.returncode != 0:
        log.warning("mux failed: %s", r2.stderr[-500:])
        return {"ok": False, "error": "mux_failed", "detail": r2.stderr[-300:]}

    # 清理中间文件
    for p in [video_only, audio_concat, concat_list]:
        try: p.unlink()
        except Exception: pass
    for p in output_dir.glob("_silent_*.wav"):
        try: p.unlink()
        except Exception: pass

    return {
        "ok": True, "path": str(final_mp4),
        "duration": sum(durations), "pages": n_pages,
        "has_audio": True,
    }
