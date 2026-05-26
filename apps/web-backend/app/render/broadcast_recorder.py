"""Backend 自己用 playwright 录 broadcast.html → mp4

跟之前的 PIL slideshow_video.py 兜底质量完全不同:
- 真渲染 broadcast.html(印刷工艺设计 + stagger reveal + 数字 count-up + 印章 + 配准十字)
- inline base64 audio 在 chromium 里 autoplay,playwright video API 自动录帧
- 1920×1080,30fps,h264 + opus(playwright 默认 webm)→ ffmpeg 转 mp4 + 加音轨

外部依赖:
- playwright python(已 uv add)+ chromium binary(~/.cache/ms-playwright)
- imageio-ffmpeg(已有 — slideshow_video.py 用)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("broadcast_recorder")


async def _record_async(
    broadcast_path: Path,
    output_video: Path,
    width: int = 1920,
    height: int = 1080,
    extra_wait_sec: float = 2.0,
) -> dict:
    """playwright headless chromium 录 broadcast.html。

    流程:
      1. 启 chromium 1920×1080 + video=on
      2. file:// 打开 broadcast.html
      3. wait_for_function `window.__broadcastReady === true`
      4. 拿 `window.__broadcastTotalSec` 总时长
      5. 睡满 totalSec + extra_wait_sec
      6. close → playwright 自动把 webm 写到指定路径
      7. ffmpeg 转 mp4(playwright video 是 webm vp8 + 无音 — 后续再 mux 音轨)

    返回 {ok, path, duration, size_bytes, error?}
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        return {"ok": False, "error": f"playwright import failed: {e}"}

    output_video.parent.mkdir(parents=True, exist_ok=True)
    # 临时 webm 输出目录(playwright 会写一个 hash 文件名,我们后面 rename)
    webm_dir = output_video.parent / "_pw_video_tmp"
    webm_dir.mkdir(parents=True, exist_ok=True)

    total_sec = 0.0
    final_webm: Path | None = None
    err: str | None = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--autoplay-policy=no-user-gesture-required",  # 关键:允许 autoplay audio
                    f"--window-size={width},{height}",
                ],
            )
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                record_video_dir=str(webm_dir),
                record_video_size={"width": width, "height": height},
            )
            page = await context.new_page()
            url = broadcast_path.resolve().as_uri()
            log.info("playwright recording: %s", url)
            await page.goto(url, wait_until="domcontentloaded")
            # 等 broadcast 就绪信号
            try:
                await page.wait_for_function(
                    "window.__broadcastReady === true",
                    timeout=20000,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("broadcast not ready in 20s: %s", e)

            try:
                total_sec = float(await page.evaluate("() => window.__broadcastTotalSec || 0"))
            except Exception:  # noqa: BLE001
                total_sec = 0
            if total_sec <= 0:
                total_sec = 60.0  # 兜底 60s

            log.info("recording for %.1fs ...", total_sec + extra_wait_sec)
            await asyncio.sleep(total_sec + extra_wait_sec)

            # 关闭 context 才会 flush webm
            await context.close()
            await browser.close()

        # 找出 playwright 写的 webm 文件
        webms = sorted(webm_dir.glob("*.webm"))
        if not webms:
            return {"ok": False, "error": "no webm produced by playwright"}
        final_webm = webms[-1]
        log.info("webm written: %s (%d bytes)", final_webm, final_webm.stat().st_size)

    except Exception as e:  # noqa: BLE001
        log.exception("playwright record crashed: %s", e)
        return {"ok": False, "error": f"playwright crash: {str(e)[:300]}"}

    # ── webm → mp4(h264 + aac)· 同时 mux audio_segments
    # 但 playwright video 已经包含 chromium 渲染出的图,**音频也在里面**
    # (因为 chromium autoplay 了 inline audio,playwright 录的是整个 page output)
    # 实际上 playwright record_video 默认只录视频帧不含声卡音频 — 但 chromium 内部 audio
    # 直接路由到 frames,我们用 ffmpeg 看一下能不能拿到。
    try:
        ff = _ffmpeg_exe()
        # 直接转 mp4 — 看 webm 是不是带音轨
        cmd = [
            ff, "-y",
            "-i", str(final_webm),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(output_video),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            err = f"ffmpeg convert failed: {p.stderr[-300:]}"
            log.warning(err)
        else:
            log.info("mp4 written: %s (%d bytes)", output_video, output_video.stat().st_size)
    except Exception as e:  # noqa: BLE001
        err = f"ffmpeg convert exception: {e}"
        log.warning(err)

    # 清临时 webm
    try:
        shutil.rmtree(webm_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    if not output_video.exists() or output_video.stat().st_size < 50_000:
        return {"ok": False, "error": err or "output mp4 too small / missing",
                "duration": total_sec}

    return {
        "ok": True,
        "path": str(output_video),
        "kind": "playwright_recording",
        "duration": round(total_sec, 2),
        "size_bytes": output_video.stat().st_size,
        "width": width,
        "height": height,
        "has_audio": _probe_has_audio(output_video),
    }


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def _probe_has_audio(p: Path) -> bool:
    try:
        ff = _ffmpeg_exe()
        r = subprocess.run([ff, "-i", str(p)], capture_output=True, text=True, timeout=10)
        return "Audio:" in (r.stderr or "")
    except Exception:  # noqa: BLE001
        return False


def _mux_audio_into_video(video_in: Path, audio_concat: Path, video_out: Path) -> bool:
    """把外部 audio_concat 混进 video_in,输出 video_out。"""
    ff = _ffmpeg_exe()
    cmd = [
        ff, "-y", "-i", str(video_in), "-i", str(audio_concat),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest",
        str(video_out),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return p.returncode == 0 and video_out.exists() and video_out.stat().st_size > 50_000
    except Exception:  # noqa: BLE001
        return False


def _concat_audio_segments(audio_segments: list[dict], work_dir: Path) -> Path | None:
    """把 audio_segments 拼成一个 long aac · 没音的段填静默。返回最终文件路径,失败 None。"""
    if not audio_segments:
        return None
    ff = _ffmpeg_exe()
    work_dir.mkdir(parents=True, exist_ok=True)

    # 规范化 path(已经是 abs / 检查存在性)
    lines: list[str] = []
    for i, seg in enumerate(audio_segments):
        if not isinstance(seg, dict):
            continue
        p = (seg.get("path") or "").strip()
        ok = bool(seg.get("ok"))
        dur = float(seg.get("duration_estimate_sec") or seg.get("duration") or 6.0)
        if not ok or not p:
            silent = work_dir / f"_silent_{i:02d}.wav"
            subprocess.run(
                [ff, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-t", str(max(dur, 0.5)), str(silent)],
                capture_output=True, timeout=20,
            )
            lines.append(f"file '{silent.absolute()}'")
            continue
        pp = Path(p)
        if not pp.is_absolute():
            # 尝试 repo-root relative
            from app.config import settings  # avoid import cycle at module load
            candidate = settings.outputs_root.parent.parent / pp
            if candidate.exists():
                pp = candidate.resolve()
        if not pp.exists():
            silent = work_dir / f"_missing_{i:02d}.wav"
            subprocess.run(
                [ff, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-t", str(max(dur, 0.5)), str(silent)],
                capture_output=True, timeout=20,
            )
            lines.append(f"file '{silent.absolute()}'")
            continue
        lines.append(f"file '{pp.absolute()}'")

    if not lines:
        return None
    concat_list = work_dir / "_audio_list.txt"
    concat_list.write_text("\n".join(lines), encoding="utf-8")
    audio_out = work_dir / "_audio_concat.aac"
    r = subprocess.run(
        [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c:a", "aac", "-b:a", "128k", str(audio_out)],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        log.warning("audio concat failed: %s", (r.stderr or "")[-300:])
        return None
    return audio_out if audio_out.exists() else None


def record_broadcast(
    broadcast_path: Path,
    output_video: Path,
    audio_segments: list[dict] | None = None,
    width: int = 1920,
    height: int = 1080,
) -> dict:
    """同步包装。playwright 录 broadcast.html → mp4 + mux audio_segments。

    playwright record_video 只录视觉帧不录系统音频,所以 audio 用 audio_segments concat 后 mux。
    """
    # 1) playwright 录视觉
    try:
        try:
            asyncio.get_running_loop()
            from concurrent.futures import ThreadPoolExecutor

            def _runner():
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(
                        _record_async(broadcast_path, output_video, width, height)
                    )
                finally:
                    loop.close()

            with ThreadPoolExecutor(max_workers=1) as ex:
                visual_result = ex.submit(_runner).result(timeout=600)
        except RuntimeError:
            visual_result = asyncio.run(
                _record_async(broadcast_path, output_video, width, height)
            )
    except Exception as e:  # noqa: BLE001
        log.exception("record_broadcast visual crashed: %s", e)
        return {"ok": False, "error": f"runner crash: {str(e)[:300]}"}

    if not visual_result.get("ok"):
        return visual_result

    # 2) 如果视觉录的 mp4 已经带音轨 → 直接返回
    if visual_result.get("has_audio"):
        return visual_result

    # 3) 视觉无音 → 用 audio_segments concat 再 mux
    if not audio_segments:
        log.info("no audio_segments to mux; returning silent video")
        return visual_result

    work_dir = output_video.parent / "_pw_audio_tmp"
    audio_concat = _concat_audio_segments(audio_segments, work_dir)
    if not audio_concat or not audio_concat.exists():
        log.warning("audio concat failed; returning silent video")
        return visual_result

    muxed = output_video.parent / (output_video.stem + "_muxed.mp4")
    ok = _mux_audio_into_video(output_video, audio_concat, muxed)
    if not ok:
        log.warning("mux failed; returning silent video")
        return visual_result

    # 用 muxed 替换原 output
    try:
        output_video.unlink()
        muxed.rename(output_video)
    except Exception as e:  # noqa: BLE001
        log.warning("rename muxed back failed: %s", e)
        # fallback:留 muxed 路径
        return {**visual_result, "path": str(muxed), "has_audio": True,
                "size_bytes": muxed.stat().st_size}

    # 清理 tmp
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    return {
        **visual_result,
        "has_audio": True,
        "size_bytes": output_video.stat().st_size,
    }
