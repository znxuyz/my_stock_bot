"""
顯示格式化小工具：股數、星等、Discord interaction option 取值、v6.2 卡片 block。
"""
from scoring import status_to_emoji, status_to_position_pct


def fmt_share(n):
    """股數顯示：1234567 → '1.2M'，12345 → '12K'，否則純數字"""
    n = int(n)
    if abs(n) >= 1_000_000:
        return f'{n / 1_000_000:.1f}M'
    if abs(n) >= 1_000:
        return f'{n / 1_000:.0f}K'
    return str(n)


def fmt_share_signed(n):
    """股數顯示，含正負號（給法人買賣超用）"""
    sign = '+' if n >= 0 else ''
    return f'{sign}{int(n):,}'


def star_str(n):
    """0~5 星顯示：'★★★☆☆（3/5）'"""
    n = max(0, min(5, round(float(n))))
    return '★' * n + '☆' * (5 - n) + f'（{n}/5）'


def get_opt(options, name, default=None):
    """從 Discord interaction options 取出特定名稱的值"""
    return next((o['value'] for o in options if o['name'] == name), default)


# ─────────── v6.2 卡片 ───────────

def fmt_status_block(entry):
    """v6.2 Discord 個股卡片。

    entry 期待欄位：
      sid / name / status / total_score / flow_score / trend_score / heat_score
      acc_buy_days / change / vol_ratio / bias_pct（10MA 乖離）
    """
    emoji = status_to_emoji(entry['status'])
    sid   = entry.get('sid', '')
    name  = entry.get('name', '')
    total = entry.get('total_score', 0)
    flow  = entry.get('flow_score',  0)
    trend = entry.get('trend_score', 0)
    heat  = entry.get('heat_score',  0)
    acc_days = entry.get('acc_buy_days', 0)
    change   = entry.get('change', 0.0)
    vol_ratio = entry.get('vol_ratio', 0.0)
    bias_pct  = (entry.get('bias') or {}).get('bias_pct')
    pos_pct   = status_to_position_pct(entry['status'])

    sign = '+' if change >= 0 else ''
    bias_str = f"{'+' if bias_pct and bias_pct >= 0 else ''}{bias_pct:.2f}%" \
        if bias_pct is not None else 'n/a'

    return (
        f"{emoji} **{entry['status']}** ({total}/10) {sid} {name}\n"
        f"分數明細：Flow {flow}/5 · Trend {trend}/3 · Heat {heat}/2\n"
        f"累積：{acc_days}/5 天買、漲幅 {sign}{change:.2f}%、"
        f"量比 {vol_ratio:.1f}x、乖離(10MA) {bias_str}\n"
        f"進場：市價 · 倉位建議 {pos_pct}%"
    )
