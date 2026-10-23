"""数据模型：对齐需求发现报告 f31 的关键字段口径。

关键约定：
- 占档状态三态：confirmed（已确认占用）/ unconfirmed（未确认）/ missing（缺失）。
  缺失值与未确认值必须区分（f31），否则下游会把「没填」误判成「没占档」。
- 所有提示均为 advisory（仅提示），不产生任何锁档动作（f05/f52）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SlotStatus(str, Enum):
    """占档状态三态，区分缺失值与未确认值（f31）。"""

    CONFIRMED = "confirmed"        # 已确认占用
    UNCONFIRMED = "unconfirmed"    # 已填报但未确认
    MISSING = "missing"            # 字段缺失（没填）


class ConflictType(str, Enum):
    ZONE_TIME_OVERLAP = "zone_time_overlap"        # 场馆区域时间窗重叠
    DOCK_TIME_OVERLAP = "dock_time_overlap"        # 同一装卸口进/撤场时间重叠（f11）
    VERSION_MISMATCH = "version_mismatch"          # 交接版本与最新版本不一致（f20/f23）
    HANDOVER_INCOMPLETE = "handover_incomplete"    # 交接三项不齐套（f20/f23）


@dataclass
class Slot:
    """一个档期占用（区域 + 时间窗 + 进撤场时长）。"""

    app_id: str                       # 统一关联编号（f30）
    zone: str                         # 场馆区域
    dock: Optional[str]               # 装卸口（可为缺失）
    window_start: datetime            # 时间窗开始
    window_end: datetime              # 时间窗结束（含恢复与检查时间口径）
    setup_minutes: int                # 进场时长
    teardown_minutes: int             # 撤场时长
    version: str                      # 版本号
    status: SlotStatus = SlotStatus.UNCONFIRMED
    source: str = ""                  # 来源（服务台/台账/文件库 + 记录号），用于可追溯（f40）

    def effective_start(self) -> datetime:
        """含进场的有效起点。"""
        return self.window_start

    def effective_end(self) -> datetime:
        """含撤场的有效终点。"""
        return self.window_end


@dataclass
class HandoverRecord:
    """一次交接记录，三项齐套：接收人、版本、未结项（f20/f23）。"""

    app_id: str
    from_role: str
    to_role: str
    receiver: Optional[str]           # 接收人（缺失=None）
    version: Optional[str]            # 交接版本（缺失=None）
    open_items: Optional[str]         # 未结项清单（缺失=None；空字符串视为"无未结项"）
    deadline: Optional[str] = None    # 截止时间（f23）
    source: str = ""

    def missing_items(self) -> list[str]:
        """返回缺失的齐套项；「没填」与「确认无」在此明确区分。"""
        missing = []
        if self.receiver is None:
            missing.append("接收人")
        if self.version is None:
            missing.append("版本")
        if self.open_items is None:
            missing.append("未结项")
        return missing


@dataclass
class Conflict:
    """一条冲突提示：只提示、不锁档；必须说明原因并附来源（f02/f40）。"""

    conflict_type: ConflictType
    app_ids: list[str]
    reason: str                       # 必须说明原因（f02）
    sources: list[str] = field(default_factory=list)
    requires_human_confirm: bool = True   # 正式结论由授权人员确认（f41）
    auto_lock: bool = False               # 恒为 False：不自动锁档（f05）

    def to_dict(self) -> dict:
        return {
            "type": self.conflict_type.value,
            "app_ids": self.app_ids,
            "reason": self.reason,
            "sources": self.sources,
            "requires_human_confirm": self.requires_human_confirm,
            "auto_lock": self.auto_lock,
        }
