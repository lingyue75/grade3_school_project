# -*- coding: utf-8 -*-
"""
Agent 工具集（Function Calling 规范 + 服务端强校验）—— 场馆排期场景

★ 与报销助手样例同构，但红线不同。本项目的四条硬约束：
    H1 只提示冲突，不自动锁档 / 签约 / 承诺容量（f05/f52）
        → 落地方式：工具集里【根本不提供】 lock_slot / sign_contract / commit_capacity
    H2 正式结论须由授权人员确认（f41）
        → draft_change_request 强制中断，授权人（傅景/顾明岚）确认后才执行
    H3 来源不清不得进入正式流转（f40）
        → validate_evidence 未通过的申请，draft_change_request 直接拒绝
    H4 占档三态必须区分（f31）
        → missing（没填）≠ 没占档；不得据缺失值判定"无冲突"，只能报"数据不完整"

四道防线（与样例一致）：
    ① 系统提示词硬约束  ② 工具被拒时返回"下一步该调什么"
    ③ 服务端校验令牌 stamp（代码级不变量）  ④ 工具动态可见性（看不见就调不了）
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core.conflict import (
    detect_handover_gaps,
    detect_slot_conflicts,
    detect_version_mismatch,
)
from ..core.models import SlotStatus
from ..utils.io import DATE_FMT, load_handovers, load_slots
from . import store

DEFAULT_DATA = Path(__file__).resolve().parents[3] / "data"   # agent/ 多一级，需上溯到项目根

# 授权人员（f41）：审核与质量负责人 傅景；业务负责人 顾明岚（范围决定权）
AUTHORIZED_REVIEWERS = ["傅景", "顾明岚"]


# ============================================================================
# 1. 数据源（★复用已有 core/io，不重写冲突检测逻辑）
# ============================================================================
def _load_all():
    slots = load_slots(DEFAULT_DATA / "slots.csv")
    handovers = load_handovers(DEFAULT_DATA / "handovers.csv")
    return slots, handovers


# ============================================================================
# 2. 工具 Schema（OpenAI Function Calling 格式）
# ============================================================================
TOOLS: list[dict] = [
    {
        "name": "query_slots",
        "description": "按区域/装卸口/时间范围查询档期占用。用于回答'某时段还有没有档期'。"
                       "【不要用】不要用它判断能不能锁档；不要用它修改档期。",
        "parameters": {"type": "object", "properties": {
            "zone": {"type": "string", "description": "场馆区域，如 A厅 / B厅 / 会议室1"},
            "dock": {"type": "string", "description": "装卸口，如 D1"},
            "date_from": {"type": "string", "description": "起始时间，格式 YYYY-MM-DD HH:MM"},
            "date_to": {"type": "string", "description": "结束时间，格式 YYYY-MM-DD HH:MM"},
            "status": {"type": "string", "enum": ["confirmed", "unconfirmed", "missing", "all"], "default": "all"},
        }, "required": []},
    },
    {
        "name": "get_slot",
        "description": "查看单个申请的档期详情（区域、时间窗、版本、占档状态、来源）。"
                       "【不要用】不要用它改档期。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string", "description": "统一关联编号 DLG-YYYYMMDD-NNNN"},
        }, "required": ["app_id"]},
    },
    {
        "name": "detect_conflict",
        "description": "检测档期冲突（区域时间窗重叠、装卸口进撤场重叠）。只输出提示，不锁档（f05）。"
                       "【注意】missing 状态的档期不参与时间窗判定，会单独报'数据不完整'（f31）。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string", "description": "限定单个申请；不传则检测全部"},
            "zone": {"type": "string"},
        }, "required": []},
    },
    {
        "name": "check_handover",
        "description": "检查交接三项齐套（接收人/版本/未结项）与版本一致性（f20/f23）。"
                       "【后续】通过后才能调用 submit_handover。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string"},
        }, "required": ["app_id"]},
    },
    {
        "name": "validate_evidence",
        "description": "★校验来源是否齐备（f40）。来源为空的申请不得进入正式流转。"
                       "【后续】通过后才能调用 draft_change_request。",
        "parameters": {"type": "object", "properties": {
            "app_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        }, "required": ["app_ids"]},
    },
    {
        "name": "assess_change",
        "description": "只读评估：把某申请改到新时间窗后，冲突会如何变化。不产生任何实际变更（f05）。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string"},
            "new_window_start": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
            "new_window_end": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
        }, "required": ["app_id", "new_window_start", "new_window_end"]},
    },
    {
        "name": "draft_change_request",
        "description": "★生成变更请求草稿。调用后会中断并向【授权人员】展示，确认后才会真正创建。"
                       "【前置】必须先通过 validate_evidence 与 detect_conflict。"
                       "【不要用】不要在未确认前告知客户'已改档'。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string"},
            "change_type": {"type": "string", "enum": ["reschedule", "cancel", "add_setup", "other"]},
            "new_window_start": {"type": "string"},
            "new_window_end": {"type": "string"},
            "reason": {"type": "string", "maxLength": 200},
        }, "required": ["app_id", "change_type"]},
    },
    {
        "name": "submit_handover",
        "description": "提交交接记录（接收人/版本/未结项）。【前置】必须先通过 check_handover。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string"},
            "receiver": {"type": "string"},
            "version": {"type": "string"},
            "open_items": {"type": "string", "description": "未结项；无则填'无'"},
        }, "required": ["app_id", "receiver", "version", "open_items"]},
    },
    {
        "name": "request_authorization",
        "description": "请求授权人员对某申请作出正式结论（f41）。只登记请求，不代替授权人做决定。",
        "parameters": {"type": "object", "properties": {
            "app_id": {"type": "string"},
            "reviewer": {"type": "string", "enum": AUTHORIZED_REVIEWERS},
        }, "required": ["app_id", "reviewer"]},
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

# ★ 安全边界：以下能力【永不提供】。能力边界即安全边界——从根上删掉比事后拦截更可靠。
FORBIDDEN_TOOLS = [
    "lock_slot", "confirm_booking", "sign_contract",
    "commit_capacity", "override_conflict", "delete_slot",
]


# ============================================================================
# 3. 防线④：工具动态可见性
# ============================================================================
def visible_tools(state: dict) -> list[dict]:
    """未通过来源校验/冲突检测前不暴露 draft_change_request；未通过交接校验前不暴露 submit_handover。"""
    visible = {"query_slots", "get_slot", "detect_conflict", "check_handover",
               "validate_evidence", "assess_change", "ask_user"}

    if _stamp_valid(state, "evidence") and _stamp_valid(state, "conflict"):
        visible.add("draft_change_request")
    if _stamp_valid(state, "handover"):
        visible.add("submit_handover")
    if state.get("draft_request_id"):
        visible.add("request_authorization")
    if state.get("ask_count", 0) >= 3:
        visible.discard("ask_user")

    # 二次保险：即便配置被改坏，也绝不暴露禁用工具
    return [t for t in TOOLS if t["name"] in visible and t["name"] not in FORBIDDEN_TOOLS]


# ============================================================================
# 4. 防线③：校验令牌 stamp（内容指纹 + 5 分钟有效期）
# ============================================================================
STAMP_TTL = 300


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _stamp_valid(state: dict, kind: str) -> bool:
    st = (state.get("stamps") or {}).get(kind)
    if not st:
        return False
    if time.time() > st.get("expires_at", 0):
        return False
    return st.get("fingerprint") == st.get("expected")


def grant_stamp(state: dict, kind: str, expected_fp: str) -> None:
    state.setdefault("stamps", {})[kind] = {
        "fingerprint": expected_fp, "expected": expected_fp,
        "expires_at": time.time() + STAMP_TTL, "issued_at": time.time(),
    }


def revoke_stamp(state: dict, kind: str) -> None:
    (state.get("stamps") or {}).pop(kind, None)


# ============================================================================
# 5. 工具执行（统一返回 {ok, code, message, data, summary}）
# ============================================================================
def execute(name: str, args: dict, ctx: dict) -> dict:
    handler = {
        "query_slots": _query_slots,
        "get_slot": _get_slot,
        "detect_conflict": _detect_conflict,
        "check_handover": _check_handover,
        "validate_evidence": _validate_evidence,
        "assess_change": _assess_change,
        "draft_change_request": _draft_change_request,
        "submit_handover": _submit_handover,
        "request_authorization": _request_authorization,
        "ask_user": _ask_user,
    }.get(name)

    if name in FORBIDDEN_TOOLS or not handler:
        return {"ok": False, "code": 5101, "message": f"工具 {name} 不可用：系统不提供锁档/签约/容量承诺类能力",
                "data": {}, "summary": "禁用工具"}
    return handler(args, ctx)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), DATE_FMT)
    except ValueError:
        return None


def _slot_dict(s) -> dict:
    return {
        "app_id": s.app_id, "zone": s.zone, "dock": s.dock,
        "window_start": s.window_start.strftime(DATE_FMT) if s.window_start else None,
        "window_end": s.window_end.strftime(DATE_FMT) if s.window_end else None,
        "setup_minutes": s.setup_minutes, "teardown_minutes": s.teardown_minutes,
        "version": s.version, "status": s.status.value, "source": s.source,
    }


# ---------------------------- 只读类 ----------------------------
def _query_slots(args: dict, ctx: dict) -> dict:
    slots, _ = _load_all()
    zone, dock = args.get("zone"), args.get("dock")
    status = args.get("status") or "all"
    d_from, d_to = _parse_dt(args.get("date_from")), _parse_dt(args.get("date_to"))

    result = []
    incomplete = []
    for s in slots:
        if zone and s.zone != zone:
            continue
        if dock and (s.dock or "") != dock:
            continue
        if d_from and s.window_end and s.window_end < d_from:
            continue
        if d_to and s.window_start and s.window_start > d_to:
            continue
        if status != "all" and s.status.value != status:
            continue
        result.append(_slot_dict(s))
        # ★ f31：状态缺失单独标记，绝不当作"没占档"
        if s.status == SlotStatus.MISSING:
            incomplete.append(s.app_id)

    msg = "已列出档期。注意：状态为 missing 的档期【不代表未占用】，只能报'数据不完整'（f31）。"
    if incomplete:
        msg += f" 本结果含 {len(incomplete)} 条状态缺失记录：{'、'.join(incomplete)}。"
    return {"ok": True, "code": 0, "message": msg,
            "data": {"slots": result, "incomplete_app_ids": incomplete},
            "summary": f"{len(result)} 条档期（{len(incomplete)} 条状态缺失）"}


def _get_slot(args: dict, ctx: dict) -> dict:
    slots, handovers = _load_all()
    app_id = args.get("app_id")
    hit = next((s for s in slots if s.app_id == app_id), None)
    if not hit:
        return {"ok": False, "code": 2001, "message": f"未找到申请 {app_id}（f30 统一编号）",
                "data": {}, "summary": "申请不存在"}
    related = [h for h in handovers if h.app_id == app_id]
    return {"ok": True, "code": 0, "message": "已获取档期详情；下一步如需变更，请先 validate_evidence 与 detect_conflict",
            "data": {"slot": _slot_dict(hit),
                     "handovers": [{"from_role": h.from_role, "to_role": h.to_role, "receiver": h.receiver,
                                    "version": h.version, "open_items": h.open_items, "source": h.source}
                                   for h in related]},
            "summary": f"{app_id} {hit.zone} {hit.status.value}"}


def _detect_conflict(args: dict, ctx: dict) -> dict:
    slots, _ = _load_all()
    app_id, zone = args.get("app_id"), args.get("zone")

    scope = slots
    if app_id:
        scope = [s for s in slots if s.app_id == app_id]
    if zone:
        base = [s for s in slots if s.zone == zone]
        scope = [s for s in slots if s in base or s.app_id == app_id] if app_id else base

    conflicts = detect_slot_conflicts(scope)
    # ★ f31：状态缺失的档期不参与时间窗判定，单独输出"数据不完整"
    incomplete = [s.app_id for s in scope if s.status == SlotStatus.MISSING]

    fp = fingerprint({"app_id": app_id, "zone": zone})
    grant_stamp(ctx["state"], "conflict", fp)

    return {"ok": True, "code": 0,
            "message": "冲突检测完成（只提示、不锁档）。如需发起变更，请先调用 validate_evidence 校验来源。",
            "data": {"conflicts": [c.to_dict() for c in conflicts],
                     "incomplete_app_ids": incomplete,
                     "advisory_only": True, "auto_lock": False},
            "summary": f"{len(conflicts)} 条冲突提示（{len(incomplete)} 条数据不完整）"}


def _check_handover(args: dict, ctx: dict) -> dict:
    slots, handovers = _load_all()
    app_id = args.get("app_id")
    related = [h for h in handovers if h.app_id == app_id]
    if not related:
        return {"ok": False, "code": 2001, "message": f"申请 {app_id} 没有交接记录，无法判断齐套（f20）",
                "data": {}, "summary": "无交接记录"}

    gaps = detect_handover_gaps(related)
    mism = detect_version_mismatch(slots, related)
    problems = [c.to_dict() for c in gaps + mism]

    if problems:
        return {"ok": True, "code": 0,
                "message": "交接存在缺口：请原样转述缺失项，不得只发一条'已处理'（f23）。补齐后可调用 submit_handover。",
                "data": {"problems": problems, "complete": False},
                "summary": f"{len(problems)} 项交接缺口"}

    grant_stamp(ctx["state"], "handover", fingerprint({"app_id": app_id}))
    return {"ok": True, "code": 0, "message": "交接三项齐套且版本一致，可调用 submit_handover 提交",
            "data": {"problems": [], "complete": True}, "summary": "交接齐套"}


def _validate_evidence(args: dict, ctx: dict) -> dict:
    """★ f40：来源不清不得进入正式流转。"""
    slots, handovers = _load_all()
    ids = args.get("app_ids") or []
    if not ids:
        return {"ok": False, "code": 1001, "message": "app_ids 不能为空", "data": {}, "summary": "参数缺失"}

    no_source, found = [], []
    for i in ids:
        s = next((x for x in slots if x.app_id == i), None)
        hs = [h for h in handovers if h.app_id == i]
        sources = [x for x in [s.source if s else ""] + [h.source for h in hs] if x]
        if not sources:
            no_source.append(i)
        else:
            found.append({"app_id": i, "sources": sources})

    if no_source:
        return {"ok": False, "code": 4003,
                "message": f"申请 {'、'.join(no_source)} 缺少来源记录，按 f40 不得进入正式流转。"
                           f"请补充服务台/台账/文件库记录号后重试，不得绕过。",
                "data": {"no_source": no_source, "with_source": found},
                "summary": f"{len(no_source)} 个申请无来源，已拦截"}

    grant_stamp(ctx["state"], "evidence", fingerprint(sorted(ids)))
    return {"ok": True, "code": 0, "message": "来源校验通过，现在可以调用 draft_change_request（来源已随请求留痕）",
            "data": {"with_source": found}, "summary": f"{len(found)} 个申请来源齐备"}


def _assess_change(args: dict, ctx: dict) -> dict:
    """只读评估：复制一份档期改成新时间窗，比较冲突变化（绝不落库、绝不锁档）。"""
    slots, _ = _load_all()
    app_id = args.get("app_id")
    ns, ne = _parse_dt(args.get("new_window_start")), _parse_dt(args.get("new_window_end"))
    if not ns or not ne:
        return {"ok": False, "code": 1001, "message": "时间格式应为 YYYY-MM-DD HH:MM",
                "data": {}, "summary": "时间格式错误"}

    target = next((s for s in slots if s.app_id == app_id), None)
    if not target:
        return {"ok": False, "code": 2001, "message": f"未找到申请 {app_id}", "data": {}, "summary": "申请不存在"}

    before = detect_slot_conflicts(slots)
    before_n = len([c for c in before if app_id in c.app_ids])

    import copy
    simulated = copy.deepcopy(slots)
    for s in simulated:
        if s.app_id == app_id:
            s.window_start, s.window_end = ns, ne
    after = detect_slot_conflicts(simulated)
    after_n = len([c for c in after if app_id in c.app_ids])

    grant_stamp(ctx["state"], "change", fingerprint({"app_id": app_id, "ns": str(ns), "ne": str(ne)}))
    return {"ok": True, "code": 0,
            "message": f"模拟评估：改期后该申请相关冲突由 {before_n} 条变为 {after_n} 条。"
                       f"本结果仅为只读模拟，未产生任何实际变更（f05）。",
            "data": {"before": before_n, "after": after_n,
                     "after_conflicts": [c.to_dict() for c in after if app_id in c.app_ids]},
            "summary": f"模拟改期：冲突 {before_n} → {after_n}"}


# ---------------------------- 写入类（有前置） ----------------------------
def _draft_change_request(args: dict, ctx: dict) -> dict:
    """★ 真正创建变更请求（仅在授权人确认后由 loop 调用）。前置：来源 + 冲突 stamp 有效。"""
    state = ctx["state"]
    app_id = args.get("app_id")
    if not _stamp_valid(state, "evidence") or not _stamp_valid(state, "conflict"):
        return {"ok": False, "code": 4003,
                "message": "来源校验或冲突检测未通过（f40），不得生成变更请求。"
                           "请先调用 validate_evidence(app_ids=[...]) 与 detect_conflict(app_id=...)。",
                "data": {}, "summary": "前置校验未通过，已拦截"}

    slots, handovers = _load_all()
    sources = []
    s = next((x for x in slots if x.app_id == app_id), None)
    if s and s.source:
        sources.append(s.source)
    sources += [h.source for h in handovers if h.app_id == app_id and h.source]
    if not sources:                                   # 二次确认 f40
        return {"ok": False, "code": 4003, "message": f"申请 {app_id} 无来源记录，不得进入正式流转（f40）。",
                "data": {}, "summary": "无来源，已拦截"}

    import secrets
    from datetime import datetime as _dt
    rid = f"CR-{_dt.now():%Y%m%d}-{secrets.token_hex(4)}"
    now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    store.execute(
        "INSERT INTO change_requests(id,app_id,change_type,payload_json,sources,status,reviewer,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (rid, app_id, args.get("change_type"), json.dumps(args, ensure_ascii=False),
         "；".join(sources), "awaiting_approval", ctx.get("reviewer", ""), now),
    )
    state["draft_request_id"] = rid
    return {"ok": True, "code": 0,
            "message": f"变更请求 {rid} 已创建，状态为【待授权人确认】。不得据此告知客户'已改档'（f41/f52）。",
            "data": {"request_id": rid, "status": "awaiting_approval", "sources": sources},
            "summary": f"变更请求 {rid} 待授权"}


def _submit_handover(args: dict, ctx: dict) -> dict:
    """★ 前置：check_handover 通过（stamp）。"""
    if not _stamp_valid(ctx["state"], "handover"):
        return {"ok": False, "code": 4003,
                "message": "交接校验未通过或内容已变更，请先调用 check_handover(app_id=...) 重新检查",
                "data": {}, "summary": "交接令牌无效，已拦截"}
    from datetime import datetime as _dt
    store.execute(
        "INSERT INTO change_requests(id,app_id,change_type,payload_json,sources,status,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (f"HO-{_dt.now():%Y%m%d}-{args.get('app_id')}", args.get("app_id"), "handover",
         json.dumps(args, ensure_ascii=False), args.get("source", ""), "submitted",
         _dt.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    return {"ok": True, "code": 0, "message": "交接记录已登记（含接收人/版本/未结项三项），可进入下游流转",
            "data": dict(args), "summary": "交接已登记"}


def _request_authorization(args: dict, ctx: dict) -> dict:
    """请求授权人作正式结论（f41）。只登记请求，不代替授权人决定。"""
    reviewer = args.get("reviewer")
    if reviewer not in AUTHORIZED_REVIEWERS:
        return {"ok": False, "code": 5101,
                "message": f"仅 {'、'.join(AUTHORIZED_REVIEWERS)} 有权作出正式结论（f41）",
                "data": {}, "summary": "非授权人员"}
    state = ctx["state"]
    rid = state.get("draft_request_id")
    if rid:
        store.execute("UPDATE change_requests SET reviewer=?, status='awaiting_approval' WHERE id=?",
                      (reviewer, rid))
    return {"ok": True, "code": 0,
            "message": f"已向 {reviewer} 发起确认请求，等待其作出正式结论。系统不得代为确认（f41）。",
            "data": {"request_id": rid, "reviewer": reviewer, "status": "awaiting_approval"},
            "summary": f"已请求 {reviewer} 确认"}


def _ask_user(args: dict, ctx: dict) -> dict:
    state = ctx["state"]
    state["ask_count"] = state.get("ask_count", 0) + 1
    return {"ok": True, "code": 0, "message": "已向用户提问，等待回答",
            "data": {"question": args.get("question"), "options": args.get("options") or []},
            "summary": "追问用户"}
