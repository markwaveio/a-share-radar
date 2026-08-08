#!/bin/bash
# 安装/卸载 launchd 定时任务。
#
#   ./install_launchd.sh install    安装并加载
#   ./install_launchd.sh uninstall  卸载
#   ./install_launchd.sh status     查看状态
#
# 两个批次：
#   11:40  午盘收盘后（11:30 收盘 + 10 分钟缓冲，等东财数据落库）
#   15:15  下午收盘后（15:00 收盘 + 15 分钟缓冲，资金流数据出得比行情晚）

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
RUNNER="$PROJECT_DIR/scripts/run_radar.sh"

make_plist() {
    local session="$1" hour="$2" minute="$3"
    local label="com.ashare-radar.${session}"
    cat > "$AGENTS_DIR/${label}.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${RUNNER}</string>
        <string>${session}</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>${hour}</integer>
        <key>Minute</key><integer>${minute}</integer>
    </dict>

    <!-- 开机不补跑：错过的批次数据当天已失去时效，补跑只会覆盖出错误时点的快照 -->
    <key>RunAtLoad</key>
    <false/>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/data/logs/launchd.${session}.out.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/data/logs/launchd.${session}.err.log</string>
</dict>
</plist>
PLIST
    echo "已写入 $AGENTS_DIR/${label}.plist"
}

case "${1:-status}" in
  install)
    mkdir -p "$AGENTS_DIR" "$PROJECT_DIR/data/logs"
    chmod +x "$RUNNER"
    make_plist midday 11 40
    make_plist close  15 15
    for s in midday close; do
        label="com.ashare-radar.${s}"
        launchctl unload "$AGENTS_DIR/${label}.plist" 2>/dev/null || true
        launchctl load  "$AGENTS_DIR/${label}.plist"
        echo "已加载 $label"
    done
    ;;
  uninstall)
    for s in midday close; do
        label="com.ashare-radar.${s}"
        launchctl unload "$AGENTS_DIR/${label}.plist" 2>/dev/null || true
        rm -f "$AGENTS_DIR/${label}.plist"
        echo "已移除 $label"
    done
    ;;
  status)
    launchctl list | grep -i diane-radar || echo "未安装"
    ;;
  *)
    echo "用法：$0 {install|uninstall|status}" >&2
    exit 1
    ;;
esac
