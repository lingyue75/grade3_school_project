# -*- coding: utf-8 -*-
"""
Agent 三条典型轨迹离线冒烟（不需要启动服务、不需要 API Key）

运行：python smoke_agent.py
说明：决策器走规则策略（LLM_PROVIDER=mock），检索用 hash 向量，全部离线可跑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 保证从任意目录运行都能 import 到 src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.agent import loop as agent_loop
from src.agent import schema as agent_schema
from src.agent import tools
from src.main import _seed_sample_document
from src.rag import get_index

# ---------------- 初始化 ----------------
db.init_db()
agent_schema.init_agent_tables()
get_index().load_or_build()
_seed_sample_document()

user = db.query_one("SELECT * FROM users WHERE employee_no='1001'")
USER_ID, GRADE = user["id"], user["grade_code"] or "P5"


def show(event: str, data: dict, indent: str = "  ") -> None:
    """按事件类型打印一行可读日志。"""
    if event == "run_start":
        print(f"\n{'=' * 78}\n诉求：{data['goal']}   （最多 {data['max_steps']} 轮，run={data['run_id']}）\n{'=' * 78}")
    elif event == "step":
        print(f"{indent}[决策 {data['step_no']}] → {data['decision']}({json.dumps(data.get('args', {}), ensure_ascii=False)})")
    elif event == "tool_result":
        flag = "OK  " if data["ok"] else "拒绝"
        print(f"{indent}[执行 {data['step_no']}] {flag} {data['tool']} · {data['summary']} · {data['ms']}ms")
        if not data["ok"]:
            print(f"{indent}        ↳ 指令回传：{data['message']}")
    elif event == "interrupt":
        if data["kind"] == "confirm_draft":
            p = data["preview"]
            print(f"{indent}⏸  【中断·等待员工确认】{data['text']}")
            print(f"{indent}    类型={p['expense_type']} 期间={p['period']} 职级={p['grade_code']}")
            print(f"{indent}    标准={p['allowance'].get('amount')} {p['allowance'].get('unit')}（条款 {p['allowance'].get('clause_no')}）")
            for i in p["invoices"]:
                print(f"{indent}    票据 {i['invoice_no']} {i['seller']} {i['amount']}元")
            print(f"{indent}    合计 {p['total']} 元")
        else:
            print(f"{indent}⏸  【中断·追问】{data['question']} 选项={data['options']}")
    elif event == "final":
        print(f"{indent}✅ 【最终答复】{data['content']}")
    elif event == "error":
        print(f"{indent}❌ 错误：{data}")
    elif event == "done":
        print(f"{indent}—— 运行结束 run={data['run_id']} ——")


def run_case(title: str, goal: str, auto_confirm: bool = True) -> None:
    print(f"\n\n########## {title} ##########")
    run_id = None
    for event, data in agent_loop.start(USER_ID, "", goal, GRADE):
        show(event, data)
        if event == "run_start":
            run_id = data["run_id"]
        if event == "interrupt" and data["kind"] == "confirm_draft" and auto_confirm:
            print("  >>> 员工点击【确认创建】，恢复执行")
            for ev2, d2 in agent_loop.resume(run_id, "confirm"):
                show(ev2, d2)
            break
    # 打印决策轨迹日志（阶段5 分析的数据源）
    if run_id:
        print("\n  决策轨迹日志（agent_step_logs）：")
        for s in agent_loop.run_steps(run_id):
            print(f"    step{s['step_no']} {s['kind']:<9} {s['tool_name'] or '-':<18} "
                  f"ok={s['ok']} {s['duration_ms']}ms  {s['result_summary'][:40]}")


def reset_env() -> None:
    """重置演示数据，保证三条轨迹互不影响（票据状态、草稿、明细）。"""
    db.execute("UPDATE invoices SET status='unused', draft_id=NULL")
    db.execute("DELETE FROM draft_items")
    db.execute("DELETE FROM reimbursement_drafts")


def case_skip_guard() -> None:
    """★ 防跳步验证：模拟模型违规（未校验就挂票 / 未校验就提交），应被服务端拦截。"""
    print("\n\n########## 验证：模型想跳过校验，会被拦住吗？ ##########")
    state = {"stamps": {}, "draft_id": "dr_demo"}   # 预置草稿 ID，专门验证"校验令牌"这一层
    ctx = {"user_id": USER_ID, "session_id": "", "grade_code": GRADE, "state": state}
    inv = db.query_one("SELECT id FROM invoices WHERE status='unused' LIMIT 1")

    r1 = tools.execute("add_item", {"draft_id": "dr_demo",
                                    "invoice_ids": [inv["id"]], "item_type": "hotel"}, ctx)
    print(f"  ① 未校验票据直接 add_item      → ok={r1['ok']} code={r1['code']}\n     {r1['message']}")

    r2 = tools.execute("submit_draft", {"draft_id": "dr_demo"}, ctx)
    print(f"  ② 未跑规则校验直接 submit_draft → ok={r2['ok']} code={r2['code']}\n     {r2['message']}")

    # 校验通过后换票（指纹变化）也应被拦
    r3 = tools.execute("validate_invoice", {"invoice_ids": [inv["id"]]}, ctx)
    print(f"  ③ 校验通过：{r3['summary']}")
    other = db.query_one("SELECT id FROM invoices WHERE status='unused' AND id<>? LIMIT 1", (inv["id"],))
    if other:
        r4 = tools.execute("add_item", {"draft_id": "dr_demo", "invoice_ids": [other["id"]], "item_type": "hotel"}, ctx)
        print(f"  ④ 校验后【换票】再 add_item     → ok={r4['ok']} code={r4['code']}\n     {r4['message']}")

    # 防线④：工具动态可见性
    vis = [t["name"] for t in tools.visible_tools(state)]
    print(f"  ⑤ 当前可见工具（防线④）：{vis}")
    print("     → 票据未校验时不含 add_item；规则未校验时不含 submit_draft")


if __name__ == "__main__":
    # a. 只查制度，不碰写入类工具
    run_case("轨迹 a：出差上海3天住宿标准是多少", "出差上海3天住宿标准是多少")
    reset_env()
    # b. 完整建单流程（含人工确认中断）
    run_case("轨迹 b：帮我报上个月去北京的差旅", "帮我报上个月去北京的差旅")
    reset_env()
    # c. 单张发票可用性校验
    run_case("轨迹 c：这个发票能报吗", "这个发票能报吗")
    reset_env()
    # d. 防跳步验证
    case_skip_guard()
