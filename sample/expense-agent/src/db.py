# -*- coding: utf-8 -*-
"""
SQLite 数据访问层（★核心复用：对应 src/infra/db.py）

设计说明：
  1. 只用标准库 sqlite3 —— 原型阶段零依赖，方便任何环境直接跑。
  2. 开 WAL + busy_timeout：SSE 长连接并发写不会互相锁死。
  3. 流式期间【不持有长事务】：先 insert 占位 → 提交 → 流式过程中分批 update，
     避免一个长事务把写锁占住几十秒（这是 SQLite 上最容易踩的坑）。
  4. 生产切换 PostgreSQL：SQL 均为标准 SQL，只需换驱动并把占位符 ? 换成 %s，
     并在并发写入 > 50 时评估连接池。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

from .security import hash_password, new_id
from .settings import settings

# 项目根目录加入搜索路径：演示数据放在 sample/ 下（生产可删除）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_lock = threading.Lock()          # SQLite 单写：所有写操作串行化


# ============================================================================
# 1. 连接管理
# ============================================================================
def connect() -> sqlite3.Connection:
    """获取连接（每个请求一个连接，行工厂设为 Row，便于按列名取值）。"""
    conn = sqlite3.connect(settings.db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # 读写并发
    conn.execute("PRAGMA busy_timeout=15000")     # 写冲突时等待而非立刻报错
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def execute(sql: str, params: Iterable = ()) -> sqlite3.Cursor:
    """写操作：串行执行并立即提交（短事务）。"""
    with _lock, connect() as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur


def query_one(sql: str, params: Iterable = ()) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute(sql, tuple(params)).fetchone()
    finally:
        conn.close()


def query_all(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ============================================================================
# 2. 建表
# ============================================================================
SCHEMA = """
-- 用户（沿用阶段1 表结构，补充鉴权字段）
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    employee_no   TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'employee',   -- employee / admin
    dept          TEXT,
    grade_code    TEXT,                                -- 职级码（报销标准只按职级，关键字段）
    status        TEXT NOT NULL DEFAULT 'active',
    failed_count  INTEGER NOT NULL DEFAULT 0,
    locked_until  INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    created_at    TEXT NOT NULL
);

-- 会话
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    title             TEXT,
    channel           TEXT NOT NULL DEFAULT 'web',
    model             TEXT,
    prompt_version_id TEXT,                            -- 本会话最后使用的提示词版本（审计回放用）
    message_count     INTEGER NOT NULL DEFAULT 0,
    token_total       INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'active',  -- active / archived
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);

-- 消息
CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    seq               INTEGER NOT NULL,                -- 会话内序号，前端排序稳定
    role              TEXT NOT NULL,                   -- user / assistant / system / tool
    content           TEXT NOT NULL DEFAULT '',
    retrieval_refs    TEXT,                            -- JSON：本次引用的制度条文，答案可溯源
    model             TEXT,
    prompt_version_id TEXT,
    latency_ms        INTEGER,
    finish_reason     TEXT,                            -- stop / aborted / error
    error_text        TEXT,
    risk_flag         INTEGER NOT NULL DEFAULT 0,      -- 是否被标记为疑似违规
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- 制度文档
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    source_type  TEXT NOT NULL,                        -- md / pdf
    file_url     TEXT,
    file_hash    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',      -- pending/parsing/chunking/embedding/ready/failed/deleted
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    error_text   TEXT,
    uploaded_by  TEXT,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT
);

-- 制度分片（向量库只存行号，真实内容以本表为准）
CREATE TABLE IF NOT EXISTS policy_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    clause_no    TEXT,
    heading_path TEXT,
    text         TEXT NOT NULL,
    char_start   INTEGER DEFAULT 0,
    char_end     INTEGER DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'active',       -- active / deleted（软删，检索层过滤）
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON policy_chunks(document_id, chunk_index);

-- 摄取任务（上传后异步跑，前端轮询进度）
CREATE TABLE IF NOT EXISTS ingest_tasks (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    stage       TEXT NOT NULL,                         -- parsing/chunking/embedding/ready/failed
    progress    INTEGER NOT NULL DEFAULT 0,
    error_text  TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

-- 提示词模板（★版本化：已发布不可改，只增版本，保证审计可回放）
CREATE TABLE IF NOT EXISTS prompt_templates (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    content      TEXT NOT NULL,
    version_no   INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',        -- draft / published / archived
    is_active    INTEGER NOT NULL DEFAULT 0,           -- 1 = 当前生效版本（全局唯一）
    created_by   TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_prompt_active ON prompt_templates(is_active);

-- 疑似违规风险规则（自动标记用，可启停可编辑）
CREATE TABLE IF NOT EXISTS risk_rules (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    pattern     TEXT NOT NULL,                         -- 关键词（逗号分隔）或正则（以 re: 开头）
    level       TEXT NOT NULL DEFAULT 'high',
    enabled     INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    created_at  TEXT NOT NULL
);

-- 风险标记（自动 + 人工）
CREATE TABLE IF NOT EXISTS audit_flags (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    source      TEXT NOT NULL,                         -- auto / manual
    rule_id     TEXT,                                  -- auto 时记录命中的规则
    note        TEXT,
    created_by  TEXT,
    resolved    INTEGER NOT NULL DEFAULT 0,
    resolution  TEXT,                                  -- confirmed_violation / false_positive
    resolved_by TEXT,
    resolved_at TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_msg ON audit_flags(message_id);
CREATE INDEX IF NOT EXISTS idx_flags_resolved ON audit_flags(resolved, created_at DESC);

-- 管理操作留痕（谁在何时改了提示词/删了文档）
CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    actor_id    TEXT,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    detail      TEXT,
    request_id  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON audit_logs(created_at DESC);
"""


# ============================================================================
# 3. 初始化（建表 + 种子数据）
# ============================================================================
# 用户指定的默认系统提示词（作为 version 1，发布并生效）
DEFAULT_PROMPT = (
    "你是公司报销制度助手，只根据提供的制度条文回答，不确定时明确说'制度中未找到'，"
    "回答末尾附引用来源，不得编造报销标准。"
)

# 默认风险规则集（自动标记疑似违规问答；只标记，不做判定）
DEFAULT_RISK_RULES = [
    ("绕过系统校验", "绕过,绕开,规避,跳过系统,怎么才能不被拦", "high", "试图绕开系统硬校验"),
    ("拆分单据", "拆单,拆分单据,分几张,分开报,拆开报销", "high", "通过拆分规避额度或标准"),
    ("票据造假", "伪造,PS发票,假发票,买发票,代开发票,虚开", "high", "涉及伪造或购买发票"),
    ("虚报冒领", "虚报,冒领,多报,虚增,凑票,没有实际发生", "high", "虚报费用或冒领款项"),
    ("超标处理", "超标怎么办,超出标准怎么报,怎么能全报,找票补", "medium", "试图全额报销超标费用"),
    ("发票重复利用", "重复报销,一票多报,同一张发票报两次", "high", "同一票据重复报销"),
    ("代他人报销", "帮别人报,代同事报,用我的名义", "medium", "代他人提交报销"),
    ("制度漏洞试探", "漏洞,钻空子,没人发现,查不出来", "medium", "试探制度或系统漏洞"),
]


def init_db() -> None:
    """建表并写入种子数据（幂等，可重复执行）。"""
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    _seed_users()
    _seed_prompt()
    _seed_risk_rules()


def _seed_users() -> None:
    """初始化演示账号（数据来自 sample/demo_seed.py，生产可删除该文件关闭种子）。"""
    try:
        from sample.demo_seed import DEMO_USERS
    except ImportError:
        return
    if query_one("SELECT id FROM users LIMIT 1"):
        return
    from .security import now_str
    ts = now_str()
    for employee_no, name, pwd, role, dept, grade in DEMO_USERS:
        execute(
            "INSERT INTO users(id,employee_no,name,password_hash,role,dept,grade_code,status,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id("u"), employee_no, name, hash_password(pwd), role, dept, grade, "active", ts),
        )


def _seed_prompt() -> None:
    """默认提示词模板 version 1：直接发布并生效。"""
    if query_one("SELECT id FROM prompt_templates LIMIT 1"):
        return
    from .security import now_str
    ts = now_str()
    execute(
        "INSERT INTO prompt_templates(id,name,content,version_no,status,is_active,created_by,note,created_at,published_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (new_id("pt"), "默认模板", DEFAULT_PROMPT, 1, "published", 1, "system",
         "系统内置：只依据制度条文作答", ts, ts),
    )


def _seed_risk_rules() -> None:
    if query_one("SELECT id FROM risk_rules LIMIT 1"):
        return
    from .security import now_str
    ts = now_str()
    for name, pattern, level, desc in DEFAULT_RISK_RULES:
        execute(
            "INSERT INTO risk_rules(id,name,pattern,level,enabled,description,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("rr"), name, pattern, level, 1, desc, ts),
        )


# ============================================================================
# 4. 通用写日志
# ============================================================================
def write_log(actor_id: str | None, action: str, target_type: str = "", target_id: str = "",
              detail: dict | None = None, request_id: str = "") -> None:
    """管理操作留痕：谁在何时改了什么。"""
    from .security import now_str
    execute(
        "INSERT INTO audit_logs(id,actor_id,action,target_type,target_id,detail,request_id,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (new_id("log"), actor_id, action, target_type, target_id,
         json.dumps(detail or {}, ensure_ascii=False), request_id, now_str()),
    )
