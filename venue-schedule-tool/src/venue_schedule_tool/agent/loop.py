# -*- coding: utf-8 -*-
"""
Agent 执行循环（自研 Harness，与报销助手样例同构）

循环：决策 → 执行工具 → 结果回填 → 再决策，自主阶段最多 5 轮。

★ 为什么自研：本场景需要精细控制三件事——
    ① 工具动态可见性（未提供来源校验/冲突检测就不暴露 draft_change_request）
    ② 校验令牌 stamp 的签发与失效
    ③ 变更请求的【授权人确认中断】与恢复
通用框架把这些做成黑盒后反而要绕开它。

★ 中断点：draft_change_request（对应 f41 正式结论须授权人员确认）。
   中断期间运行状态持久化到 SQLite，授权人确认后从现场恢复继续执行。
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from typing import Iterator

from . import planner, store, tools

MAX_STEPS = 5                # 自主决策轮次上限
MAX_POST_CONFIRM_STEPS = 3   # 授权人确认后，执行"已批准计划"的追加轮次


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


# ============================================================================
# 1. 运行实例
# ============================================================================
def create_run(goal: str, ctx: dict | None = None) -> tuple[str, dict]:
    run_id = _new_id("run")
    state = {"goal": goal, "run_id": run_id, "stamps": {}, "ask_count": 0}
    state.update(ctx or {})
    store.init_db()
    store.execute(
        "INSERT INTO agent_runs(id,goal,status,step_count,state_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (run_id, goal, "running", 0, json.dumps(state, ensure_ascii=False), _now(), _now()),
    )
    return run_id, state


def load_run(run_id: str) -> tuple[dict, dict]:
    row = store.query_one("SELECT * FROM agent_runs WHERE id=?", (run_id,))
    if not row:
        raise RuntimeError("运行实例不存在")
    return row, json.loads(row["state_json"] or "{}")


def save_run(run_id: str, state: dict, status: str | None = None, step_count: int | None = None,
             pending_tool: str | None = None, pending_args: dict | None = None) -> None:
    sql = "UPDATE agent_runs SET state_json=?, updated_at=?"
    params: list = [json.dumps(state, ensure_ascii=False), _now()]
    if status:
        sql += ", status=?"; params.append(status)
    if step_count is not None:
        sql += ", step_count=?"; params.append(step_count)
    if pending_tool is not None:
        sql += ", pending_tool=?, pending_args=?"
        params += [pending_tool, json.dumps(pending_args or {}, ensure_ascii=False)]
    sql += " WHERE id=?"
    params.append(run_id)
    store.execute(sql, tuple(params))


# ============================================================================
# 2. 每轮决策日志（★阶段8 评测与阶段9 审计的数据源）
# ============================================================================
def log_step(run_id: str, step_no: int, kind: str, tool_name: str = "",
             arguments: dict | None = None, summary: str = "", ok: int | None = None,
             duration_ms: int = 0) -> None:
    store.execute(
        "INSERT INTO agent_step_logs(id,run_id,step_no,kind,tool_name,arguments,result_summary,"
        "ok,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (_new_id("st"), run_id, step_no, kind, tool_name,
         json.dumps(arguments or {}, ensure_ascii=False)[:1000], (summary or "")[:500],
         ok, duration_ms, _now()),
    )


# ============================================================================
# 3. 结果回填
# ============================================================================
def apply_result(state: dict, tool: str, args: dict, result: dict) -> None:
    data = result.get("data") or {}
    if tool == "query_slots":
        state["queried"] = True
        state["slots"] = data.get("slots", [])
        state["incomplete_ids"] = data.get("incomplete_app_ids", [])
    elif tool == "get_slot":
        state["got_slot"] = True
        state["slot"] = data.get("slot", {})
    elif tool == "detect_conflict":
        state["conflict_checked"] = True
        state["conflicts"] = data.get("conflicts", [])
        if data.get("incomplete_app_ids"):
            state["incomplete_ids"] = data["incomplete_app_ids"]
    elif tool == "check_handover":
        state["handover_checked"] = True
        state["handover_problems"] = data.get("problems", [])
    elif tool == "validate_evidence":
        state["evidence_ok"] = True
    elif tool == "assess_change":
        state["assessed"] = True
        state["assessment"] = {"before": data.get("before"), "after": data.get("after")}
    elif tool == "draft_change_request":
        state["draft_request_id"] = data.get("request_id")
    elif tool == "request_authorization":
        state["auth_requested"] = True
        state["reviewer"] = data.get("reviewer")


# ============================================================================
# 4. 主循环
# ============================================================================
def _loop(run_id: str, state: dict, ctx: dict, start_step: int = 0,
          limit: int = MAX_STEPS) -> Iterator[tuple[str, dict]]:
    step = start_step
    trace: list[dict] = []

    while step < limit:
        step += 1
        t0 = time.time()
        decision = planner.plan(state, ctx, trace)
        ms = int((time.time() - t0) * 1000)
        log_step(run_id, step, "decision", tool_name=decision.get("tool", ""),
                 arguments=decision.get("args"), summary=f"决策：{decision.get('tool') or 'final'}",
                 duration_ms=ms)
        yield ("step", {"step_no": step, "decision": decision.get("tool") or "final",
                        "args": decision.get("args", {}), "ms": ms})

        if decision["type"] == "final":
            log_step(run_id, step, "final", summary=decision["content"][:200])
            save_run(run_id, state, status="done", step_count=step)
            yield ("final", {"content": decision["content"], "steps": step})
            return

        name, args = decision["tool"], decision.get("args", {})

        # ★ 中断点：变更请求必须经授权人确认（f41）
        if name == "draft_change_request":
            preview = _build_preview(state, args)
            log_step(run_id, step, "interrupt", tool_name=name, arguments=args,
                     summary="等待授权人员确认变更请求")
            save_run(run_id, state, status="awaiting_confirm", step_count=step,
                     pending_tool=name, pending_args=args)
            yield ("interrupt", {"kind": "confirm_change", "run_id": run_id, "tool": name,
                                 "preview": preview,
                                 "text": "我将生成如下变更请求，须经授权人员确认后才会创建（f41）。"})
            return

        t1 = time.time()
        result = tools.execute(name, args, ctx)
        cost = int((time.time() - t1) * 1000)
        log_step(run_id, step, "tool", tool_name=name, arguments=args,
                 summary=result.get("summary", ""), ok=1 if result.get("ok") else 0, duration_ms=cost)
        apply_result(state, name, args, result)
        trace.append({"type": "tool_call", "id": str(step), "tool": name, "args": args, "result": result})
        yield ("tool_result", {"step_no": step, "tool": name, "ok": result.get("ok"),
                               "code": result.get("code"), "message": result.get("message"),
                               "summary": result.get("summary"), "ms": cost,
                               "data": result.get("data", {})})

        if name == "ask_user":
            save_run(run_id, state, status="awaiting_input", step_count=step)
            yield ("interrupt", {"kind": "ask_user", "run_id": run_id,
                                 "question": result["data"].get("question"),
                                 "options": result["data"].get("options", [])})
            return

        if not result.get("ok"):
            trace.append({"type": "tool_result", "id": str(step), "result": {"error": result.get("message")}})

        save_run(run_id, state, step_count=step)

    log_step(run_id, step, "error", summary="达到最大轮次上限")
    save_run(run_id, state, status="done", step_count=step)
    yield ("final", {"content": f"已达到 {limit} 轮上限，我先停下。当前进度已保存，"
                                f"请补充信息或联系流程协调员唐映秋。", "steps": step, "limited": True})


def _build_preview(state: dict, args: dict) -> dict:
    """构造待授权人确认的变更请求预览。"""
    slot = state.get("slot") or {}
    return {
        "app_id": args.get("app_id"),
        "change_type": args.get("change_type"),
        "current_window": f"{slot.get('window_start')} ~ {slot.get('window_end')}" if slot else "未知",
        "current_version": slot.get("version"),
        "current_status": slot.get("status"),
        "new_window": f"{args.get('new_window_start')} ~ {args.get('new_window_end')}",
        "reason": args.get("reason", ""),
        "conflicts_before": (state.get("conflicts") or []),
        "assessment": state.get("assessment"),
        "reviewer_required": "傅景（审核与质量负责人）/ 顾明岚（业务负责人）",
        "disclaimer": "只提示、不锁档；确认后仍由人工在系统内执行实际变更（f05/f52）。",
    }


# ============================================================================
# 5. 对外入口
# ============================================================================
def start(goal: str, ctx: dict | None = None) -> Iterator[tuple[str, dict]]:
    run_id, state = create_run(goal, ctx)
    runtime_ctx = {"state": state, "reviewer": (ctx or {}).get("reviewer", "")}
    yield ("run_start", {"run_id": run_id, "goal": goal, "max_steps": MAX_STEPS})
    try:
        for ev in _loop(run_id, state, runtime_ctx):
            yield ev
    except Exception as exc:
        log_step(run_id, 0, "error", summary=str(exc)[:200])
        save_run(run_id, state, status="failed")
        yield ("error", {"code": 5000, "message": f"执行失败：{exc}"})
    yield ("done", {"run_id": run_id})


def resume(run_id: str, action: str, reviewer: str = "", edits: dict | None = None) -> Iterator[tuple[str, dict]]:
    """授权人确认后恢复执行。action: confirm / reject"""
    run, state = load_run(run_id)
    runtime_ctx = {"state": state, "reviewer": reviewer}
    pending_tool = run.get("pending_tool")
    pending_args = json.loads(run.get("pending_args") or "{}")
    step = run.get("step_count") or 0

    if action != "confirm":
        save_run(run_id, state, status="done")
        yield ("final", {"content": f"变更请求已被{reviewer or '授权人员'}驳回，未创建任何实际变更。",
                         "steps": step})
        yield ("done", {"run_id": run_id})
        return

    if reviewer and reviewer not in tools.AUTHORIZED_REVIEWERS:
        save_run(run_id, state, status="done")
        yield ("error", {"code": 5101, "message": f"{reviewer} 非授权人员，无权作出正式结论（f41）。"})
        yield ("done", {"run_id": run_id})
        return

    pending_args.update(edits or {})
    t0 = time.time()
    result = tools.execute(pending_tool, pending_args, runtime_ctx)
    cost = int((time.time() - t0) * 1000)
    log_step(run_id, step, "tool", tool_name=pending_tool, arguments=pending_args,
             summary=result.get("summary", ""), ok=1 if result.get("ok") else 0, duration_ms=cost)
    apply_result(state, pending_tool, pending_args, result)
    save_run(run_id, state, status="running", step_count=step, pending_tool=None, pending_args=None)
    yield ("tool_result", {"step_no": step, "tool": pending_tool, "ok": result.get("ok"),
                           "summary": result.get("summary"), "ms": cost, "data": result.get("data", {})})

    if not result.get("ok"):
        save_run(run_id, state, status="done")
        yield ("final", {"content": f"变更请求未通过前置校验，已终止：{result.get('message')}", "steps": step})
        yield ("done", {"run_id": run_id})
        return

    try:
        for ev in _loop(run_id, state, runtime_ctx, start_step=step,
                        limit=step + MAX_POST_CONFIRM_STEPS):
            yield ev
    except Exception as exc:
        save_run(run_id, state, status="failed")
        yield ("error", {"code": 5000, "message": f"执行失败：{exc}"})
    yield ("done", {"run_id": run_id})


def run_steps(run_id: str) -> list[dict]:
    """查询一次运行的决策轨迹（阶段8 评测 / 阶段9 审计用）。"""
    return store.query_all(
        "SELECT step_no, kind, tool_name, arguments, result_summary, ok, duration_ms, created_at "
        "FROM agent_step_logs WHERE run_id=? ORDER BY step_no, rowid", (run_id,))
