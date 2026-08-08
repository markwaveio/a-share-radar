# 安装与验收

## 三种安装方式

### 方式 1：一键脚本（推荐）

```bash
git clone <this-repo> a-share-radar && cd a-share-radar
./install.sh
```

脚本会依次做：Python 版本检查 → 依赖安装 → 数据源连通性 → 指标自检 →
链路自检（抓 30 只） → 装 launchd 定时任务 → 打印下一步。

**任何一步失败都会停下并说清原因，不会静默跳过。**

只想体检不改任何东西：

```bash
./install.sh --check      # 只检查，不装依赖、不装定时任务
./install.sh --no-cron    # 装但不加定时任务
```

### 方式 2：引导式安装（Claude Code）

把仓库放进 skills 目录后，直接说：

```
安装 A股行情雷达
```

它会按 `SKILL.md` 的流程走一遍，包括教你怎么验证数据可信、怎么读第一份报表。

### 方式 3：手动

```bash
python3 -m pip install -r requirements.txt
python3 tests/test_indicators.py          # 必须 11 个全绿
python3 run.py --task 1 --limit 30        # 链路自检
python3 run.py --task all                 # 全市场
./scripts/install_launchd.sh install      # 定时任务
```

---

## 环境要求

| 项 | 要求 | 备注 |
|---|---|---|
| Python | 3.9+ | macOS 系统自带的 `/usr/bin/python3` 可以直接用 |
| 依赖 | pandas / numpy / openpyxl / requests | 系统 Python 通常已自带，先检查再装 |
| 网络 | 能访问腾讯或东财行情接口 | 有多源降级，一个通就能跑 |
| 定时 | macOS launchd | 其他系统用 cron 调 `scripts/run_radar.sh` |

指定 Python 解释器：

```bash
RADAR_PYTHON=/opt/homebrew/bin/python3.12 ./install.sh
```

本机走代理时，Python 默认会读 `HTTP_PROXY`。代理挂了会全线失败，绕过：

```bash
RADAR_USE_PROXY=0 python3 run.py --task 1 --limit 10
```

---

## 验收清单

装完按这个表逐项确认。**最后一项才是真正的验收标准。**

| # | 项目 | 验收方式 | 通过标准 |
|---|---|---|---|
| 1 | 指标算法 | `python3 tests/test_indicators.py` | 11 个测试全绿 |
| 2 | 链路自检 | `python3 run.py --task 1 --limit 30` | 生成 xlsx，失败 0 只 |
| 3 | 数据源命中 | 看日志 `数据源命中：` | 命中数 = 股票数 × 6 |
| 4 | 表头结构 | 打开 `01_KDJ全周期` | 32 列、两行合并表头、12 个合并区 |
| 5 | **KDJ 准确性** | 挑 3~5 只与行情软件手工对照 | 差值 < 0.05 |
| 6 | 空值处理 | 找一只新上市股票 | 月线列留空，**不是 0** |
| 7 | 需求 2 | 打开 `02_成交量异动` | 四个指标齐全，有「放量TOP200」页 |
| 8 | 需求 3 | `--task 3 --sentiment-universe watchlist` | 有条数、有升温倍数、有截断标记 |
| 9 | 需求 4 | 打开 `04_板块热度` | 板块按今日占比降序 |
| 10 | 口径说明 | 每个 xlsx 最后一个 sheet | 参数、数据源、空值含义齐全 |
| 11 | 全市场跑通 | `python3 run.py --task all` | 4 个文件，失败 < 100 只 |
| 12 | 交易日闸门 | 周末跑 `./scripts/run_radar.sh close` | 退出码 2，日志写「非交易日，跳过」 |
| 13 | 定时任务 | `./scripts/install_launchd.sh status` | 两个 label 都在 |
| 14 | **长期稳定** | 观察 10 个交易日 | 无人工干预按时出表 |

第 5 项不能跳过 —— **指标算错不会报错，只会安静给出错误结论。**

如果装了富途 OpenD（`127.0.0.1:11111`），可以用它做自动交叉验证：
富途是完全独立的数据商，两边 KDJ 一致就能排除数据源风险。

---

## 装完第一天要知道的三件事

**1. 需求 4 只有 1 列数据是正常的。**

A-3 / A-2 / A-1 三列会是空的。没有接口能稳定给多日板块资金流历史
（`push2his` 间歇性、`push2delay` 只给最新 1 天），所以改成本地按日累积。
每天跑一次，**3 个交易日后自动补满**。

**2. 午盘批和收盘批的「本日」不一样。**

「本日/本周/本月」指的是当前尚未走完的周期。11:40 拿到的是半天数据，
15:15 才是完整的一天。这是定义决定的，不是 bug。

**3. 每次打开表先看「口径说明」页的"抓取失败"数。**

正常是几十只以内（停牌、新股）。突然变成几百上千说明接口出问题了，
**当天这份数据不能用** —— 不要用"至少大部分是对的"说服自己，你不知道错的是哪几百只。

---

## 卸载

```bash
./scripts/install_launchd.sh uninstall   # 移除定时任务
rm -rf data/                              # 清掉所有本地数据（含历史快照）
```

代码目录直接删即可，没有写入系统其他位置。

---

## 常见安装问题

**`./install.sh` 提示找不到 python3**

装 Python 3.9+，或用 `RADAR_PYTHON=/path/to/python3 ./install.sh` 指定。

**依赖安装失败（权限）**

```bash
python3 -m pip install --user -r requirements.txt
```

**两个数据源都不通**

先手工验证：

```bash
curl -s -o /dev/null -w "%{http_code}\n" 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,3,qfq'
```

200 说明网络没问题，是 Python 侧的问题（多半是代理）。
其他返回值说明到不了数据源，检查本机网络/代理。

**链路自检通过但命中数不对**

比如 30 只股票只命中 90 次（应该 180 次），说明有周期缺失。
看日志里有没有 `数据源 xxx 连续失败 N 次，熔断` —— 某个源挂了而没有兜底。
查 `radar/providers.py` 的 `PROVIDER_CHAIN`。

**手动跑没问题，launchd 定时跑就失败**

最常见是 PATH —— launchd 的环境变量和终端不一样。

```bash
cat data/logs/launchd.close.err.log
```

确认 `~/Library/LaunchAgents/com.ashare-radar.*.plist` 里的 `PATH` 包含 python3 所在目录。
