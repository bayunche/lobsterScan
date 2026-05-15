#!/usr/bin/env python3
"""MiniMax TTS · 单段文本 → mp3 文件

Agent 通过 Bash 调用本脚本完成一段合成。API key 通过环境变量 MINIMAX_API_KEY 注入。

Usage:
  python3 synthesize.py \
    --text "..." \
    --voice male-qn-qingse \
    --model speech-02-hd \
    --output data/outputs/<task>/audio/01.mp3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def synthesize(
    *,
    text: str,
    voice: str,
    model: str,
    output: Path,
    speed: float = 1.0,
    pitch: int = 0,
    vol: float = 1.0,
) -> dict:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        return {"ok": False, "error": "MINIMAX_API_KEY not set in env"}

    url = os.environ.get("MINIMAX_TTS_URL", "https://api.minimaxi.com/v1/t2a_v2")
    body = {
        "model": model,
        "text": text,
        "voice_setting": {
            "voice_id": voice, "speed": speed, "vol": vol, "pitch": pitch,
        },
        "audio_setting": {
            "sample_rate": 32000, "bitrate": 128000, "format": "mp3",
        },
    }
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http_{e.code}", "detail": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "network", "detail": str(e)}

    base = data.get("base_resp", {})
    if base.get("status_code") != 0:
        return {
            "ok": False,
            "error": "minimax",
            "status_code": base.get("status_code"),
            "status_msg": base.get("status_msg"),
        }

    audio_hex = data.get("data", {}).get("audio")
    if not audio_hex:
        return {"ok": False, "error": "no_audio_in_response"}

    audio_bytes = bytes.fromhex(audio_hex)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio_bytes)

    # 时长粗估：中文 4.5 字/秒
    duration = round(len(text) / 4.5, 2)

    return {
        "ok": True,
        "path": str(output),
        "bytes": len(audio_bytes),
        "duration_estimate": duration,
        "voice": voice,
        "model": model,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--voice", default="male-qn-qingse")
    p.add_argument("--model", default="speech-02-hd")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--speed", default=1.0, type=float)
    args = p.parse_args()

    result = synthesize(
        text=args.text, voice=args.voice, model=args.model,
        output=args.output, speed=args.speed,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
