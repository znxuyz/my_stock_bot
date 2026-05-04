"""
顯示格式化小工具：股數、星等、Discord interaction option 取值。
"""


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
