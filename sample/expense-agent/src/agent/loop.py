# -*- coding: utf-8 -*-
"""
Agent 执行循环（Harness 自研编排器）

循环：模型决策 → 执行工具 → 结果回填 → 再决策，最多 5 轮（MAX_STEPS）。

★ 为什么自研 Harness 而不是 LangGraph：
   本场景是"强顺序 + 强校验"的流程型智能体，需要精细控制三件事——
   ① 工具动态可见性（未校验就不暴露 add_item/submit_draft）
   ② 校验令牌 stamp 的签发与失效
   ③ create_draft 的人工确认中断与恢复
   通用框架把这些做成黑盒后反而要绕开它；自研 200 行的循环可控、可测、可回放。
   若后续需要多智能体协作或复杂分支，再迁到 LangGraph 也不迟。

★ 中断与恢复：create_draft 前强制中断，把草稿内容交给员工确认；
   确认后从 agent_runs 恢复现场继续执行（状态、stamp、已走步骤全部持久化）。
"""

from __future__ import annotations

import json
import time
from typing import Iterator

from .. import db
from ..security import new_id, now_str
from . import planner, tools

MAX_STEPS = 5              # ★ 自主决策最多 5 轮：防止无限循环，也限制单次成本
MAX_POST_CONFIRM_STEPS = 3  # 人工确认后的"已批准计划执行"额外轮次
# 说明：人工确认是信任边界。确认前限制 5 轮（模型自主探索），确认后属于执行
#       用户已批准的计划（建单→挂票→规则校验），再给 3 轮，避免半途而废。


# ============================================================================
# 1. 运行实例管理
# ============================================================================
def create_run(user_id: str, session_id: str, goal: str, grade_code: str) -> tuple[str, dict]:
    """新建一次 Agent 运行，返回 (run_id, state)。"""
    run_id = new_id("run")
    state = {
        "goal": goal, "user_id": user_id, "session_id": session_id, "run_id": run_id,
        "stamps": {},            # 校验令牌
        "grade_code": grade_code,
        "ask_count": 0,
    }
    db.execute(
        "INSERT INTO agent_runs(id,user_id,session_id,goal,status,step_count,state_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id, user_id, session_id, goal, "running", 0, json.dumps(state, ensure_ascii=False), now_str(), now_str()),
    )
    return run_id, state


def load_run(run_id: str) -> tuple[dict, dict]:
    row = db.query_one("SELECT * FROM agent_runs WHERE id=?", (run_id,))
    if not row:
        raise RuntimeError("运行实例不存在")
    return dict(row), json.loads(row["state_json"] or "{}")


def save_run(run_id: str, state: dict, status: str | None = None, step_count: int | None = None,
             pending_tool: str | None = None, pending_args: dict | None = None) -> None:
    """持久化运行现场（中断恢复依赖它）。"""
    sql = "UPDATE agent_runs SET state_json=?, updated_at=?"
    params: list = [json.dumps(state, ensure_ascii=False), now_str()]
    if status:
        sql += ", status=?"; params.append(status)
    if step_count is not None:
        sql += ", step_count=?"; params.append(step_count)
    if pending_tool is not None:
        sql += ", pending_tool=?, pending_args=?"; params += [pending_tool, json.dumps(pending_args or {}, ensure_ascii=False)]
    sql += " WHERE id=?"
    params.append(run_id)
    db.execute(sql, params)


# ============================================================================
# 2. ★ 每轮决策日志（阶段5 分析的数据源）
# ============================================================================
def log_step(run_id: str, user_id: str, step_no: int, kind: str, tool_name: str = "",
             arguments: dict | None = None, summary: str = "", ok: int | None = None,
             duration_ms: int = 0) -> None:
    """
    kind: decision（模型决策）/ tool（工具执行）/ interrupt（人工确认中断）/ final / error
    注意：arguments 与 summary 都做截断，避免把大文本写进日志表。
    """
    db.execute(
        "INSERT INTO agent_step_logs(id,run_id,user_id,step_no,kind,tool_name,arguments,result_summary,"
        "ok,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (new_id("st"), run_id, user_id, step_no, kind, tool_name,
         json.dumps(arguments or {}, ensure_ascii=False)[:1000], (summary or "")[:500],
         ok, duration_ms, now_str()),
    )


# ============================================================================
# 3. 结果回填：把工具返回写进 state，供下一轮决策使用
# ============================================================================
def apply_result(state: dict, tool: str, args: dict, result: dict) -> None:
    state.setdefault("trace", [])
    if tool == "search_policy":
        state["searched"] = True
        state["sources"] = result.get("data", {}).get("sources", [])
    elif tool == "get_allowance":
        state["allowance_done"] = True
        d = dict(result.get("data", {}))
        d.update({"city": args.get("city"), "grade_code": args.get("grade_code"),
                  "expense_type": args.get("expense_type")})
        state["allowance"] = d
    elif tool == "list_invoices":
        state["listed"] = True
        invs = result.get("data", {}).get("invoices", [])
        state["invoices"] = invs
        # 默认取前两张可用票据作为候选（真实场景由用户选择）
        state["invoice_ids"] = [i["id"] for i in invs][:2]
    elif tool == "validate_invoice":
        state["validated"] = bool(result.get("ok"))
        state["validate_result"] = {"blocked": result.get("data", {}).get("blocked", []),
                                    "usable": result.get("data", {}).get("usable", [])}
    elif tool == "create_draft":
        state["draft_no"] = result.get("data", {}).get("draft_no", "-")
    elif tool == "add_item":
        state["items_added"] = True
        state["total_amount"] = result.get("data", {}).get("total", 0)
    elif tool == "check_rules":
        state["rule_checked"] = True
        state["violations"] = result.get("data", {}).get("violations", [])
    elif tool == "submit_draft":
        state["submitted"] = True


# ============================================================================
# 4. 主循环（生成器：每产生一个事件就 yield 给 SSE 层）
# ============================================================================
def _loop(run_id: str, state: dict, ctx: dict, start_step: int = 0,
          limit: int = MAX_STEPS) -> Iterator[tuple[str, dict]]:
    step = start_step
    trace: list[dict] = []

    while step < limit:
        step += 1
        t0 = time.time()

        # ---------- ① 模型决策 ----------
        decision = planner.plan(state, ctx, trace)
        decision_ms = int((time.time() - t0) * 1000)
        log_step(run_id, ctx["user_id"], step, "decision",
                 tool_name=decision.get("tool", ""), arguments=decision.get("args"),
                 summary=f"决策：{decision.get('tool') or 'final'}", duration_ms=decision_ms)
        yield ("step", {"step_no": step, "decision": decision.get("tool") or "final",
                        "args": decision.get("args", {}), "ms": decision_ms})

        # ---------- ② 结束条件 ----------
        if decision["type"] == "final":
            log_step(run_id, ctx["user_id"], step, "final", summary=decision["content"][:200])
            save_run(run_id, state, status="done", step_count=step)
            yield ("final", {"content": decision["content"], "steps": step})
            return

        tool_name, tool_args = decision["tool"], decision.get("args", {})

        # ---------- ③ create_draft 强制中断：先给员工看草稿，确认后才执行 ----------
        if tool_name == "create_draft":
            preview = _build_draft_preview(state, tool_args)
            log_step(run_id, ctx["user_id"], step, "interrupt", tool_name=tool_name,
                     arguments=tool_args, summary="等待员工确认草稿")
            save_run(run_id, state, status="awaiting_confirm", step_count=step,
                     pending_tool=tool_name, pending_args=tool_args)
            yield ("interrupt", {"kind": "confirm_draft", "run_id": run_id,
                                 "tool": tool_name, "preview": preview,
                                 "text": "我将为您创建如下报销单草稿，请确认后再执行。"})
            return

        # ---------- ④ 执行工具 ----------
        t1 = time.time()
        result = tools.execute(tool_name, tool_args, ctx)
        cost = int((time.time() - t1) * 1000)
        log_step(run_id, ctx["user_id"], step, "tool", tool_name=tool_name, arguments=tool_args,
                 summary=result.get("summary", ""), ok=1 if result.get("ok") else 0, duration_ms=cost)
        apply_result(state, tool_name, tool_args, result)
        trace.append({"type": "tool_call", "id": str(step), "tool": tool_name,
                      "args": tool_args, "result": result})
        yield ("tool_result", {"step_no": step, "tool": tool_name, "ok": result.get("ok"),
                               "code": result.get("code"), "message": result.get("message"),
                               "summary": result.get("summary"), "ms": cost,
                               "data": _safe_data(result.get("data", {}))})

        # ---------- ⑤ 追问：中断交还用户 ----------
        if tool_name == "ask_user":
            save_run(run_id, state, status="awaiting_input", step_count=step)
            yield ("interrupt", {"kind": "ask_user", "run_id": run_id,
                                 "question": result["data"].get("question"),
                                 "options": result["data"].get("options", [])})
            return

        # 被拒时把"下一步该调什么"也回传给模型（防线②）
        if not result.get("ok"):
            trace.append({"type": "tool_result", "id": str(step),
                          "result": {"error": result.get("message")}})

        save_run(run_id, state, step_count=step)

    # ---------- ⑥ 超过 5 轮：停止并交还用户 ----------
    log_step(run_id, ctx["user_id"], step, "error", summary="达到最大轮次上限")
    save_run(run_id, state, status="done", step_count=step)
    yield ("final", {"content": f"已达到 {limit} 轮操作上限，为避免误操作我先停下。当前进度已保存，"
                                f"您可以继续补充信息或联系财务。", "steps": step, "limited": True})


def _safe_data(data: dict) -> dict:
    """回传给前端的数据做瘦身，避免把整篇制度原文塞进 SSE。"""
    out = {}
    for k, v in (data or {}).items():
        if isinstance(v, list):
            out[k] = v[:5]
        else:
            out[k] = v
    return out


def _build_draft_preview(state: dict, args: dict) -> dict:
    """构造待确认的草稿内容（流式发给员工看）。"""
    invs = state.get("invoices") or []
    ids = set(state.get("invoice_ids") or [])
    picked = [i for i in invs if i["id"] in ids]
    allow = state.get("allowance") or {}
    return {
        "expense_type": args.get("expense_type"),
        "period": f"{args.get('period_start')} ~ {args.get('period_end')}",
        "reason": args.get("reason", ""),
        "grade_code": state.get("grade_code"),
        "allowance": allow,
        "invoices": [{"invoice_no": i["invoice_no"], "amount": i["amount"],
                      "seller": i["seller"], "city": i.get("city", ""), "item_type": i["item_type"]}
                     for i in picked],
        "total": round(sum(i["amount"] for i in picked), 2),
        "sources": (state.get("sources") or [])[:3],
    }


# ============================================================================
# 5. 对外入口：开启一次运行 / 确认后恢复
# ============================================================================
def start(user_id: str, session_id: str, goal: str, grade_code: str = "P5") -> Iterator[tuple[str, dict]]:
    run_id, state = create_run(user_id, session_id, goal, grade_code)
    ctx = {"user_id": user_id, "session_id": session_id, "grade_code": grade_code, "state": state}
    yield ("run_start", {"run_id": run_id, "goal": goal, "max_steps": MAX_STEPS})
    try:
        for ev in _loop(run_id, state, ctx):
            yield ev
    except Exception as exc:
        log_step(run_id, user_id, 0, "error", summary=str(exc)[:200])
        save_run(run_id, state, status="failed")
        yield ("error", {"code": 5000, "message": f"执行失败：{exc}"})
    yield ("done", {"run_id": run_id})


def resume(run_id: str, action: str, edits: dict | None = None) -> Iterator[tuple[str, dict]]:
    """
    人工确认后恢复执行。
    action: confirm（确认并执行被中断的工具）/ cancel（取消）
    """
    run, state = load_run(run_id)
    ctx = {"user_id": run["user_id"], "session_id": run["session_id"] or "",
           "grade_code": state.get("grade_code", "P5"), "state": state}
    pending_tool = run.get("pending_tool")
    pending_args = json.loads(run.get("pending_args") or "{}")
    step = run.get("step_count") or 0

    if action != "confirm":
        save_run(run_id, state, status="done")
        yield ("final", {"content": "已取消，未创建报销单。您可以随时重新发起。", "steps": step})
        yield ("done", {"run_id": run_id})
        return

    # 用户可能修改草稿内容（edits）→ 合并参数
    pending_args.update(edits or {})

    # 注意：中断前已产出过一次该工具的 decision 事件，这里不再重复，避免轨迹里出现两个 create_draft
    t0 = time.time()
    result = tools.execute(pending_tool, pending_args, ctx)
    cost = int((time.time() - t0) * 1000)
    log_step(run_id, ctx["user_id"], step, "tool", tool_name=pending_tool, arguments=pending_args,
             summary=result.get("summary", ""), ok=1 if result.get("ok") else 0, duration_ms=cost)
    apply_result(state, pending_tool, pending_args, result)
    save_run(run_id, state, status="running", step_count=step, pending_tool=None, pending_args=None)
    yield ("tool_result", {"step_no": step, "tool": pending_tool, "ok": result.get("ok"),
                           "summary": result.get("summary"), "ms": cost,
                           "data": _safe_data(result.get("data", {}))})

    try:
        # ★ 人工确认后放宽轮次：执行的是用户已批准的计划，不再是模型自主探索
        for ev in _loop(run_id, state, ctx, start_step=step, limit=step + MAX_POST_CONFIRM_STEPS):
            yield ev
    except Exception as exc:
        save_run(run_id, state, status="failed")
        yield ("error", {"code": 5000, "message": f"执行失败：{exc}"})
    yield ("done", {"run_id": run_id})


def run_steps(run_id: str) -> list[dict]:
    """查询一次运行的完整决策轨迹（阶段5 分析用）。"""
    # 注意：id 是随机串，必须按 rowid 排序才能保证同一轮内"决策在工具之前"
    return db.rows_to_dicts(db.query_all(
        "SELECT step_no, kind, tool_name, arguments, result_summary, ok, duration_ms, created_at "
        "FROM agent_step_logs WHERE run_id=? ORDER BY step_no, rowid", (run_id,)))
