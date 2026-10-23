#!/usr/bin/env bash
# 部署：打包源码并输出部署产物（实验室环境内部分发用）
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python -c "import sys; sys.path.insert(0,'src'); import venue_schedule_tool; print(venue_schedule_tool.__version__)")
OUT="dist/venue-schedule-tool-${VERSION}"
mkdir -p "$OUT"

cp -r src config data docs examples scripts requirements.txt README.md LICENSE "$OUT/"
tar -czf "dist/venue-schedule-tool-${VERSION}.tar.gz" -C dist "venue-schedule-tool-${VERSION}"

echo "部署包已生成：dist/venue-schedule-tool-${VERSION}.tar.gz"
echo "提醒：仅限实验室环境内部分发；输出不得作为实际业务指令（f40）。"
