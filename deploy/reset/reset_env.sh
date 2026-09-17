#!/usr/bin/env sh
# AzurPilot 环境重置（Linux / macOS）
#
# 用法：
#   sh deploy/reset/reset_env.sh            # 交互式
#   sh deploy/reset/reset_env.sh --yes      # 跳过确认
#   sh deploy/reset/reset_env.sh --list     # 只列出将被删除的内容
#
# 本脚本不依赖 .venv（.venv 正是要被删除的目标），
# 解释器按 python3 -> python 的顺序查找。

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$REPO_ROOT"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[!] 没有找到 python3/python，无法执行重置。"
    echo "    请先安装 Python 3，或改用启动器上的「重置环境后退出」。"
    exit 1
fi

exec "$PY" "deploy/reset/reset_env.py" "$@"
