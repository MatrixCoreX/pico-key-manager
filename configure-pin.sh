#!/usr/bin/env bash
set -uo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$task_root/.venv/bin/python" "$task_root/set-pin-local.py"
task_status=$?
if [[ "$task_status" -ne 0 ]]; then
    echo 'PIN 设置未完成；可关闭窗口后重新运行 configure-pin.sh。'
fi
read -r -p '按 Enter 关闭窗口。' _
exit "$task_status"
