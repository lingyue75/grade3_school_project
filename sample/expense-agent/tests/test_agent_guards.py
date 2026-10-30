# -*- coding: utf-8 -*-
"""
自动化回归测试：Agent 防跳步护栏（★上线门槛 4.1/4.3 相关的可自动化部分）

运行：pytest tests/ -v
说明：这些用例不依赖 LLM 与网络，直接测服务端不变量——
      即使模型违规调用，也必须被拦截且不产生脏数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src import db  # noqa: E402
from src.agent import schema as agent_schema  # noqa: E402
from src.agent import tools  # noqa: E402
from src.rag import get_index  # noqa: E402

USER_ID = "test_user"
GRADE = "P3"


@pytest.fixture(scope="module", autouse=True)
def setup_env():
    """初始化数据库、演示数据与检索索引（整模块只做一次）。"""
    db.init_db()
    agent_schema.init_agent_tables()
    get_index().load_or_build()
    user = db.query_one("SELECT id FROM users WHERE employee_no='1001'")
    globals()["USER_ID"] = user["id"] if user else "unknown"
    yield


def _ctx(state: dict) -> dict:
    return {"user_id": USER_ID, "session_id": "", "grade_code": GRADE, "state": state}


def _unused_invoice_id() -> str:
    row = db.query_one("SELECT id FROM invoices WHERE status='unused' LIMIT 1")
    return row["id"] if row else ""


def test_add_item_without_validate_is_blocked():
    """未校验票据直接挂票 → 必须被拦截（H1 / 防线③）"""
    state = {"stamps": {}, "draft_id": "dr_test"}
    inv = _unused_invoice_id()
    r = tools.execute("add_item", {"draft_id": "dr_test", "invoice_ids": [inv], "item_type": "hotel"}, _ctx(state))
    assert r["ok"] is False
    assert "validate_invoice" in r["message"]          # 拒绝信息必须指明下一步该调什么（防线②）


def test_submit_without_check_rules_is_blocked():
    """未跑规则校验直接提交 → 必须被拦截（H2 / 防线③）"""
    state = {"stamps": {}, "draft_id": "dr_test"}
    r = tools.execute("submit_draft", {"draft_id": "dr_test"}, _ctx(state))
    assert r["ok"] is False
    assert "check_rules" in r["message"]


def test_stamp_invalid_after_invoice_changed():
    """校验通过后【换票】→ 指纹不匹配，令牌失效（H3）"""
    state = {"stamps": {}, "draft_id": "dr_test"}
    inv = _unused_invoice_id()
    assert tools.execute("validate_invoice", {"invoice_ids": [inv]}, _ctx(state))["ok"] is True

    other = db.query_one("SELECT id FROM invoices WHERE status='unused' AND id<>? LIMIT 1", (inv,))
    if other:
        r = tools.execute("add_item", {"draft_id": "dr_test", "invoice_ids": [other["id"]],
                                       "item_type": "hotel"}, _ctx(state))
        assert r["ok"] is False


def test_submit_blocked_when_item_changed_after_rule_check():
    """规则校验通过后修改明细 → 规则令牌失效，必须重跑 check_rules（H3）"""
    state = {"stamps": {}, "draft_id": "dr_test"}
    tools.grant_stamp(state, "rule", "fingerprint-A")
    tools.revoke_stamp(state, "rule")                  # 模拟明细变更触发失效
    r = tools.execute("submit_draft", {"draft_id": "dr_test"}, _ctx(state))
    assert r["ok"] is False


def test_tool_visibility_hides_write_tools():
    """防线④：未拿到令牌时，add_item / submit_draft 不出现在可用工具列表"""
    names = [t["name"] for t in tools.visible_tools({"stamps": {}})]
    assert "add_item" not in names
    assert "submit_draft" not in names

    granted = {"stamps": {}, "draft_id": "dr_test"}
    tools.grant_stamp(granted, "invoice", "x")
    tools.grant_stamp(granted, "rule", "y")
    names2 = [t["name"] for t in tools.visible_tools(granted)]
    assert "add_item" in names2 and "submit_draft" in names2


def test_get_allowance_is_deterministic():
    """标准金额必须来自查表，且与职级/城市档严格对应（H5 的基础）"""
    r1 = tools.execute("get_allowance", {"grade_code": "P3", "city": "北京", "expense_type": "hotel"}, _ctx({}))
    r2 = tools.execute("get_allowance", {"grade_code": "P3", "city": "上海", "expense_type": "hotel"}, _ctx({}))
    r3 = tools.execute("get_allowance", {"grade_code": "P4", "city": "北京", "expense_type": "hotel"}, _ctx({}))
    assert r1["data"]["amount"] == r2["data"]["amount"] == 500      # 京沪同为一线城市
    assert r3["data"]["amount"] == 400                              # P4/P5 更低
    assert r1["data"]["clause_no"] == "3.2.1"
