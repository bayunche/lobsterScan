#!/usr/bin/env python3
"""把多段 TTS 元数据拼成 SRT 字幕

输入: stdin 一行一段 JSON：
  {"index":1,"text":"...","duration_estimate":6.4}
输出: stdout SRT 内容
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def to_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def chunk(text: str, max_chars: int = 14) -> list[str]:
    """把一段文本切成 ≤max_chars 字的字幕条；优先在标点处切"""
    if not text:
        return []
    parts: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars and ch in "，。、！？；":
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts or [text]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, help="一行一 JSON 文件；不给则读 stdin")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    src = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    segments = []
    for line in src.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            segments.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    srt_lines: list[str] = []
    idx = 1
    cursor = 0.0
    for seg in segments:
        text = seg.get("text", "")
        dur = float(seg.get("duration_estimate") or seg.get("duration") or 0) or max(1.0, len(text) / 4.5)
        chunks = chunk(text)
        each = dur / len(chunks)
        for c in chunks:
            srt_lines += [
                str(idx),
                f"{to_ts(cursor)} --> {to_ts(cursor + each)}",
                c.strip(),
                "",
            ]
            idx += 1
            cursor += each

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(srt_lines), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(args.output),
                      "segments": idx - 1, "total_seconds": round(cursor, 2)},
                     ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
