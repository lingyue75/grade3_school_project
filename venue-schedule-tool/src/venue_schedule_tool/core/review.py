"""人工复核留痕（REQ-3 / f40、f41）。

每条冲突提示在进入正式流转前必须：
1. 关联来源（sources 非空）；
2. 附人工复核记录（复核人 + 结论）；
3. 正式结论由授权人员确认。
来源不清的申请不得进入正式流转（f40）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import Conflict


@dataclass
class ReviewRecord:
    """一条人工复核记录。"""

    conflict_ref: str                 # 对应冲突的引用（类型+涉及申请）
    reviewer: str                     # 复核人（须为授权人员）
    decision: str                     # confirmed / rejected / escalated
    note: str = ""
    reviewed_at: datetime = field(default_factory=datetime.now)


@dataclass
class ReviewLedger:
    """复核台账：冲突提示 → 人工复核 → 授权确认，全程留痕。"""

    records: list[ReviewRecord] = field(default_factory=list)
    authorized_reviewers: set[str] = field(default_factory=set)  # 授权人员清单（f41）

    def add_review(self, record: ReviewRecord) -> None:
        if not record.reviewer:
            raise ValueError("复核人不能为空（f41：正式结论须由授权人员确认）")
        self.records.append(record)

    def can_finalize(self, reviewer: str) -> bool:
        """是否具备正式结论确认资格；授权清单未配置时返回 False（默认收紧）。"""
        if not self.authorized_reviewers:
            return False
        return reviewer in self.authorized_reviewers

    def finalize(self, conflict: Conflict, reviewer: str) -> dict:
        """授权人员确认正式结论；来源不清的提示拒绝进入正式流转（f40）。"""
        if not conflict.sources:
            raise PermissionError("来源不清的提示不得进入正式流转（f40）")
        if not self.can_finalize(reviewer):
            raise PermissionError(f"{reviewer} 不在授权人员清单内，无法确认正式结论（f41）")
        record = ReviewRecord(
            conflict_ref=f"{conflict.conflict_type.value}:{'/'.join(conflict.app_ids)}",
            reviewer=reviewer,
            decision="confirmed",
            note="正式结论已由授权人员确认",
        )
        self.add_review(record)
        return record.__dict__ | {"reviewed_at": record.reviewed_at.isoformat()}


def replay_report(conflicts: list[Conflict], ledger: Optional[ReviewLedger] = None) -> dict:
    """生成回放报告（对齐 f02 验收口径：识别全部已标注冲突并说明原因）。"""
    unlabeled = [c for c in conflicts if not c.sources]
    return {
        "total_conflicts": len(conflicts),
        "all_with_reason": all(c.reason for c in conflicts),
        "all_with_source": not unlabeled,
        "auto_lock_used": any(c.auto_lock for c in conflicts),  # 恒应为 False
        "review_records": len(ledger.records) if ledger else 0,
    }
