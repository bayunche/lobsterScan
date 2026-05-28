"""T026 · 用户可见层脱敏自动扫描（宪章原则 I）

不允许在用户可见层（chat 气泡、SSE payload、API error message、导出文件名构造）
出现技术标识符字面量：message_id / artifact_version / harness_version。

本测试用 grep 的方式做白盒扫描；命中即 fail。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# 仓根：tests/orchestrator/ → ../../../../
REPO_ROOT = Path(__file__).resolve().parents[4]

# 禁词（出现在用户可见层即 fail）
FORBIDDEN_TOKENS = ("message_id", "artifact_version", "harness_version")

# 在 web 前端不允许出现的位置（用户可见层）
WEB_FRONTEND_DIRS = [
    REPO_ROOT / "apps" / "web-frontend" / "app",
    REPO_ROOT / "apps" / "web-frontend" / "components",
]


def _scan_dir(d: Path) -> list[tuple[Path, int, str, str]]:
    """返回 [(file, lineno, token, line)]。"""
    hits: list[tuple[Path, int, str, str]] = []
    if not d.is_dir():
        return hits
    for p in d.rglob("*"):
        if not p.is_file():
            continue
        # 只扫源码文件
        if p.suffix not in {".tsx", ".ts", ".jsx", ".js"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            # 跳过 import / 类型 / 注释 行（这些不构成"用户可见"）
            stripped = line.strip()
            if (stripped.startswith("//") or stripped.startswith("/*")
                    or stripped.startswith("*") or stripped.startswith("import ")
                    or stripped.startswith("type ") or stripped.startswith("interface ")
                    or stripped.startswith("export type ") or stripped.startswith("export interface ")):
                continue
            for tok in FORBIDDEN_TOKENS:
                if tok in line:
                    hits.append((p, i, tok, line.rstrip()))
    return hits


def test_web_frontend_no_forbidden_tokens():
    """web-frontend/app + components 内不出现 message_id / artifact_version / harness_version。"""
    all_hits: list[tuple[Path, int, str, str]] = []
    for d in WEB_FRONTEND_DIRS:
        all_hits.extend(_scan_dir(d))

    if all_hits:
        msg = "宪章原则 I 违规 — 用户可见层不应出现以下字面量:\n"
        for p, ln, tok, line in all_hits:
            try:
                rel = p.relative_to(REPO_ROOT)
            except ValueError:
                rel = p
            msg += f"  {rel}:{ln}  [{tok}]  {line[:120]}\n"
        pytest.fail(msg)


def test_v2_field_names_only_in_schemas_and_backend():
    """harness_version 字段在前端不暴露（应只在 backend 内部）。"""
    hits = []
    for d in WEB_FRONTEND_DIRS:
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in (".tsx", ".ts"):
                try:
                    if "harness_version" in p.read_text(encoding="utf-8"):
                        hits.append(p)
                except (UnicodeDecodeError, OSError):
                    pass
    assert not hits, f"harness_version 不应出现在 web-frontend: {hits}"
