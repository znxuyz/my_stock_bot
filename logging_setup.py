"""
全域 logging 設定。bot.py 進入點呼叫 setup_logging() 一次即可，
其他模組只需 `import logging; logger = logging.getLogger(__name__)`。

環境變數：
  LOG_LEVEL   ── 預設 INFO（可改 DEBUG / WARNING）
  LOG_FORMAT  ── 自訂格式字串（少用，預設帶時間戳 + level + 模組）
"""
import logging
import os
import sys


_DEFAULT_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
_DATE_FORMAT    = '%Y-%m-%d %H:%M:%S'

_configured = False


def setup_logging(level=None, force=False):
    """
    設定 root logger。重複呼叫無害（除非 force=True 才會覆寫）。
    預設輸出到 stderr（Railway log 會收到）。
    """
    global _configured
    if _configured and not force:
        return

    level = (level or os.environ.get('LOG_LEVEL', 'INFO')).upper()
    fmt   = os.environ.get('LOG_FORMAT', _DEFAULT_FORMAT)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    # 移除既有 handler 避免重複輸出（重啟測試 / pytest 場景）
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # 第三方套件吵雜的 logger 降級
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    _configured = True
