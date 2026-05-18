#!/usr/bin/env python3
"""MiniMax TTS · 单段文本 → mp3 文件

实现优先级:
  1. 优先调本地 `mmx speech synthesize`(mmx-cli,user `mmx auth login` 已配 key)
  2. 失败回退到裸 HTTP(MINIMAX_API_KEY env)

Agent 调用示例:
  python3 synthesize.py --text "..." --voice male-qn-qingse --output 01.mp3

注意:mmx 默认 model 是 speech-2.8-hd(plan 面板 `speech-hd` 是简写,不是 speech-02-hd!)
HTTP fallback 用我们历史的 speech-02-hd 名字(api.minimaxi.com v1 接受)。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


# ─────────────────────────────────────────────────────────────
# mmx CLI 路径(首选)
# ─────────────────────────────────────────────────────────────


def _try_mmx(text: str, voice: str | None, model: str, output: Path,
             speed: float, with_subtitles: bool) -> dict | None:
    mmx = shutil.which("mmx")
    if not mmx:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [mmx, "speech", "synthesize",
           "--text", text,
           "--model", model,
           "--out", str(output),
           "--output", "json",
           "--non-interactive",
           "--no-color"]
    if voice:
        cmd += ["--voice", voice]
    if speed and abs(speed - 1.0) > 1e-6:
        cmd += ["--speed", str(speed)]
    if with_subtitles:
        cmd += ["--subtitles"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "mmx_timeout", "via": "mmx"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "mmx_invocation_failed", "detail": str(e)[:200], "via": "mmx"}
    if proc.returncode != 0:
        return {
            "ok": False, "error": "mmx_nonzero", "exit_code": proc.returncode,
            "stderr": (proc.stderr or "")[:400],
            "stdout": (proc.stdout or "")[:400],
            "via": "mmx",
        }
    if not output.exists():
        return {"ok": False, "error": "mmx_no_output_file", "via": "mmx"}
    duration = round(len(text) / 4.5, 2)
    return {
        "ok": True,
        "path": str(output),
        "bytes": output.stat().st_size,
        "duration_estimate": duration,
        "voice": voice,
        "model": model,
        "via": "mmx",
    }


# ─────────────────────────────────────────────────────────────
# HTTP fallback(MINIMAX_API_KEY 走 web-backend 双通道路由注入)
# ─────────────────────────────────────────────────────────────


def _http_synthesize(text: str, voice: str, model: str, output: Path,
                     speed: float, pitch: int, vol: float) -> dict:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        return {"ok": False, "error": "MINIMAX_API_KEY not set in env", "via": "http"}

    url = os.environ.get("MINIMAX_TTS_URL", "https://api.minimaxi.com/v1/t2a_v2")
    body = {
        "model": model,
        "text": text,
        "voice_setting": {"voice_id": voice, "speed": speed, "vol": vol, "pitch": pitch},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"},
    }
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http_{e.code}", "detail": e.read().decode()[:300], "via": "http"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "network", "detail": str(e), "via": "http"}

    base = data.get("base_resp", {})
    if base.get("status_code") != 0:
        return {
            "ok": False, "error": "minimax",
            "status_code": base.get("status_code"),
            "status_msg": base.get("status_msg"),
            "via": "http",
        }
    audio_hex = data.get("data", {}).get("audio")
    if not audio_hex:
        return {"ok": False, "error": "no_audio_in_response", "via": "http"}

    audio_bytes = bytes.fromhex(audio_hex)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio_bytes)
    duration = round(len(text) / 4.5, 2)
    return {
        "ok": True, "path": str(output), "bytes": len(audio_bytes),
        "duration_estimate": duration, "voice": voice, "model": model, "via": "http",
    }


def synthesize(*, text: str, voice: str, model: str, output: Path,
               speed: float = 1.0, pitch: int = 0, vol: float = 1.0,
               with_subtitles: bool = False) -> dict:
    """mmx 优先,失败回退 HTTP。"""
    r = _try_mmx(text, voice, model, output, speed, with_subtitles)
    if r is not None and r.get("ok"):
        return r
    # mmx 不在或失败 → HTTP fallback
    http = _http_synthesize(text, voice, model, output, speed, pitch, vol)
    if r is not None and not http.get("ok"):
        # mmx 也试过失败,聚合两边的错误信息便于排查
        http["mmx_first_attempt"] = r
    return http


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--voice", default="male-qn-qingse",
                   help="HTTP fallback 默认 male-qn-qingse;mmx 默认走 model 的 default voice")
    p.add_argument("--model", default="speech-2.8-hd",
                   help="mmx 默认 speech-2.8-hd;HTTP fallback 兼容 speech-02-hd 等")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--speed", default=1.0, type=float)
    p.add_argument("--subtitles", action="store_true", help="mmx 路径下额外产 subtitle 时序")
    args = p.parse_args()

    result = synthesize(
        text=args.text, voice=args.voice, model=args.model,
        output=args.output, speed=args.speed,
        with_subtitles=args.subtitles,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
