"""把 token 用量上报到管台"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("openclaw.tokens")

ADMIN_BASE = "http://127.0.0.1:8100"


async def report_usage(
    *,
    agent_id: str,
    task_id: str | None,
    provider: str | None,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """fire-and-forget；上报失败不影响业务."""
    if not (prompt_tokens or completion_tokens):
        return
    # 极粗成本估算（micros = 1e-6 USD）—— 真实定价后续接 provider 单价表
    cost_per_million = {"anthropic": 3, "deepseek": 0.3, "minimax": 0.5, "openai": 5}.get(provider or "", 1)
    cost_micros = int(
        (prompt_tokens * cost_per_million) + (completion_tokens * cost_per_million * 2)
    )
    payload = {
        "agent_id": agent_id,
        "task_id": task_id,
        "provider": provider or "unknown",
        "model": model or "unknown",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd_micros": cost_micros,
    }
    try:
        async with httpx.AsyncClient(timeout=3) as cli:
            await cli.post(f"{ADMIN_BASE}/admin/api/tokens/usage", json=payload)
    except Exception as e:  # noqa: BLE001
        log.warning("token usage report failed: %s", e)
