"""
背景排程：
  - 平日 17:00 觸發盤後分析（用 DB 狀態避免重啟重複觸發）
  - 週五 18:00 兩次結算
  - 週五 21:00 挑戰結算
"""
import threading
import time
from datetime import timedelta

import config
import db
from time_utils import tw_now

from discord_bot.handlers import LAST_RUN
from discord_bot.challenge_commands import settle_challenge
from discord_bot.settle import settle_weekly


def _run_analysis_with_status(attempt=0, mode='auto'):
    """包一層：用 DB 狀態管理避免 Bot 重啟造成的重複觸發。"""
    from analysis import run_analysis
    today = tw_now().date()
    try:
        db.record_run_start(today, attempt)
    except Exception as e:
        print(f'[排程] record_run_start 失敗：{e}')

    try:
        status = run_analysis(attempt=attempt, run_mode=mode) or 'fail'
    except Exception as e:
        status = 'fail'
        print(f'[排程] run_analysis 例外：{e}')

    try:
        db.record_run_end(today, status)
    except Exception as e:
        print(f'[排程] record_run_end 失敗：{e}')

    LAST_RUN.update({
        'time': tw_now(), 'date': today, 'mode': mode,
        'status': status, 'attempt': attempt,
    })
    print(f'[排程] run_analysis 結果：status={status} attempt={attempt}')


def scheduler():
    """
    平日 17:00 → 盤後分析（DB 狀態管理）
    週五 18:00 → 兩次結算
    週五 21:00 → 挑戰結算
    """
    boot_time         = time.time()
    last_check_minute = None
    fired             = set()

    while True:
        if time.time() - boot_time < config.SCHEDULER_STARTUP_BUFFER_SEC:
            time.sleep(10)
            continue

        now = tw_now()
        h, wd, mn = now.hour, now.weekday(), now.minute

        # 平日盤後分析
        if wd < 5 and (h, mn) in config.ANALYSIS_TRIGGER_TIMES:
            check_key = (now.date(), h, mn)
            if check_key != last_check_minute:
                last_check_minute = check_key
                today = now.date()
                try:
                    can_run, attempt, reason = db.can_run_today(today, now)
                    print(f'[排程] {h:02d}:{mn:02d} can_run={can_run} attempt={attempt} reason={reason}')
                    if can_run:
                        threading.Thread(
                            target=_run_analysis_with_status,
                            kwargs={'attempt': attempt, 'mode': 'auto'},
                            daemon=True,
                        ).start()
                except Exception as e:
                    print(f'[排程] 檢查狀態失敗：{e}')
                    # DB 出問題的 fallback：第一次觸發直接跑
                    if (h, mn) == (17, 0):
                        threading.Thread(
                            target=_run_analysis_with_status,
                            kwargs={'attempt': 0, 'mode': 'auto'},
                            daemon=True,
                        ).start()

        # 週五 18:00 結算
        if wd == 4 and h == 18 and mn == 0:
            k = (now.date(), 'weekly_settle')
            if k not in fired:
                fired.add(k)

                def _do_settle(_d=now.date()):
                    try:
                        guilds = db.get_all_webhooks()
                    except Exception as e:
                        print(f'[結算] 取 webhooks 失敗：{e}')
                        guilds = []
                    for gw in guilds:
                        settle_weekly(_d, 1, gw['guild_id'])
                        time.sleep(1)
                        settle_weekly(_d, 2, gw['guild_id'])
                        time.sleep(1)
                    try:
                        import web_export as _we
                        _we.export_dashboard()
                    except Exception as e:
                        print(f'[Web] 結算後 Dashboard 匯出失敗：{e}')

                threading.Thread(target=_do_settle, daemon=True).start()

        # 週五 21:00 挑戰結算
        if wd == 4 and h == 21 and mn == 0:
            k = (now.date(), 'challenge_settle')
            if k not in fired:
                fired.add(k)
                threading.Thread(target=settle_challenge, daemon=True).start()

        # 每天清掉 7 天前的 fired key
        if h == 0 and mn == 1:
            cutoff = now.date() - timedelta(days=7)
            fired  = {k for k in fired if k[0] >= cutoff}

        time.sleep(60)
