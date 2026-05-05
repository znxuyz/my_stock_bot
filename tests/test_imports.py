"""所有公開模組 import smoke test：保證 PR 不會打破基本載入。"""


def test_top_level_modules_import():
    import importlib
    for name in ('bot', 'stock_bot', 'analysis', 'matching', 'config', 'db',
                 'discord_bot', 'web_export', 'logging_setup'):
        m = importlib.import_module(name)
        assert m is not None, f'{name} import returned None'


def test_db_package_exports():
    import db
    expected = [
        'get_conn', 'is_available', 'init_db',
        'set_guild_webhook', 'get_all_webhooks',
        'record_run_start', 'record_run_end', 'can_run_today',
        'save_screen_records', 'determine_t1_fill', 'fill_t1_entry',
        'get_pending_settle', 'update_settle',
        'get_cumulative_stats', 'get_aggregated_stats',
        'get_holdings', 'add_holding', 'remove_holding',
        'get_challenge', 'add_challenge', 'clear_challenges',
    ]
    for name in expected:
        assert hasattr(db, name), f'db package missing {name}'


def test_discord_bot_exports():
    import discord_bot
    from discord_bot.handlers import get_last_run, update_last_run
    assert callable(discord_bot.InteractionHandler)
    assert callable(discord_bot.register_commands)
    assert callable(discord_bot.scheduler)
    assert isinstance(discord_bot.LAST_RUN, dict)
    assert callable(update_last_run)
    assert callable(get_last_run)


def test_stock_bot_shim_exports():
    """向下相容 shim：原本的呼叫者要還能用。"""
    import stock_bot
    expected = [
        'run_analysis', 'safe_get', 'safe_read_csv',
        'fetch_stock_day_fast', '_fetch_kbars_with_open',
        'fetch_t86_cached', 'parse_t86',
        'calc_macd', 'calc_score', 'calc_volume_ratio',
        'check_ema_bull', 'count_consecutive_limit_ups',
    ]
    for name in expected:
        assert hasattr(stock_bot, name), f'stock_bot shim missing {name}'
