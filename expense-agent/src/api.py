# -*- coding: utf-8 -*-
"""
JSON / SSE 接口（★核心复用：对应 src/api/v1/*）

约定：
  · 非流式接口统一返回 {code, message, data, request_id}
  · POST /chat 返回 SSE 流（text/event-stream），事件定义见《接口设计-阶段2》§2
  · 所有错误经全局异常处理转成中文错误码，不泄露堆栈
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import db, deps, prompt_service, service_chat
from .errors import ERRORS, BizError, fail, ok
from .rag import get_index, parse_file, split_text
from .security import new_id, now_str, rand_filename
from .settings import settings

router = APIRouter(prefix="/api/v1")


# ============================================================================
# 请求体定义
# ============================================================================
class LoginIn(BaseModel):
    employee_no: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class ChatIn(BaseModel):
    session_id: Optional[str] = None       # 为空则自动新建会话
    question: str = Field(..., min_length=1, max_length=1000)
    stream: bool = True
    top_k: Optional[int] = None


class SessionIn(BaseModel):
    title: Optional[str] = "新会话"


class PromptIn(BaseModel):
    name: Optional[str] = None
    content: str = ""
    note: Optional[str] = ""


class TryIn(BaseModel):
    question: str


class FlagIn(BaseModel):
    note: str = ""
    resolve: Optional[str] = None          # confirmed_violation / false_positive


# 登录限流（内存计数，生产换 Redis）
_login_attempts: dict[str, list[float]] = {}


def _rate_ok(key: str) -> bool:
    now = time.time()
    hits = [t for t in _login_attempts.get(key, []) if now - t < 300]
    hits.append(now)
    _login_attempts[key] = hits
    return len(hits) <= 10


# ============================================================================
# 健康检查
# ============================================================================
@router.get("/health")
def health():
    index = get_index()
    return ok({
        "app": settings.app_name,
        "db": Path(settings.db_path).exists(),
        "rag_ready": index.ready,
        "chunks": len(index.texts),
        "llm_provider": settings.llm_provider,
        "embed_backend": settings.embed_backend,
    })


# ============================================================================
# 鉴权
# ============================================================================
@router.post("/auth/login")
def login(body: LoginIn, request: Request):
    """登录：校验工号密码 → 签发 JWT 写入 HttpOnly Cookie。"""
    if not _rate_ok(body.employee_no):
        raise BizError(1201)

    user = db.query_one("SELECT * FROM users WHERE employee_no=?", (body.employee_no,))
    from .security import verify_password, create_token, now_ts
    if not user or not verify_password(body.password, user["password_hash"]):
        if user:                                     # 失败计数 + 锁定时长
            db.execute("UPDATE users SET failed_count=failed_count+1 WHERE id=?", (user["id"],))
            if (user["failed_count"] or 0) + 1 >= settings.max_failed_login:
                db.execute("UPDATE users SET locked_until=? WHERE id=?",
                           (now_ts() + settings.lock_minutes * 60, user["id"]))
        raise BizError(1103)

    if (user["locked_until"] or 0) > now_ts():
        raise BizError(1104)

    db.execute("UPDATE users SET failed_count=0, last_login_at=? WHERE id=?", (now_str(), user["id"]))
    token = create_token(user["id"], user["role"], user["employee_no"])

    resp = JSONResponse(ok({"user_id": user["id"], "name": user["name"], "role": user["role"]}))
    resp.set_cookie(settings.cookie_name, token, httponly=True, samesite="lax",
                    max_age=settings.jwt_expire_hours * 3600)
    return resp


@router.post("/auth/logout")
def logout():
    resp = JSONResponse(ok({"logout": True}))
    resp.delete_cookie(settings.cookie_name)
    return resp


@router.get("/auth/me")
def me(user=Depends(deps.require_login)):
    from .settings import PERMISSIONS
    return ok({"user_id": user["id"], "name": user["name"], "employee_no": user["employee_no"],
               "role": user["role"], "permissions": sorted(PERMISSIONS.get(user["role"], set()))})


# ============================================================================
# 问答（SSE）
# ============================================================================
@router.post("/chat")
def chat(body: ChatIn, request: Request, user=Depends(deps.require_perm("chat:use"))):
    question = (body.question or "").strip()
    if not question:
        raise BizError(1001, "问题内容不能为空")
    if len(question) > 1000:
        raise BizError(1002)

    request_id = new_id("req")

    if not body.stream:                              # 非流式：便于自动化测试与评测
        res = service_chat.chat_once(question, top_k=body.top_k)
        return ok({"answer": res["answer"], "sources": res["sources"], "request_id": request_id})

    # 顶层判空：知识库为空时直接给业务性提示，避免空检索浪费一次模型调用
    index = get_index()
    if not index.ready:
        def _empty():
            yield service_chat.sse("meta", {"session_id": body.session_id or "", "message_id": "",
                                            "created_at": now_str(), "request_id": request_id})
            yield service_chat.sse("sources", {"sources": []})
            yield service_chat.sse("delta", {"content": ERRORS[3001]})
            yield service_chat.sse("done", {"finish_reason": "stop", "code": 3001})
            yield "data: [DONE]\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def _gen():
        # 心跳注释：防止反向代理掐断长连接（数据持续到达时无需心跳）
        yield service_chat.sse_comment("connected")
        for chunk in service_chat.chat_events(user["id"], body.session_id, question, request_id):
            yield chunk

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ============================================================================
# 会话
# ============================================================================
@router.post("/sessions")
def create_session(body: SessionIn, user=Depends(deps.require_login)):
    sess = service_chat.create_session(user["id"], body.title or "新会话", settings.llm_model)
    return ok({"session_id": sess["id"], "title": sess["title"], "created_at": sess["created_at"]})


@router.get("/sessions")
def sessions(user=Depends(deps.require_login)):
    return ok({"items": service_chat.list_sessions(user["id"])})


@router.get("/sessions/{session_id}/messages")
def session_messages(session_id: str, user=Depends(deps.require_login)):
    sess = service_chat.get_session(session_id)
    if not sess:
        raise BizError(2001)
    if sess["user_id"] != user["id"] and user["role"] != "admin":
        raise BizError(1102)
    rows = db.query_all(
        "SELECT id, seq, role, content, retrieval_refs, finish_reason, risk_flag, created_at "
        "FROM messages WHERE session_id=? ORDER BY seq", (session_id,))
    items = []
    for r in rows:
        d = dict(r)
        d["retrieval_refs"] = _json_load(d.get("retrieval_refs"))
        items.append(d)
    return ok({"session": sess, "items": items})


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user=Depends(deps.require_login)):
    sess = service_chat.get_session(session_id)
    if not sess:
        raise BizError(2001)
    if sess["user_id"] != user["id"] and user["role"] != "admin":
        raise BizError(1102)
    db.execute("UPDATE sessions SET status='archived' WHERE id=?", (session_id,))
    return ok({"session_id": session_id, "status": "archived"})


@router.get("/sources/{chunk_id}")
def source_detail(chunk_id: int, user=Depends(deps.require_login)):
    row = db.query_one(
        "SELECT c.*, d.title AS file FROM policy_chunks c "
        "LEFT JOIN knowledge_documents d ON d.id=c.document_id WHERE c.id=?", (chunk_id,))
    if not row:
        raise BizError(3001, "未找到该条文")
    return ok(dict(row))


def _json_load(s):
    import json
    try:
        return json.loads(s) if s else []
    except Exception:
        return []


# ============================================================================
# 管理：制度知识库
# ============================================================================
@router.post("/admin/knowledge/upload")
async def upload_document(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    csrf: str = Form(""),
    user=Depends(deps.require_perm("knowledge:write")),
):
    deps.verify_csrf(request, csrf)

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in (".md", ".markdown", ".pdf"):
        raise BizError(5201)
    source_type = "md" if ext in (".md", ".markdown") else "pdf"

    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise BizError(5202, f"文件大小超过 {settings.max_upload_mb}MB 限制")

    file_hash = hashlib.md5(content).hexdigest()
    dup = db.query_one("SELECT id FROM knowledge_documents WHERE file_hash=? AND status<>'deleted'", (file_hash,))
    if dup:
        raise BizError(5206)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved = upload_dir / rand_filename(filename)          # 随机名，防路径穿越
    saved.write_bytes(content)

    doc_id = new_id("doc")
    task_id = new_id("task")
    db.execute(
        "INSERT INTO knowledge_documents(id,title,source_type,file_url,file_hash,status,uploaded_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (doc_id, filename, source_type, str(saved), file_hash, "pending", user["id"], now_str()),
    )
    db.execute(
        "INSERT INTO ingest_tasks(id,document_id,stage,progress,started_at) VALUES(?,?,?,?,?)",
        (task_id, doc_id, "parsing", 0, now_str()),
    )
    db.write_log(user["id"], "knowledge.upload", "document", doc_id, {"title": filename})

    # 原型阶段用 BackgroundTasks；生产应换成 Celery/ARQ 独立 worker（避免阻塞请求线程）
    background.add_task(_ingest, doc_id, task_id, saved, source_type, filename)
    return ok({"document_id": doc_id, "task_id": task_id, "title": filename})


def _ingest(doc_id: str, task_id: str, path: Path, source_type: str, title: str) -> None:
    """摄取任务：解析 → 切片 → 向量化入库。任何失败都落库并写清原因，不静默失败。"""
    index = get_index()
    try:
        db.execute("UPDATE knowledge_documents SET status='parsing' WHERE id=?", (doc_id,))
        text = parse_file(path, source_type)                       # 5203 在此抛出

        db.execute("UPDATE knowledge_documents SET status='chunking' WHERE id=?", (doc_id,))
        db.execute("UPDATE ingest_tasks SET stage='chunking', progress=40 WHERE id=?", (task_id,))
        chunks = split_text(text, title, source_type)
        if not chunks:
            raise RuntimeError("未能从文档中切出任何内容，请确认文档非空")

        db.execute("UPDATE knowledge_documents SET status='embedding' WHERE id=?", (doc_id,))
        db.execute("UPDATE ingest_tasks SET stage='embedding', progress=70 WHERE id=?", (task_id,))
        for c in chunks:
            c["document_id"] = doc_id
        count = index.add_chunks(chunks)

        db.execute("UPDATE knowledge_documents SET status='ready', chunk_count=?, error_text=NULL WHERE id=?",
                   (count, doc_id))
        db.execute("UPDATE ingest_tasks SET stage='ready', progress=100, finished_at=? WHERE id=?",
                   (now_str(), task_id))
    except Exception as exc:
        msg = str(exc)[:500]
        db.execute("UPDATE knowledge_documents SET status='failed', error_text=? WHERE id=?", (msg, doc_id))
        db.execute("UPDATE ingest_tasks SET stage='failed', error_text=?, finished_at=? WHERE id=?",
                   (msg, now_str(), task_id))


@router.get("/admin/knowledge")
def list_documents(user=Depends(deps.require_perm("knowledge:read")), status: str = ""):
    sql = ("SELECT d.*, u.name AS uploader FROM knowledge_documents d "
           "LEFT JOIN users u ON u.id=d.uploaded_by")
    params: list = []
    if status:
        sql += " WHERE d.status=?"
        params.append(status)
    sql += " ORDER BY d.created_at DESC"
    return ok({"items": db.rows_to_dicts(db.query_all(sql, params))})


@router.get("/admin/knowledge/{doc_id}")
def document_detail(doc_id: str, user=Depends(deps.require_perm("knowledge:read"))):
    row = db.query_one("SELECT * FROM knowledge_documents WHERE id=?", (doc_id,))
    if not row:
        raise BizError(5205)
    return ok(dict(row))


@router.get("/admin/knowledge/{doc_id}/chunks")
def document_chunks(doc_id: str, page: int = 1, size: int = 20,
                    user=Depends(deps.require_perm("knowledge:read"))):
    """分片预览：核对切片质量的唯一手段（决定 RAG 成败，必须有）。"""
    offset = (max(page, 1) - 1) * size
    rows = db.query_all(
        "SELECT id, chunk_index, clause_no, heading_path, text, status FROM policy_chunks "
        "WHERE document_id=? ORDER BY chunk_index LIMIT ? OFFSET ?", (doc_id, size, offset))
    total = db.query_one("SELECT COUNT(*) AS c FROM policy_chunks WHERE document_id=?", (doc_id,))
    return ok({"items": db.rows_to_dicts(rows), "total": total["c"] if total else 0, "page": page, "size": size})


@router.delete("/admin/knowledge/{doc_id}")
def delete_document(doc_id: str, request: Request, csrf: str = Form(""),
                    user=Depends(deps.require_perm("knowledge:write"))):
    deps.verify_csrf(request, csrf)
    row = db.query_one("SELECT * FROM knowledge_documents WHERE id=? AND status<>'deleted'", (doc_id,))
    if not row:
        raise BizError(5205)
    index = get_index()
    removed = index.soft_delete_document(doc_id)          # 软删：向量库留待重建
    db.execute("UPDATE knowledge_documents SET status='deleted', deleted_at=? WHERE id=?", (now_str(), doc_id))
    db.write_log(user["id"], "knowledge.delete", "document", doc_id, {"title": row["title"], "chunks": removed})
    return ok({"document_id": doc_id, "removed_chunks": removed,
               "message": "已删除，向量索引将在下次重建时清理"})


@router.post("/admin/knowledge/{doc_id}/delete")
def delete_document_post(doc_id: str, request: Request, csrf: str = Form(""),
                         user=Depends(deps.require_perm("knowledge:write"))):
    """删除的 POST 别名：服务端渲染页面用表单提交（DELETE 方法不便在 HTML 表单中使用）。"""
    return delete_document(doc_id, request, csrf, user)


@router.post("/admin/knowledge/reindex")
def reindex(request: Request, csrf: str = Form(""), user=Depends(deps.require_perm("knowledge:write"))):
    deps.verify_csrf(request, csrf)
    index = get_index()
    index.rebuild()                                      # 全量重灌，清掉已软删分片
    db.write_log(user["id"], "knowledge.reindex", "index", "", {"chunks": len(index.texts)})
    return ok({"chunks": len(index.texts), "message": "向量索引已重建"})


@router.get("/admin/tasks/{task_id}")
def task_status(task_id: str, user=Depends(deps.require_perm("knowledge:read"))):
    row = db.query_one("SELECT * FROM ingest_tasks WHERE id=?", (task_id,))
    if not row:
        raise BizError(5205, "任务不存在")
    return ok(dict(row))


# ============================================================================
# 管理：提示词模板
# ============================================================================
@router.get("/admin/prompts")
def list_prompts(user=Depends(deps.require_perm("prompt:read"))):
    return ok({"items": prompt_service.list_templates(), "active": prompt_service.get_active_for_display()})


@router.post("/admin/prompts")
def create_prompt(body: PromptIn, user=Depends(deps.require_perm("prompt:write"))):
    return ok(prompt_service.create_template(body.name or "", body.content, user["id"], body.note or ""))


@router.get("/admin/prompts/{tpl_id}")
def get_prompt(tpl_id: str, user=Depends(deps.require_perm("prompt:read"))):
    return ok(prompt_service.get_template(tpl_id))


@router.put("/admin/prompts/{tpl_id}")
def update_prompt(tpl_id: str, body: PromptIn, user=Depends(deps.require_perm("prompt:write"))):
    return ok(prompt_service.update_draft(tpl_id, body.content, body.name, user["id"]))


@router.post("/admin/prompts/{tpl_id}/publish")
def publish_prompt(tpl_id: str, request: Request, csrf: str = Form(""),
                   user=Depends(deps.require_perm("prompt:write"))):
    deps.verify_csrf(request, csrf)
    return ok(prompt_service.publish(tpl_id, user["id"]))


@router.post("/admin/prompts/{tpl_id}/activate")
def activate_prompt(tpl_id: str, request: Request, csrf: str = Form(""),
                    user=Depends(deps.require_perm("prompt:write"))):
    deps.verify_csrf(request, csrf)
    return ok(prompt_service.activate(tpl_id, user["id"]))


@router.post("/admin/prompts/{tpl_id}/archive")
def archive_prompt(tpl_id: str, request: Request, csrf: str = Form(""),
                   user=Depends(deps.require_perm("prompt:write"))):
    deps.verify_csrf(request, csrf)
    prompt_service.archive(tpl_id, user["id"])
    return ok({"id": tpl_id, "status": "archived"})


@router.post("/admin/prompts/{tpl_id}/try")
def try_prompt(tpl_id: str, body: TryIn, user=Depends(deps.require_perm("prompt:read"))):
    """在线试问：用指定模板跑一句，验证效果再发布（不落库）。"""
    tpl = prompt_service.get_template(tpl_id)
    res = service_chat.chat_once(body.question, system_prompt=tpl["content"] + prompt_service.OUTPUT_RULES)
    return ok({"answer": res["answer"], "sources": res["sources"], "latency_ms": res["latency_ms"]})


# ============================================================================
# 管理：对话审计
# ============================================================================
@router.get("/admin/audit/conversations")
def audit_conversations(
    user=Depends(deps.require_perm("audit:read")),
    employee_no: str = "", date_from: str = "", date_to: str = "",
    keyword: str = "", risk_only: bool = False, page: int = 1, size: int = 20,
):
    """按员工/时间/关键词筛选；risk_only=只看被标记的。"""
    sql = ("SELECT m.id, m.session_id, m.seq, m.role, m.content, m.risk_flag, m.created_at, "
           "s.user_id, u.name AS user_name, u.employee_no "
           "FROM messages m JOIN sessions s ON s.id=m.session_id "
           "LEFT JOIN users u ON u.id=s.user_id WHERE m.role='user'")
    params: list = []
    if employee_no:
        sql += " AND u.employee_no LIKE ?"
        params.append(f"%{employee_no}%")
    if date_from:
        sql += " AND m.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND m.created_at <= ?"
        params.append(date_to + " 23:59:59")
    if keyword:
        sql += " AND m.content LIKE ?"
        params.append(f"%{keyword}%")
    if risk_only:
        sql += " AND EXISTS(SELECT 1 FROM audit_flags f WHERE f.session_id=m.session_id AND f.resolved=0)"
    sql += " ORDER BY m.created_at DESC LIMIT ? OFFSET ?"
    params += [size, (max(page, 1) - 1) * size]
    return ok({"items": db.rows_to_dicts(db.query_all(sql, params)), "page": page, "size": size})


@router.get("/admin/audit/sessions/{session_id}")
def audit_session(session_id: str, user=Depends(deps.require_perm("audit:read"))):
    sess = service_chat.get_session(session_id)
    if not sess:
        raise BizError(5401)
    msgs = db.query_all("SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,))
    items = []
    for m in msgs:
        d = dict(m)
        d["retrieval_refs"] = _json_load(d.get("retrieval_refs"))
        flags = db.query_all(
            "SELECT f.*, r.name AS rule_name FROM audit_flags f LEFT JOIN risk_rules r ON r.id=f.rule_id "
            "WHERE f.message_id=?", (d["id"],))
        d["flags"] = db.rows_to_dicts(flags)
        items.append(d)
    return ok({"session": sess, "items": items})


@router.post("/admin/audit/messages/{message_id}/flag")
def flag_message(message_id: str, body: FlagIn, request: Request, csrf: str = Form(""),
                 user=Depends(deps.require_perm("audit:flag"))):
    """人工标记/复核。系统只做"疑似"标记，不做违规判定。"""
    deps.verify_csrf(request, csrf)
    msg = db.query_one("SELECT m.*, s.user_id FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.id=?",
                       (message_id,))
    if not msg:
        raise BizError(5401)
    if body.resolve:                                   # 复核结论
        db.execute("UPDATE audit_flags SET resolved=1, resolution=?, resolved_by=?, resolved_at=? WHERE message_id=?",
                   (body.resolve, user["id"], now_str(), message_id))
        db.execute("UPDATE messages SET risk_flag=? WHERE id=?", (0 if body.resolve == "false_positive" else 1,
                                                                  message_id))
        db.write_log(user["id"], "audit.resolve", "message", message_id, {"resolution": body.resolve})
        return ok({"message_id": message_id, "resolution": body.resolve})
    # 人工新增标记：说明必填，避免无依据标记
    if not body.note.strip():
        raise BizError(5402)
    service_chat.flag_message(message_id, msg["session_id"], msg["user_id"], {}, auto=False,
                              note=body.note, created_by=user["id"])
    db.write_log(user["id"], "audit.flag", "message", message_id, {"note": body.note})
    return ok({"message_id": message_id, "flagged": True})


@router.get("/admin/audit/risk-rules")
def risk_rules(user=Depends(deps.require_perm("audit:read"))):
    return ok({"items": db.rows_to_dicts(db.query_all("SELECT * FROM risk_rules ORDER BY level, name"))})


@router.put("/admin/audit/risk-rules/{rule_id}")
def update_risk_rule(rule_id: str, request: Request, enabled: int = Form(1), csrf: str = Form(""),
                     user=Depends(deps.require_perm("audit:flag"))):
    deps.verify_csrf(request, csrf)
    db.execute("UPDATE risk_rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))
    return ok({"id": rule_id, "enabled": bool(enabled)})


@router.get("/admin/audit/export")
def export_audit(user=Depends(deps.require_perm("audit:read")), date_from: str = "", date_to: str = ""):
    """导出 CSV（审计留痕）。"""
    sql = ("SELECT m.created_at, u.employee_no, u.name, m.session_id, m.role, m.content, m.risk_flag "
           "FROM messages m JOIN sessions s ON s.id=m.session_id LEFT JOIN users u ON u.id=s.user_id "
           "WHERE 1=1")
    params: list = []
    if date_from:
        sql += " AND m.created_at >= ?"; params.append(date_from)
    if date_to:
        sql += " AND m.created_at <= ?"; params.append(date_to + " 23:59:59")
    sql += " ORDER BY m.created_at"
    rows = db.query_all(sql, params)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["时间", "工号", "姓名", "会话ID", "角色", "内容", "疑似违规"])
    for r in rows:
        writer.writerow([r["created_at"], r["employee_no"], r["name"], r["session_id"],
                         r["role"], (r["content"] or "").replace("\n", " "), "是" if r["risk_flag"] else ""])
    buf.seek(0)
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=audit.csv"})
