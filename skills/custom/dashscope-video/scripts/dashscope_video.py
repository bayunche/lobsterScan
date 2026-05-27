#!/usr/bin/env python3
"""DashScope 万相 文生视频 CLI

Usage:
  python3 dashscope_video.py --prompt "..." --output_dir ./output [--model wan2.7-t2v-2026-04-25] [--duration 5] [--resolution 720P]
  python3 dashscope_video.py --task_id <id> --download --output_dir ./output
  python3 dashscope_video.py --check

API key via env DASHSCOPE_API_KEY.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

API_BASE = "https://dashscope.aliyuncs.com"
SUBMIT_URL = f"{API_BASE}/api/v1/services/aigc/video-generation/video-synthesis"
TASK_URL = f"{API_BASE}/api/v1/tasks"
POLL_INTERVAL = 15
POLL_MAX = 40  # 10 minutes

def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)

def api_key():
    k = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not k:
        emit({"ok": False, "error": "DASHSCOPE_API_KEY not set"}, 2)
    return k

def _req(method, url, data=None):
    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        try:
            detail = json.loads(body)
        except Exception:
            detail = {"raw": body[:500]}
        emit({"ok": False, "error": "http_error", "status": e.code, "detail": detail}, 1)

def submit(args):
    payload = {
        "model": args.model,
        "input": {"prompt": args.prompt},
        "parameters": {
            "resolution": args.resolution,
            "ratio": args.ratio,
            "duration": args.duration,
            "prompt_extend": True,
        },
    }
    if args.negative_prompt:
        payload["input"]["negative_prompt"] = args.negative_prompt

    resp = _req("POST", SUBMIT_URL, payload)
    task_id = (resp.get("output") or {}).get("task_id") or resp.get("task_id")
    if not task_id:
        emit({"ok": False, "error": "no_task_id", "response": resp}, 1)

    if args.no_wait:
        emit({"ok": True, "task_id": task_id, "model": args.model, "wait": False})

    return poll_and_download(task_id, args.output_dir)

def poll_and_download(task_id, output_dir):
    headers = {"Authorization": f"Bearer {api_key()}"}
    for i in range(POLL_MAX):
        req = urllib.request.Request(f"{TASK_URL}/{task_id}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            print(f"poll error: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)
            continue

        output = data.get("output") or {}
        status = output.get("task_status") or data.get("task_status", "")
        if status == "SUCCEEDED":
            video_url = output.get("video_url") or ""
            if not video_url:
                results = output.get("results") or output.get("videos") or []
                if results and isinstance(results, list):
                    video_url = results[0].get("url") or results[0].get("video_url") or ""
            if not video_url:
                emit({"ok": False, "error": "no_video_url", "task_id": task_id, "output": output}, 1)
            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, f"{task_id}.mp4")
            urllib.request.urlretrieve(video_url, out_path)
            size = os.path.getsize(out_path)
            emit({"ok": True, "task_id": task_id, "path": out_path, "bytes": size,
                  "model": data.get("model", ""), "video_url": video_url})
        elif status == "FAILED":
            msg = output.get("message") or output.get("task_status_msg") or ""
            emit({"ok": False, "error": "task_failed", "task_id": task_id, "message": msg}, 1)
        else:
            print(f"[{i+1}/{POLL_MAX}] status={status}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)
    emit({"ok": False, "error": "timeout", "task_id": task_id}, 4)

def check():
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    payload = {
        "model": "wan2.7-t2v-2026-04-25",
        "input": {"prompt": "test"},
        "parameters": {"resolution": "720P", "duration": 5},
    }
    req = urllib.request.Request(SUBMIT_URL, data=json.dumps(payload).encode(),
                                 headers={**headers, "X-DashScope-Async": "enable"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            task_id = (data.get("output") or {}).get("task_id")
            emit({"ok": True, "source": "dashscope_api_key", "task_id": task_id,
                  "hint": "DashScope API auth ok"})
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            emit({"ok": False, "error": "auth_failed", "status": e.code}, 2)
        body = e.read().decode() if e.fp else ""
        emit({"ok": True, "source": "dashscope_api_key",
              "hint": f"API reachable (HTTP {e.code}), key may be valid"})

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", help="Text prompt for video generation")
    p.add_argument("--model", default="wan2.7-t2v-2026-04-25")
    p.add_argument("--duration", type=int, default=5, choices=[5, 10])
    p.add_argument("--resolution", default="720P", choices=["480P", "720P"])
    p.add_argument("--ratio", default="16:9", choices=["16:9", "9:16", "1:1"])
    p.add_argument("--negative_prompt", default="")
    p.add_argument("--output_dir", default="./output")
    p.add_argument("--no_wait", action="store_true")
    p.add_argument("--task_id", help="Poll existing task")
    p.add_argument("--download", action="store_true")
    p.add_argument("--check", action="store_true")
    args = p.parse_args()

    if args.check:
        check()
    elif args.task_id:
        poll_and_download(args.task_id, args.output_dir)
    elif args.prompt:
        submit(args)
    else:
        p.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
