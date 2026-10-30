# -*- coding: utf-8 -*-
"""
Agent 运行持久化（SQLite，标准库 sqlite3 —— 保持项目"核心逻辑零第三方依赖"的约定）

三张表：
    agent_runs        运行实例，支持「中断—恢复」（变更请求需授权人确认）
    agent_step_logs   每轮决策日志（★阶段8 评测与阶段9 审计的数据源）
    change_requests   变更请求（★只记录请求与审批状态，永不包含任何锁档动作）

边界提醒（f05/f52）：本模块只存"请求/提示/复核"，不存任何锁档、签约、容量承诺结果。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "agent.db"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id           TEXT PRIMARY KEY,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',   -- running/awaiting_confirm/awaiting_input/done/failed
    step_count   INTEGER NOT NULL DEFAULT 0,
    state_json   TEXT,
    pending_tool TEXT,
    pending_args TEXT,
    error_text   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_step_logs (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    step_no        INTEGER NOT NULL,
    kind           TEXT NOT NULL,   -- decision / tool / interrupt / final / error
    tool_name      TEXT,
    arguments      TEXT,
    result_summary TEXT,
    ok             INTEGER,
    duration_ms    INTEGER,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON agent_step_logs(run_id, step_no);

-- 变更请求：只记录"请求 + 审批"，不产生任何锁档动作（f05/f52）
CREATE TABLE IF NOT EXISTS change_requests (
    id           TEXT PRIMARY KEY,
    app_id       TEXT NOT NULL,          -- 统一关联编号 DLG-YYYYMMDD-NNNN（f30）
    change_type  TEXT NOT NULL,          -- reschedule / cancel / add_setup / other
    payload_json TEXT NOT NULL,
    sources      TEXT NOT NULL DEFAULT '',   -- 来源（f40），为空则不得进入正式流转
    status       TEXT NOT NULL DEFAULT 'draft',  -- draft/awaiting_approval/approved/rejected
    requested_by TEXT,
    reviewer     TEXT,                   -- 授权人员（f41）
    decided_at   TEXT,
    created_at   TEXT NOT NULL
);
"""


def init_db(db_path: str | Path | None = None) -> None:
    """建表（幂等）。"""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def execute(sql: str, params: tuple = (), db_path: str | Path | None = None) -> sqlite3.Cursor:
    with _lock, connect(db_path) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def query_all(sql: str, params: tuple = (), db_path: str | Path | None = None) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: tuple = (), db_path: str | Path | None = None) -> dict | None:
    rows = query_all(sql, params, db_path)
    return rows[0] if rows else None
