#!/usr/bin/env bash
# 一条命令启动园区访客车辆临时通行系统（仅需 Python 3.10+，无第三方依赖）
set -e
cd "$(dirname "$0")"

# 默认端口 8000；可：./run.sh --port 9000
# 重置演示数据：./run.sh --reset
# 离线重放命令行样例：python3 -m park.demo
exec python3 -m park "$@"
