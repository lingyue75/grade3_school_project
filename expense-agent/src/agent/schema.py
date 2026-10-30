# -*- coding: utf-8 -*-
"""
Agent 相关数据表与演示数据（阶段4）

新增表：
    invoices              票据池（演示数据）
    reimbursement_drafts  报销单草稿（沿用阶段1 需求清单的表结构，精简版）
    draft_items           草稿明细行
    agent_runs            Agent 运行实例（支持"中断—恢复"，用于 create_draft 人工确认）
    agent_step_logs       每轮决策日志（★阶段5 分析的数据源：工具名/参数/耗时/结果摘要）
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import db                      # 注意：本文件在 src/agent/ 下，上级才是 src 包
from ..security import new_id, now_str

# 项目根目录加入搜索路径：演示数据放在 sample/ 下（生产可删除）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

AGENT_SCHEMA = """
-- 票据池
CREATE TABLE IF NOT EXISTS invoices (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL,
    invoice_no   TEXT NOT NULL,
    invoice_code TEXT NOT NULL,
    amount       REAL NOT NULL,
    issue_date   TEXT NOT NULL,
    seller       TEXT,
    item_type    TEXT,                              -- hotel/transport/meal/office
    city         TEXT,
    status       TEXT NOT NULL DEFAULT 'unused',    -- unused/reserved/used/voided
    draft_id     TEXT,
    created_at   TEXT NOT NULL
);

-- 报销单草稿
CREATE TABLE IF NOT EXISTS reimbursement_drafts (
    id                    TEXT PRIMARY KEY,
    draft_no              TEXT UNIQUE,
    user_id               TEXT NOT NULL,
    session_id            TEXT,
    status                TEXT NOT NULL DEFAULT 'draft',   -- draft/submitted/approved/rejected
    expense_type          TEXT,
    period_start          TEXT,
    period_end            TEXT,
    reason                TEXT,
    total_amount          REAL NOT NULL DEFAULT 0,
    currency              TEXT DEFAULT 'CNY',
    grade_code_snapshot   TEXT,                     -- 提交时职级快照
    ruleset_version       TEXT DEFAULT 'v1',        -- 规则版本快照（审计可回放）
    rule_result           TEXT,                     -- JSON：最近一次规则校验结果
    approval_instance_id  TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

-- 草稿明细
CREATE TABLE IF NOT EXISTS draft_items (
    id           TEXT PRIMARY KEY,
    draft_id     TEXT NOT NULL,
    invoice_id   TEXT,
    item_type    TEXT NOT NULL,
    amount       REAL NOT NULL,
    expense_date TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL
);

-- Agent 运行实例（中断后可恢复）
CREATE TABLE IF NOT EXISTS agent_runs (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    session_id    TEXT,
    goal          TEXT NOT NULL,                    -- 用户原始诉求
    status        TEXT NOT NULL DEFAULT 'running',  -- running/awaiting_confirm/done/failed
    step_count    INTEGER NOT NULL DEFAULT 0,
    state_json    TEXT,                             -- 运行期状态：stamp、draft_id、已收集槽位等
    pending_tool  TEXT,                             -- 被中断待确认的工具名
    pending_args  TEXT,                             -- 被中断工具的参数
    error_text    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- ★ 每轮决策日志（阶段5 分析的数据源）
CREATE TABLE IF NOT EXISTS agent_step_logs (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    step_no        INTEGER NOT NULL,
    kind           TEXT NOT NULL,        -- decision / tool / interrupt / final / error
    tool_name      TEXT,                 -- 工具名（decision 轮为空）
    arguments      TEXT,                 -- JSON：调用参数
    result_summary TEXT,                 -- 结果摘要（截断存储，不存全量大文本）
    ok             INTEGER,              -- 1 成功 / 0 失败 / NULL 非工具步骤
    duration_ms    INTEGER,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON agent_step_logs(run_id, step_no);
CREATE INDEX IF NOT EXISTS idx_steps_tool ON agent_step_logs(tool_name, created_at);
"""

def init_agent_tables() -> None:
    """建 Agent 表（幂等）并写入演示票据。"""
    conn = db.connect()
    try:
        conn.executescript(AGENT_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    _seed_invoices()


def _seed_invoices() -> None:
    """写入演示票据（数据来自 sample/demo_seed.py；删除该模块即自动跳过）。"""
    try:
        from sample.demo_seed import DEMO_INVOICES
    except ImportError:
        return
    if db.query_one("SELECT id FROM invoices LIMIT 1"):
        return
    owner = db.query_one("SELECT id FROM users WHERE employee_no='1001'")
    owner_id = owner["id"] if owner else "unknown"
    for no, code, amount, date, seller, item_type, city, status in DEMO_INVOICES:
        db.execute(
            "INSERT INTO invoices(id,owner_id,invoice_no,invoice_code,amount,issue_date,seller,"
            "item_type,city,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("inv"), owner_id, no, code, amount, date, seller, item_type, city, status, now_str()),
        )
