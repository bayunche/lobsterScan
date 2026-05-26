"""Backend 自带 TTS 兜底链 — 不依赖 agent,直接生成 audio_segments。

链路(从严到宽):
  1. mmx CLI:走 user TokenPlan plan 配额(免费/便宜)
  2. edge-tts:微软免费 TTS(网络可达即用)
  3. 静默 wav 占位(4s/段,标 ok=False)

入口 build_audio_segments(narrations, voice_pref, output_dir) → list[{path, duration, ok, via}]
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("tts_fallback")


# 中文 voice 默认值(mmx + edge-tts 各一套)
MMX_DEFAULT_VOICE = "male-qn-qingse"
MMX_DEFAULT_MODEL = "speech-2.8-hd"

EDGE_TTS_VOICES = {
    "male": "zh-CN-YunyangNeural",       # 沉稳男声,适合汇报
    "male-soft": "zh-CN-YunjianNeural",
    "female": "zh-CN-XiaoxiaoNeural",
    "female-pro": "zh-CN-XiaoyiNeural",
}


def _mmx_synthesize(text: str, output: Path, voice: str = MMX_DEFAULT_VOICE,
                    model: str = MMX_DEFAULT_MODEL, speed: float = 1.0) -> dict:
    """优先 mmx,失败返回 ok=False 让上层 fallback."""
    mmx = shutil.which("mmx")
    if not mmx:
        return {"ok": False, "error": "mmx_not_in_path", "via": "mmx"}
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [mmx, "speech", "synthesize",
           "--text", text, "--model", model, "--voice", voice,
           "--out", str(output),
           "--output", "json", "--non-interactive", "--no-color"]
    if abs(speed - 1.0) > 1e-6:
        cmd += ["--speed", str(speed)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "mmx_exception", "detail": str(e)[:200], "via": "mmx"}
    if p.returncode != 0:
        return {
            "ok": False, "error": "mmx_nonzero",
            "exit_code": p.returncode,
            "stderr": (p.stderr or "")[:300],
            "via": "mmx",
        }
    if not output.exists():
        return {"ok": False, "error": "mmx_no_file", "via": "mmx"}
    # mmx stdout 末尾 JSON 含 duration_ms
    duration = 0.0
    try:
        # mmx stdout 是表格 + json,找最后一个 {...}
        s = p.stdout
        start = s.rfind("{")
        if start >= 0:
            j = json.loads(s[start:])
            duration = round(j.get("duration_ms", 0) / 1000.0, 2)
    except Exception:  # noqa: BLE001
        pass
    if not duration:
        # 估算:中文 ≈ 4.5 字/秒
        duration = round(len(text) / 4.5, 2)
    return {"ok": True, "path": str(output), "duration": duration,
            "bytes": output.stat().st_size, "via": "mmx"}


async def _edge_tts_async(text: str, output: Path,
                          voice: str = "zh-CN-YunyangNeural") -> dict:
    """edge-tts:免费,需网络。"""
    try:
        import edge_tts
    except ImportError:
        return {"ok": False, "error": "edge_tts_not_installed", "via": "edge-tts"}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "edge_tts_exception",
                "detail": str(e)[:200], "via": "edge-tts"}
    if not output.exists() or output.stat().st_size < 1000:
        return {"ok": False, "error": "edge_tts_empty_output", "via": "edge-tts"}
    duration = round(len(text) / 4.5, 2)
    return {"ok": True, "path": str(output), "duration": duration,
            "bytes": output.stat().st_size, "via": "edge-tts"}


def _edge_tts(text: str, output: Path, voice: str) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_edge_tts_async(text, output, voice))
    # 已经在事件循环里(pipeline.execute 是 async)— 在独立线程跑新 loop
    from concurrent.futures import ThreadPoolExecutor

    def _runner() -> dict:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_edge_tts_async(text, output, voice))
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_runner).result()


def synthesize_one(text: str, output: Path, *,
                   prefer_voice_kind: str = "male") -> dict:
    """单段合成 — mmx 优先,失败 edge-tts 兜底。

    prefer_voice_kind: "male" | "male-soft" | "female" | "female-pro"
    """
    # 第一腿:mmx
    r = _mmx_synthesize(text, output)
    if r.get("ok"):
        return r
    log.warning("mmx fail for narration (text=%d chars): %s. Falling back to edge-tts.",
                len(text), r.get("error"))

    # 第二腿:edge-tts
    voice = EDGE_TTS_VOICES.get(prefer_voice_kind, EDGE_TTS_VOICES["male"])
    e = _edge_tts(text, output, voice)
    if e.get("ok"):
        e["mmx_first_attempt"] = r
        return e

    return {
        "ok": False,
        "error": "all_tts_failed",
        "mmx_attempt": r,
        "edge_attempt": e,
    }


def build_audio_segments(narrations: list[dict], output_dir: Path,
                         prefer_voice_kind: str = "male") -> list[dict]:
    """对每条 narration 跑 TTS,返回 audio_segments list。

    返回结构:
      [{"index":1, "text":"...", "path":"01.mp3",
        "duration_estimate_sec":6.4, "via":"mmx", "ok":true}, ...]

    全失败的段 ok=false / path=None,build_slideshow 会用静默占位。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    segments: list[dict] = []
    for i, n in enumerate(narrations, 1):
        text = (n.get("text") or "").strip()
        if not text:
            segments.append({"index": i, "text": "", "path": None,
                             "ok": False, "error": "empty_text"})
            continue
        out = output_dir / f"{i:02d}.mp3"
        r = synthesize_one(text, out, prefer_voice_kind=prefer_voice_kind)
        seg = {
            "index": i,
            "text": text,
            "chapter": n.get("chapter"),
            "step": n.get("step"),
            "path": r.get("path") if r.get("ok") else None,
            "duration_estimate_sec": r.get("duration", 0),
            "via": r.get("via"),
            "ok": r.get("ok", False),
        }
        if not r.get("ok"):
            seg["error"] = r.get("error")
        segments.append(seg)
    return segments
