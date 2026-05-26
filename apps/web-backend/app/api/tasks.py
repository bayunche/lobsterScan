"""POST /api/tasks · GET /api/tasks/{id} · POST /api/tasks/{id}/refine"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..orchestrator import pipeline

router = APIRouter(tags=["tasks"])


class CreateTaskRequest(BaseModel):
    report_type: Literal["daily", "project_progress", "review", "introduction"]
    title: str
    audience: Literal["直属领导", "团队内部", "跨部门", "客户"] = "直属领导"
    # 汇报时长 → 总分结构:
    #   1分钟  · 总-分 精要(3-4 章节 / 4-5 段 narration / 200-300 字)
    #   3分钟  · 总-分-总 标准(5-6 章节 / 7-9 段 / 500-750 字)
    #   5分钟  · 总-分-分-总 展开(7-8 章节 / 12-15 段 / 900-1200 字,带具体案例)
    duration: Literal["1分钟", "3分钟", "5分钟"] = Field(
        default="3分钟",
        description="汇报时长 — 决定章节数、段落字数、是否展开案例细节",
    )
    style: Literal["简洁正式", "成果突出", "问题导向", "述职风"] = "简洁正式"
    raw_text: str = ""
    # 用户补充说明 — 跨 8 个 step 全局共享,作为最高优先级业务指令注入到所有 agent prompt 头部。
    # 示例(几种典型场景,用户可参考):
    #   1) 受众微调:    "audience 实际是市领导,语气更稳健保守,避免主观判断"
    #   2) 重点引导:    "重点突出对客户合同金额的影响,客户名称需要保密用 X 公司"
    #   3) 风险显化:    "本周延迟交付要如实说明,不要美化,给出具体补救计划"
    #   4) 内容裁剪:    "不要提及 Project Atlas,所有数据脱敏到部门级"
    #   5) 表达约束:    "讲稿要给市领导念,数据前置,每段不超过 25 字"
    supplement: str = Field(
        default="",
        description=(
            "用户补充说明(可选 · 最高优先级指令,所有 agent 都会看到)。"
            "用一两句话讲清楚:受众真实身份 / 重点突出什么 / 不要提什么 / 表达约束。"
        ),
        examples=[
            "对市领导稳健保守,优先突出已落地能力的业务价值;不要 emoji 或夸大词。",
            "突出客户合同影响,客户名脱敏成 X 公司;风险要如实说不要美化。",
            "讲稿要节奏感强,数据前置;每段不超过 25 字,适合数字人朗读。",
        ],
    )
    file_ids: list[str] = Field(default_factory=list)
    user_hints: dict = Field(default_factory=dict)


class CreateTaskResponse(BaseModel):
    task_id: str


@router.post("/tasks", response_model=CreateTaskResponse)
async def create_task(req: CreateTaskRequest) -> CreateTaskResponse:
    if not req.raw_text and not req.file_ids:
        raise HTTPException(
            status_code=400,
            detail={"error": {
                "code": "INPUT_TOO_SHORT",
                "biz_message": "材料不足以生成汇报，请补充本周完成事项",
                "field": "raw_text", "retryable": True,
            }},
        )

    task_id = f"tsk_{uuid.uuid4().hex[:12]}"
    run = pipeline.create_task(
        task_id=task_id,
        title=req.title,
        report_type=req.report_type,
        audience=req.audience,
        duration=req.duration,
        style=req.style,
        raw_text=req.raw_text,
        supplement=req.supplement,
    )
    # 异步跑 pipeline，不阻塞响应
    asyncio.create_task(pipeline.execute(run))
    return CreateTaskResponse(task_id=task_id)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    run = pipeline.get_run(task_id)
    if not run:
        raise HTTPException(status_code=404)
    return run.to_dict()


@router.get("/tasks/{task_id}/steps/{step_key}")
async def get_step_detail(task_id: str, step_key: str) -> dict:
    run = pipeline.get_run(task_id)
    if not run:
        raise HTTPException(status_code=404)
    detail = run.step_detail(step_key)
    if not detail:
        raise HTTPException(status_code=404)
    return detail


@router.get("/tasks")
async def list_tasks(limit: int = 20) -> dict:
    return {"items": [r.to_dict() for r in pipeline.list_runs(limit=limit)]}


class RefineRequest(BaseModel):
    action: Literal[
        "shorter", "more_problem", "more_formal", "more_result", "regenerate_segment"
    ]
    segment_id: str | None = None


@router.post("/tasks/{task_id}/refine")
async def refine_task(task_id: str, req: RefineRequest) -> dict:
    # V0：先记到 audit；真实 refine 需要重跑特定 step（后续扩）
    return {"ok": True, "note": "refine 已接受，稍后接入真实重跑"}


class UserMessageRequest(BaseModel):
    text: str


@router.get("/tasks/{task_id}/chat")
async def get_chat(task_id: str) -> dict:
    run = pipeline.get_run(task_id)
    if not run:
        raise HTTPException(status_code=404)
    return {"messages": pipeline.get_chat(task_id)}


@router.post("/tasks/{task_id}/chat")
async def send_user_message(task_id: str, req: UserMessageRequest) -> dict:
    run = pipeline.get_run(task_id)
    if not run:
        raise HTTPException(status_code=404)
    import time, uuid as _uuid
    user_msg = {
        "id": _uuid.uuid4().hex[:12],
        "agent": "user", "display_name": "你", "avatar": "🧑",
        "ts": time.time(), "kind": "user", "text": req.text,
    }
    pipeline._persist_chat(run, user_msg)                       # noqa: SLF001
    await pipeline._broadcast(run, "chat.message", user_msg)    # noqa: SLF001

    # 异步触发 coordinator 做 refine plan → 重跑 → 群里讨论
    import asyncio
    asyncio.create_task(pipeline.handle_user_feedback(run, req.text))
    return {"ok": True}
