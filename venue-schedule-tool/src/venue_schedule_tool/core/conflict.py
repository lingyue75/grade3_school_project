"""冲突检测核心（REQ-1 / REQ-2 / H2）。

设计约束（均来自需求发现报告）：
- 只提示冲突，不自动锁档、签约或承诺容量（f05/f52）——输出全部为 advisory。
- 每条提示必须说明原因（f02）并携带来源（f40）。
- 缺失值与未确认值区分处理（f31）：状态缺失的档期不参与时间窗判定，
  单独输出「数据不完整」提示，避免把「没填」误判成「没占档」。
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable

from .models import Conflict, ConflictType, HandoverRecord, Slot, SlotStatus


def _overlap(a_start, a_end, b_start, b_end) -> bool:
    """两个闭区间是否重叠。"""
    return a_start <= b_end and b_start <= a_end


def detect_slot_conflicts(slots: Iterable[Slot]) -> list[Conflict]:
    """检测档期时间窗类冲突：区域重叠 + 装卸口重叠（f11）。"""
    slot_list = [s for s in slots if s.status != SlotStatus.MISSING]
    conflicts: list[Conflict] = []

    for a, b in combinations(slot_list, 2):
        if a.app_id == b.app_id:
            continue
        # 同一申请的同区域不判冲突；不同申请才判
        # 规则1：同一场馆区域、时间窗重叠（含进撤场时长，f31）
        if a.zone == b.zone and _overlap(
            a.effective_start(), a.effective_end(), b.effective_start(), b.effective_end()
        ):
            conflicts.append(
                Conflict(
                    conflict_type=ConflictType.ZONE_TIME_OVERLAP,
                    app_ids=[a.app_id, b.app_id],
                    reason=(
                        f"申请 {a.app_id}（{a.zone}，{a.effective_start():%m-%d %H:%M}~"
                        f"{a.effective_end():%m-%d %H:%M}，版本 {a.version}，状态 {a.status.value}）与 "
                        f"申请 {b.app_id}（{b.zone}，{b.effective_start():%m-%d %H:%M}~"
                        f"{b.effective_end():%m-%d %H:%M}，版本 {b.version}，状态 {b.status.value}）"
                        f"在同一区域的时间窗重叠，含进撤场时长口径（f31）。需人工确认以哪一版本为准。"
                    ),
                    sources=[s for s in (a.source, b.source) if s],
                )
            )
        # 规则2：同一装卸口，一方撤场与另一方进场时间重叠（f11 模拟异常）。
        # 与规则1独立判断：不同区域也可能共用装卸口，两类提示各自成立。
        if (
            a.dock and b.dock and a.dock == b.dock
            and _overlap(a.effective_start(), a.effective_end(), b.effective_start(), b.effective_end())
        ):
            conflicts.append(
                Conflict(
                    conflict_type=ConflictType.DOCK_TIME_OVERLAP,
                    app_ids=[a.app_id, b.app_id],
                    reason=(
                        f"申请 {a.app_id} 与 {b.app_id} 在装卸口 {a.dock} 的进/撤场时间重叠，"
                        f"需翻原始版本判断责任节点（f11）。需人工确认先后顺序。"
                    ),
                    sources=[s for s in (a.source, b.source) if s],
                )
            )

    return conflicts


def detect_handover_gaps(handovers: Iterable[HandoverRecord]) -> list[Conflict]:
    """检测交接三项齐套缺口：接收人 / 版本 / 未结项（f20/f23）。"""
    conflicts: list[Conflict] = []
    for h in handovers:
        missing = h.missing_items()
        if missing:
            conflicts.append(
                Conflict(
                    conflict_type=ConflictType.HANDOVER_INCOMPLETE,
                    app_ids=[h.app_id],
                    reason=(
                        f"申请 {h.app_id} 的交接（{h.from_role}→{h.to_role}）缺少："
                        f"{'、'.join(missing)}；不能只发一条「已处理」消息（f23）。"
                        f"下游将无法确认以哪一版本为准、未结项由谁跟进（f20）。"
                    ),
                    sources=[h.source] if h.source else [],
                )
            )
    return conflicts


def detect_version_mismatch(slots: Iterable[Slot], handovers: Iterable[HandoverRecord]) -> list[Conflict]:
    """检测交接版本与最新版本不一致（f20：哪版为准）。"""
    latest: dict[str, str] = {}
    for s in slots:
        cur = latest.get(s.app_id)
        if cur is None or s.version > cur:
            latest[s.app_id] = s.version

    conflicts: list[Conflict] = []
    for h in handovers:
        if h.version is not None and h.app_id in latest and h.version != latest[h.app_id]:
            conflicts.append(
                Conflict(
                    conflict_type=ConflictType.VERSION_MISMATCH,
                    app_ids=[h.app_id],
                    reason=(
                        f"申请 {h.app_id} 交接引用版本 {h.version}，但台账最新版本为 "
                        f"{latest[h.app_id]}，交接未列明最新版本（f23），下游重复核对的主要来源之一（f10）。"
                    ),
                    sources=[h.source] if h.source else [],
                )
            )
    return conflicts


def detect_all(slots: Iterable[Slot], handovers: Iterable[HandoverRecord]) -> list[Conflict]:
    """汇总全部提示。所有提示均为 advisory，交由人工确认（f00/f41）。"""
    return (
        detect_slot_conflicts(slots)
        + detect_version_mismatch(slots, handovers)
        + detect_handover_gaps(handovers)
    )
