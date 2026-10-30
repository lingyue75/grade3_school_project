# -*- coding: utf-8 -*-
"""
应用装配入口（main.py）

职责：初始化数据库与检索索引 → 挂载静态资源 → 注册路由 → 全局异常与 CSRF 中间件。
运行：uvicorn app.main:app --reload   或   python run_server.py
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import admin_pages, agent_api, api, db, deps
from .agent import schema as agent_schema
from .errors import BizError, fail
from .rag import get_index, parse_file, split_text
from .security import new_csrf_token, new_id, now_str
from .settings import settings

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title=settings.app_name, debug=settings.debug)

# 静态资源与后台页面路由
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "web" / "static")), name="static")
app.include_router(api.router)
app.include_router(agent_api.router)
app.include_router(admin_pages.router)


# ============================================================================
# CSRF 双提交 Cookie：为每个请求准备 token，缺失时写入 Cookie（JS 需可读，故非 HttpOnly）
# ============================================================================
@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    token = request.cookies.get(settings.csrf_cookie_name) or new_csrf_token()
    request.state.csrf = token
    response = await call_next(request)
    if not request.cookies.get(settings.csrf_cookie_name):
        response.set_cookie(settings.csrf_cookie_name, token, samesite="lax", httponly=False)
    return response


# ============================================================================
# 全局异常处理：业务异常 → 中文错误码；未捕获异常 → 5000（不泄露堆栈）
# ============================================================================
@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    return JSONResponse(status_code=exc.http_status,
                        content=fail(exc.code, exc.message, getattr(request.state, "request_id", "")))


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    if settings.debug:
        print(f"[未捕获异常] {request.url}: {exc}")
    return JSONResponse(status_code=500, content=fail(5000))


# ============================================================================
# 启动：建表 + 加载索引 + 导入示例制度文档（首次运行自动完成，保证"跑即可用"）
# ============================================================================
@app.on_event("startup")
def on_startup():
    import time
    t0 = time.time()
    db.init_db()
    agent_schema.init_agent_tables()      # Agent 相关表与演示票据
    get_index().load_or_build()
    _seed_sample_document()
    print(f"[启动完成] 耗时 {time.time() - t0:.2f}s → http://{settings.host}:{settings.port}")


def _seed_sample_document() -> None:
    """首次运行时把示例制度文档自动导入知识库，避免"跑起来却没有数据"。"""
    if db.query_one("SELECT id FROM knowledge_documents WHERE status<>'deleted' LIMIT 1"):
        return
    sample = BASE_DIR / "sample" / "data" / "差旅报销制度.md"
    if not sample.exists():
        return
    doc_id, task_id = new_id("doc"), new_id("task")
    db.execute(
        "INSERT INTO knowledge_documents(id,title,source_type,file_url,file_hash,status,uploaded_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (doc_id, sample.name, "md", str(sample), "seed", "ready", "system", now_str()),
    )
    try:
        text = parse_file(sample, "md")
        chunks = split_text(text, sample.name, "md")
        for c in chunks:
            c["document_id"] = doc_id
        count = get_index().add_chunks(chunks)
        db.execute("UPDATE knowledge_documents SET chunk_count=? WHERE id=?", (count, doc_id))
        print(f"[初始化] 已导入示例制度文档：{sample.name}（{count} 个分片）")
    except Exception as exc:
        db.execute("UPDATE knowledge_documents SET status='failed', error_text=? WHERE id=?", (str(exc), doc_id))


# ============================================================================
# 根路径
# ============================================================================
@app.get("/")
def root():
    return RedirectResponse(url="/chat")
