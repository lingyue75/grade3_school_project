# -*- coding: utf-8 -*-
"""
Agent 编排包（场馆排期场景）

    tools.py    工具 Schema + 服务端强校验 + 校验令牌（★红线在此落地）
    planner.py  决策器：规则策略 / 真实大模型 Function Calling
    loop.py     执行循环：最多 5 轮 + 变更请求的授权人确认中断与恢复
    store.py    运行实例 / 决策日志 / 变更请求（SQLite，标准库）

边界（f05/f40/f41/f52）：只提示、不锁档；无来源不流转；正式结论须授权人员确认。
"""

from . import loop, planner, store, tools

__all__ = ["loop", "planner", "store", "tools"]
