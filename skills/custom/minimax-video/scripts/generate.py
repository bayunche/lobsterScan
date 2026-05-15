#!/usr/bin/env python3
"""MiniMax Hailuo 视频生成 · 单段 prompt → mp4 文件

Agent 通过 Bash 调用：
  python3 generate.py --prompt "..." --duration 6 --output xx.mp4

API key 通过 MINIMAX_API_KEY 环境变量注入。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com/v1")
POLL_INTERVAL = 8
POLL_MAX = 35          # 35 * 8s = 280s ≈ 5 min


def _http_json(method: str, url: str, key: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _http_get(url: str, key: str | None) -> bytes:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def generate(
    *,
    prompt: str,
    duration: int,
    resolution: str,
    model: str,
    output: Path,
) -> dict:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        return {"ok": False, "error": "MINIMAX_API_KEY missing"}

    # 1. 提交生成任务
    body = {
        "model": model,
        "prompt": prompt,
        "prompt_optimizer": True,
        "duration": duration,
        "resolution": resolution,
    }
    try:
        submit = _http_json("POST", f"{API_BASE}/video_generation", key, body)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http_{e.code}", "detail": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "submit_network", "detail": str(e)}

    base = submit.get("base_resp", {})
    if base.get("status_code") != 0:
        return {
            "ok": False,
            "error": "minimax_submit",
            "status_code": base.get("status_code"),
            "status_msg": base.get("status_msg"),
        }

    task_id = submit.get("task_id")
    if not task_id:
        return {"ok": False, "error": "no_task_id"}

    # 2. 轮询
    file_id: str | None = None
    for _ in range(POLL_MAX):
        time.sleep(POLL_INTERVAL)
        try:
            q = _http_json("GET", f"{API_BASE}/query/video_generation?task_id={task_id}", key)
        except Exception:  # noqa: BLE001
            continue
        status = q.get("status")
        if status == "Success":
            file_id = q.get("file_id")
            break
        if status == "Fail":
            return {"ok": False, "error": "minimax_fail", "task_id": task_id}

    if not file_id:
        return {"ok": False, "error": "timeout", "task_id": task_id}

    # 3. 取文件 URL
    try:
        fr = _http_json("GET", f"{API_BASE}/files/retrieve?file_id={file_id}", key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "retrieve_failed", "detail": str(e)}

    download_url = fr.get("file", {}).get("download_url")
    if not download_url:
        return {"ok": False, "error": "no_download_url"}

    # 4. 下载 mp4
    try:
        data = _http_get(download_url, key=None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "download_failed", "detail": str(e)}

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)

    return {
        "ok": True,
        "path": str(output),
        "bytes": len(data),
        "task_id": task_id,
        "duration": duration,
        "model": model,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--duration", default=6, type=int)
    p.add_argument("--resolution", default="768P")
    p.add_argument("--model", default="MiniMax-Hailuo-02")
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    result = generate(
        prompt=args.prompt, duration=args.duration,
        resolution=args.resolution, model=args.model,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
