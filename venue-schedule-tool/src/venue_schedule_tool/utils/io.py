"""样例数据读写：CSV 进 → 模型对象，缺失值与未确认值区分标注（f31）。"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core.models import HandoverRecord, Slot, SlotStatus

DATE_FMT = "%Y-%m-%d %H:%M"


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None or value.strip() == "":
        return None
    return datetime.strptime(value.strip(), DATE_FMT)


def load_slots(csv_path: str | Path) -> list[Slot]:
    """读取档期样例。status 取值：confirmed / unconfirmed /（空=missing）。"""
    slots: list[Slot] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            status_raw = (row.get("status") or "").strip().lower()
            try:
                status = SlotStatus(status_raw)
            except ValueError:
                status = SlotStatus.MISSING  # 没填 ≠ 没占档（f31）
            slots.append(
                Slot(
                    app_id=row["app_id"].strip(),
                    zone=row["zone"].strip(),
                    dock=(row.get("dock") or "").strip() or None,
                    window_start=_parse_dt(row.get("window_start")),
                    window_end=_parse_dt(row.get("window_end")),
                    setup_minutes=int(row.get("setup_minutes") or 0),
                    teardown_minutes=int(row.get("teardown_minutes") or 0),
                    version=row.get("version", "").strip() or "v0",
                    status=status,
                    source=row.get("source", "").strip(),
                )
            )
    return slots


def load_handovers(csv_path: str | Path) -> list[HandoverRecord]:
    """读取交接记录。空字段读为 None（缺失），与「确认无」区分。"""
    handovers: list[HandoverRecord] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            def _opt(key: str) -> Optional[str]:
                v = (row.get(key) or "").strip()
                return v if v else None

            handovers.append(
                HandoverRecord(
                    app_id=row["app_id"].strip(),
                    from_role=row.get("from_role", "").strip(),
                    to_role=row.get("to_role", "").strip(),
                    receiver=_opt("receiver"),
                    version=_opt("version"),
                    open_items=_opt("open_items"),
                    deadline=_opt("deadline"),
                    source=row.get("source", "").strip(),
                )
            )
    return handovers
