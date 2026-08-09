#!/bin/bash
# A股行情雷达 —— launchd 调用入口
#
#   ./run_radar.sh midday   午盘批（11:40 跑，只出需求 1）
#   ./run_radar.sh close     收盘批（15:15 跑，四个需求全出）
#
# 退出码：0 成功 / 1 失败 / 2 非交易日跳过

set -uo pipefail

SESSION="${1:-close}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# 解释器优先级：环境变量 > 项目 .venv > 系统 python3。
# .venv 是 install.sh 在系统 Python 不允许装包时（PEP 668）建的，
# launchd 不会继承任何 shell 的 activate 状态，所以必须在这里显式指向它。
if [ -n "${RADAR_PYTHON:-}" ]; then
    PYTHON="$RADAR_PYTHON"
elif [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
else
    PYTHON="$(command -v python3 || echo /usr/bin/python3)"
fi
LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/radar_$(date +%Y%m%d)_${SESSION}.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "===== 批次 $SESSION 开始 ====="

# 交易日闸门：非交易日直接跳过，省掉 3 万次无意义请求
if ! "$PYTHON" -m radar.trading_calendar >>"$LOG_FILE" 2>&1; then
    log "非交易日，跳过"
    exit 2
fi

case "$SESSION" in
    midday)
        # 午盘只出 KDJ：分钟级周期已经更新，但成交量/资金流要等收盘才有完整数据
        TASKS="1"
        ;;
    close)
        TASKS="all"
        ;;
    *)
        log "未知批次：$SESSION（应为 midday 或 close）"
        exit 1
        ;;
esac

log "执行任务：$TASKS"
"$PYTHON" run.py --task "$TASKS" --session "$SESSION" >>"$LOG_FILE" 2>&1
STATUS=$?

if [ $STATUS -eq 0 ]; then
    log "===== 批次 $SESSION 完成 ====="
    # 保留最近 30 天日志和 60 天报表
    find "$LOG_DIR" -name 'radar_*.log' -mtime +30 -delete 2>/dev/null
    find "$PROJECT_DIR/data/output" -name '*.xlsx' -mtime +60 -delete 2>/dev/null
else
    log "===== 批次 $SESSION 失败，退出码 $STATUS ====="
fi

exit $STATUS
