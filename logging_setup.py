"""
全域 logging 設定。bot.py 進入點呼叫 setup_logging() 一次即可，
其他模組只需 `import logging; logger = logging.getLogger(__name__)`。

Stream 路由（重要 — Railway / 一般 Linux 主機都依此分顏色）：
  DEBUG / INFO        → stdout（Railway log UI 顯示為一般訊息）
  WARNING / ERROR / CRITICAL → stderr（Railway log UI 顯示為紅色，方便挑出真正要看的）

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


class _MaxLevelFilter(logging.Filter):
    """只放行 levelno ≤ max_level 的紀錄。給 stdout handler 用，避免 WARNING+ 也跑去 stdout。"""

    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno <= self.max_level


def _make_handler(stream, level, formatter, max_level=None):
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    if max_level is not None:
        handler.addFilter(_MaxLevelFilter(max_level))
    return handler


def setup_logging(level=None, force=False, *, stdout=None, stderr=None):
    """
    設定 root logger。重複呼叫無害（除非 force=True 才會覆寫既有 handler）。

    參數：
      level    ── 預設讀 LOG_LEVEL env，再 fallback 'INFO'
      force    ── True 時即使 _configured 也重新設置（測試 / hot-reload 用）
      stdout / stderr ── 依賴注入；單元測試可傳 io.StringIO，平常傳 None 用 sys.stdout/stderr
    """
    global _configured
    if _configured and not force:
        return

    level = (level or os.environ.get('LOG_LEVEL', 'INFO')).upper()
    fmt   = os.environ.get('LOG_FORMAT', _DEFAULT_FORMAT)
    formatter = logging.Formatter(fmt, datefmt=_DATE_FORMAT)

    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    # 兩個 handler 分流：
    #   stdout ← DEBUG / INFO（用 _MaxLevelFilter 擋掉 WARNING 以上）
    #   stderr ← WARNING / ERROR / CRITICAL
    stdout_h = _make_handler(stdout, logging.DEBUG,   formatter, max_level=logging.INFO)
    stderr_h = _make_handler(stderr, logging.WARNING, formatter)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(stdout_h)
    root.addHandler(stderr_h)

    # 第三方套件吵雜的 logger 降級
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    _configured = True
