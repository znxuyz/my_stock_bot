"""v6.2 Stalker 全 12 道過濾 regression（_enrich_candidate）。

每個測試構造一筆會在某一道被擋下的 candidate，確認 _enrich_candidate 回 None。
最後一個測試構造一筆全過的 candidate，確認回 dict 且分數合理。
"""
from datetime import date, timedelta

import pandas as pd

from analysis import _enrich_candidate


TARGET_DATE = date(2025, 3, 31)


def _make_df_hist(closes, highs=None, lows=None, volumes=None):
    """構造一個有 80 天歷史的 K 棒 DataFrame（足夠跑 EMA60、60 日量、MACD 等）。"""
    n = len(closes)
    return pd.DataFrame({
        'date':   [TARGET_DATE - timedelta(days=n - 1 - i) for i in range(n)],
        'close':  [float(c) for c in closes],
        'high':   highs   if highs   is not None else [c + 0.3 for c in closes],
        'low':    lows    if lows    is not None else [c - 0.3 for c in closes],
        'volume': volumes if volumes is not None else [10_000] * n,
    })


def _make_inst(nets):
    """5 天法人淨買，由舊到新。"""
    return [(TARGET_DATE - timedelta(days=4 - i), int(n)) for i, n in enumerate(nets)]


def _good_setup():
    """構造一筆「應該通過所有 12 道過濾」的 candidate。

    特徵：80 天平穩緩漲，今日量比 1.3x，60 日均量內，乖離均線都低，
    法人 5/5 連續買、累積 ≥ 500K、漲幅 ~+1%。
    """
    # 80 天 K 棒：前 75 天緩慢從 95 漲到 100，後 5 天在 100~101 之間盤整
    closes = [95 + i * 0.067 for i in range(75)]  # 95.0 ~ 99.96
    closes += [100.0, 100.5, 100.0, 100.5, 100.2]  # 最後 5 天盤整
    # 確保最新一筆比前一筆高 +1.0%（前 = 100.2, 最新 = ?）
    # 我們把最新一筆設為 101.0（漲幅 +0.8% from 100.2）→ 加進去
    closes.append(101.0)

    # 量：前 75 天 10_000、後 6 天 13_000（量比 ~1.3x、量/60 ~1.3）
    volumes = [10_000] * 75 + [13_000] * 6

    # high/low 振幅控制在 5% 內：最後 5 天的 high/low 控制
    n = len(closes)
    highs = [c + 0.3 for c in closes]
    lows  = [c - 0.3 for c in closes]
    # 保證最後 5 天振幅小：max high 101.3, min low 99.7 → 1.6%
    df = pd.DataFrame({
        'date':   [TARGET_DATE - timedelta(days=n - 1 - i) for i in range(n)],
        'close':  closes,
        'high':   highs,
        'low':    lows,
        'volume': volumes,
    })
    # 法人 5/5 連續買 + 累積 ≥ 500K
    inst = _make_inst([100_000, 110_000, 120_000, 130_000, 140_000])
    # candidate
    entry = {
        'sid': '2330', 'name': '測試股',
        'price': 101.0, 'change': round((101.0 - 100.2) / 100.2 * 100, 2),
        'foreign': 1500, 'trust': 0, 'total': 1500,
    }
    return entry, df, inst


# ─────────── 各道過濾 reject ───────────

def test_filter_vol_ratio_too_high():
    """量比 > 1.8 → 擋下。"""
    entry, df, inst = _good_setup()
    # 改最後一筆 volume 讓量比 > 1.8（前 5 天均 13_000，所以最後一筆 = 30_000 → 量比 ~2.3）
    df.loc[df.index[-1], 'volume'] = 30_000
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_vol_ratio_too_low():
    """量比 < 1.0 → 擋下。"""
    entry, df, inst = _good_setup()
    # 最後一筆 = 5000 → 比前 5 日均 13_000 還低
    df.loc[df.index[-1], 'volume'] = 5_000
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_vol_vs_60d_too_high():
    """量 / 60 日均量 ≥ 2.0 → 擋下。"""
    entry, df, inst = _good_setup()
    # 前 75 天 vol=10_000、後 5 天 vol=13_000；最後一筆改 25_000 → 60 日均 ~10500，比 2.38x
    df.loc[df.index[-1], 'volume'] = 25_000
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_amount_too_low():
    """日成交金額 < 5000 萬 → 擋下。"""
    entry, df, inst = _good_setup()
    # 把整體收盤價拉低到 3 元、最後價也 3 元 → close × volume × 1000 = 3 × 13000 × 1000 = 39M < 50M
    df['close'] = 3.0
    df.loc[df.index[-1], 'volume'] = 13_000
    entry['price'] = 3.0
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_cum_5d_too_high():
    """5 日累積 > +3% → 擋下。"""
    entry, df, inst = _good_setup()
    # 把第 -6 筆設成 90.0、最後一筆 100 → +11.1%
    df.loc[df.index[-6], 'close'] = 90.0
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_price_range_too_wide():
    """5 日振幅 ≥ 5% → 擋下。"""
    entry, df, inst = _good_setup()
    # 最後 5 天有一筆 high 飆到 110，low 跌到 95 → 振幅 > 15%
    last5_idx = df.index[-5:]
    df.loc[last5_idx[2], 'high'] = 110.0
    df.loc[last5_idx[3], 'low'] = 95.0
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_limit_ups_in_10d():
    """10 日內有漲停 → 擋下。"""
    entry, df, inst = _good_setup()
    # 在 -8 筆製造一日漲停（前一日 close × 1.10）
    df.loc[df.index[-8], 'close'] = float(df['close'].iloc[-9]) * 1.10
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_ema_not_bullish():
    """EMA 20 ≤ EMA 60 → 擋下。"""
    entry, df, inst = _good_setup()
    # 把前 60 天設成「高 → 低」走勢（EMA60 高、EMA20 低）
    closes = [120 - i * 0.3 for i in range(75)] + list(df['close'].iloc[-6:])
    df['close'] = closes
    df['high'] = [c + 0.3 for c in closes]
    df['low']  = [c - 0.3 for c in closes]
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_bias_10_too_high():
    """乖離 10MA > 5% → 擋下。"""
    entry, df, inst = _good_setup()
    # 把最後一天 close 改成遠高於 10MA
    df.loc[df.index[-1], 'close'] = 115.0
    entry['price'] = 115.0
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_bias_20_too_high():
    """乖離 20MA > 3% → 擋下。

    構造：MA20 ~ 100，今收 105 → bias_20 ~+5%，但 MA10 ~ 100.2 → bias_10 ~+4.8% 也可能擋下；
    所以我們調整成 MA10 也夠近、但 MA20 偏離。需要前 20 天均偏低 + 最近 10 天均接近今收。
    """
    entry, df, inst = _good_setup()
    # 把第 -20 ~ -11 筆都改 90（讓 MA20 變低），其他不動
    for i in range(-20, -10):
        df.loc[df.index[i], 'close'] = 90.0
    # 今收 ~101，MA20 因為包含這 10 筆 90 + 10 筆 ~100 ≈ 95；bias = (101-95)/95 ≈ 6.3%
    # bias_10 = MA10 用最近 10 筆 ~100，bias 約 +1%（過得了 bias_10 ≤ 5%）
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


def test_filter_acc_3_of_5_fails_stalker():
    """法人 3/5 天買 → Stalker 不通過 → 擋下。"""
    entry, df, inst = _good_setup()
    inst = _make_inst([100_000, -50_000, 100_000, -100_000, 100_000])  # 3/5
    assert _enrich_candidate(entry, df, TARGET_DATE, inst) is None


# ─────────── 完整通過 ───────────

def test_full_pass_returns_enriched_with_score():
    entry, df, inst = _good_setup()
    result = _enrich_candidate(entry, df, TARGET_DATE, inst)
    assert result is not None, '理想的 setup 應該通過所有 12 道過濾'
    # 應該有完整 v6.2 評分欄
    assert 'flow_score'  in result and 0 <= result['flow_score']  <= 5
    assert 'trend_score' in result and 0 <= result['trend_score'] <= 3
    assert 'heat_score'  in result and 0 <= result['heat_score']  <= 2
    assert 'total_score' in result and 0 <= result['total_score'] <= 10
    assert 'status' in result
    assert result['status'] in ('MOMENTUM', 'ACTIVE', 'SETUP', 'WATCH', 'NOISE')
    # acc_buy_days = 5、cum_net ≥ 500K → Flow 至少拿到前兩條 +4 分
    assert result['acc_buy_days'] == 5
    assert result['acc_cum_net'] >= 500_000
    assert result['flow_score'] >= 4
