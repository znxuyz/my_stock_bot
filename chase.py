"""
連續漲停 + 強勢追漲門檻判定。

v6.2 起 CHASE 路徑整個移除：check_strong_chase 標 @deprecated，永遠回 reject。
count_consecutive_limit_ups 保留——Stalker filter 第 12 道（10 日內漲停次數 = 0）
仍會用到，且 /stock 個股查詢顯示有用。
"""
import warnings


def count_consecutive_limit_ups(df, threshold=9.5):
    """
    從最後一筆往回計算連續漲停天數（含當日）。
    台股漲停 +10%，實務上 ≥ +9.5% 視為漲停（含計算誤差）。
    """
    if len(df) < 2:
        return 0
    closes = df['close'].astype(float).values
    cnt = 0
    for i in range(len(closes) - 1, 0, -1):
        prev = closes[i - 1]
        if prev == 0:
            break
        chg = (closes[i] - prev) / prev * 100
        if chg >= threshold:
            cnt += 1
        else:
            break
    return cnt


def check_strong_chase(entry, macd_info, market_score):  # noqa: ARG001
    """v6.2 已移除 CHASE 路徑：永遠回 reject。

    保留簽名給歷史 import 鏈不斷裂；新程式不要呼叫。
    """
    warnings.warn(
        'chase.check_strong_chase is deprecated since v6.2 (CHASE path removed).',
        DeprecationWarning,
        stacklevel=2,
    )
    return {
        'passed': 0,
        'checks': [],
        'reasons': ['v6.2 已移除 CHASE 路徑'],
    }
