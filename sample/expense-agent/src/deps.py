# -*- coding: utf-8 -*-
"""
依赖注入：登录态、角色、权限点（★核心复用：对应 src/core/deps.py）

使用方式：
    @router.post("/admin/knowledge/upload")
    def upload(user = Depends(require_perm("knowledge:write"))): ...

要点：
    · 权限校验在【接口级】，页面上的菜单显隐只是体验优化，不作为安全边界。
    · JWT 从 HttpOnly Cookie 读取（服务端渲染场景不用 localStorage）。
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from . import db, security
from .errors import BizError
from .settings import PERMISSIONS


def current_user(request: Request) -> dict | None:
    """从 Cookie 中的 JWT 解析当前用户；未登录/过期返回 None（不抛异常）。"""
    token = request.cookies.get(settings_cookie_name())
    if not token:
        return None
    payload = security.decode_token(token)
    if not payload:
        return None
    row = db.query_one("SELECT * FROM users WHERE id=? AND status='active'", (payload.get("sub", ""),))
    if not row:
        return None
    user = dict(row)
    user["_token"] = payload
    return user


def settings_cookie_name() -> str:
    from .settings import settings
    return settings.cookie_name


def require_login(request: Request) -> dict:
    """必须登录，否则 1101。"""
    user = current_user(request)
    if not user:
        raise BizError(1101)
    return user


def require_perm(perm: str):
    """接口级权限点校验：无权限抛 5101。"""
    def _dep(user: dict = Depends(require_login)) -> dict:
        role = user.get("role", "employee")
        if perm not in PERMISSIONS.get(role, set()):
            raise BizError(5101)
        return user
    return _dep


def require_admin(user: dict = Depends(require_login)) -> dict:
    if user.get("role") != "admin":
        raise BizError(5101)
    return user


# ============================================================================
# 页面用：未登录跳转登录页（服务端渲染）
# ============================================================================
def page_login_required(request: Request):
    """SSR 页面使用：未登录直接 302 到 /login，并带 return_url。"""
    user = current_user(request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)
    return user


def page_admin_required(request: Request):
    """SSR 管理页：未登录跳登录页；已登录但非管理员跳 /chat 并提示。"""
    user = current_user(request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse(url="/chat?err=5101", status_code=302)
    return user


# ============================================================================
# CSRF（双提交 Cookie）
# ============================================================================
def verify_csrf(request: Request, form_value: str | None) -> None:
    cookie_val = request.cookies.get(security_csrf_name())
    if not security.csrf_ok(cookie_val, form_value):
        raise BizError(1105)


def security_csrf_name() -> str:
    from .settings import settings
    return settings.csrf_cookie_name


def issue_csrf(request: Request) -> str:
    """为表单生成 CSRF token（同时写入 Cookie，由路由在响应里 set_cookie）。"""
    token = request.cookies.get(security_csrf_name()) or security.new_csrf_token()
    return token
