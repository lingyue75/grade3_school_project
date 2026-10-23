"""统一关联编号（f30）：跨工具串联的先决条件。

编号规则：DLG-<YYYYMMDD>-<4位序号>，例如 DLG-20261023-0007。
服务台、业务台账、文件库三类工具统一引用该编号，人工比对降为一次核对。
"""

from __future__ import annotations

import re

APP_ID_PATTERN = re.compile(r"^DLG-\d{8}-\d{4}$")


def is_valid_app_id(app_id: str) -> bool:
    """校验统一关联编号格式。"""
    return bool(APP_ID_PATTERN.match(app_id or ""))


def make_app_id(date_str: str, seq: int) -> str:
    """生成统一关联编号。date_str 形如 20261023，seq 从 1 开始。"""
    if len(date_str) != 8 or not date_str.isdigit():
        raise ValueError(f"日期格式应为 YYYYMMDD，收到：{date_str!r}")
    if seq < 1 or seq > 9999:
        raise ValueError(f"序号应在 1-9999 之间，收到：{seq}")
    return f"DLG-{date_str}-{seq:04d}"
