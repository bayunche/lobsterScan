#!/usr/bin/env python3
"""MiniMax music · 音乐生成

mmx CLI 优先(`mmx music generate`),失败时也无 HTTP fallback(mmx 是唯一公开
渠道,直接 raw HTTP 用法 MiniMax 文档暂未稳定开放)。

Agent 调用示例:
  python3 generate.py --prompt "cinematic ambient" --instrumental --output bgm.mp3
  python3 generate.py --prompt "uplifting pop" --lyrics "[Verse]..." --output song.mp3
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _try_mmx(prompt: str, model: str, output: Path,
             lyrics: str | None, lyrics_file: Path | None,
             lyrics_optimizer: bool, instrumental: bool,
             vocals: str | None, genre: str | None, mood: str | None,
             bpm: int | None, audio_format: str) -> dict:
    mmx = shutil.which("mmx")
    if not mmx:
        return {"ok": False, "error": "mmx_not_found", "hint": "npm i -g mmx-cli && mmx auth login"}
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [mmx, "music", "generate",
           "--prompt", prompt,
           "--model", model,
           "--out", str(output),
           "--format", audio_format,
           "--output", "json",
           "--non-interactive",
           "--no-color"]
    # 歌词来源(四选一)
    if lyrics:
        cmd += ["--lyrics", lyrics]
    elif lyrics_file:
        cmd += ["--lyrics-file", str(lyrics_file)]
    elif lyrics_optimizer:
        cmd += ["--lyrics-optimizer"]
    elif instrumental:
        cmd += ["--instrumental"]
    else:
        return {"ok": False, "error": "need_lyrics_source",
                "hint": "至少给一个: --lyrics / --lyrics-file / --lyrics-optimizer / --instrumental"}
    if vocals:  cmd += ["--vocals", vocals]
    if genre:   cmd += ["--genre", genre]
    if mood:    cmd += ["--mood", mood]
    if bpm:     cmd += ["--bpm", str(bpm)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "mmx_timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "mmx_invocation_failed", "detail": str(e)[:200]}
    if proc.returncode != 0:
        return {
            "ok": False, "error": "mmx_nonzero", "exit_code": proc.returncode,
            "stderr": (proc.stderr or "")[:400],
            "stdout": (proc.stdout or "")[:400],
        }
    if not output.exists():
        return {"ok": False, "error": "mmx_no_output_file"}
    return {
        "ok": True,
        "path": str(output),
        "bytes": output.stat().st_size,
        "model": model,
        "via": "mmx",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    # music-2.6-free 需要 Max plan;基础 plan 用 music-2.6(plan 配额 100/day, 700/week)
    p.add_argument("--model", default="music-2.6",
                   help="music-2.6(default,plan 周配额)/music-2.6-free(需 Max plan)/music-2.5+/music-2.5")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--lyrics", default=None)
    p.add_argument("--lyrics-file", default=None, type=Path, dest="lyrics_file")
    p.add_argument("--lyrics-optimizer", action="store_true", dest="lyrics_optimizer")
    p.add_argument("--instrumental", action="store_true")
    p.add_argument("--vocals", default=None)
    p.add_argument("--genre", default=None)
    p.add_argument("--mood", default=None)
    p.add_argument("--bpm", default=None, type=int)
    p.add_argument("--format", default="mp3", dest="audio_format")
    args = p.parse_args()

    result = _try_mmx(
        prompt=args.prompt, model=args.model, output=args.output,
        lyrics=args.lyrics, lyrics_file=args.lyrics_file,
        lyrics_optimizer=args.lyrics_optimizer, instrumental=args.instrumental,
        vocals=args.vocals, genre=args.genre, mood=args.mood, bpm=args.bpm,
        audio_format=args.audio_format,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
