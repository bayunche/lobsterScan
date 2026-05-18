#!/usr/bin/env python3
"""MiniMax Hailuo 视频生成 · 单段 prompt → mp4 文件

Agent 通过 Bash 调用:
  python3 generate.py --prompt "..." --output xx.mp4 [--model MiniMax-Hailuo-2.3-Fast]

实现优先级:
  1. 优先调本地 `mmx` CLI(mmx-cli npm 包,用户 `mmx auth login` 后免传 key)
     mmx 自己处理:auth / model ID 转换 / TokenPlan vs PAYG 路由 / 错误码
  2. mmx 不在 PATH 或调用失败 → 回退到裸 HTTP(MINIMAX_API_KEY env)

注意 API model ID 跟 plan 面板的计量名不一样:
  - 真 API ID:MiniMax-Hailuo-2.3 / MiniMax-Hailuo-2.3-Fast / MiniMax-Hailuo-02 / S2V-01
  - plan 面板:MiniMax-Hailuo-2.3-Fast-6s-768p(把 model+duration+resolution 拼在一起作计费 metric)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com/v1")
POLL_INTERVAL = 8
POLL_MAX = 35          # 35 * 8s = 280s ≈ 5 min


# ─────────────────────────────────────────────────────────────
# mmx CLI 路径(首选)
# ─────────────────────────────────────────────────────────────


def _try_mmx(prompt: str, model: str, output: Path,
             first_frame: str | None = None, last_frame: str | None = None,
             subject_image: str | None = None) -> dict | None:
    """优先用 mmx CLI 调用;返回 None 表示 mmx 不可用,调用方走 HTTP fallback。

    mmx auto-switches model:
      - 默认 T2V 用 MiniMax-Hailuo-2.3
      - --first-frame → I2V(可选 fast),没 --last-frame 时
      - --first-frame + --last-frame → SEF(强制 MiniMax-Hailuo-02)
      - --subject-image → S2V-01
    """
    mmx = shutil.which("mmx")
    if not mmx:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [mmx, "video", "generate",
           "--prompt", prompt,
           "--model", model,
           "--download", str(output),
           "--output", "json",
           "--non-interactive",
           "--no-color"]
    if first_frame:
        cmd += ["--first-frame", first_frame]
    if last_frame:
        cmd += ["--last-frame", last_frame]
    if subject_image:
        cmd += ["--subject-image", subject_image]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "mmx_timeout", "via": "mmx"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "mmx_invocation_failed", "detail": str(e)[:200], "via": "mmx"}

    if proc.returncode != 0:
        # mmx 用 stderr 给错;把它带回给 agent
        return {
            "ok": False,
            "error": "mmx_nonzero",
            "exit_code": proc.returncode,
            "stderr": (proc.stderr or "")[:400],
            "stdout": (proc.stdout or "")[:400],
            "via": "mmx",
        }

    # mmx --output json 时 stdout 是 task 元数据;mp4 已经 --download 落盘了
    meta: dict = {}
    try:
        meta = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        pass

    if not output.exists():
        return {"ok": False, "error": "mmx_no_output_file", "via": "mmx", "stdout": proc.stdout[:300]}

    return {
        "ok": True,
        "path": str(output),
        "bytes": output.stat().st_size,
        "task_id": meta.get("task_id") or meta.get("id"),
        "model": model,
        "via": "mmx",
    }


# ─────────────────────────────────────────────────────────────
# HTTP fallback(MINIMAX_API_KEY 走 web-backend 双通道路由注入)
# ─────────────────────────────────────────────────────────────


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


def _http_generate(prompt: str, duration: int, resolution: str, model: str, output: Path) -> dict:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        return {"ok": False, "error": "MINIMAX_API_KEY missing", "via": "http"}

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
        return {"ok": False, "error": f"http_{e.code}", "detail": e.read().decode()[:300], "via": "http"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "submit_network", "detail": str(e), "via": "http"}

    base = submit.get("base_resp", {})
    if base.get("status_code") != 0:
        return {
            "ok": False, "error": "minimax_submit",
            "status_code": base.get("status_code"),
            "status_msg": base.get("status_msg"),
            "via": "http",
        }

    task_id = submit.get("task_id")
    if not task_id:
        return {"ok": False, "error": "no_task_id", "via": "http"}

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
            return {"ok": False, "error": "minimax_fail", "task_id": task_id, "via": "http"}

    if not file_id:
        return {"ok": False, "error": "timeout", "task_id": task_id, "via": "http"}

    try:
        fr = _http_json("GET", f"{API_BASE}/files/retrieve?file_id={file_id}", key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "retrieve_failed", "detail": str(e), "via": "http"}

    download_url = fr.get("file", {}).get("download_url")
    if not download_url:
        return {"ok": False, "error": "no_download_url", "via": "http"}

    try:
        data = _http_get(download_url, key=None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "download_failed", "detail": str(e), "via": "http"}

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)

    return {
        "ok": True,
        "path": str(output),
        "bytes": len(data),
        "task_id": task_id,
        "duration": duration,
        "model": model,
        "via": "http",
    }


def generate(*, prompt: str, duration: int, resolution: str, model: str, output: Path,
             first_frame: str | None = None, last_frame: str | None = None,
             subject_image: str | None = None) -> dict:
    """mmx 优先;mmx 不可用或失败 → HTTP fallback。"""
    r = _try_mmx(prompt, model, output,
                 first_frame=first_frame, last_frame=last_frame,
                 subject_image=subject_image)
    if r is not None and r.get("ok"):
        return r
    if r is not None:
        # mmx 在但失败 — agent 能看到 mmx 的错误码,顺手保留;继续走 HTTP fallback 试一次
        mmx_err = r
        http = _http_generate(prompt, duration, resolution, model, output)
        if http.get("ok"):
            http["mmx_fallback_reason"] = mmx_err
        return http
    # mmx 完全不在
    return _http_generate(prompt, duration, resolution, model, output)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True)
    p.add_argument("--duration", default=6, type=int, help="HTTP fallback 用;mmx 走 model 默认")
    p.add_argument("--resolution", default="768P", help="HTTP fallback 用")
    # mmx 接受的真 API model ID(plan 面板的 -6s-768p 是计费 metric,不是 API id):
    #   T2V(纯文本→视频)= MiniMax-Hailuo-2.3
    #   I2V(图→视频)   = MiniMax-Hailuo-2.3 / MiniMax-Hailuo-2.3-Fast(更便宜,要 --first-frame)
    #   SEF(首尾帧)    = MiniMax-Hailuo-02 (要 --first-frame + --last-frame)
    #   S2V(主体一致)  = S2V-01 (要 --subject-image)
    # 默认走 T2V 路径(汇报开场镜头不需要参考图)
    p.add_argument("--model", default="MiniMax-Hailuo-2.3",
                   help="MiniMax-Hailuo-2.3(T2V) / MiniMax-Hailuo-2.3-Fast(I2V) / MiniMax-Hailuo-02(SEF) / S2V-01")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--first-frame", default=None, dest="first_frame")
    p.add_argument("--last-frame", default=None, dest="last_frame")
    p.add_argument("--subject-image", default=None, dest="subject_image")
    args = p.parse_args()

    result = generate(
        prompt=args.prompt, duration=args.duration,
        resolution=args.resolution, model=args.model,
        output=args.output,
        first_frame=args.first_frame, last_frame=args.last_frame,
        subject_image=args.subject_image,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
