"""30 样本回放 demo（对齐验收口径 f02：识别全部已标注冲突并说明原因）。

运行：python examples/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from venue_schedule_tool.core.conflict import detect_all  # noqa: E402
from venue_schedule_tool.core.registry import is_valid_app_id  # noqa: E402
from venue_schedule_tool.core.review import replay_report  # noqa: E402
from venue_schedule_tool.utils.io import load_handovers, load_slots  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"

LABELED_CONFLICT_PAIRS = {
    # 已标注的档期冲突（区域/装卸口时间重叠），见 data/slots.csv 备注
    ("DLG-20261023-0001", "DLG-20261023-0002"),
    ("DLG-20261023-0005", "DLG-20261023-0006"),
    ("DLG-20261023-0008", "DLG-20261023-0009"),
    ("DLG-20261023-0017", "DLG-20261023-0018"),
    ("DLG-20261023-0017", "DLG-20261023-0019"),
    ("DLG-20261023-0018", "DLG-20261023-0019"),
    ("DLG-20261023-0021", "DLG-20261023-0022"),
}


def main() -> int:
    slots = load_slots(DATA / "slots.csv")
    handovers = load_handovers(DATA / "handovers.csv")

    assert len(slots) == 30, f"样本数应为 30，实际 {len(slots)}（f02 验收口径）"
    assert all(is_valid_app_id(s.app_id) for s in slots), "存在不合规的统一关联编号"

    conflicts = detect_all(slots, handovers)

    # 检查已标注冲突是否全部被识别
    detected_pairs = {
        tuple(sorted(c.app_ids))
        for c in conflicts
        if c.conflict_type.value.endswith("overlap")
    }
    missed = [
        pair
        for pair in LABELED_CONFLICT_PAIRS
        if tuple(sorted(pair)) not in detected_pairs
    ]

    report = replay_report(conflicts)
    print(f"样本数：{len(slots)}")
    print(f"识别冲突提示：{report['total_conflicts']} 条（含交接齐套/版本提示）")
    print(f"已标注档期冲突识别：{len(LABELED_CONFLICT_PAIRS) - len(missed)}/{len(LABELED_CONFLICT_PAIRS)}")
    print(f"全部提示含原因：{report['all_with_reason']}；全部含来源：{report['all_with_source']}")
    print(f"自动锁档动作：{report['auto_lock_used']}（应恒为 False，f05/f52）")

    if missed:
        print(f"未识别的已标注冲突：{missed}")
        return 1
    print("\n回放结论：30 样本回放识别全部已标注冲突且均说明原因 —— 验收口径满足（f02）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
