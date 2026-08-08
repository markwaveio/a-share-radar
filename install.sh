#!/bin/bash
# A股行情雷达 —— 引导式安装
#
#   ./install.sh            完整引导安装
#   ./install.sh --check    只做环境体检，不改任何东西
#   ./install.sh --no-cron  安装但不装定时任务
#
# 每一步失败就停下并说清原因，不静默跳过。

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

CHECK_ONLY=0
INSTALL_CRON=1
for arg in "$@"; do
    case "$arg" in
        --check)   CHECK_ONLY=1 ;;
        --no-cron) INSTALL_CRON=0 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 1 ;;
    esac
done

BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
step() { echo; echo "${BOLD}▶ $*${RESET}"; }
ok()   { echo "  ${GREEN}✓${RESET} $*"; }
warn() { echo "  ${YELLOW}!${RESET} $*"; }
fail() { echo "  ${RED}✗${RESET} $*"; }
die()  { echo; echo "${RED}安装中止：$*${RESET}"; exit 1; }

PYTHON="${RADAR_PYTHON:-python3}"

echo "${BOLD}A股行情雷达 · 安装${RESET}"
echo "${DIM}目录：$PROJECT_DIR${RESET}"

# ---------------------------------------------------------------- 1. Python
step "1/7  检查 Python"
command -v "$PYTHON" >/dev/null 2>&1 || die "找不到 $PYTHON，请先安装 Python 3.9+"
PYVER=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
ok "$PYTHON  版本 $PYVER"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "需要 Python 3.9 或更高，当前 $PYVER"

# ---------------------------------------------------------------- 2. 依赖
step "2/7  检查依赖"
MISSING=$("$PYTHON" - <<'PY'
import importlib
missing = [m for m in ("pandas", "numpy", "openpyxl", "requests")
           if importlib.util.find_spec(m) is None]
print(" ".join(missing))
PY
)
if [ -z "$MISSING" ]; then
    ok "pandas / numpy / openpyxl / requests 齐全"
elif [ "$CHECK_ONLY" -eq 1 ]; then
    warn "缺少：$MISSING（--check 模式不安装）"
else
    warn "缺少：$MISSING，正在安装"
    "$PYTHON" -m pip install --quiet -r requirements.txt \
        || die "依赖安装失败。试试 $PYTHON -m pip install --user -r requirements.txt"
    ok "依赖安装完成"
fi

# ---------------------------------------------------------------- 3. 网络
step "3/7  检查数据源连通性"
probe() {
    local name="$1" url="$2"
    local code
    code=$(curl -s -o /dev/null -m 12 -w "%{http_code}" "$url" 2>/dev/null)
    if [ "$code" = "200" ]; then ok "$name  HTTP 200"; return 0
    else fail "$name  HTTP ${code:-000}"; return 1; fi
}
TENCENT_OK=0
probe "东方财富 (push2delay)" 'https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=2&po=1&np=1&fltt=2&invt=2&fid=f12&fs=m:1+t:2&fields=f12,f14' || true
probe "腾讯     (ifzq.gtimg)" 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,3,qfq' && TENCENT_OK=1

if [ "$TENCENT_OK" -eq 0 ]; then
    warn "腾讯源不通。项目有多源降级，但至少要有一个源可用"
    if [ -n "${HTTP_PROXY:-}${http_proxy:-}" ]; then
        warn "检测到代理 ${HTTP_PROXY:-$http_proxy} —— Python 默认会走它，代理挂了会全线失败"
        warn "可以试：RADAR_USE_PROXY=0 $PYTHON run.py --task 1 --limit 5"
    fi
    [ "$CHECK_ONLY" -eq 1 ] || die "没有可用数据源，先解决网络再重试"
fi

# ---------------------------------------------------------------- 4. 离线测试
step "4/7  指标算法自检（离线，不需要网络）"
if "$PYTHON" tests/test_indicators.py 2>&1 | tail -1 | grep -q "全部通过"; then
    ok "11 个单元测试全绿"
else
    "$PYTHON" tests/test_indicators.py 2>&1 | grep -E "FAIL|ERROR" | head -5
    die "指标测试未通过。指标算错不会报错，只会安静给出错误结论 —— 不要带着它继续"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo; echo "${GREEN}体检完成，未做任何改动。${RESET}"
    echo "去掉 --check 即可正式安装。"
    exit 0
fi

# ---------------------------------------------------------------- 5. 链路自检
step "5/7  链路自检（抓 30 只股票，约 15 秒）"
mkdir -p data/output data/logs data/cache data/flow_history
LOGFILE="$(mktemp -t radar_install)"
# 先落盘再 grep：`... | tee f | grep -q` 在 pipefail 下会误报失败 ——
# grep -q 命中即退出关闭管道，tee 收到 SIGPIPE 返回非零，整条管道被判成失败。
"$PYTHON" run.py --task 1 --limit 30 >"$LOGFILE" 2>&1
if grep -q "已生成" "$LOGFILE"; then
    SUCCESS=$(grep -oE "成功 [0-9]+，失败 [0-9]+" "$LOGFILE" | head -1)
    HITS=$(grep -oE "数据源命中：.*" "$LOGFILE" | head -1)
    ok "抓取正常（${SUCCESS:-已完成}）"
    [ -n "$HITS" ] && echo "     ${DIM}${HITS}${RESET}"
    ok "$(ls -t data/output/*.xlsx 2>/dev/null | head -1)"
else
    grep -vE "请求失败" "$LOGFILE" | tail -12
    rm -f "$LOGFILE"
    die "链路自检失败，看上面的日志"
fi
rm -f "$LOGFILE"

# ---------------------------------------------------------------- 6. 定时任务
step "6/7  定时任务"
if [ "$INSTALL_CRON" -eq 0 ]; then
    warn "已跳过（--no-cron）。之后可以运行 ./scripts/install_launchd.sh install"
elif [ "$(uname)" != "Darwin" ]; then
    warn "非 macOS，launchd 不可用。请自行用 cron 调度 scripts/run_radar.sh"
    echo "     ${DIM}40 11 * * 1-5  $PROJECT_DIR/scripts/run_radar.sh midday${RESET}"
    echo "     ${DIM}15 15 * * 1-5  $PROJECT_DIR/scripts/run_radar.sh close${RESET}"
else
    chmod +x scripts/*.sh
    if ./scripts/install_launchd.sh install >/dev/null 2>&1; then
        ok "已装两个批次：11:40 午盘 / 15:15 收盘"
        ./scripts/install_launchd.sh status 2>/dev/null | sed 's/^/     /'
    else
        warn "launchd 安装失败，可手动运行 ./scripts/install_launchd.sh install"
    fi
fi

# ---------------------------------------------------------------- 7. 完成
step "7/7  完成"
cat <<EOF

  ${GREEN}安装完成。${RESET}

  ${BOLD}下一步${RESET}
    $PYTHON run.py --task all          跑全部四个需求（全市场约 15~25 分钟）
    $PYTHON run.py --task all --limit 300   先跑个 1 分钟的样本版看看结构

  ${BOLD}报表在${RESET}  data/output/
    01_KDJ全周期      全市场 × 10 个周期的 K/D/J
    02_成交量异动      变异系数 CV + 放量倍数（日常只看「放量TOP200」页）
    03_咨询量变化      48 小时资讯量 vs 前两周日均
    04_板块热度        板块主力净流入占全市场比例

  ${BOLD}第一次要知道的三件事${RESET}
    1. 需求 4 第一天只有 1 列数据。没有接口能稳定给多日历史，
       改成本地按日累积 —— 跑满 4 个交易日才会补满。
    2. 打开 Excel 先看「口径说明」页的"抓取失败"数。
       失败几百上千的那天，数据不能用。
    3. 空格代表数据不足，不代表 0。J 值填 0 会被读成"超卖"。

  ${BOLD}验证数据可信${RESET}
    挑 3~5 只股票，用你自己的行情软件对照日线 KDJ(9,3,3)。
    差 0.05 以内正常，差 1 以上看 README 的「KDJ 和行情软件对不上」。

  ${BOLD}边界${RESET}  只做数据加工。产出是候选池，不是买卖信号。

  详见 README.md
EOF
