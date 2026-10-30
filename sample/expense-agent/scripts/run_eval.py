# -*- coding: utf-8 -*-
"""
评测执行器：跑 eval/cases.json 的 30 条用例，输出 eval/results.md 与 eval/results.json

自动判定：工具调用顺序（expected_tools）与实际调用序列比对
人工判定：答案内容是否符合 expected_behavior（尤其陷阱题与违规意图题，必须人工过目）

运行：python scripts/run_eval.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.agent import loop as agent_loop  # noqa: E402
from src.agent import schema as agent_schema  # noqa: E402
from src.rag import get_index  # noqa: E402

CASES = ROOT / "eval" / "cases.json"
OUT_MD = ROOT / "eval" / "results.md"
OUT_JSON = ROOT / "eval" / "results.json"

# 尚未实现的工具（P2），出现在实际序列中属于例外，单独标注
PENDING_TOOLS = {"query_status", "withdraw_draft"}

db.init_db()
agent_schema.init_agent_tables()
get_index().load_or_build()
USER = db.query_one("SELECT * FROM users WHERE employee_no='1001'")
USER_ID, GRADE = USER["id"], USER["grade_code"] or "P3"


def reset_env() -> None:
    """每条用例前重置演示数据，避免票据被前一条占用。"""
    db.execute("UPDATE invoices SET status='unused', draft_id=NULL")
    db.execute("DELETE FROM draft_items")
    db.execute("DELETE FROM reimbursement_drafts")


def actual_tools(question: str) -> tuple[list[str], str, bool]:
    """跑一次 Agent，返回 (实际工具序列, 最终答复, 是否曾中断等待确认)。"""
    seq: list[str] = []
    answer, interrupted, run_id = "", False, None
    for event, data in agent_loop.start(USER_ID, "", question, GRADE):
        if event == "run_start":
            run_id = data["run_id"]
        elif event == "step":
            d = data.get("decision")
            if d and d != "final":
                seq.append(d)
        elif event == "interrupt" and data.get("kind") == "confirm_draft":
            interrupted = True
            for ev2, d2 in agent_loop.resume(run_id, "confirm"):
                if ev2 == "step" and d2.get("decision") not in (None, "final"):
                    seq.append(d2["decision"])
                elif ev2 == "final":
                    answer = d2["content"]
            break
        elif event == "final":
            answer = data["content"]
    return seq, answer, interrupted


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    results = []
    for c in cases:
        reset_env()
        question = c["question"]
        if c.get("context"):                       # 多轮用例：把前文并入问题，模拟指代场景
            question = "；".join(c["context"]) + " || " + question
        seq, answer, interrupted = actual_tools(question)

        expected = c.get("expected_tools") or []
        cmp_exp = [t for t in expected if t not in PENDING_TOOLS]
        cmp_act = [t for t in seq if t not in PENDING_TOOLS]
        tools_ok = (cmp_act == cmp_exp)

        results.append({
            "id": c["id"], "category": c["category"], "question": c["question"],
            "expected_tools": expected, "actual_tools": seq,
            "tools_match": tools_ok, "interrupted_for_confirm": interrupted,
            "expected_behavior": c.get("expected_behavior", ""),
            "answer": answer, "human_review": None,   # 人工判定留空，由 QA 填写 pass/fail
        })
        flag = "✓" if tools_ok else "✗"
        print(f"{flag} {c['id']} [{c['category']}] 期望={expected} 实际={seq}")

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成 Markdown 报告
    lines = [
        "# 评测结果", "",
        f"- 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 用例总数：{len(results)}",
        f"- 工具顺序自动通过：{sum(1 for r in results if r['tools_match'])}/{len(results)}",
        "",
        "> 工具顺序可自动判定；**答案内容是否符合 expected_behavior 必须人工过目**，",
        "> 尤其是 3 条陷阱题（期望'未找到'）与 5 条违规意图题（期望合规指引，不得给规避方案）。",
        "",
        "| ID | 类别 | 问题 | 期望工具 | 实际工具 | 工具顺序 | 人工判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['category']} | {r['question'][:28]} | "
            f"{' → '.join(r['expected_tools']) or '-'} | {' → '.join(r['actual_tools']) or '-'} | "
            f"{'通过' if r['tools_match'] else '不通过'} | 待填 |"
        )
    lines += ["", "## 逐条答复（人工判定依据）", ""]
    for r in results:
        lines += [f"### {r['id']} · {r['question']}", "",
                  f"**期望行为**：{r['expected_behavior']}", "",
                  "**实际答复**：", "```", (r["answer"] or "(无)")[:600], "```", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已生成：{OUT_MD.name} / {OUT_JSON.name}")


if __name__ == "__main__":
    main()
