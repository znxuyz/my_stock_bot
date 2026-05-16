"""法人前哨累積偵測（Stalker）。

v6.2：對候選股檢查近 N 日法人連續買進的累積特徵，這是純策略條件，
不依賴 DB，純函式測試友善。
"""
import config


def detect_stalker_setup(df_hist, inst_history):
    """
    判斷是否為 Stalker setup。

    參數：
      df_hist        近期日 K 棒 DataFrame，至少 N+1 筆。
      inst_history   list[(date, net_int)] 由舊到新，至少 N 筆。net 為外資+投信合計。

    回傳 dict：
      is_stalker        是否通過
      buy_days          N 日內法人淨買日數
      cum_net           N 日累積淨買股數
      cum_change_pct    N 日累積漲幅（%）
      price_range_pct   N 日 high/low 振幅（%）
      reason            未通過時的簡述（'insufficient_data' / 'failed_conditions'）
    """
    N = config.STALKER_DAYS
    if df_hist is None or len(df_hist) < N + 1 or inst_history is None or len(inst_history) < N:
        return {
            'is_stalker': False,
            'reason': 'insufficient_data',
            'buy_days': 0,
            'cum_net': 0,
            'cum_change_pct': None,
            'price_range_pct': None,
        }

    recent = df_hist.tail(N)
    closes = recent['close'].astype(float)
    base = float(closes.iloc[0])
    cum_change = (float(closes.iloc[-1]) / base - 1) * 100 if base > 0 else 0.0

    highs = recent['high'].astype(float)
    lows  = recent['low'].astype(float)
    low_min  = float(lows.min())
    high_max = float(highs.max())
    price_range = (high_max / low_min - 1) * 100 if low_min > 0 else float('inf')

    nets = [int(n) for _, n in inst_history[-N:]]
    buy_days = sum(1 for n in nets if n > 0)
    cum_net = sum(nets)

    is_stalker = (
        buy_days >= config.STALKER_MIN_BUY_DAYS and
        cum_net > 0 and
        config.STALKER_MIN_CUM_CHANGE <= cum_change <= config.STALKER_MAX_CUM_CHANGE and
        price_range < config.STALKER_MAX_PRICE_RANGE
    )

    return {
        'is_stalker': is_stalker,
        'reason': '' if is_stalker else 'failed_conditions',
        'buy_days': buy_days,
        'cum_net': cum_net,
        'cum_change_pct': round(cum_change, 2),
        'price_range_pct': round(price_range, 2),
    }
