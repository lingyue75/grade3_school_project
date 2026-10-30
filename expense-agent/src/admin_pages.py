# -*- coding: utf-8 -*-
"""
服务端渲染页面（Jinja2 + Bootstrap 风格自研 CSS，不依赖外网 CDN）

页面清单见《接口设计-阶段3-管理后台》§1：
    /login  /chat  /admin  /admin/knowledge[/{id}]  /admin/prompts[/{id}]  /admin/audit[/{id}]
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from . import db, deps, prompt_service
from .errors import ERRORS
from .security import create_token, now_ts, verify_password
from .settings import PERMISSIONS, settings

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "web" / "templates"))
router = APIRouter()


def render(request: Request, name: str, ctx: dict | None = None):
    """统一渲染：注入当前用户、权限点、CSRF token，减少模板里的重复取值。"""
    user = deps.current_user(request)
    base = {
        "request": request,
        "user": user,
        "perms": PERMISSIONS.get(user["role"], set()) if user else set(),
        "csrf": getattr(request.state, "csrf", ""),
        "app_name": settings.app_name,
    }
    base.update(ctx or {})
    # 新版 Starlette 要求显式传 request / name / context（旧的两参数写法已移除）
    return templates.TemplateResponse(request=request, name=name, context=base)


# ============================================================================
# 登录 / 登出
# ============================================================================
@router.get("/login")
def login_page(request: Request, next: str = "/chat", err: str = ""):
    if deps.current_user(request):
        return RedirectResponse(url=next or "/chat")
    return render(request, "login.html", {"next": next, "err": ERRORS.get(int(err or 0), "")})


@router.post("/login")
def login_action(request: Request, employee_no: str = Form(""), password: str = Form(""),
                 csrf: str = Form(""), next: str = Form("/chat")):
    try:
        deps.verify_csrf(request, csrf)
    except Exception:
        return RedirectResponse(url="/login?err=1105", status_code=302)

    user = db.query_one("SELECT * FROM users WHERE employee_no=?", (employee_no,))
    if not user or not verify_password(password, user["password_hash"]):
        if user:
            db.execute("UPDATE users SET failed_count=failed_count+1 WHERE id=?", (user["id"],))
            if (user["failed_count"] or 0) + 1 >= settings.max_failed_login:
                db.execute("UPDATE users SET locked_until=? WHERE id=?",
                           (now_ts() + settings.lock_minutes * 60, user["id"]))
        return RedirectResponse(url=f"/login?err=1103&next={next}", status_code=302)

    if (user["locked_until"] or 0) > now_ts():
        return RedirectResponse(url="/login?err=1104", status_code=302)

    db.execute("UPDATE users SET failed_count=0 WHERE id=?", (user["id"],))
    token = create_token(user["id"], user["role"], user["employee_no"])
    target = "/admin" if user["role"] == "admin" and next in ("", "/chat") else (next or "/chat")
    resp = RedirectResponse(url=target, status_code=302)
    resp.set_cookie(settings.cookie_name, token, httponly=True, samesite="lax",
                    max_age=settings.jwt_expire_hours * 3600)
    return resp


@router.get("/logout")
def logout_page():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(settings.cookie_name)
    return resp


# ============================================================================
# 员工端：问答（阶段2 前端）
# ============================================================================
@router.get("/chat")
def chat_page(request: Request):
    user = deps.page_login_required(request)
    if isinstance(user, RedirectResponse):
        return user
    sessions = db.query_all(
        "SELECT * FROM sessions WHERE user_id=? AND status='active' ORDER BY updated_at DESC LIMIT 20",
        (user["id"],))
    return render(request, "chat.html", {
        "sessions": db.rows_to_dicts(sessions),
        "err": ERRORS.get(int(request.query_params.get("err") or 0), ""),
    })


# ============================================================================
# 管理端
# ============================================================================
@router.get("/admin")
def admin_home(request: Request):
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    docs = db.query_one("SELECT COUNT(*) AS c FROM knowledge_documents WHERE status<>'deleted'")
    chunks = db.query_one("SELECT COUNT(*) AS c FROM policy_chunks WHERE status='active'")
    talks = db.query_one("SELECT COUNT(*) AS c FROM messages WHERE role='user'")
    risks = db.query_one("SELECT COUNT(*) AS c FROM audit_flags WHERE resolved=0")
    from .rag import get_index
    index = get_index()
    active = prompt_service.get_active_for_display()
    logs = db.query_all(
        "SELECT l.*, u.name AS actor FROM audit_logs l LEFT JOIN users u ON u.id=l.actor_id "
        "ORDER BY l.created_at DESC LIMIT 8")
    return render(request, "admin_overview.html", {
        "stat": {"docs": docs["c"], "chunks": chunks["c"], "talks": talks["c"], "risks": risks["c"],
                 "ready": index.ready},
        "active_prompt": active,
        "logs": db.rows_to_dicts(logs),
    })


@router.get("/admin/knowledge")
def admin_knowledge(request: Request, status: str = ""):
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    sql = ("SELECT d.*, u.name AS uploader FROM knowledge_documents d LEFT JOIN users u ON u.id=d.uploaded_by "
           "WHERE d.status<>'deleted'")
    params: list = []
    if status:
        sql += " AND d.status=?"; params.append(status)
    sql += " ORDER BY d.created_at DESC"
    return render(request, "admin_knowledge.html", {"docs": db.rows_to_dicts(db.query_all(sql, params)),
                                                    "status": status})


@router.get("/admin/knowledge/{doc_id}")
def admin_knowledge_detail(request: Request, doc_id: str, page: int = 1):
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    doc = db.query_one("SELECT * FROM knowledge_documents WHERE id=?", (doc_id,))
    if not doc:
        return RedirectResponse(url="/admin/knowledge", status_code=302)
    size = 20
    rows = db.query_all(
        "SELECT id, chunk_index, clause_no, heading_path, text, status FROM policy_chunks "
        "WHERE document_id=? ORDER BY chunk_index LIMIT ? OFFSET ?", (doc_id, size, (page - 1) * size))
    total = db.query_one("SELECT COUNT(*) AS c FROM policy_chunks WHERE document_id=?", (doc_id,))
    return render(request, "admin_knowledge_detail.html", {
        "doc": dict(doc), "chunks": db.rows_to_dicts(rows), "page": page, "size": size,
        "total": total["c"] if total else 0})


@router.get("/admin/prompts")
def admin_prompts(request: Request):
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "admin_prompts.html", {
        "items": prompt_service.list_templates(),
        "active": prompt_service.get_active_for_display()})


@router.get("/admin/prompts/new")
def admin_prompt_new(request: Request):
    """新建模板：以当前生效版本为初始内容，创建草稿后跳转编辑页。"""
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    active = prompt_service.get_active_for_display()
    tpl = prompt_service.create_template(f"模板 v{active['version_no'] + 1}", active["content"], user["id"],
                                         "由管理员新建")
    return RedirectResponse(url=f"/admin/prompts/{tpl['id']}", status_code=302)


@router.get("/admin/prompts/{tpl_id}")
def admin_prompt_edit(request: Request, tpl_id: str):
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    try:
        tpl = prompt_service.get_template(tpl_id)
    except Exception:
        return RedirectResponse(url="/admin/prompts", status_code=302)
    return render(request, "admin_prompt_edit.html", {"tpl": tpl})


@router.get("/admin/audit")
def admin_audit(request: Request, employee_no: str = "", keyword: str = "",
                risk_only: bool = False, date_from: str = "", date_to: str = "", page: int = 1):
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    size = 20
    sql = ("SELECT m.id, m.session_id, m.content, m.risk_flag, m.created_at, s.user_id, "
           "u.name AS user_name, u.employee_no FROM messages m "
           "JOIN sessions s ON s.id=m.session_id LEFT JOIN users u ON u.id=s.user_id "
           "WHERE m.role='user'")
    params: list = []
    if employee_no:
        sql += " AND u.employee_no LIKE ?"; params.append(f"%{employee_no}%")
    if keyword:
        sql += " AND m.content LIKE ?"; params.append(f"%{keyword}%")
    if date_from:
        sql += " AND m.created_at >= ?"; params.append(date_from)
    if date_to:
        sql += " AND m.created_at <= ?"; params.append(date_to + " 23:59:59")
    if risk_only:
        sql += " AND EXISTS(SELECT 1 FROM audit_flags f WHERE f.session_id=m.session_id AND f.resolved=0)"
    sql += " ORDER BY m.created_at DESC LIMIT ? OFFSET ?"
    params += [size, (page - 1) * size]
    rows = db.rows_to_dicts(db.query_all(sql, params))
    # 附带每个会话的标记数
    for r in rows:
        f = db.query_one("SELECT COUNT(*) AS c FROM audit_flags WHERE session_id=? AND resolved=0", (r["session_id"],))
        r["flag_count"] = f["c"] if f else 0
    rules = db.rows_to_dicts(db.query_all("SELECT * FROM risk_rules ORDER BY level, name"))
    return render(request, "admin_audit.html", {
        "rows": rows, "rules": rules, "filters": {"employee_no": employee_no, "keyword": keyword,
                                                  "risk_only": risk_only, "date_from": date_from,
                                                  "date_to": date_to}, "page": page})


@router.get("/admin/audit/{session_id}")
def admin_audit_detail(request: Request, session_id: str):
    import json
    user = deps.page_admin_required(request)
    if isinstance(user, RedirectResponse):
        return user
    sess = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not sess:
        return RedirectResponse(url="/admin/audit", status_code=302)
    msgs = db.query_all("SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,))
    items = []
    for m in msgs:
        d = dict(m)
        try:
            d["retrieval_refs"] = json.loads(d.get("retrieval_refs") or "[]")
        except Exception:
            d["retrieval_refs"] = []
        d["flags"] = db.rows_to_dicts(db.query_all(
            "SELECT f.*, r.name AS rule_name FROM audit_flags f LEFT JOIN risk_rules r ON r.id=f.rule_id "
            "WHERE f.message_id=?", (d["id"],)))
        items.append(d)
    owner = db.query_one("SELECT name, employee_no FROM users WHERE id=?", (sess["user_id"],))
    return render(request, "admin_audit_detail.html", {
        "session": dict(sess), "items": items, "owner": dict(owner) if owner else {}})
