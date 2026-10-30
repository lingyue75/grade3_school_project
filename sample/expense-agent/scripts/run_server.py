# -*- coding: utf-8 -*-
"""
启动脚本：python scripts/run_server.py

等价于 uvicorn src.main:app，这里把项目根目录加进搜索路径，避免"从 scripts/ 运行找不到 src"的问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from src.settings import settings  # noqa: E402

if __name__ == "__main__":
    print(f"启动 {settings.app_name} → http://{settings.host}:{settings.port}")
    print("演示账号：admin/admin123（管理员）  1001/123456（员工）")
    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=True)
