"""程序入口：回放样例数据，输出冲突提示报告。

用法：
    python -m venue_schedule_tool.main                # 使用默认样例
    python -m venue_schedule_tool.main --slots path --handovers path --out report.json

边界（f05/f40/f52）：只提示冲突，不自动锁档；输出结果仅供人工复核，
不得作为实际业务指令。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.conflict import detect_all
from .core.review import replay_report
from .utils.io import load_handovers, load_slots

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="场馆档期冲突提示（advisory only）")
    p.add_argument("--slots", default=str(DEFAULT_DATA / "slots.csv"), help="档期样例 CSV")
    p.add_argument("--handovers", default=str(DEFAULT_DATA / "handovers.csv"), help="交接记录 CSV")
    p.add_argument("--out", default=None, help="报告输出 JSON 路径（缺省仅打印）")
    return p


def run(slots_csv: str, handovers_csv: str) -> dict:
    slots = load_slots(slots_csv)
    handovers = load_handovers(handovers_csv)
    conflicts = detect_all(slots, handovers)
    report = replay_report(conflicts)
    report["conflicts"] = [c.to_dict() for c in conflicts]
    report["disclaimer"] = (
        "本报告为演练系统输出，仅作冲突提示，不构成锁档/签约/容量承诺；"
        "正式结论须由授权人员确认（f40/f41/f52）。"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run(args.slots, args.handovers)

    print(f"共识别 {report['total_conflicts']} 条冲突提示（只提示、不锁档）：\n")
    for i, c in enumerate(report["conflicts"], 1):
        print(f"[{i}] {c['type']}  涉及：{'、'.join(c['app_ids'])}")
        print(f"    原因：{c['reason']}")
        print(f"    来源：{'、'.join(c['sources']) or '（无，不得进入正式流转）'}\n")

    print(
        "回放自检："
        f"全部提示含原因={report['all_with_reason']}，"
        f"全部提示含来源={report['all_with_source']}，"
        f"自动锁档={report['auto_lock_used']}"
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
