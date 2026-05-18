#!/usr/bin/env python3
"""MiniMax image-01 · 文生图

实现优先级:
  1. 优先调本地 `mmx image generate`(mmx-cli)
  2. mmx 不可用 / 调用失败 → HTTP fallback(MINIMAX_API_KEY env)

Agent 调用示例:
  python3 generate.py --prompt "..." --aspect-ratio 16:9 --output cover.jpg
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

API_BASE = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com/v1")


def _try_mmx(prompt: str, aspect_ratio: str, n: int,
             output: Path | None, out_dir: Path | None,
             seed: int | None, prompt_optimizer: bool) -> dict | None:
    mmx = shutil.which("mmx")
    if not mmx:
        return None
    cmd = [mmx, "image", "generate",
           "--prompt", prompt,
           "--aspect-ratio", aspect_ratio,
           "--n", str(n),
           "--output", "json",
           "--non-interactive",
           "--no-color"]
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--out", str(output)]
    elif out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["--out-dir", str(out_dir)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if prompt_optimizer:
        cmd += ["--prompt-optimizer"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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
    if output and not output.exists():
        return {"ok": False, "error": "mmx_no_output_file", "via": "mmx"}
    meta: dict = {}
    try:
        meta = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "path": str(output) if output else None,
        "out_dir": str(out_dir) if out_dir else None,
        "bytes": output.stat().st_size if (output and output.exists()) else None,
        "n": n,
        "model": "image-01",
        "task_id": meta.get("task_id") or meta.get("id"),
        "via": "mmx",
    }


def _http_generate(prompt: str, aspect_ratio: str, n: int, output: Path | None) -> dict:
    """HTTP fallback — MiniMax 文生图 v1/image_generation。"""
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        return {"ok": False, "error": "MINIMAX_API_KEY missing", "via": "http"}
    body = {
        "model": "image-01",
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "n": n,
        "response_format": "url",
    }
    try:
        req = urllib.request.Request(
            f"{API_BASE}/image_generation", method="POST",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http_{e.code}", "detail": e.read().decode()[:300], "via": "http"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "network", "detail": str(e), "via": "http"}

    base = data.get("base_resp", {})
    if base.get("status_code") != 0:
        return {"ok": False, "error": "minimax", "status_code": base.get("status_code"),
                "status_msg": base.get("status_msg"), "via": "http"}
    image_urls = data.get("data", {}).get("image_urls") or []
    if not image_urls:
        return {"ok": False, "error": "no_image_urls", "via": "http"}
    if not output:
        return {"ok": True, "urls": image_urls, "n": len(image_urls), "model": "image-01", "via": "http"}

    # 下载第一张到 output
    try:
        with urllib.request.urlopen(image_urls[0], timeout=60) as r:
            img = r.read()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "download_failed", "detail": str(e), "via": "http"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(img)
    return {
        "ok": True, "path": str(output), "bytes": len(img), "n": 1,
        "model": "image-01", "via": "http",
        "extra_urls": image_urls[1:],
    }


def generate(*, prompt: str, aspect_ratio: str, n: int,
             output: Path | None, out_dir: Path | None,
             seed: int | None = None, prompt_optimizer: bool = False) -> dict:
    r = _try_mmx(prompt, aspect_ratio, n, output, out_dir, seed, prompt_optimizer)
    if r is not None and r.get("ok"):
        return r
    if r is not None:
        http = _http_generate(prompt, aspect_ratio, n, output)
        if not http.get("ok"):
            http["mmx_first_attempt"] = r
        return http
    return _http_generate(prompt, aspect_ratio, n, output)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--aspect-ratio", default="16:9", dest="aspect_ratio")
    p.add_argument("--n", default=1, type=int)
    p.add_argument("--output", default=None, type=Path)
    p.add_argument("--out-dir", default=None, type=Path, dest="out_dir")
    p.add_argument("--seed", default=None, type=int)
    p.add_argument("--prompt-optimizer", action="store_true", dest="prompt_optimizer")
    args = p.parse_args()
    if not args.output and not args.out_dir:
        print(json.dumps({"ok": False, "error": "must give --output or --out-dir"}), file=sys.stderr)
        return 2
    result = generate(
        prompt=args.prompt, aspect_ratio=args.aspect_ratio, n=args.n,
        output=args.output, out_dir=args.out_dir,
        seed=args.seed, prompt_optimizer=args.prompt_optimizer,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
