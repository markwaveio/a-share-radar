# 数据源

数据来自三家的公开行情接口 —— 和各家 App / 网页版用的是同一套。

**这些接口没有官方文档，也没有服务承诺。** 字段可能改名，接口可能下线，
而且**会在没有任何预告的情况下对部分出口 IP 停止服务**。
所以代码里每次失败都要计数，失败率是这套系统唯一的健康指标。

## 当前实测状态（2026-08-09）

| 接口 | 用途 | 状态 |
|---|---|---|
| `ifzq.gtimg.cn/appstock/app/fqkline/get` | 腾讯 日/周/月 K 线 | ✅ 主源 |
| `ifzq.gtimg.cn/appstock/app/kline/mkline` | 腾讯 分钟 K 线 | ✅ 主源 |
| `push2delay.eastmoney.com/api/qt/clist/get` | 东财 股票池 / 板块 / 个股资金流 | ✅ |
| `push2his.eastmoney.com/.../fflow/daykline/get` | 东财 板块资金流历史 | ⚠️ **间歇性** |
| `search-api-web.eastmoney.com/search/jsonp` | 东财 资讯检索 | ✅ |
| `money.finance.sina.com.cn/.../getKLineData` | 新浪 K 线 | ⚠️ 兜底，扛不住全市场负载 |
| `push2his.eastmoney.com/.../stock/kline/get` | 东财 K 线 | ❌ **不通**（恢复后代码自动切回）|

自查命令：

```bash
for u in \
  'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,3,qfq' \
  'https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=2&po=1&np=1&fltt=2&invt=2&fid=f12&fs=m:1+t:2&fields=f12,f14' \
  'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600000&klt=101&lmt=3&fields1=f1&fields2=f51,f52'
do printf "%-70s " "${u:0:68}"; curl -s -o /dev/null -m 12 -w "%{http_code}\n" "$u"; done
```

## 三种失败形态（都遇到过）

**1. 连接被服务端主动断开** —— `curl: (52) Empty reply from server`

TCP + TLS 握手都成功，服务端接受连接后直接关闭、一个字节都不回。
不是网络故障：同一时刻同一台机器上，同一域名的其他路径完全正常。
**封锁粒度是按路径的** —— 同一台 `push2his` 上 `kline` 死了、`fflow` 活着。

**2. HTTP 200 但没有数据** —— 最危险的一种

`push2delay` 的 kline 接口，状态码 200、JSON 格式合法，但里面没有 K 线。
而且它"没有数据"的长相**会变**，两天内观察到两种：

```text
2026-08-08:  {"rc":0,  ..., "data":{"code":"600000","klines":[]}}
2026-08-09:  {"rc":102,..., "data":null}
```

**只检查 HTTP 状态码的代码会认为一切正常**，然后把空数据一路传下去，
最后 Excel 那一列全是空白，日志里一条错误都没有。
只检查 `rc == 0` 也挡不住第一种。

```text
接口"可用"有四个层次：能连上 → 200 → 业务码正常 → 真的有你要的数据。
唯一可靠的判据是把你真正要用的字段取出来，看它是不是空的。
```

所以 `radar/providers.py` 里业务码只用来快速短路，
**最终判据始终是"目标字段取出来非空"**，取不到就返回 `None` 交给下一个源。
而且不能假设 `data` 一定是对象 —— 代码里写 `(payload.get("data") or {})` 就是为了吃掉 `null`。

**3. WAF 拦截返回 HTML** —— 也是 HTTP 200

腾讯的 `web.ifzq.gtimg.cn` 子域在稍高频率下会返回一段跳转到 `waf.tencent.com`
的 HTML 而不是 JSON。**用不带 `web.` 前缀的 `ifzq.gtimg.cn` 就没有这个问题。**

---

## 1. 全市场股票清单

```
GET https://push2.eastmoney.com/api/qt/clist/get
```

| 参数 | 值 | 说明 |
|---|---|---|
| `pn` / `pz` | 页码 / 每页条数 | 单页上限 100 |
| `fs` | 市场过滤 | 见下表 |
| `fields` | `f12,f13,f14,f2` | 代码、市场、名称、最新价 |
| `ut` | `bd1d9ddb04089700cf9c27f6f7426281` | 固定 token |

市场过滤表达式（逗号分隔可组合）：

| 表达式 | 市场 |
|---|---|
| `m:1 t:2` | 上交所主板 |
| `m:1 t:23` | 科创板 |
| `m:0 t:6` | 深交所主板 |
| `m:0 t:80` | 创业板 |
| `m:0 t:81 s:2048` | 北交所 |
| `i:1.000300` | 指数成分股（沪深300） |
| `m:90 t:2` | 行业板块 |
| `m:90 t:3` | 概念板块 |

返回 `data.total` 是总数，`data.diff` 是当页列表。实测全 A 股约 **5547** 只。

---

## 2. K 线

```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get
```

| 参数 | 值 | 说明 |
|---|---|---|
| `secid` | `{市场}.{代码}` | `1.600000` 上海，`0.000001` 深圳，`90.BK0465` 板块 |
| `klt` | 周期 | 见下表 |
| `fqt` | 复权 | 0 不复权 / **1 前复权** / 2 后复权 |
| `lmt` | 返回根数 | 配合 `end=20500101` 使用 |
| `fields2` | 字段 | 决定返回字符串的字段顺序 |
| `ut` | `fa5fd1943c7b386f172d6893dbfba10b` | 固定 token |

周期：

| klt | 周期 | klt | 周期 |
|---|---|---|---|
| 1 | 1 分钟 | 60 | 60 分钟 |
| 5 | 5 分钟 | 101 | 日线 |
| 15 | 15 分钟 | 102 | 周线 |
| 30 | 30 分钟 | 103 | 月线 |

**没有 120 分钟。** 需求要，所以用 60 分钟合成（见 `radar/kline.py` 的 `resample_120m`）。

### 返回格式

`data.klines` 是逗号分隔的字符串数组，字段顺序由 `fields2` 决定：

```text
fields2 = f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61
          日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
```

⚠️ 顺序是 **开、收、高、低**，不是常见的 OHLC（开、高、低、收）。
搞错的话 KDJ 会算出"看起来差不多但就是不对"的值。

实例：

```text
1999-11-10,-1.48,-1.75,-1.44,-1.86,1740850,4859102000.00,-9.46,60.59,2.69,54.40
```

前复权下老数据出现负价是正常的（累计复权因子很大），只影响历史，不影响近期指标。

### 时间戳含义

分钟线的时间戳是**收盘时刻**：60 分钟线为 10:30 / 11:30 / 14:00 / 15:00。

---

## 3. 板块资金流历史

```
GET https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
```

| 参数 | 值 |
|---|---|
| `secid` | `90.BK0465`（板块代码来自 clist 接口的 `f12`） |
| `klt` | 101（日线） |
| `lmt` | 返回天数 |
| `fields2` | `f51,f52,f53,f54,f55,f56,...` |
| `ut` | `b2884a393a59ad64002292a3e90d46a5` |

返回字段顺序：

```text
f51 日期
f52 主力净流入-净额     ← 我们要的
f53 小单净流入-净额
f54 中单净流入-净额
f55 大单净流入-净额
f56 超大单净流入-净额
```

单位是**元**。

---

## 4. 资讯检索

```
GET https://search-api-web.eastmoney.com/search/jsonp
```

返回的是 JSONP（`cb({...})`），需要剥掉外层回调。

`param` 是 URL 编码的 JSON：

```json
{
  "keyword": "浦发银行",
  "type": ["cmsArticleWebOld"],
  "param": {
    "cmsArticleWebOld": {
      "sort": "time", "pageIndex": 1, "pageSize": 100
    }
  }
}
```

返回 `result.cmsArticleWebOld` 是文章列表，每条含 `date` 字段。

**这个接口比行情接口脆弱得多**，并发要压到行情接口的一半。

---

## 5. 腾讯 K 线（当前主源）

⚠️ **必须用不带 `web.` 前缀的域名**：`web.ifzq.gtimg.cn` 上分钟线路径是 301 跳转，
而且在稍高频率下会触发 WAF 返回 HTML。

### 日 / 周 / 月线（前复权）

```
GET https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,120,qfq
                                                         │        │       │   └ 复权 qfq/hfq/留空
                                                         │        │       └ 根数
                                                         │        └ day / week / month
                                                         └ sh=上海 sz=深圳
```

返回体的 key **带 `qfq` 前缀**：`qfqday` / `qfqweek` / `qfqmonth`。

```json
{"code":0,"data":{"sh600000":{"qfqday":[
  ["2026-08-07","9.260","9.210","9.290","9.140","565457.000"]]}}}
   日期        开盘    收盘    最高    最低    成交量(手)
```

字段顺序和东财**完全一致**（开、收、高、低），解析逻辑可以复用。

### 分钟线

```
GET https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600000,m60,,120
```

周期写 `m60` / `m30` / `m15`。返回格式和日线**不同**：

```json
["202608071500","9.18","9.21","9.27","9.17","139162.00",{},"4.18"]
  时间(紧凑)     开     收     高     低    成交量(手)
```

时间是 `YYYYMMDDHHMM` 紧凑格式，要转成标准时间戳才能给 `resample_120m` 用。

实测每根 60 分钟量之和等于当日日线量（171496+70893+183907+139162 = 565458
vs 日线 565457），说明和日线是同一套口径。

---

## 6. 新浪 K 线（兜底）

```
GET https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
    ?symbol=sh600000&scale=60&ma=no&datalen=120
```

`scale` = 15 / 30 / 60（分钟）或 240（日线）。返回是对象数组：

```json
[{"day":"2026-08-07 15:00:00","open":"9.180","high":"9.270",
  "low":"9.170","close":"9.210","volume":"13916181"}]
```

⚠️ **两个和其他源不一样的地方**：

1. 字段顺序是 `open, high, low, close`（标准 OHLC），不是东财/腾讯的开收高低
2. **`volume` 单位是「股」，东财和腾讯是「手」，差 100 倍**

`radar/providers.py` 里统一 ÷100 折算成手。不折算的话 CV 本身不受影响（无量纲），
但「成交量标准差」列会差 100 倍，而且两个源混用时同一只股票前后不一致。

**新浪扛不住全市场负载**：实测 5200 只的压力下 28 秒就被限流打死，
之后 20 次串行请求全部失败。所以只做兜底，不做主源。

---

## 网络环境注意事项

本机如果配置了代理（如 Clash，`HTTP_PROXY=http://127.0.0.1:7890`），
Python 的 requests 默认会读环境变量走代理。**代理挂掉时会全线失败。**

排查：

```bash
# 1. 先用 curl 确认接口通不通（用当前的主源）
curl -s -o /dev/null -w "%{http_code}\n" \
  'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,3,qfq'

# 2. 000 说明连不上，检查代理；200 说明网络没问题，是 Python 侧的事

# 3. 临时绕过代理
RADAR_USE_PROXY=0 python3 run.py --task 1 --limit 10
```

对应代码：`radar/config.py` 的 `use_system_proxy`，
读环境变量 `RADAR_USE_PROXY`，设为 `0` 则 `session.trust_env = False`。

---

## 使用纪律

- 令牌桶默认 30 QPS，并发 12。**不要为了跑得快无限调高** ——
  被限流之后重试反而更慢，而且对公开服务不礼貌。
- 每次运行都统计失败数。突然从几十涨到几百，就是接口变了或被限流。
- 改代码之前先用 curl 手工验证接口，确认是接口问题还是代码问题。
- 缓存 TTL 30 分钟，崩溃后重跑可复用，避免重复请求。
