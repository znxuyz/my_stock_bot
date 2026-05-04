"""
TWSE MI_MARGN 融資融券餘額：今日 vs. 5 個交易日前增幅。
"""
import re as _re
import time
from datetime import datetime, timedelta

import config
from twse_http import safe_get


_MI_MARGN_URL = 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN'


def _get_margin_value(sid, date_str):
    """抓 sid 在 date_str 的融資餘額（股）。找不到回 None。"""
    r = safe_get(
        _MI_MARGN_URL,
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=12, retries=1, wait=3,
    )
    if not r or '查詢無資料' in r.text:
        return None
    for line in r.text.splitlines():
        parts = [v.strip().strip('"').replace(',', '') for v in line.split(',')]
        if len(parts) < 6:
            continue
        # MI_MARGN 第一欄常含 = / " 等雜訊，洗乾淨後比對 sid
        raw = _re.sub(r'[="\s\t ]', '', parts[0]).strip()
        if raw != sid:
            continue
        try:
            return float(parts[5]) * 1000  # 張 → 股
        except ValueError:
            return None
    return None


def fetch_margin_change(sid, date_str):
    """
    回傳 {'score': int, 'label': str}。資料缺一不可。
    +30% 以上：扣 8 分；+15% 以上：扣 4 分；負成長加 3 分。
    """
    base = datetime.strptime(date_str, '%Y%m%d').date()
    prev_days = []
    d = base - timedelta(days=1)
    while len(prev_days) < 5:
        if d.weekday() < 5:
            prev_days.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    date_5d = prev_days[-1]

    margin_today = _get_margin_value(sid, date_str)
    time.sleep(config.TWSE_CALL_INTERVAL_SEC)
    margin_5d    = _get_margin_value(sid, date_5d)

    if margin_today is None or margin_5d is None or margin_5d == 0:
        return {'score': 0, 'label': ''}

    pct = (margin_today - margin_5d) / margin_5d * 100
    if pct >= 30:
        return {'score': -8, 'label': f'❌ 融資5日暴增 +{pct:.1f}%'}
    if pct >= 15:
        return {'score': -4, 'label': f'⚠️ 融資5日增加 +{pct:.1f}%'}
    if pct >= 0:
        return {'score': 0,  'label': f'🔄 融資5日 +{pct:.1f}%'}
    return {'score': 3, 'label': f'✅ 融資5日 {pct:.1f}%，籌碼健康'}
