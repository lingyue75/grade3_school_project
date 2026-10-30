# -*- coding: utf-8 -*-
"""
Agent 护栏回归测试（★对齐业务红线 f05 / f31 / f40 / f41 / f52）

这些用例不依赖任何模型与网络，直接验证服务端不变量：
即使模型违规调用，也【必须】被拦截且不产生任何锁档动作。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from venue_schedule_tool.agent import store, tools  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    store.init_db()
    yield


def _ctx(state: dict | None = None) -> dict:
    return {"state": state if state is not None else {}, "reviewer": ""}


# ---------------------------------------------------------------- H1 不锁档
def test_forbidden_tools_are_rejected():
    """锁档/签约/承诺容量类能力一律不可用（H1，能力边界即安全边界）"""
    for name in tools.FORBIDDEN_TOOLS:
        r = tools.execute(name, {"app_id": "DLG-20261023-0001"}, _ctx())
        assert r["ok"] is False
        assert "不提供" in r["message"]


def test_tool_list_never_contains_lock_capabilities():
    """工具清单里不存在任何锁档类工具"""
    names = [t["name"] for t in tools.TOOLS]
    for bad in ("lock_slot", "confirm_booking", "sign_contract", "commit_capacity"):
        assert bad not in names


# ---------------------------------------------------------------- H2 授权确认
def test_draft_change_request_blocked_without_evidence():
    """未做来源校验与冲突检测 → 变更请求必须被拦截（H2/H3，f40）"""
    r = tools.execute("draft_change_request",
                      {"app_id": "DLG-20261023-0001", "change_type": "reschedule"}, _ctx())
    assert r["ok"] is False
    assert "validate_evidence" in r["message"] and "detect_conflict" in r["message"]


def test_change_request_visibility_requires_stamps():
    """防线④：未拿令牌时 draft_change_request 不可见"""
    names = [t["name"] for t in tools.visible_tools({"stamps": {}})]
    assert "draft_change_request" not in names
    assert "submit_handover" not in names


def test_non_authorized_reviewer_rejected():
    """非授权人员不得作出正式结论（f41）"""
    r = tools.execute("request_authorization",
                      {"app_id": "DLG-20261023-0001", "reviewer": "林亦舟"}, _ctx())
    assert r["ok"] is False
    assert r["code"] == 5101


# ---------------------------------------------------------------- H3 来源校验
def test_validate_evidence_rejects_missing_source():
    """来源为空 → 不得进入正式流转（f40）"""
    r = tools.execute("validate_evidence", {"app_ids": ["DLG-NOT-EXIST-0000"]}, _ctx())
    assert r["ok"] is False
    assert "不得进入正式流转" in r["message"] or "缺少来源" in r["message"]


def test_validate_evidence_grants_stamp_for_valid_app():
    """来源齐备 → 签发校验令牌"""
    state: dict = {"stamps": {}}
    r = tools.execute("validate_evidence", {"app_ids": ["DLG-20261023-0001"]}, _ctx(state))
    assert r["ok"] is True
    assert tools._stamp_valid(state, "evidence")


def test_stamp_invalid_after_app_changed():
    """校验通过后换申请 → 指纹不匹配，令牌失效"""
    state: dict = {"stamps": {}}
    tools.execute("validate_evidence", {"app_ids": ["DLG-20261023-0001"]}, _ctx(state))
    tools.execute("detect_conflict", {"app_id": "DLG-20261023-0001"}, _ctx(state))
    tools.revoke_stamp(state, "conflict")      # 模拟数据变更导致令牌失效
    r = tools.execute("draft_change_request",
                      {"app_id": "DLG-20261023-0002", "change_type": "reschedule"}, _ctx(state))
    assert r["ok"] is False


# ---------------------------------------------------------------- H4 三态区分
def test_missing_status_reported_as_incomplete_not_free():
    """missing 状态不得当作'未占用'，必须报数据不完整（f31）"""
    r = tools.execute("query_slots", {"status": "missing"}, _ctx())
    assert r["ok"] is True
    assert len(r["data"]["slots"]) >= 1
    assert r["data"]["incomplete_app_ids"]
    # 关键断言：返回文案必须出现"不代表未占用"或"数据不完整"
    assert ("不代表未占用" in r["message"]) or ("数据不完整" in r["message"])


def test_missing_slots_excluded_from_time_window_judgement():
    """状态缺失的档期不参与时间窗冲突判定（f31）"""
    r = tools.execute("detect_conflict", {"status": "all"}, _ctx())
    assert r["ok"] is True
    # 检测不报错，且缺失记录被单独列出而非参与判定
    assert isinstance(r["data"]["incomplete_app_ids"], list)


# ---------------------------------------------------------------- 只读评估
def test_assess_change_is_read_only():
    """改期评估只模拟，不产生实际变更（f05）"""
    r = tools.execute("assess_change", {"app_id": "DLG-20261023-0001",
                                        "new_window_start": "2026-11-06 08:00",
                                        "new_window_end": "2026-11-08 18:00"}, _ctx())
    assert r["ok"] is True
    assert "before" in r["data"] and "after" in r["data"]
    assert "未产生任何实际变更" in r["message"]
