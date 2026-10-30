# -*- coding: utf-8 -*-
"""
Agent 决策器：决定"下一步调哪个工具"还是"给出结论"

两种实现，接口一致：
    plan_with_rules —— 规则策略器（离线、可复现，用于演示/回归/无模型环境）
    plan_with_llm   —— 真实大模型 Function Calling（LLM_PROVIDER=openai 时）

★ 无论哪种决策器都绕不过 tools.py 的服务端校验（防线③）与工具可见性（防线④）。
   决策器只是"提议"，能不能执行由服务端说了算。
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import tools

APP_ID_RE = re.compile(r"DLG-\d{8}-\d{4}")


# ============================================================================
# 1. 意图识别与槽位抽取
# ============================================================================
def detect_intent(goal: str) -> str:
    """
    顺序即优先级（与样例同构的经验：泛化意图必须排最后，否则会吃掉具体意图）。
    check_handover   交接齐套 / 版本核对
    request_change   改期 / 取消 / 变更
    conflict_check   查冲突 / 是否撞档
    query_slots      查档期 / 有没有空档（默认）
    """
    g = goal or ""
    if any(k in g for k in ("交接", "齐套", "接收人", "未结项", "版本不一致")):
        return "check_handover"
    if any(k in g for k in ("改期", "延后", "提前", "调整档期", "取消", "变更", "挪")):
        return "request_change"
    if any(k in g for k in ("冲突", "撞档", "重叠", "撞车")):
        return "conflict_check"
    return "query_slots"


def extract_slots(goal: str) -> dict:
    """抽取申请号、区域、装卸口、时间范围。"""
    g = goal or ""
    app_ids = APP_ID_RE.findall(g)
    zone = next((z for z in ("A厅", "B厅", "会议室1", "会议室2") if z in g), "")
    dock = next((d for d in re.findall(r"D\d", g)), "")
    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", g)
    return {"app_ids": app_ids, "zone": zone, "dock": dock,
            "date_from": dates[0] if dates else "", "date_to": dates[1] if len(dates) > 1 else ""}


# ============================================================================
# 2. 规则策略器（离线决策）
# ============================================================================
def plan_with_rules(state: dict, ctx: dict) -> dict:
    goal = state.get("goal", "")
    slots_info = state.setdefault("slot_args", extract_slots(goal))
    intent = state.setdefault("intent", detect_intent(goal))

    # ---------- 轨迹 a：查档期/可用性（只读，不碰写入类工具） ----------
    if intent == "query_slots":
        if not state.get("queried"):
            return {"type": "tool", "tool": "query_slots",
                    "args": {"zone": slots_info["zone"] or None,
                             "date_from": slots_info["date_from"] or None,
                             "date_to": slots_info["date_to"] or None}}
        if not state.get("conflict_checked") and slots_info["zone"]:
            return {"type": "tool", "tool": "detect_conflict", "args": {"zone": slots_info["zone"]}}
        return {"type": "final", "content": _compose_availability(state)}

    # ---------- 轨迹 b：查交接（只读 + 可提交） ----------
    if intent == "check_handover":
        app_id = slots_info["app_ids"][0] if slots_info["app_ids"] else None
        if not app_id:
            return {"type": "tool", "tool": "ask_user",
                    "args": {"question": "请问要检查哪一条申请的交接？请提供统一关联编号（DLG-YYYYMMDD-NNNN）。"}}
        if not state.get("handover_checked"):
            return {"type": "tool", "tool": "check_handover", "args": {"app_id": app_id}}
        return {"type": "final", "content": _compose_handover(state)}

    # ---------- 轨迹 c：发起变更（★含人工确认中断） ----------
    if intent == "request_change":
        app_id = slots_info["app_ids"][0] if slots_info["app_ids"] else None
        if not app_id:
            return {"type": "tool", "tool": "ask_user",
                    "args": {"question": "请问要变更哪一条申请？请提供统一关联编号（DLG-YYYYMMDD-NNNN）。"}}
        if not state.get("got_slot"):
            return {"type": "tool", "tool": "get_slot", "args": {"app_id": app_id}}
        if not state.get("conflict_checked"):
            return {"type": "tool", "tool": "detect_conflict", "args": {"app_id": app_id}}
        if not state.get("evidence_ok"):
            return {"type": "tool", "tool": "validate_evidence", "args": {"app_ids": [app_id]}}
        if not state.get("assessed"):
            return {"type": "tool", "tool": "assess_change",
                    "args": {"app_id": app_id,
                             "new_window_start": state.get("new_start") or "2026-11-05 08:00",
                             "new_window_end": state.get("new_end") or "2026-11-07 18:00"}}
        if not state.get("draft_request_id"):
            # ★ draft_change_request 由循环层拦截并请求授权人确认，不在此直接执行
            return {"type": "tool", "tool": "draft_change_request",
                    "args": {"app_id": app_id, "change_type": "reschedule",
                             "new_window_start": state.get("new_start") or "2026-11-05 08:00",
                             "new_window_end": state.get("new_end") or "2026-11-07 18:00",
                             "reason": "客户提出档期调整（待授权人确认）"}}
        if not state.get("auth_requested"):
            return {"type": "tool", "tool": "request_authorization",
                    "args": {"app_id": app_id, "reviewer": "傅景"}}
        return {"type": "final", "content": _compose_change(state)}

    # ---------- 查冲突 ----------
    if not state.get("conflict_checked"):
        return {"type": "tool", "tool": "detect_conflict",
                "args": {"app_id": slots_info["app_ids"][0] if slots_info["app_ids"] else None}}
    return {"type": "final", "content": _compose_conflict(state)}


# ============================================================================
# 3. 结论组装（★不承诺、不锁档；缺失值如实报"数据不完整"）
# ============================================================================
def _compose_availability(state: dict) -> str:
    slots = state.get("slots") or []
    incomplete = state.get("incomplete_ids") or []
    conflicts = state.get("conflicts") or []
    if not slots:
        return ("未查到该条件的档期记录。注意：未查到≠未占用——"
                "若存在状态缺失（missing）记录，请联系流程协调员唐映秋补齐台账后再判断（f31）。")
    confirmed = [s for s in slots if s.get("status") == "confirmed"]
    text = (f"该条件下共 {len(slots)} 条档期记录，其中 **已确认占用 {len(confirmed)} 条**"
            f"（另有 {len(slots) - len(confirmed)} 条未确认/状态缺失）。")
    if incomplete:
        text += f"\n⚠ {len(incomplete)} 条记录占档状态缺失（{'、'.join(map(str, incomplete))}）：" \
                f"按 f31 只能报'数据不完整'，不能判定为未占用。"
    if conflicts:
        text += f"\n检测到 {len(conflicts)} 条冲突提示，均为 advisory（只提示、不锁档）。"
    text += "\n结论仅供人工确认，正式排期以授权人员（傅景/顾明岚）确认结果为准（f41）。"
    return text


def _compose_handover(state: dict) -> str:
    problems = state.get("handover_problems") or []
    if problems:
        items = "；".join(p.get("reason", "") for p in problems)
        return f"交接存在缺口：{items}\n请补齐后再流转，不能只发一条'已处理'（f23）。"
    return "交接三项齐套（接收人/版本/未结项）且版本一致，可进入下一环节。"


def _compose_conflict(state: dict) -> str:
    conflicts = state.get("conflicts") or []
    if not conflicts:
        return "未检测到档期冲突。注意：本结论基于当前台账，若存在状态缺失记录则不代表无风险（f31）。"
    lines = "\n".join(f"· {c.get('type')}：{c.get('reason')}" for c in conflicts[:5])
    return f"检测到 {len(conflicts)} 条冲突提示（advisory，不锁档）：\n{lines}"


def _compose_change(state: dict) -> str:
    return (f"变更请求 {state.get('draft_request_id', '-')} 已提交，当前状态：**待授权人确认**。\n"
            f"系统只做提示，不自动锁档、不签约、不承诺容量（f05/f52）；\n"
            f"不得据此告知客户'档期已改'，须待 {state.get('reviewer', '授权人员')} 确认后生效（f41）。")


# ============================================================================
# 4. 真实大模型决策（Function Calling）
# ============================================================================
AGENT_SYSTEM_PROMPT = """你是场馆排期助手智能体，通过调用工具帮助业务人员处理档期与交接事务。

# 硬约束（H = Hard，违反即为严重错误）
H1 你只能【提示】，绝不能锁档、签约或承诺容量。工具集中不存在这类能力，也不要声称能操作。
H2 任何变更请求在发出前必须中断，由授权人员（傅景/顾明岚）确认；正式结论由人作出（f41）。
H3 来源不清（无服务台/台账/文件库记录号）的申请不得进入正式流转（f40），必须拒绝并说明缺什么。
H4 占档状态三态必须区分：confirmed=已确认占用，unconfirmed=已填报未确认，missing=没填。
   missing【绝不等于没占档】，只能回答"数据不完整，无法判断"，不得据此给出"无冲突/有空档"的结论（f31）。
H5 不得编造档期、版本、来源与结论；所有事实必须来自工具返回。
H6 不得承诺结果：禁止说"档期已锁定""已为您保留"，只能陈述系统返回的事实与"待授权人确认"。

# 标准流程
查询档期 → 冲突检测 → 来源校验 → 变更影响评估 → 生成变更请求（请授权人确认）→ 请求授权

# 停止条件
工具调用超过 5 轮、连续 2 次被拒、或信息缺失需要追问时，停止并交还用户。"""


def plan_with_llm(state: dict, ctx: dict, trace: list[dict]) -> dict:
    import os
    from openai import OpenAI

    base_url = os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY，无法使用模型决策（可用规则策略器离线运行）")

    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": state.get("goal", "")}]
    for t in trace:
        if t.get("type") == "tool_call":
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": t["id"], "type": "function",
                 "function": {"name": t["tool"], "arguments": json.dumps(t["args"], ensure_ascii=False)}}]})
        else:
            messages.append({"role": "tool", "tool_call_id": t.get("id", ""),
                             "content": json.dumps(t.get("result", {}), ensure_ascii=False)[:2000]})

    resp = client.chat.completions.create(
        model=model, messages=messages,
        tools=[{"type": "function", "function": dict(t, name=t["name"])}
               for t in tools.visible_tools(state)],   # ★ 防线④：只暴露当前可用工具
        tool_choice="auto", temperature=0.1,
    )
    msg = resp.choices[0].message
    if msg.tool_calls:
        call = msg.tool_calls[0]
        return {"type": "tool", "tool": call.function.name,
                "args": json.loads(call.function.arguments or "{}"), "call_id": call.id}
    return {"type": "final", "content": (msg.content or "").strip()}


def plan(state: dict, ctx: dict, trace: list[dict]) -> dict:
    import os
    if os.getenv("LLM_PROVIDER", "") == "openai":
        return plan_with_llm(state, ctx, trace)
    return plan_with_rules(state, ctx)
