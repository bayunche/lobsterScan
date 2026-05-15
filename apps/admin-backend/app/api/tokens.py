"""管台 · Token 计费

V0：只暴露查询接口；写入由业务后端调用 POST /tokens/usage 上报。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from ..db import TokenUsage, db_session

router = APIRouter(prefix="/tokens", tags=["tokens"])


class UsageEvent(BaseModel):
    agent_id: str
    task_id: str | None = None
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd_micros: int = 0


@router.post("/usage")
async def report_usage(ev: UsageEvent) -> dict:
    with db_session() as s:
        s.add(TokenUsage(**ev.model_dump()))
    return {"ok": True}


@router.get("/summary")
async def summary(days: int = 7) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    with db_session() as s:
        total_prompt = s.execute(
            select(func.coalesce(func.sum(TokenUsage.prompt_tokens), 0)).where(TokenUsage.ts >= since)
        ).scalar_one()
        total_completion = s.execute(
            select(func.coalesce(func.sum(TokenUsage.completion_tokens), 0)).where(TokenUsage.ts >= since)
        ).scalar_one()
        total_cost = s.execute(
            select(func.coalesce(func.sum(TokenUsage.cost_usd_micros), 0)).where(TokenUsage.ts >= since)
        ).scalar_one()
        by_agent = s.execute(
            select(
                TokenUsage.agent_id,
                func.sum(TokenUsage.prompt_tokens + TokenUsage.completion_tokens),
                func.sum(TokenUsage.cost_usd_micros),
            )
            .where(TokenUsage.ts >= since)
            .group_by(TokenUsage.agent_id)
        ).all()
    return {
        "days": days,
        "total": {
            "prompt_tokens": int(total_prompt or 0),
            "completion_tokens": int(total_completion or 0),
            "cost_usd": (total_cost or 0) / 1_000_000,
        },
        "by_agent": [
            {"agent_id": a, "tokens": int(t or 0), "cost_usd": (c or 0) / 1_000_000}
            for a, t, c in by_agent
        ],
    }


@router.get("/recent")
async def recent(limit: int = 100) -> list[dict]:
    with db_session() as s:
        rows = s.execute(
            select(TokenUsage).order_by(TokenUsage.ts.desc()).limit(limit)
        ).scalars().all()
    return [
        {
            "ts": r.ts.isoformat(),
            "agent_id": r.agent_id,
            "task_id": r.task_id,
            "provider": r.provider,
            "model": r.model,
            "tokens": r.prompt_tokens + r.completion_tokens,
            "cost_usd": (r.cost_usd_micros or 0) / 1_000_000,
        }
        for r in rows
    ]


@router.post("/mock")
async def mock_usage(count: int = 24) -> dict:
    """批量灌入测试数据，让 Token 页有图表可看；接入真实链路前用."""
    import random
    from datetime import datetime, timedelta
    from ..db import audit
    agents = ["coordinator", "material", "point-extractor", "structure",
              "upward-opt", "copywriter", "html-designer", "video-producer", "reviewer"]
    models = [("anthropic", "claude-sonnet-4-6", 3),
              ("anthropic", "claude-opus-4-7", 15)]
    now = datetime.utcnow()
    with db_session() as s:
        for i in range(count):
            agent = random.choice(agents)
            provider, model, cost_per_m = random.choice(models)
            prompt = random.randint(800, 4000)
            completion = random.randint(200, 1500)
            # 粗算：3 美元/百万 tokens 的输入；输出按 2x 这个数字
            cost_micros = int((prompt * cost_per_m + completion * cost_per_m * 2))
            ts = now - timedelta(minutes=random.randint(0, 60 * 24 * 5))
            s.add(TokenUsage(
                ts=ts, agent_id=agent, task_id=f"tsk_mock_{i % 5}",
                provider=provider, model=model,
                prompt_tokens=prompt, completion_tokens=completion,
                cost_usd_micros=cost_micros,
            ))
    audit("tokens.mock", detail={"count": count})
    return {"ok": True, "inserted": count}


@router.delete("/all")
async def clear_all() -> dict:
    from sqlalchemy import delete
    from ..db import audit
    with db_session() as s:
        s.execute(delete(TokenUsage))
    audit("tokens.clear_all")
    return {"ok": True}
