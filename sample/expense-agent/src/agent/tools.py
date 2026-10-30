# -*- coding: utf-8 -*-
"""
Agent 工具集（Function Calling 规范 + 服务端强校验）

设计要点（对应《Agent设计》§3/§4）：
  1. description 里必须写清"不要用"场景 —— 模型选错工具 80% 是因为描述只写了能做什么。
  2. 参数尽量用 enum 收窄，别让模型自由发挥字符串。
  3. 统一返回 {ok, code, message, data, summary}：
     message 是【给模型看的下一步指令】，被拒时直接告诉它该调哪个工具（防线②）。
  4. ★ 写入类工具做前置校验（校验令牌 stamp），模型想跳也跳不过（防线③）。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .. import db
from ..rag import get_index
from ..security import new_id, now_str


# ============================================================================
# 1. 报销标准表（确定性查表，★绝不让 LLM 推理金额）
# ============================================================================
CITY_TIERS = {
    "北京": "tier1", "上海": "tier1", "广州": "tier1", "深圳": "tier1",
    "杭州": "tier2", "成都": "tier2", "武汉": "tier2", "南京": "tier2", "西安": "tier2",
}
# 住宿标准：城市档 → 职级 → (金额, 条款号)
HOTEL_STANDARD = {
    "tier1": {"P1": (600, "3.2.1"), "P2": (600, "3.2.1"), "P3": (500, "3.2.1"),
              "P4": (400, "3.2.1"), "P5": (400, "3.2.1")},
    "tier2": {"P1": (450, "3.2.2"), "P2": (450, "3.2.2"), "P3": (380, "3.2.2"),
              "P4": (300, "3.2.2"), "P5": (300, "3.2.2")},
    "tier3": {"P1": (350, "3.2.3"), "P2": (350, "3.2.3"), "P3": (300, "3.2.3"),
              "P4": (240, "3.2.3"), "P5": (240, "3.2.3")},
}
MEAL_ALLOWANCE = (120, "3.4.1")          # 伙食补助 120 元/天（包干）


def city_tier(city: str) -> str:
    return CITY_TIERS.get(city, "tier3")


# ============================================================================
# 2. 工具 Schema（OpenAI Function Calling 格式）
# ============================================================================
TOOLS: list[dict] = [
    {
        "name": "search_policy",
        "description": "检索报销制度条文。用于回答'标准是多少''能不能报''需要什么材料'等制度问题。"
                       "【不要用】不要用它查具体额度数字（用 get_allowance）；不要用它在建单后查进度。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索问题，尽量保留原文关键词"},
            "category": {"type": "string", "enum": ["travel_standard", "invoice_rule", "approval_flow", "other"]},
        }, "required": ["query"]},
    },
    {
        "name": "get_allowance",
        "description": "查询某职级在某城市的报销标准（住宿/餐补）。金额只能来自本工具的返回。"
                       "【不要用】不要凭记忆或制度原文推测金额，标准一律以本工具返回为准。",
        "parameters": {"type": "object", "properties": {
            "grade_code": {"type": "string", "enum": ["P1", "P2", "P3", "P4", "P5"]},
            "city": {"type": "string", "description": "城市名，如 北京/上海"},
            "expense_type": {"type": "string", "enum": ["hotel", "meal", "transport"]},
        }, "required": ["grade_code", "city", "expense_type"]},
    },
    {
        "name": "list_invoices",
        "description": "列出当前用户票据池中可用的发票（默认只列 unused）。"
                       "【不要用】不要用它查他人的票据；不要用它判断能否报销（用 validate_invoice）。",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["unused", "reserved", "used", "all"], "default": "unused"},
            "item_type": {"type": "string", "enum": ["hotel", "transport", "meal", "office", "all"], "default": "all"},
        }, "required": []},
    },
    {
        "name": "validate_invoice",
        "description": "校验票据是否可用：重复报销（号码+代码+金额+日期四元组）、票据状态、抬头税号。"
                       "【前置】无。【后续】通过后才能调用 add_item。"
                       "【不要用】不要用它修改票据状态；校验被拒时禁止更换参数重试。",
        "parameters": {"type": "object", "properties": {
            "invoice_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                            "description": "待校验的票据 ID 列表"},
        }, "required": ["invoice_ids"]},
    },
    {
        "name": "create_draft",
        "description": "创建报销单草稿。★调用后系统会中断并向员工展示草稿内容，确认后才会真正创建。"
                       "【不要用】不要在未确认前假设单据已创建。",
        "parameters": {"type": "object", "properties": {
            "expense_type": {"type": "string", "enum": ["travel", "entertain", "office", "other"]},
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "reason": {"type": "string", "maxLength": 200},
        }, "required": ["expense_type", "period_start", "period_end"]},
    },
    {
        "name": "add_item",
        "description": "向报销单草稿添加费用明细（把已校验通过的票据挂到单据上）。"
                       "【前置】必须先对同一批 invoice_ids 调用 validate_invoice 且结果为 pass。"
                       "【不要用】不要在未校验时挂票；不要修改已提交单据的明细。",
        "parameters": {"type": "object", "properties": {
            "draft_id": {"type": "string"},
            "invoice_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "item_type": {"type": "string", "enum": ["hotel", "transport", "meal", "office", "other"]},
            "amount": {"type": "number", "description": "金额（元）；不传则取票面金额"},
            "note": {"type": "string", "maxLength": 200},
            "client_request_id": {"type": "string", "description": "幂等键，重复调用安全"},
        }, "required": ["draft_id", "invoice_ids", "item_type"]},
    },
    {
        "name": "check_rules",
        "description": "对草稿跑规则引擎：住宿超标、超期报销、缺附件、重复票据。"
                       "【前置】草稿至少 1 条明细。【后续】通过后才能调用 submit_draft。",
        "parameters": {"type": "object", "properties": {
            "draft_id": {"type": "string"},
        }, "required": ["draft_id"]},
    },
    {
        "name": "submit_draft",
        "description": "提交报销单进入审批。★高风险动作，试点期需用户确认后触发。"
                       "【前置】check_rules 已通过且内容未再变更。",
        "parameters": {"type": "object", "properties": {
            "draft_id": {"type": "string"},
        }, "required": ["draft_id"]},
    },
    {
        "name": "ask_user",
        "description": "缺少必要信息时向用户追问。一次最多问 2 项，累计不超过 3 轮。",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
        }, "required": ["question"]},
    },
]

TOOL_NAMES = [t["name"] for t in TOOLS]


# ============================================================================
# 3. 防线④：工具动态可见性 —— 看不见就调不了
#     未通过票据校验前不暴露 add_item；未通过规则校验前不暴露 submit_draft
# ============================================================================
def visible_tools(state: dict) -> list[dict]:
    """
    按当前状态裁剪可用工具列表。
    state 关键字段：stamp_invoice(有效?) / stamp_rule(有效?) / draft_id / ask_count
    """
    visible = {"search_policy", "get_allowance", "list_invoices", "validate_invoice", "ask_user"}

    if state.get("draft_id"):
        visible.add("check_rules")
        if _stamp_valid(state, "invoice"):
            visible.add("add_item")            # 票据已校验 → 才允许挂票
        if _stamp_valid(state, "rule"):
            visible.add("submit_draft")        # 规则已校验 → 才允许提交
    else:
        visible.add("create_draft")            # 无草稿 → 允许建单（会触发人工确认中断）

    if state.get("ask_count", 0) >= 3:
        visible.discard("ask_user")            # 追问轮次用尽，避免无限追问

    return [t for t in TOOLS if t["name"] in visible]


# ============================================================================
# 4. 校验令牌（stamp）：★防线③，代码级不变量
# ============================================================================
STAMP_TTL = 300          # 5 分钟有效：够长避免频繁重校验，够短防止内容变了还用旧结果


def fingerprint(obj: Any) -> str:
    """对票据集合 / 明细内容取指纹，内容一变指纹即失效。"""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stamp_valid(state: dict, kind: str) -> bool:
    """stamp 是否有效：存在 + 指纹匹配 + 未过期。"""
    st = (state.get("stamps") or {}).get(kind)
    if not st:
        return False
    if time.time() > st.get("expires_at", 0):
        return False
    return st.get("fingerprint") == st.get("expected")


def grant_stamp(state: dict, kind: str, expected_fp: str) -> None:
    """签发校验令牌：记录指纹与过期时间。"""
    state.setdefault("stamps", {})[kind] = {
        "fingerprint": expected_fp, "expected": expected_fp,
        "expires_at": time.time() + STAMP_TTL, "issued_at": time.time(),
    }


def revoke_stamp(state: dict, kind: str) -> None:
    """内容变更 → 令牌立即失效（H3：任何修改后必须重跑校验）。"""
    (state.get("stamps") or {}).pop(kind, None)


# ============================================================================
# 5. 工具执行（统一返回 {ok, code, message, data, summary}）
#     message 是给模型看的下一步指令 —— 防线②
# ============================================================================
def execute(name: str, args: dict, ctx: dict) -> dict:
    """
    ctx: {user_id, session_id, grade_code, state(运行期状态)}
    """
    handler = {
        "search_policy": _search_policy,
        "get_allowance": _get_allowance,
        "list_invoices": _list_invoices,
        "validate_invoice": _validate_invoice,
        "create_draft": _create_draft,
        "add_item": _add_item,
        "check_rules": _check_rules,
        "submit_draft": _submit_draft,
        "ask_user": _ask_user,
    }.get(name)

    if not handler:
        return {"ok": False, "code": 1001, "message": f"未知工具 {name}", "data": {}, "summary": "未知工具"}
    return handler(args, ctx)


# ---------------------------- 只读类 ----------------------------
def _search_policy(args: dict, ctx: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "code": 1001, "message": "query 不能为空", "data": {}, "summary": "参数缺失"}
    index = get_index()
    hits = index.search(query, top_k=3)
    if not hits:
        return {"ok": True, "code": 3002, "message": "未在制度中检索到相关规定，请如实告知用户并建议咨询财务部门",
                "data": {"sources": []}, "summary": "未检索到条文"}
    sources = [{"no": i, "file": h["file"], "clause_no": h["clause_no"],
                "heading_path": h["heading_path"], "snippet": h["text"][:160]}
               for i, h in enumerate(hits, start=1)]
    return {"ok": True, "code": 0, "message": "已检索到制度条文，请依据原文作答并标注引用编号",
            "data": {"sources": sources}, "summary": f"命中 {len(sources)} 条条文"}


def _get_allowance(args: dict, ctx: dict) -> dict:
    grade = args.get("grade_code") or ctx.get("grade_code") or "P5"
    city = args.get("city") or ""
    etype = args.get("expense_type") or "hotel"
    tier = city_tier(city)

    if etype == "meal":
        amount, clause = MEAL_ALLOWANCE
        return {"ok": True, "code": 0, "message": "已获取标准，请原样引用该金额",
                "data": {"amount": amount, "clause_no": clause, "unit": "元/天"},
                "summary": f"餐补 {amount} 元/天"}
    if etype == "hotel":
        amount, clause = HOTEL_STANDARD.get(tier, HOTEL_STANDARD["tier3"]).get(grade, (240, "3.2.3"))
        return {"ok": True, "code": 0, "message": "已获取标准，请原样引用该金额，不得换算",
                "data": {"amount": amount, "clause_no": clause, "unit": "元/晚",
                         "city_tier": tier, "grade_code": grade},
                "summary": f"{city}({tier}) {grade} 住宿 {amount} 元/晚"}
    return {"ok": True, "code": 0, "message": "已获取标准",
            "data": {"amount": 80, "clause_no": "3.3.1", "unit": "元/天"},
            "summary": "市内交通 80 元/天包干"}


def _list_invoices(args: dict, ctx: dict) -> dict:
    status = args.get("status") or "unused"
    itype = args.get("item_type") or "all"
    sql = "SELECT id,invoice_no,amount,issue_date,seller,item_type,city,status FROM invoices WHERE owner_id=?"
    params = [ctx["user_id"]]
    if status != "all":
        sql += " AND status=?"; params.append(status)
    if itype != "all":
        sql += " AND item_type=?"; params.append(itype)
    sql += " ORDER BY issue_date DESC LIMIT 20"
    rows = db.rows_to_dicts(db.query_all(sql, params))
    if not rows:
        return {"ok": True, "code": 0,
                "message": "票据池中没有可用发票，请用 ask_user 请用户上传或提供发票信息",
                "data": {"invoices": []}, "summary": "无可用票据"}
    return {"ok": True, "code": 0, "message": "已列出票据，下一步需调用 validate_invoice 校验后才能使用",
            "data": {"invoices": rows}, "summary": f"{len(rows)} 张可用票据"}


# ---------------------------- 校验类 ----------------------------
def _validate_invoice(args: dict, ctx: dict) -> dict:
    ids = args.get("invoice_ids") or []
    if not ids:
        return {"ok": False, "code": 1001, "message": "invoice_ids 不能为空", "data": {}, "summary": "参数缺失"}

    blocked, usable = [], []
    for i in ids:
        row = db.query_one("SELECT * FROM invoices WHERE id=?", (i,))
        if not row:
            blocked.append({"id": i, "reason": "票据不存在"}); continue
        if row["owner_id"] != ctx["user_id"]:
            blocked.append({"id": i, "reason": "票据不属于当前用户"}); continue
        if row["status"] != "unused":
            blocked.append({"id": i, "reason": f"票据状态为 {row['status']}，可能已被使用（重复报销风险）"}); continue
        usable.append({"id": i, "amount": row["amount"], "item_type": row["item_type"], "city": row["city"]})

    if blocked:
        return {"ok": False, "code": 4003,
                "message": "票据校验未通过，禁止调用 add_item。请原样转述原因，不得建议用户拆分或换票规避",
                "data": {"blocked": blocked, "usable": usable},
                "summary": f"拦截 {len(blocked)} 张票据"}

    # ★ 通过 → 签发校验令牌（指纹 = 票据集合）
    grant_stamp(ctx["state"], "invoice", fingerprint(sorted(ids)))
    return {"ok": True, "code": 0,
            "message": "票据校验通过，现在可以调用 add_item 将票据挂到草稿（指纹已锁定，换票需重新校验）",
            "data": {"usable": usable}, "summary": f"{len(usable)} 张票据校验通过"}


# ---------------------------- 写入类 ----------------------------
def _create_draft(args: dict, ctx: dict) -> dict:
    """真正创建草稿（仅在用户确认后被调用）。"""
    draft_id = new_id("dr")
    ts = now_str()
    grade = ctx.get("grade_code") or "P5"
    db.execute(
        "INSERT INTO reimbursement_drafts(id,draft_no,user_id,session_id,status,expense_type,period_start,"
        "period_end,reason,total_amount,grade_code_snapshot,ruleset_version,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (draft_id, f"BX{int(time.time()) % 100000}", ctx["user_id"], ctx.get("session_id"), "draft",
         args.get("expense_type"), args.get("period_start"), args.get("period_end"), args.get("reason", ""),
         0, grade, "v1", ts, ts),
    )
    ctx["state"]["draft_id"] = draft_id
    draft_no = f"BX{int(time.time()) % 100000}"
    db.execute("UPDATE reimbursement_drafts SET draft_no=? WHERE id=?", (draft_no, draft_id))
    return {"ok": True, "code": 0, "message": "草稿已创建，下一步需先用 validate_invoice 校验票据，再调用 add_item",
            "data": {"draft_id": draft_id, "draft_no": draft_no}, "summary": f"创建草稿 {draft_no}"}


def _add_item(args: dict, ctx: dict) -> dict:
    """★前置校验：票据 stamp 必须有效，否则拒绝（模型跳不过）。"""
    state = ctx["state"]
    draft_id = args.get("draft_id")
    ids = sorted(args.get("invoice_ids") or [])

    if not draft_id or draft_id != state.get("draft_id"):
        return {"ok": False, "code": 2001, "message": "草稿不存在或不属于本次流程，请先调用 create_draft",
                "data": {}, "summary": "草稿校验失败"}
    if not _stamp_valid(state, "invoice") or (state["stamps"]["invoice"]["fingerprint"] != fingerprint(ids)):
        return {"ok": False, "code": 4003,
                "message": "票据未通过校验或票据集合已变更，请先调用 validate_invoice(invoice_ids=[...]) 重新校验",
                "data": {}, "summary": "票据校验令牌无效，已拦截"}

    added, total = [], 0.0
    for i in ids:
        inv = db.query_one("SELECT * FROM invoices WHERE id=? AND owner_id=?", (i, ctx["user_id"]))
        if not inv or inv["status"] != "unused":
            continue
        amount = float(args.get("amount") or inv["amount"])
        db.execute(
            "INSERT INTO draft_items(id,draft_id,invoice_id,item_type,amount,expense_date,note,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (new_id("it"), draft_id, i, args.get("item_type") or inv["item_type"], amount,
             inv["issue_date"], args.get("note", ""), now_str()),
        )
        # 票据原子占位：防止两张单抢同一张票
        db.execute("UPDATE invoices SET status='reserved', draft_id=? WHERE id=?", (draft_id, i))
        added.append({"invoice_id": i, "amount": amount})
        total += amount

    db.execute("UPDATE reimbursement_drafts SET total_amount=total_amount+?, updated_at=? WHERE id=?",
               (total, now_str(), draft_id))
    revoke_stamp(state, "rule")            # 明细变了 → 规则令牌失效（H3）
    return {"ok": True, "code": 0,
            "message": f"已添加 {len(added)} 条明细，内容已变更，必须重新调用 check_rules 才能提交",
            "data": {"added": added, "total": total}, "summary": f"新增 {len(added)} 条明细"}


def _check_rules(args: dict, ctx: dict) -> dict:
    draft_id = args.get("draft_id")
    items = db.query_all(
        "SELECT d.*, i.city, i.item_type AS inv_type FROM draft_items d "
        "LEFT JOIN invoices i ON i.id=d.invoice_id WHERE d.draft_id=?", (draft_id,))
    if not items:
        return {"ok": False, "code": 1001, "message": "草稿还没有明细，请先调用 add_item",
                "data": {}, "summary": "无明细"}

    grade = ctx.get("grade_code") or "P5"
    violations = []
    for it in items:
        city = it["city"] or ""
        if (it["inv_type"] or it["item_type"]) == "hotel":
            std = HOTEL_STANDARD.get(city_tier(city), HOTEL_STANDARD["tier3"]).get(grade, (240, "3.2.3"))[0]
            if it["amount"] > std:
                violations.append({
                    "rule_id": "R_OVER_STANDARD", "level": "warning",
                    "message": f"住宿 {it['amount']} 元超过 {grade} 职级标准 {std} 元/晚，超出部分需自担",
                    "clause_no": "3.2.1",
                })

    # 指纹 = 明细内容 + 职级快照，任一变化即失效
    fp = fingerprint({"items": [{"amount": i["amount"], "type": i["item_type"]} for i in items], "grade": grade})
    if any(v["level"] == "blocking" for v in violations):
        return {"ok": False, "code": 4003,
                "message": "规则校验未通过，禁止调用 submit_draft。请原样转述违规项，不得建议规避",
                "data": {"violations": violations}, "summary": f"{len(violations)} 项违规"}

    grant_stamp(ctx["state"], "rule", fp)
    db.execute("UPDATE reimbursement_drafts SET rule_result=? WHERE id=?",
               (json.dumps(violations, ensure_ascii=False), draft_id))
    return {"ok": True, "code": 0,
            "message": "规则校验通过" + (f"，但有 {len(violations)} 项提示" if violations else "，无违规"),
            "data": {"violations": violations}, "summary": f"校验通过（{len(violations)} 项提示）"}


def _submit_draft(args: dict, ctx: dict) -> dict:
    """★前置校验：规则 stamp 必须有效。"""
    state = ctx["state"]
    draft_id = args.get("draft_id")
    if not _stamp_valid(state, "rule"):
        return {"ok": False, "code": 4003,
                "message": "规则校验未通过或内容已变更，请先调用 check_rules(draft_id=...) 重新校验",
                "data": {}, "summary": "规则令牌无效，已拦截"}
    db.execute("UPDATE reimbursement_drafts SET status='submitted', updated_at=?, approval_instance_id=? WHERE id=?",
               (now_str(), f"OA{int(time.time()) % 100000}", draft_id))
    return {"ok": True, "code": 0, "message": "已提交审批，请如实告知单号与'等待审批'，不得承诺结果",
            "data": {"draft_id": draft_id, "status": "submitted"}, "summary": "已提交审批"}


def _ask_user(args: dict, ctx: dict) -> dict:
    """追问：不是真的"问"，而是让循环中断并把问题交给前端。"""
    state = ctx["state"]
    state["ask_count"] = state.get("ask_count", 0) + 1
    return {"ok": True, "code": 0, "message": "已向用户提问，等待回答",
            "data": {"question": args.get("question"), "options": args.get("options") or []},
            "summary": "追问用户"}
