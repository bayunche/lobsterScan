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
        "shorter", "more_problem", "more_formal", "more_result", "regenerate_segment",
        "retry_video",
    ]
    segment_id: str | None = None


# PRD §9.4 5 个快捷动作 → step + instruction 映射。
# 设计原则:
#  - "更突出问题"重跑 point_extraction(回到分析师拔 risks,最彻底);其它都从 copywriting 起
#  - "更正式"重跑 upward_optimization(表达教练的活,正确分工)
#  - 级联下游影响自动由 _expand_impacted_steps 算
#  - 业务方若觉得"更突出问题"应只调讲稿语气而不重提炼,把 step 改成 copywriting 即可
REFINE_ACTION_MAP: dict[str, dict] = {
    "shorter": {
        "step": "copywriting",
        "instruction": "用户要求把讲稿砍 30-40%,**保留最关键 facts_used 和钩子**,删铺垫与重复;narrations 段数可减,但 facts_in_pool 覆盖率不降。",
        "user_note": "好,我让文书把讲稿精简,只留最关键的。",
    },
    "more_problem": {
        "step": "point_extraction",
        "instruction": "用户要求**更突出问题**:把 risks 提到 key_points 前列;每条 risk 必须有具体业务 impact;progress_status 实事求是不要美化。下游 copywriting 在 narration 中给风险段加强语气。",
        "user_note": "明白,我让分析师把风险拔到更前面,文书也会调整讲稿语气。",
    },
    "more_formal": {
        "step": "upward_optimization",
        "instruction": "用户要求**更正式语气**:升级到更书面措辞,去口语化与拟人化;**禁用** emoji、感叹号、'我们''咱们'等口语词;数据前置,避免主观判断词。",
        "user_note": "好,我让表达教练把语气调整得更书面、更稳健。",
    },
    "more_result": {
        "step": "copywriting",
        "instruction": "用户要求**更突出成果**:strengthening 已完成事项的量化结果,每条 completed 都要配具体数据(数字+单位);削减过程描述,重排顺序让成果段在风险段前。",
        "user_note": "好,我让文书把已落地的成果讲得更突出、更带数据。",
    },
    "regenerate_segment": {
        "step": "copywriting",
        "instruction": "用户要求重新生成讲稿,请基于当前 ReportCore 重写 narrations(可换钩子、换叙述顺序),保持 info_retention.coverage_pct ≥ 60。",
        "user_note": "好,我让文书重新写一版讲稿。",
    },
    "retry_video": {
        "step": "video_production",
        "instruction": "请重新生成数字人视频片段(intro + outro)。上次未成功调用数字人 provider,这次请务必真正执行 skill 脚本生成视频文件。",
        "user_note": "好,我让视频制作重新跑一次。",
    },
}


@router.post("/tasks/{task_id}/refine")
async def refine_task(task_id: str, req: RefineRequest) -> dict:
    run = pipeline.get_run(task_id)
    if not run:
        raise HTTPException(status_code=404)

    plan = REFINE_ACTION_MAP.get(req.action)
    if not plan:
        raise HTTPException(status_code=400,
                            detail={"error": {"code": "UNKNOWN_ACTION",
                                              "biz_message": "暂不支持该快捷操作"}})

    # 异步触发实际重跑(不阻塞 API 响应);chat 里会业务化广播进度
    asyncio.create_task(pipeline.refine_with_action(
        run=run, action=req.action, plan=plan, segment_id=req.segment_id,
    ))

    return {"ok": True, "message": plan["user_note"]}


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
