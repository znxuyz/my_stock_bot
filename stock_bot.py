"""
向下相容 shim：原本的 stock_bot.py 已拆成多個模組，本檔只重新匯出公開 API。
任何 `import stock_bot as sb` 的舊呼叫都仍然可用。

v6.2：移除 GRADE_SS/GRADE_S/GRADE_A re-export（v5 等級制已棄用）；
calc_score / check_strong_chase 仍 re-export（標 deprecated，不刪以免 import 鏈斷裂）。
"""
from analysis import INDICATOR_GUIDE, run_analysis
from advanced_indicators import calc_advanced_indicators
from chase import check_strong_chase, count_consecutive_limit_ups
from format_utils import fmt_share_signed as fmt_share
from indicators import (
    calc_atr,
    calc_bias_and_entry,
    calc_ema,
    calc_macd,
    calc_obv,
    calc_rsi,
    calc_volume_ratio,
    check_ema_bull,
)
from matching import (
    fill_pending_t1_entries,
    get_period_kbars,
    get_t1_kbar,
)
from scoring import (
    calc_chip_concentration,
    calc_margin_score,
    calc_market_env,
    calc_score,             # deprecated
    calc_score_v62,
    classify_status,
    status_to_emoji,
    status_to_position_pct,
)
from time_utils import get_target_date, prev_months
from topflow import extract_top_flow
from twse_http import clean_sid, find_col, safe_get, safe_read_csv
from twse_kbar import build_history_fast, fetch_kbars_with_open as _fetch_kbars_with_open, fetch_stock_day_fast
from twse_margin import fetch_margin_change
from twse_market import fetch_market_foreign_history, get_market_info
from twse_t86 import fetch_t86_cached, parse_t86

import config

# 為了極少數舊呼叫者保留同名常數（GRADE_* 已棄用，不再 re-export）
WEBHOOK_URL          = config.DISCORD_WEBHOOK
DATA_READY_HOUR      = config.DATA_READY_HOUR
MIN_PRICE            = config.MIN_PRICE
MAX_CANDIDATES       = config.MAX_CANDIDATES
EMA_SHORT  = config.EMA_SHORT
EMA_MID    = config.EMA_MID
EMA_LONG1  = config.EMA_LONG1
EMA_LONG2  = config.EMA_LONG2
EMA_FALLBACK_MIN = config.EMA_FALLBACK_MIN

__all__ = [
    'run_analysis', 'INDICATOR_GUIDE',
    'safe_get', 'safe_read_csv', 'find_col', 'clean_sid',
    'fetch_stock_day_fast', '_fetch_kbars_with_open', 'build_history_fast',
    'fetch_t86_cached', 'parse_t86',
    'get_market_info', 'fetch_market_foreign_history',
    'fetch_margin_change',
    'calc_ema', 'check_ema_bull', 'calc_volume_ratio',
    'calc_rsi', 'calc_atr', 'calc_obv', 'calc_macd',
    'calc_bias_and_entry',
    'calc_advanced_indicators',
    'count_consecutive_limit_ups', 'check_strong_chase',
    'calc_market_env', 'calc_margin_score', 'calc_chip_concentration', 'calc_score',
    # v6.2
    'calc_score_v62', 'classify_status', 'status_to_emoji', 'status_to_position_pct',
    'extract_top_flow',
    'get_t1_kbar', 'get_period_kbars', 'fill_pending_t1_entries',
    'get_target_date', 'prev_months', 'fmt_share',
    'WEBHOOK_URL', 'DATA_READY_HOUR',
]


if __name__ == '__main__':
    from logging_setup import setup_logging
    setup_logging()
    run_analysis()
