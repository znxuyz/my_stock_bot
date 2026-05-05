"""
db 套件入口：把所有公開 API 重新匯出，外面只要 `import db` 即可。
原本的 db.py 已拆成多個檔案，但對外介面完全相同。
"""
from db.conn import get_conn, is_available
from db.schema import init_db
from db.guilds import (
    set_guild_webhook,
    remove_guild,
    get_all_webhooks,
    get_guild_webhook,
)
from db.runs import (
    record_run_start,
    record_run_end,
    get_run_state,
    can_run_today,
)
from db.screens import (
    save_screen_records,
    get_records_needing_t1_check,
    get_total_screened,
)
from db.settle import (
    next_friday,
    calc_position_pct,
    determine_t1_fill,
    fill_t1_entry,
    get_pending_settle,
    update_settle,
)
from db.stats import (
    get_cumulative_stats,
    get_latest_screen_date,
    get_screens_by_date,
    get_history_records,
    get_aggregated_stats,
    get_aggregated_summary,
    get_settlement_timeline,
)
from db.holdings import (
    get_holdings,
    add_holding,
    remove_holding,
    get_pnl,
    get_leaderboard,
)
from db.challenges import (
    get_challenge,
    add_challenge,
    get_all_challenges,
    clear_challenges,
)

# 為了某些舊呼叫者，保留 SCHEMA_VERSION 與 RUN_TIMEOUT_SEC 常數
import config as _config
SCHEMA_VERSION  = _config.SCHEMA_VERSION
RUN_TIMEOUT_SEC = _config.RUN_TIMEOUT_SEC

__all__ = [
    'get_conn', 'is_available', 'init_db',
    'set_guild_webhook', 'remove_guild', 'get_all_webhooks', 'get_guild_webhook',
    'record_run_start', 'record_run_end', 'get_run_state', 'can_run_today',
    'save_screen_records', 'get_records_needing_t1_check', 'get_total_screened',
    'next_friday', 'calc_position_pct',
    'determine_t1_fill', 'fill_t1_entry', 'get_pending_settle', 'update_settle',
    'get_cumulative_stats', 'get_latest_screen_date', 'get_screens_by_date',
    'get_history_records', 'get_aggregated_stats', 'get_aggregated_summary',
    'get_settlement_timeline',
    'get_holdings', 'add_holding', 'remove_holding', 'get_pnl', 'get_leaderboard',
    'get_challenge', 'add_challenge', 'get_all_challenges', 'clear_challenges',
    'SCHEMA_VERSION', 'RUN_TIMEOUT_SEC',
]
