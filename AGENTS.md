# A股行情雷达 · Agent 工作协议

## 项目性质

A 股全市场行情雷达，一套要长期运行的自动化工具。**代码的可读性和注释质量是交付物的一部分。**

## 先读什么

1. `README.md` —— 项目定位和快速开始
2. `docs/methodology.md` —— **指标口径的唯一权威**，代码和它不一致时改代码
3. `docs/data-source.md` —— 各数据源接口细节、字段顺序、已知故障形态
4. `operations/next-actions.md` —— 当前状态和待办

## 硬性约束

- **不接任何交易接口。** 这是项目边界，不是技术限制。
- **不做涨跌预测。** 所有指标都是对已发生事实的统计描述。
- **不给投资建议。** 产出是候选池，不是决策。
- 数据源只用东方财富公开接口，**不要把请求频率调到远超正常使用**。

## 改代码的规矩

| 改什么 | 必须做什么 |
|---|---|
| 指标算法 | 先改 `docs/methodology.md`，再改代码，补测试，跑 `tests/test_indicators.py` |
| Excel 表头 | 对照 需求 Excel 模板，表头就是验收标准 |
| 任何代码 | 改完跑 `python3 run.py --task 1 --limit 30` 验证，不要直接跑全市场 |
| 口径 | 同步更新每个 xlsx 的「口径说明」sheet |

**11 个单元测试必须全绿才算改完。**

```bash
python3 tests/test_indicators.py
```

## 三条不能违反的数据原则

1. **数据不足时返回 None，不返回 0。**
   J 值填 0 会被读成"超卖"，这是会导致真实亏损的错误。

2. **不用更短的窗口凑数。**
   样本从 30 降到 5，CV 的方差会大到没有意义，但表格里看起来"正常" ——
   这种静默失真比缺数危险得多。

3. **口径必须写进产出物。**
   Excel 会被转发，文档不会跟着走。每个 xlsx 都要有「口径说明」页。

## 环境

- Python：系统 `/usr/bin/python3`（3.9），已有 pandas / numpy / openpyxl / requests
- 代码必须兼容 3.9：不要用 `X | Y` 类型标注（除非有 `from __future__ import annotations`）
- 本机有代理 `HTTP_PROXY=http://127.0.0.1:7890`，代理挂掉会导致全线请求失败
- 绕过代理：`RADAR_USE_PROXY=0`

## 常用命令

```bash
cd "/path/to/a-share-radar"

python3 tests/test_indicators.py                    # 指标测试（无需网络）
python3 run.py --task 1 --limit 30                  # 链路自检
python3 run.py --task all                           # 全市场
python3 -m radar.trading_calendar                   # 交易日判断
./scripts/install_launchd.sh install|status|uninstall
./scripts/run_radar.sh close                        # 手动跑一次批次
```

## 目录边界

| 目录 | 可以改 | 说明 |
|---|---|---|
| `radar/` `run.py` `scripts/` `tests/` | ✅ | 代码 |
| `docs/` `automation/` `guide/` | ✅ | 文档与手册 |
| `operations/` | ✅ | 运行记录，改动后更新 |
| `data/output/` | ❌ | 生成物，改动会被下次运行覆盖 |
| `data/cache/` `data/logs/` | ❌ | 临时文件 |
| `需求原件` 需求 Excel 模板 | ❌ | **需求原件，只读** |

## 手册的写法

`guide/` 里的内容是给  使用的，不是 API 文档。要求：

- 每个概念都要说清"为什么"，不只是"是什么"
- 坑要写出来，而且要写清楚踩了会怎样
- 能演示的就给可复制的命令
- 结论性的话要具体（"倍数 > 3 且 CV < 0.6"），不要写"要谨慎"
