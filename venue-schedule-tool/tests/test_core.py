"""核心逻辑测试（与 src 结构对应）。"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from venue_schedule_tool.core.conflict import (  # noqa: E402
    detect_handover_gaps,
    detect_slot_conflicts,
    detect_version_mismatch,
)
from venue_schedule_tool.core.models import (  # noqa: E402
    ConflictType,
    HandoverRecord,
    Slot,
    SlotStatus,
)
from venue_schedule_tool.core.registry import is_valid_app_id, make_app_id  # noqa: E402
from venue_schedule_tool.core.review import ReviewLedger, replay_report  # noqa: E402


def mk_slot(app_id, zone="A厅", dock="D1", start="2026-11-03 08:00", end="2026-11-05 18:00",
            version="v1", status=SlotStatus.CONFIRMED, source="台账-EXP-001"):
    return Slot(
        app_id=app_id, zone=zone, dock=dock,
        window_start=datetime.fromisoformat(start),
        window_end=datetime.fromisoformat(end),
        setup_minutes=240, teardown_minutes=240,
        version=version, status=status, source=source,
    )


class TestRegistry:
    def test_make_and_validate(self):
        app_id = make_app_id("20261023", 7)
        assert app_id == "DLG-20261023-0007"
        assert is_valid_app_id(app_id)
        assert not is_valid_app_id("EXP-001")

    def test_invalid_seq(self):
        with pytest.raises(ValueError):
            make_app_id("20261023", 0)


class TestSlotConflict:
    def test_zone_overlap_detected_with_reason_and_source(self):
        a = mk_slot("DLG-20261023-0001", dock="D1")
        b = mk_slot("DLG-20261023-0002", dock="D2", start="2026-11-04 09:00", end="2026-11-06 17:00",
                    source="服务台-TK-1024")
        conflicts = detect_slot_conflicts([a, b])
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.conflict_type == ConflictType.ZONE_TIME_OVERLAP
        assert c.reason and "重叠" in c.reason          # f02：说明原因
        assert set(c.sources) == {a.source, b.source}   # f40：来源留痕
        assert not c.auto_lock                          # f05：不自动锁档

    def test_dock_overlap_detected(self):
        a = mk_slot("DLG-20261023-0005", zone="C馆", dock="D1", end="2026-11-18 18:00")
        b = mk_slot("DLG-20261023-0006", zone="C馆", dock="D1", start="2026-11-18 12:00",
                    end="2026-11-20 18:00")
        conflicts = detect_slot_conflicts([a, b])
        assert any(c.conflict_type == ConflictType.DOCK_TIME_OVERLAP for c in conflicts)

    def test_missing_status_not_judged_as_free(self):
        """缺失值不参与时间窗判定，也不能被当成「没占档」（f31）。"""
        a = mk_slot("DLG-20261023-0001")
        b = mk_slot("DLG-20261023-0025", status=SlotStatus.MISSING)
        assert detect_slot_conflicts([a, b]) == []


class TestHandover:
    def test_three_items_missing(self):
        h = HandoverRecord(app_id="DLG-20261023-0012", from_role="销售", to_role="流程协调",
                           receiver=None, version=None, open_items="无", source="服务台-TK-1038")
        conflicts = detect_handover_gaps([h])
        assert len(conflicts) == 1
        assert "接收人" in conflicts[0].reason and "版本" in conflicts[0].reason

    def test_open_items_empty_string_is_not_missing(self):
        """「确认无未结项」与「没填」区分（f31）。"""
        h = HandoverRecord(app_id="X", from_role="a", to_role="b",
                           receiver="唐映秋", version="v1", open_items="无")
        assert detect_handover_gaps([h]) == []

    def test_version_mismatch(self):
        s = mk_slot("DLG-20261023-0015", version="v3")
        h = HandoverRecord(app_id="DLG-20261023-0015", from_role="流程协调", to_role="审核",
                           receiver="傅景", version="v2", open_items="无", source="台账-EXP-027")
        conflicts = detect_version_mismatch([s], [h])
        assert len(conflicts) == 1
        assert "v3" in conflicts[0].reason


class TestReview:
    def test_finalize_requires_authorization(self):
        ledger = ReviewLedger(authorized_reviewers={"傅景"})
        c = detect_slot_conflicts([mk_slot("DLG-20261023-0001"),
                                   mk_slot("DLG-20261023-0002", source="服务台-TK-1024")])[0]
        record = ledger.finalize(c, "傅景")
        assert record["decision"] == "confirmed"

    def test_finalize_rejects_unauthorized(self):
        ledger = ReviewLedger(authorized_reviewers={"傅景"})
        c = detect_slot_conflicts([mk_slot("DLG-20261023-0001"),
                                   mk_slot("DLG-20261023-0002", source="服务台-TK-1024")])[0]
        with pytest.raises(PermissionError):
            ledger.finalize(c, "林亦舟")

    def test_no_source_cannot_flow(self):
        """来源不清的提示不得进入正式流转（f40）。"""
        ledger = ReviewLedger(authorized_reviewers={"傅景"})
        c = detect_slot_conflicts([mk_slot("DLG-20261023-0001", source=""),
                                   mk_slot("DLG-20261023-0002", source="")])[0]
        assert not c.sources
        with pytest.raises(PermissionError):
            ledger.finalize(c, "傅景")

    def test_replay_report_flags(self):
        c = detect_slot_conflicts([mk_slot("DLG-20261023-0001"),
                                   mk_slot("DLG-20261023-0002", source="服务台-TK-1024")])[0]
        report = replay_report([c])
        assert report["all_with_reason"] and report["all_with_source"]
        assert report["auto_lock_used"] is False
