#!/usr/bin/env bash
# 构建：跑测试 + 回放自检，产出可分发的检查报告
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python}

echo "==> 安装依赖"
"$PYTHON" -m pip install -q -r requirements.txt

echo "==> 运行测试"
"$PYTHON" -m pytest tests/ -v

echo "==> 30 样本回放"
"$PYTHON" examples/demo.py

echo "==> 构建完成"
