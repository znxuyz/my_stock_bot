"""
進場區間：v6.2 全面改純市價（T+1 開盤即成交，一字漲跌停 missed）。

v5 的限價區間 / 強勢追漲區間 / WATCH 不撮合等三種模式都已移除。
函式簽名保留向下相容，永遠回 (None, None)，呼叫端據此走純市價路徑。
"""


def calc_entry_zone(close, chase_mode=None, grade=None, precision=2):  # noqa: ARG001
    """v6.2：永遠回 (None, None)，所有舊參數忽略。"""
    return None, None
