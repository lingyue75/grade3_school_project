# -*- coding: utf-8 -*-
"""
Agent 三条典型轨迹演示（离线可跑，不需要任何模型与 API Key）

    python examples/agent_demo.py

轨迹：
    a. 「A厅 11-03 到 11-05 还有档期吗」     —— 只查不写
    b. 「客户想把 DLG-…0001 的档期延后两天」  —— 查 → 冲突 → 来源 → 评估 → 变更请求（中断）→ 授权确认
    c. 「DLG-…0002 的交接齐不齐」            —— 交接三项 + 版本核对
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from venue_schedule_tool.agent import loop as agent_loop  # noqa: E402
from venue_schedule_tool.agent import store  # noqa: E402

REVIEWER = "傅景"      # 授权人员（f41）


def show(event: str, data: dict, indent: str = "  ") -> None:
    if event == "run_start":
        print(f"\n{'=' * 76}\n诉求：{data['goal']}   （自主阶段最多 {data['max_steps']} 轮）\n{'=' * 76}")
    elif event == "step":
        arg = json.dumps(data.get("args", {}), ensure_ascii=False)
        print(f"{indent}[决策 {data['step_no']}] → {data['decision']}({arg})")
    elif event == "tool_result":
        flag = "OK  " if data["ok"] else "拒绝"
        print(f"{indent}[执行 {data['step_no']}] {flag} {data['tool']} · {data['summary']} · {data['ms']}ms")
        if not data["ok"]:
            print(f"{indent}        ↳ 指令回传：{data['message']}")
    elif event == "interrupt":
        if data["kind"] == "confirm_change":
            p = data["preview"]
            print(f"{indent}⏸  【中断·等待授权人员确认】{data['text']}")
            print(f"{indent}    申请 {p['app_id']}  类型 {p['change_type']}")
            print(f"{indent}    原档期 {p['current_window']}（版本 {p['current_version']}，状态 {p['current_status']}）")
            print(f"{indent}    新档期 {p['new_window']}")
            print(f"{indent}    评估 {p['assessment']}")
            print(f"{indent}    需确认人：{p['reviewer_required']}")
        else:
            print(f"{indent}⏸  【中断·追问】{data['question']}{data['options'] or ''}")
    elif event == "final":
        print(f"{indent}✅ 【结论】{data['content']}")
    elif event == "error":
        print(f"{indent}❌ 错误：{data}")
    elif event == "done":
        print(f"{indent}—— 运行结束 run={data['run_id']} ——")


def run_case(title: str, goal: str, auto_confirm: bool = True) -> None:
    print(f"\n\n########## {title} ##########")
    run_id = None
    for event, data in agent_loop.start(goal, {"operator": "林亦舟"}):
        show(event, data)
        if event == "run_start":
            run_id = data["run_id"]
        if event == "interrupt" and data["kind"] == "confirm_change" and auto_confirm:
            print(f"  >>> 授权人员【{REVIEWER}】点击确认，恢复执行")
            for ev2, d2 in agent_loop.resume(run_id, "confirm", reviewer=REVIEWER):
                show(ev2, d2)
            break
    if run_id:
        print("\n  决策轨迹日志（agent_step_logs，供阶段8 评测与阶段9 审计）：")
        for s in agent_loop.run_steps(run_id):
            print(f"    step{s['step_no']} {s['kind']:<9} {s['tool_name'] or '-':<22} "
                  f"ok={s['ok']} {s['duration_ms']}ms  {s['result_summary'][:38]}")


def guard_demo() -> None:
    """★ 防跳步验证：模型想违规（跳过来源校验直接发变更请求 / 尝试锁档）会被拦住吗"""
    from venue_schedule_tool.agent import tools

    print("\n\n########## 验证：模型想跳步 / 想锁档，会被拦住吗？ ##########")
    ctx = {"state": {"stamps": {}}, "reviewer": ""}

    r1 = tools.execute("draft_change_request",
                       {"app_id": "DLG-20261023-0001", "change_type": "reschedule"}, ctx)
    print(f"  ① 未校验来源/冲突直接发变更请求 → ok={r1['ok']} code={r1['code']}\n     {r1['message']}")

    r2 = tools.execute("lock_slot", {"app_id": "DLG-20261023-0001"}, ctx)
    print(f"  ② 试图调用 lock_slot（锁档）      → ok={r2['ok']} code={r2['code']}\n     {r2['message']}")

    r3 = tools.execute("request_authorization",
                       {"app_id": "DLG-20261023-0001", "reviewer": "林亦舟"}, ctx)
    print(f"  ③ 非授权人想作出正式结论          → ok={r3['ok']} code={r3['code']}\n     {r3['message']}")

    r4 = tools.execute("submit_handover",
                       {"app_id": "DLG-20261023-0002", "receiver": "唐映秋",
                        "version": "v2", "open_items": "无"}, ctx)
    print(f"  ④ 未做交接检查直接提交交接        → ok={r4['ok']} code={r4['code']}\n     {r4['message']}")

    names = [t["name"] for t in tools.visible_tools({"stamps": {}})]
    print(f"  ⑤ 当前可见工具（防线④）：{names}")
    print("     → 无 lock_slot / sign_contract / commit_capacity；未校验时不含 draft_change_request")

    r5 = tools.execute("query_slots", {"status": "missing"}, ctx)
    print(f"  ⑥ 状态缺失档期如何表述（f31）    → {r5['message'][:60]}…")


if __name__ == "__main__":
    store.init_db()
    run_case("轨迹 a：A厅 11-03 到 11-05 还有档期吗", "A厅 2026-11-03 到 2026-11-05 还有档期吗")
    run_case("轨迹 b：客户想把 DLG-20261023-0001 的档期延后两天", "把 DLG-20261023-0001 的档期延后两天")
    run_case("轨迹 c：DLG-20261023-0002 的交接齐不齐", "DLG-20261023-0002 的交接齐不齐")
    guard_demo()
    print("\n说明：以上均为 advisory 提示，系统不产生任何锁档/签约/容量承诺动作（f05/f52）。")
