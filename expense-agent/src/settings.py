# -*- coding: utf-8 -*-
"""
全局配置（★核心复用：对应 src/core/config.py）

约定：所有外部依赖（密钥、模型地址、开关）只在本文件出现，业务代码不直接读环境变量。
阶段1 的 EmbeddingClient 直接消费本 Settings 的 embed_* 字段，实现零改动复用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)


# 项目根目录与本文件同级目录的上级（expense-agent/）
BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    """应用配置。生产环境建议换成 pydantic-settings，字段可原样迁移。"""

    # ---------------- 服务 ----------------
    app_name: str = "报销助手"
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))
    debug: bool = True

    # ---------------- 数据库（SQLite，原型；生产换 PostgreSQL） ----------------
    # 切换点说明：db.py 中所有 SQL 均为标准 SQL，换 PG 只需替换驱动与占位符（? → %s）
    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(BASE_DIR / "data" / "app.db")))

    # ---------------- 安全 ----------------
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-only-secret-change-me"))
    jwt_expire_hours: int = field(default_factory=lambda: int(_env("JWT_EXPIRE_HOURS", "8")))
    cookie_name: str = "ea_token"
    csrf_cookie_name: str = "ea_csrf"
    # 登录失败锁定策略
    max_failed_login: int = 5
    lock_minutes: int = 10

    # ---------------- LLM（可替换供应商） ----------------
    # mock = 内置模拟模型（离线跑通全链路，用于演示/联调）；openai = OpenAI 兼容协议
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "mock"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "deepseek-chat"))
    llm_timeout: int = 60
    temperature: float = 0.2
    max_tokens: int = 800

    # ---------------- Embedding（★阶段1 复用字段，名字必须与其一致） ----------------
    embed_base_url: str = field(default_factory=lambda: _env("EMBED_BASE_URL", "") or _env("LLM_BASE_URL", ""))
    embed_api_key: str = field(default_factory=lambda: _env("EMBED_API_KEY", "") or _env("LLM_API_KEY", ""))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "text-embedding-v3"))
    embed_dim: int = field(default_factory=lambda: int(_env("EMBED_DIM", "1024")))
    # hash = 本地哈希伪向量（离线可跑，检索质量弱）；api = 真实 embedding 接口
    embed_backend: str = field(default_factory=lambda: _env("EMBED_BACKEND", "hash"))

    # ---------------- 检索 ----------------
    chunk_size: int = 500
    chunk_overlap: int = 80
    top_k: int = 3
    candidate_pool: int = 20
    fusion: str = "rrf"              # rrf | weighted
    # 低置信度阈值：低于该分数会给前端 weak 标记（接入真实 embedding 后需重新标定）
    low_score_threshold: float = 0.15
    # 上下文轮数：最近 6 轮 = 最多 12 条消息
    history_rounds: int = 6
    history_max_chars: int = 3000    # 历史上下文字符兜底，超出从最早整轮丢弃

    # ---------------- 上传 ----------------
    max_upload_mb: int = 20
    upload_dir: str = field(default_factory=lambda: str(BASE_DIR / "data" / "uploads"))
    index_dir: str = field(default_factory=lambda: str(BASE_DIR / "data" / "index"))


settings = Settings()


# ============================================================================
# 权限点定义（RBAC）
#   角色只有 employee / admin 两种，但权限点细分，后续加"财务复核员"无需改代码
# ============================================================================
PERMISSIONS: dict[str, set[str]] = {
    "employee": {"chat:use"},
    "admin": {
        "chat:use",
        "knowledge:read", "knowledge:write",
        "prompt:read", "prompt:write",
        "audit:read", "audit:flag",
        "user:manage",
    },
}
