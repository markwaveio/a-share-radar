# 安装与验收

在一台全新的 Mac 上，从零到自动出报表约 10 分钟。

---

# 第一步：拿到代码

仓库是 **private** 的，clone 需要认证。三选一。

## 方式 A：`gh` 认证（推荐，最省事）

```bash
brew install gh          # 没装过 gh 的话
gh auth login
# 选：GitHub.com → HTTPS → Authenticate Git: Yes → Login with a web browser
# 记下 8 位配对码，回车开浏览器，粘贴，点 Authorize

gh auth status           # 必须看到 ✓ Logged in，看到 X 就是没成功
gh repo clone markwaveio/a-share-radar
cd a-share-radar
```

⚠️ **`gh auth status` 这一步不要跳过。** 中途断掉的 `gh auth login` 会写下账号信息
但不写 token，看起来像成功了，实际 clone 会 401。

## 方式 B：SSH 密钥

适合这台机器以后要频繁推代码。

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N "" -C "$(whoami)@$(hostname -s)"
cat ~/.ssh/github.pub    # 复制整行
```

粘到 https://github.com/settings/keys → New SSH key → 类型选 **Authentication Key**
（不是 Signing Key，后者不能 push）。然后：

```bash
cat >> ~/.ssh/config <<'EOF'

Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

ssh -T git@github.com    # 应该看到 Hi <你的用户名>!
git clone git@github.com:markwaveio/a-share-radar.git
cd a-share-radar
```

## 方式 C：直接下载压缩包

不打算在这台机器上改代码时最省事。

```bash
gh release download        # 有 release 的话
# 或者网页版 Code → Download ZIP，解压后 cd 进去
```

代价：拿不到 git 历史，以后更新要重新下载。

---

# 第二步：安装

## 方式 1：一键脚本（推荐）

```bash
./install.sh
```

七步渐进，**任何一步失败都会停下说清原因，不会静默跳过**：

```
1 Python 版本 → 2 依赖 → 3 数据源连通性 → 4 指标自检（离线）
→ 5 链路自检（抓 30 只）→ 6 定时任务 → 7 打印下一步
```

常用变体：

```bash
./install.sh --check      # 只体检，不装依赖、不装定时任务、不改任何东西
./install.sh --no-cron    # 装但不加定时任务
RADAR_PYTHON=/opt/homebrew/bin/python3.12 ./install.sh   # 指定解释器
```

### 依赖它会自己处理

脚本按 **直装 → `--user` → 建 `.venv`** 三段回退。

这一段是有实际原因的：**Homebrew 的 Python 3.12+ 是 PEP 668 externally-managed**，
`pip install` 会直接报 `error: externally-managed-environment`。
脚本这时会在项目里建 `.venv` 并装进去，而不是用 `--break-system-packages` 污染系统环境。

建了 `.venv` 之后**不需要手动 activate** —— `run.py`、`scripts/run_radar.sh`
和 launchd 都会自动认它。

## 方式 2：让 Agent 引导安装（Claude Code）

把仓库目录加进 Claude Code 的工作区，或把 `SKILL.md` 放进 skills 目录，然后直接说：

```
安装 A股行情雷达
```

它会按 `SKILL.md` 的流程走完整个安装，并且比脚本多做三件事：

1. **数据可信度验证** —— 带你挑几只股票和自己的行情软件对照 KDJ，
   这一步脚本做不了，但它是最重要的一步
2. **解释每个异常** —— 比如日志里出现"数据源 eastmoney 熔断"是正常的，
   会告诉你为什么
3. **带你读第一份报表** —— 四张表的阅读顺序、哪些数不能信

日常也可以直接问它：「今天哪些板块资金在流入」「为什么这一列是空的」「定时任务没跑」。

## 方式 3：手动

想清楚每一步在做什么时用这个。

```bash
# 1. 依赖。系统 python3 通常可以直装；被 PEP 668 挡住就用 venv
python3 -m pip install -r requirements.txt \
  || python3 -m pip install --user -r requirements.txt \
  || { python3 -m venv .venv && .venv/bin/python3 -m pip install -r requirements.txt; }

# 2. 指标自检（不需要网络），必须 11 个全绿
python3 tests/test_indicators.py

# 3. 数据源连通性。只要腾讯是 200 就能跑
curl -s -o /dev/null -w "腾讯 %{http_code}\n" \
  'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,3,qfq'

# 4. 链路自检
python3 run.py --task 1 --limit 30

# 5. 全市场（25~35 分钟）
python3 run.py --task all

# 6. 定时任务
./scripts/install_launchd.sh install
```

建了 `.venv` 的话，上面的 `python3` 全部换成 `.venv/bin/python3`。

---

## 环境要求

| 项 | 要求 | 备注 |
|---|---|---|
| Python | 3.9+ | macOS 自带的 `/usr/bin/python3` 就够（14/15 是 3.9.6） |
| 依赖 | pandas / numpy / openpyxl / requests | **全新机器没有**，`install.sh` 会自动装 |
| 网络 | 能访问腾讯或东财行情接口 | 有多源降级，一个通就能跑 |
| 定时 | macOS launchd | 其他系统用 cron 调 `scripts/run_radar.sh` |
| 磁盘 | 约 500MB | 依赖 ~300MB，请求缓存跑一次全市场约 260MB（30 分钟后失效） |

### 关于 Python 版本的选择

| 解释器 | 能不能直接装依赖 | 建议 |
|---|---|---|
| `/usr/bin/python3`（系统自带 3.9） | 可以（必要时加 `--user`） | **默认用这个，最省事** |
| Homebrew `python@3.11` | 可以 | 也行 |
| Homebrew `python@3.12` 及以上 | **不行** —— PEP 668 externally-managed | 脚本会自动建 `.venv` |

后者的报错长这样：

```text
error: externally-managed-environment
× This environment is externally managed
```

不是环境坏了，是 Homebrew 刻意禁止往系统 site-packages 里装包。
`install.sh` 遇到它会自动建项目级 `.venv`，不需要你做任何事。

**不要用 `--break-system-packages` 绕过** —— 那会污染 Homebrew 管理的环境，
以后 `brew upgrade` 可能把你的包冲掉。

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
