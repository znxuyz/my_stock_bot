"""
川投顧量化系統 ── 進入點。
所有實作已拆到對應模組：
  - config.py / time_utils.py / format_utils.py
  - twse_*.py（HTTP / K 棒 / T86 / 大盤 / 融資）
  - indicators.py / advanced_indicators.py / scoring.py / chase.py / topflow.py
  - matching.py / analysis.py
  - db/ 套件
  - discord_bot/ 套件
  - web_export.py
"""
import logging
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import config
from logging_setup import setup_logging

setup_logging()

import db
from discord_bot import InteractionHandler, register_commands, scheduler

logger = logging.getLogger(__name__)


def _check_required_env():
    missing = [
        k for k, v in (
            ('DISCORD_BOT_TOKEN',  config.DISCORD_BOT_TOKEN),
            ('DISCORD_PUBLIC_KEY', config.DISCORD_PUBLIC_KEY),
            ('DISCORD_APP_ID',     config.DISCORD_APP_ID),
        ) if not v
    ]
    if missing:
        logger.error('[錯誤] 缺少環境變數：%s', ', '.join(missing))
        sys.exit(1)


def _startup_export():
    """Bot 重啟時讓 dashboard 立刻同步歷史資料；內容無變動時 GitHub 端會跳過 commit。"""
    time.sleep(5)  # 等資料庫連線穩定
    try:
        import web_export as _we
        _we.export_dashboard()
    except Exception as e:
        logger.error('[Web] 啟動時 Dashboard 匯出失敗：%s', e)


def main():
    _check_required_env()

    if config.DATABASE_URL:
        try:
            db.init_db()
        except Exception as e:
            logger.error('[DB] 初始化失敗：%s', e)
    else:
        logger.warning('[DB] DATABASE_URL 未設定，跳過資料庫初始化')

    register_commands()
    threading.Thread(target=scheduler,        daemon=True).start()
    threading.Thread(target=_startup_export, daemon=True).start()

    logger.info('[Bot] 已啟動，監聽 port %d', config.PORT)
    ThreadingHTTPServer(('0.0.0.0', config.PORT), InteractionHandler).serve_forever()


if __name__ == '__main__':
    main()
