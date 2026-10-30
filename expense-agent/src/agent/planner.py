# -*- coding: utf-8 -*-
"""
Agent 决策器（planner）：决定"下一步调用哪个工具"还是"给出最终答复"

两种实现，接口一致：
    1) plan_with_rules  —— 规则策略器（离线可用，演示/回归测试用；行为确定、可复现）
    2) plan_with_llm    —— 真实大模型 Function Calling（LLM_PROVIDER=openai 时启用）

★ 注意：无论哪种决策器，都【不能绕过】tools.py 里的服务端前置校验（防线③）与
   工具动态可见性（防线④）。决策器只是"提议"，能不能执行由服务端说了算。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..settings import settings
from . import tools


# ============================================================================
# 1. 意图识别与槽位抽取（规则实现；接 LLM 后由模型完成）
# ============================================================================
def detect_intent(goal: str) -> str:
    """
    意图识别。★ 顺序即优先级：越具体的意图越靠前，否则会被泛化意图吃掉
    （真实案例：'必须在多少天内提交报销'曾因含'提交报销'被误判为 file_claim）。

    refuse          违规请求（伪造/PS 发票等），直接拒绝，不调工具
    query_status    查进度 / 撤销（P2 未实现，如实说明）
    check_invoice   查某张票能不能报
    query_invoices  列出自己的票据
    file_claim      要发起报销（要建单）
    ask_standard    问制度条款（默认）
    """
    g = goal or ""
    # ① 违规请求：不提供任何操作建议，直接拒绝
    if any(k in g for k in ("伪造", "PS", "ps", "假发票", "买发票", "虚开", "代开发票")):
        return "refuse"
    # ② 进度 / 撤回（注意必须排在 file_claim 之前，否则"帮我查进度"会被吃掉）
    if any(k in g for k in ("审批到哪", "到哪一步", "审批进度", "撤销", "撤回")):
        return "query_status"
    # ③ 票据可用性
    if any(k in g for k in ("这个发票", "这张票", "这个票", "这张发票")) and \
            any(k in g for k in ("能报", "可以报", "能用", "能不能")):
        return "check_invoice"
    # ④ 票据查询
    if any(k in g for k in ("几张", "有哪些票", "没用过", "未使用", "我的发票")):
        return "query_invoices"
    # ⑤ 发起报销：必须有明确的"帮我 / 加到 / 发起"等动作词
    if any(k in g for k in ("帮我", "我要", "加到", "添加到", "发起", "报一下", "报销一下")):
        return "file_claim"
    return "ask_standard"


def extract_slots(goal: str) -> dict:
    """抽取城市、天数、费用类型、时间范围等槽位。"""
    g = goal or ""
    city = next((c for c in tools.CITY_TIERS if c in g), "")
    m = re.search(r"(\d+)\s*天", g)
    days = int(m.group(1)) if m else 0
    # ★ 默认必须为空：早期写成默认 hotel，导致任何制度问题都被追问城市
    item = "hotel" if any(k in g for k in ("住宿", "酒店", "住")) else \
        "transport" if any(k in g for k in ("交通", "高铁", "机票", "火车")) else \
        "meal" if any(k in g for k in ("餐", "吃饭")) else ""
    period = "last_month" if "上个月" in g or "上月" in g else ""
    return {"city": city, "days": days, "item_type": item, "period": period}


# ============================================================================
# 2. 规则策略器（离线决策）
#     按"意图 → 槽位 → 已完成步骤"推导下一步，保证三条典型轨迹可复现
# ============================================================================
def plan_with_rules(state: dict, ctx: dict) -> dict:
    """
    返回：
        {"type": "tool", "tool": 名称, "args": {...}}   下一步调用工具
        {"type": "final", "content": "..."}             结束并答复
    """
    goal = state.get("goal", "")
    slots = state.setdefault("slots", extract_slots(goal))
    intent = state.setdefault("intent", detect_intent(goal))
    grade = ctx.get("grade_code") or "P5"

    # ---------- 违规请求：拒绝，不调用任何工具 ----------
    if intent == "refuse":
        return {"type": "final", "content":
                "抱歉，伪造或变造票据属于严重违规：公司不予报销，已报销款项将予以追回，"
                "并按违纪处理规定追究责任（见制度 6.1）。我不能提供任何相关操作建议。"
                "如发票遗失，可向开票方申请开具证明或提供加盖公章的复印件（制度 2.4）。"}

    # ---------- 进度 / 撤回：P2 未实现，如实说明，不编造状态 ----------
    if intent == "query_status":
        return {"type": "final", "content":
                "目前系统暂不支持查询审批进度 / 撤回已提交单据（该功能与预算额度释放耦合，排在二期）。"
                "请通过 OA 查看审批进度，或联系财务部门处理撤回事宜。"}

    # ---------- 列出自己的票据（只读） ----------
    if intent == "query_invoices":
        if not state.get("listed"):
            return {"type": "tool", "tool": "list_invoices", "args": {"status": "unused"}}
        n = len(state.get("invoices") or [])
        return {"type": "final", "content": f"您当前有 {n} 张未使用的发票：" +
                "；".join(f"{i['seller']} {i['amount']}元" for i in (state.get("invoices") or [])[:5])}

    # ---------- 轨迹 a：只查制度，不碰写入类工具 ----------
    if intent == "ask_standard":
        if not state.get("searched"):
            return {"type": "tool", "tool": "search_policy", "args": {"query": goal}}
        need_amount = slots.get("item_type") and any(
            k in goal for k in ("标准", "多少", "超标", "能报", "上限", "元"))
        if not state.get("allowance_done") and (slots.get("city") or need_amount):
            # ★ 住宿按城市分档：城市缺失时不许用默认档给金额（会误导），必须先问清楚
            if slots.get("item_type") == "hotel" and not slots.get("city") and state.get("ask_count", 0) < 3:
                return {"type": "tool", "tool": "ask_user",
                        "args": {"question": "请问是哪座城市？住宿标准按城市分档，确认后我才能给出准确金额。",
                                 "options": ["北京", "上海", "杭州"]}}
            return {"type": "tool", "tool": "get_allowance",
                    "args": {"grade_code": grade, "city": slots.get("city") or "其他",
                             "expense_type": slots["item_type"]}}
        return {"type": "final", "content": _compose_standard_answer(state)}

    # ---------- 轨迹 c：查票据 → 给结论 ----------
    if intent == "check_invoice":
        ids = state.get("invoice_ids") or []
        if not ids:
            if not state.get("listed"):
                return {"type": "tool", "tool": "list_invoices", "args": {"status": "unused"}}
            return {"type": "tool", "tool": "ask_user",
                    "args": {"question": "请问您要查询哪张发票？可以提供发票号码后四位或上传发票。",
                             "options": ["上传发票", "提供发票号码"]}}
        if not state.get("validated"):
            return {"type": "tool", "tool": "validate_invoice", "args": {"invoice_ids": ids}}
        return {"type": "final", "content": _compose_invoice_answer(state)}

    # ---------- 轨迹 b：查制度 → 查额度 → 找票 → 校验 → 建草稿(中断) → 挂票 → 规则 ----------
    if not state.get("searched"):
        return {"type": "tool", "tool": "search_policy", "args": {"query": goal}}
    if not state.get("allowance_done") and slots.get("city"):
        return {"type": "tool", "tool": "get_allowance",
                "args": {"grade_code": grade, "city": slots["city"], "expense_type": "hotel"}}
    if not state.get("listed"):
        return {"type": "tool", "tool": "list_invoices", "args": {"status": "unused"}}
    if not state.get("invoice_ids"):
        return {"type": "tool", "tool": "ask_user",
                "args": {"question": "票据池中未找到可用发票，请先上传发票或提供发票信息。",
                         "options": ["上传发票", "稍后再报"]}}
    if not state.get("validated"):
        return {"type": "tool", "tool": "validate_invoice", "args": {"invoice_ids": state["invoice_ids"]}}
    if not state.get("draft_id"):
        # ★ create_draft 由循环层拦截并请求人工确认，不会在这里直接执行
        return {"type": "tool", "tool": "create_draft",
                "args": {"expense_type": "travel",
                         "period_start": state.get("period_start") or "2026-09-01",
                         "period_end": state.get("period_end") or "2026-09-30",
                         "reason": f"{slots.get('city') or ''}出差报销"}}
    if not state.get("items_added"):
        return {"type": "tool", "tool": "add_item",
                "args": {"draft_id": state["draft_id"], "invoice_ids": state["invoice_ids"],
                         "item_type": "hotel", "client_request_id": state.get("run_id", "")}}
    if not state.get("rule_checked"):
        return {"type": "tool", "tool": "check_rules", "args": {"draft_id": state["draft_id"]}}
    # ★ L1 自主度：不自动 submit_draft，交回用户确认后由用户触发
    return {"type": "final", "content": _compose_draft_answer(state)}


# ============================================================================
# 3. 最终答复组装（★ 金额只来自工具返回，绝不编造 —— 对应 H5）
# ============================================================================
def _compose_standard_answer(state: dict) -> str:
    allow = state.get("allowance") or {}
    sources = state.get("sources") or []
    if allow:
        unit = allow.get("unit", "元/晚")
        text = (f"按您的职级 {allow.get('grade_code','')}，{allow.get('city','')}"
                f"（{allow.get('city_tier','')}）{allow.get('expense_type','')}标准为 "
                f"**{allow.get('amount')} {unit}**，在标准内据实报销，超出部分需自行承担。")
    else:
        text = "制度中未找到与您情况直接对应的条款，建议咨询财务部门确认。"
    refs = "".join(f"\n[{s['no']}] {s['file']}" + (f" 条款{s['clause_no']}" if s.get("clause_no") else "")
                   for s in sources)
    return text + ("\n来源：" + refs if refs else "")


def _compose_invoice_answer(state: dict) -> str:
    vr = state.get("validate_result") or {}
    if vr.get("blocked"):
        reasons = "；".join(b.get("reason", "") for b in vr["blocked"])
        return f"这张发票**暂不能报销**：{reasons}。如有疑问请联系财务部门，不要重复上传或拆分规避。"
    usable = vr.get("usable") or []
    total = sum(u.get("amount", 0) for u in usable)
    return (f"校验通过：{len(usable)} 张发票可用，合计 {total:.2f} 元。"
            f"下一步可为您创建报销单草稿（需您确认后才会真正创建）。")


def _compose_draft_answer(state: dict) -> str:
    viol = state.get("violations") or []
    tip = ""
    if viol:
        tip = "\n提示：" + "；".join(v.get("message", "") for v in viol)
    return (f"草稿已就绪（单号 {state.get('draft_no','-')}，合计 "
            f"{state.get('total_amount', 0):.2f} 元），明细与制度依据如上。{tip}\n"
            f"试点期我不会自动提交——请您确认无误后点击「提交审批」。")


# ============================================================================
# 4. 真实大模型决策（Function Calling）
# ============================================================================
AGENT_SYSTEM_PROMPT = """你是企业报销助手智能体，通过调用工具帮助用户完成报销。

# 硬约束（H = Hard，违反即为严重错误）
H1 validate_invoice（票据校验）通过前，禁止调用 add_item。
H2 check_rules（规则校验）通过前，禁止调用 submit_draft。
H3 任何一次对草稿的修改（新增明细、修改金额）之后，必须重新执行对应校验，旧结果立即失效。
H4 校验返回 blocked 时，禁止重试绕过、禁止更换参数规避、禁止告知用户"已通过"；
   必须原样转述原因，并给出制度允许的替代路径。
H5 不得自行解释或放宽报销标准；金额只能来自 get_allowance 或 search_policy 的返回。
H6 不得承诺结果：禁止说"一定能报销""审批会通过"，只能陈述系统返回的事实。

# 标准流程
理解需求 → search_policy → get_allowance → list_invoices → validate_invoice
→ create_draft（会请用户确认）→ add_item → check_rules → 向用户复述并等待确认

# 停止条件
工具调用超过 5 轮、连续 2 次被拒、或信息缺失需要追问时，停止并交还用户。"""


def plan_with_llm(state: dict, ctx: dict, trace: list[dict]) -> dict:
    """用 Function Calling 让模型自主决策；返回结构与 plan_with_rules 一致。"""
    from openai import OpenAI
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY，无法使用模型决策（可设 LLM_PROVIDER=mock 走规则策略）")

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.llm_timeout)
    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": state.get("goal", "")}]
    # 把已发生的工具调用与结果回填给模型（结果回填 → 再决策）
    for t in trace:
        if t.get("type") == "tool_call":
            messages.append({"role": "assistant",
                             "content": None,
                             "tool_calls": [{"id": t["id"], "type": "function",
                                             "function": {"name": t["tool"], "arguments": json.dumps(t["args"], ensure_ascii=False)}}]})
        else:
            messages.append({"role": "tool", "tool_call_id": t.get("id", ""),
                             "content": json.dumps(t.get("result", {}), ensure_ascii=False)[:2000]})

    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        tools=[{"type": "function", "function": {k: v for k, v in t.items() if k != "name"} | {"name": t["name"]}}
               for t in tools.visible_tools(state)],   # ★ 防线④：只暴露当前可用工具
        tool_choice="auto",
        temperature=0.1,
    )
    msg = resp.choices[0].message
    if msg.tool_calls:
        call = msg.tool_calls[0]
        return {"type": "tool", "tool": call.function.name,
                "args": json.loads(call.function.arguments or "{}"), "call_id": call.id}
    return {"type": "final", "content": (msg.content or "").strip()}


def plan(state: dict, ctx: dict, trace: list[dict]) -> dict:
    """统一入口：按配置选择决策器。"""
    if settings.llm_provider == "openai":
        return plan_with_llm(state, ctx, trace)
    return plan_with_rules(state, ctx)
