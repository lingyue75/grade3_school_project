# -*- coding: utf-8 -*-
"""
安全工具：密码哈希、JWT 签发/校验、CSRF（★核心复用：对应 src/core/security.py）

设计说明：
  · 依赖只用标准库（hashlib / hmac / base64 / json），避免 bcrypt、passlib 等编译型依赖，
    原型阶段可直接在任何环境跑起来；生产建议换成 passlib[argon2]。
  · JWT 存 HttpOnly Cookie（服务端渲染场景不用 localStorage，防 XSS 窃取）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from .settings import settings


# ============================================================================
# 1. 密码哈希：PBKDF2-HMAC-SHA256 + 随机盐
# ============================================================================
def hash_password(password: str) -> str:
    """返回 'salt$hash' 形式的密码摘要。"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码；存储格式损坏时返回 False（不抛异常，避免泄露内部状态）。"""
    if not stored or "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return hmac.compare_digest(dk.hex(), h)   # 定长比较，防时序攻击


# ============================================================================
# 2. JWT（HS256，标准库实现）
#    header.payload.signature，payload 含 exp / sub / role / iat / jti
# ============================================================================
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(user_id: str, role: str, employee_no: str = "") -> str:
    """签发 JWT，有效期由 settings.jwt_expire_hours 控制。"""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,                 # 用户 ID
        "role": role,                   # employee / admin
        "eno": employee_no,             # 工号，审计展示用
        "iat": now,
        "exp": now + settings.jwt_expire_hours * 3600,
        "jti": secrets.token_hex(8),    # 便于将来做黑名单
    }
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def decode_token(token: str) -> dict[str, Any] | None:
    """校验并解析 JWT；失败（篡改/过期/格式错）一律返回 None，不抛异常。"""
    if not token or token.count(".") != 2:
        return None
    h, p, s = token.split(".")
    try:
        expected = hmac.new(settings.jwt_secret.encode(), f"{h}.{p}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_encode(expected), s):
            return None                                  # 签名不匹配
        payload = json.loads(_b64url_decode(p))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None                                  # 已过期
        return payload
    except Exception:
        return None


# ============================================================================
# 3. CSRF：双提交 Cookie（Cookie 一份 + 表单隐藏域一份，服务端比对）
# ============================================================================
def new_csrf_token() -> str:
    return secrets.token_hex(24)


def csrf_ok(cookie_value: str | None, form_value: str | None) -> bool:
    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(cookie_value, form_value)


# ============================================================================
# 4. 其他小工具
# ============================================================================
def new_id(prefix: str) -> str:
    """生成可读的带前缀 ID，如 se_9f3a1c、ms_7b21de。"""
    return f"{prefix}_{secrets.token_hex(8)}"


def now_ts() -> int:
    return int(time.time())


def now_str() -> str:
    """SQLite 可直接比较的时间字符串（UTC）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def rand_filename(original: str) -> str:
    """上传文件重命名：防止路径穿越与中文名乱码，保留扩展名。"""
    ext = os.path.splitext(original)[1].lower()
    return f"{secrets.token_hex(12)}{ext}"
