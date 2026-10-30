# -*- coding: utf-8 -*-
"""
Agent 接口层：把执行循环的事件流转成 SSE，供前端流式展示"决策—执行—结果"全过程。

事件（与阶段2 的 SSE 协议保持一致，新增 step / tool_result / interrupt）：
    run_start → step(决策) → tool_result → … → interrupt(人工确认) | final → done
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import db, deps
from .agent import loop as agent_loop
from .errors import BizError
from .security import new_id

router = APIRouter(prefix="/api/v1/agent")


class AgentIn(BaseModel):
    goal: str
    session_id: Optional[str] = None


class ConfirmIn(BaseModel):
    run_id: str
    action: str = "confirm"          # confirm / cancel
    edits: Optional[dict] = None     # 用户在确认前对草稿内容的修改


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _as_sse(gen) -> StreamingResponse:
    """把 (event, data) 元组流转成 SSE 文本流。"""
    def _wrap():
        for event, data in gen:
            yield _sse(event, data)
        yield "data: [DONE]\n\n"
    return StreamingResponse(_wrap(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat")
def agent_chat(body: AgentIn, request: Request, user=Depends(deps.require_perm("chat:use"))):
    """发起一次 Agent 运行；流式返回每一步的决策与工具结果。"""
    goal = (body.goal or "").strip()
    if not goal:
        raise BizError(1001, "请输入您要办理的事项")
    grade = user.get("grade_code") or "P5"
    return _as_sse(agent_loop.start(user["id"], body.session_id or "", goal, grade))


@router.post("/confirm")
def agent_confirm(body: ConfirmIn, request: Request, user=Depends(deps.require_perm("chat:use"))):
    """人工确认后恢复执行（create_draft 的唯一放行入口）。"""
    row = db.query_one("SELECT user_id FROM agent_runs WHERE id=?", (body.run_id,))
    if not row:
        raise BizError(2001, "运行实例不存在")
    if row["user_id"] != user["id"] and user["role"] != "admin":
        raise BizError(1102)
    return _as_sse(agent_loop.resume(body.run_id, body.action, body.edits))


@router.get("/runs/{run_id}/steps")
def run_steps(run_id: str, user=Depends(deps.require_perm("chat:use"))):
    """查询运行轨迹（阶段5 分析用：工具名、参数、耗时、结果摘要）。"""
    row = db.query_one("SELECT user_id FROM agent_runs WHERE id=?", (run_id,))
    if not row:
        raise BizError(2001, "运行实例不存在")
    if row["user_id"] != user["id"] and user["role"] != "admin":
        raise BizError(1102)
    return {"code": 0, "message": "成功", "data": {"items": agent_loop.run_steps(run_id)},
            "request_id": new_id("req")}
