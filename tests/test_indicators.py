"""指标正确性测试。不需要网络，随时可跑：

    cd 学员&& python3 -m pytest tests -q
    或   python3 tests/test_indicators.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.indicators import (coefficient_of_variation, kdj, kdj_at,  # noqa: E402
                              volume_metrics)
from radar.kline import resample_120m  # noqa: E402


def _bars(highs, lows, closes):
    return pd.DataFrame({"high": highs, "low": lows, "close": closes,
                         "open": closes, "volume": [1] * len(closes)})


def test_kdj_matches_manual_recursion():
    """用手工递推的结果校验 KDJ，确认 SMA 用的是通达信口径而不是简单均值。"""
    closes = [10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = _bars(highs, lows, closes)

    got = kdj(df, n=9, m1=3, m2=3)

    # 手工重算
    k_prev = d_prev = 50.0
    expected_k, expected_d = [], []
    for i in range(len(closes)):
        window = slice(max(0, i - 8), i + 1)
        llv, hhv = min(lows[window]), max(highs[window])
        rsv = 100.0 if hhv == llv else (closes[i] - llv) / (hhv - llv) * 100
        k_prev = (2 * k_prev + rsv) / 3
        d_prev = (2 * d_prev + k_prev) / 3
        expected_k.append(k_prev)
        expected_d.append(d_prev)

    assert got["K"].round(8).tolist() == [round(v, 8) for v in expected_k]
    assert got["D"].round(8).tolist() == [round(v, 8) for v in expected_d]
    assert got["J"].round(8).tolist() == [
        round(3 * k - 2 * d, 8) for k, d in zip(expected_k, expected_d)
    ]


def test_kdj_initial_value_is_50_not_zero():
    """第一根 K 线的 K 必须是 (2×50+RSV)/3，不是 RSV。初值错会让整列偏移。"""
    df = _bars([10.0], [10.0], [10.0])
    got = kdj(df)
    assert abs(got["K"].iloc[0] - (2 * 50 + 100) / 3) < 1e-9


def test_kdj_flat_bar_does_not_divide_by_zero():
    """一字板：最高=最低，分母为 0。必须给出 RSV=100 而不是 NaN。"""
    df = _bars([10.0] * 12, [10.0] * 12, [10.0] * 12)
    got = kdj(df)
    assert got.notna().all().all()
    assert got["K"].iloc[-1] > 99  # 连续一字涨停，K 应该收敛到 100


def test_kdj_at_returns_none_when_history_too_short():
    """数据不够时必须返回 None，不能返回 0 —— 0 会被读成"超卖"。"""
    df = _bars([10.0], [9.0], [9.5])
    assert kdj_at(df, offset=-2) == (None, None, None)
    assert kdj_at(None, offset=-1) == (None, None, None)
    k, d, j = kdj_at(df, offset=-1)
    assert k is not None


def test_kdj_offset_selects_previous_bar():
    # 用锯齿序列，避免单调上涨时 K 收敛到 100、相邻两根四舍五入后相等
    closes = [10 + (i % 7) * 2 for i in range(40)]
    df = _bars([c + 1 for c in closes], [c - 1 for c in closes], closes)
    full = kdj(df)
    k2, d2, j2 = kdj_at(df, offset=-2)
    # kdj_at 对外只保留 2 位小数（Excel 展示口径），所以容差取 0.005
    assert abs(k2 - full["K"].iloc[-2]) < 0.005
    assert abs(d2 - full["D"].iloc[-2]) < 0.005
    k1, _, _ = kdj_at(df, offset=-1)
    assert abs(k1 - full["K"].iloc[-1]) < 0.005
    assert k1 != k2  # 确认 offset 真的选了不同的 K 线


# ------------------------------------------------------------------ 成交量

def test_cv_is_std_over_mean():
    s = pd.Series([10, 12, 14, 16, 18])
    expected = s.std(ddof=1) / s.mean()
    assert abs(coefficient_of_variation(s) - round(expected, 4)) < 1e-6


def test_cv_rejects_degenerate_input():
    assert coefficient_of_variation(pd.Series([5])) is None
    assert coefficient_of_variation(pd.Series([0, 0, 0])) is None


def test_volume_range_window_excludes_recent_days():
    """过去 40~10 日必须排除最近 10 天，否则异动会污染自己的基准。"""
    # 前 30 天量恒为 100，最近 10 天量为 1000
    volumes = [100] * 40 + [1000] * 10
    df = pd.DataFrame({"volume": volumes})
    m = volume_metrics(df, cv_windows=(20, 30), cv_range=(40, 10), spike_days=10)

    # 基准窗口 vol[-40:-10] 全是 100 → CV 应为 0
    assert m["cv_range"] == 0.0
    # 放量倍数 = 1000 / 100 = 10
    assert abs(m["spike_ratio"] - 10.0) < 1e-6
    # 最近 20 天里有 10 天 100、10 天 1000 → CV 明显大于 0
    assert m["cv_20"] > 0.4


def test_volume_metrics_short_history_gives_none():
    df = pd.DataFrame({"volume": [100] * 15})
    m = volume_metrics(df)
    assert m["cv_20"] is None
    assert m["cv_range"] is None
    assert m["spike_ratio"] is None


# ------------------------------------------------------------------ 120分钟

def test_resample_120m_merges_two_60m_bars_per_half_day():
    """A股每天 4 根 60 分钟线 → 2 根 120 分钟线。"""
    rows = []
    for day in ("2026-08-05", "2026-08-06"):
        for i, t in enumerate(("10:30", "11:30", "14:00", "15:00")):
            rows.append({
                "date": f"{day} {t}", "open": 10 + i, "close": 10.5 + i,
                "high": 11 + i, "low": 9 + i, "volume": 100 * (i + 1),
                "amount": 0.0, "amplitude": 0.0, "pct_chg": 0.0,
                "chg": 0.0, "turnover": 0.0,
            })
    out = resample_120m(pd.DataFrame(rows))

    assert len(out) == 4  # 2 天 × 2 根
    first = out.iloc[0]
    assert first["date"] == "2026-08-05 11:30"   # 时间戳取后一根
    assert first["open"] == 10                    # 开盘取前一根
    assert first["close"] == 11.5                 # 收盘取后一根
    assert first["high"] == 12                    # 最高取两根最大
    assert first["low"] == 9                      # 最低取两根最小
    assert first["volume"] == 300                 # 成交量相加


def test_resample_120m_keeps_unfinished_bar():
    """盘中运行时最后半根未走完，必须保留 —— 那正是"本周期"。"""
    rows = [{"date": f"2026-08-06 {t}", "open": 10, "close": 10, "high": 10,
             "low": 10, "volume": 100, "amount": 0.0, "amplitude": 0.0,
             "pct_chg": 0.0, "chg": 0.0, "turnover": 0.0}
            for t in ("10:30", "11:30", "14:00")]
    out = resample_120m(pd.DataFrame(rows))
    assert len(out) == 2
    assert out.iloc[-1]["volume"] == 100  # 下午只走了一根


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'全部通过' if failures == 0 else str(failures) + ' 个失败'}")
    sys.exit(1 if failures else 0)
