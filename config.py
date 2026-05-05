"""
集中管理所有環境變數、檔案路徑與策略參數。
任何模組需要可變設定都從這裡 import，避免散在各檔案裡的魔術數字。
"""
import os

# ─────────── Discord ───────────
DISCORD_BOT_TOKEN  = os.environ.get('DISCORD_BOT_TOKEN', '')
DISCORD_PUBLIC_KEY = os.environ.get('DISCORD_PUBLIC_KEY', '')
DISCORD_APP_ID     = os.environ.get('DISCORD_APP_ID', '')
DISCORD_WEBHOOK    = os.environ.get('DISCORD_WEBHOOK', '')

# HTTP server port（Railway 會自動注入 PORT）
PORT = int(os.environ.get('PORT', 8080))

# ─────────── 資料庫 ───────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# 改變此版本號 → init_db 會 DROP 重建 screen_records（清空舊資料）
SCHEMA_VERSION = 'v4-macd-chase'

# 若 status='running' 超過此時間視為卡死，允許重跑
RUN_TIMEOUT_SEC = 1800

# ─────────── GitHub Pages ───────────
GITHUB_TOKEN  = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO   = os.environ.get('GITHUB_REPO', 'znxuyz/my_stock_bot')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_API    = 'https://api.github.com'

# ─────────── 本機暫存路徑 ───────────
DATA_FILE         = os.environ.get('DATA_FILE',         '/tmp/stockbot_data.json')
TOP_FLOW_CACHE    = os.environ.get('TOP_FLOW_CACHE',    '/tmp/stockbot_topflow_cache.json')
KBAR_CACHE_DIR    = os.environ.get('KBAR_CACHE_DIR',    '/tmp/stock_kbar_cache_v2')

# ─────────── TWSE / 抓資料 ───────────
TWSE_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
TWSE_HEADERS = {
    'User-Agent':      TWSE_USER_AGENT,
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer':         'https://www.twse.com.tw/',
}
TWSE_VERIFY_SSL = os.environ.get('TWSE_VERIFY_SSL', '0').lower() in ('1', 'true', 'yes')

T86_CACHE_TTL_SEC          = 1800   # T86 快取 30 分鐘
KBAR_CACHE_TTL_CURRENT_SEC = 86400         # 當月 K 棒快取 1 天
KBAR_CACHE_TTL_HISTORY_SEC = 86400 * 30    # 歷史月 K 棒快取 30 天
TWSE_CALL_INTERVAL_SEC     = 0.8    # TWSE 呼叫間隔（避免限速）
RATE_LIMIT_THRESHOLD       = 3      # 連續 N 檔抓不到 → 退避
RATE_LIMIT_BACKOFF_SEC     = 60     # 退避 60 秒讓 TWSE 恢復

STOCK_API_CACHE_TTL_SEC = 900       # /api/stock 個股查詢快取 15 分鐘

# ─────────── 篩選參數 ───────────
DATA_READY_HOUR   = 17     # 台灣時間 17:00 後才有當日資料
MIN_PRICE         = 10     # 收盤價下限
MIN_INST_SHARE    = 50000  # 法人合計買超最低股數（保留歷史相容；目前實際用下面兩個）
MAX_CANDIDATES    = 30     # 候選數量保護上限
VOLUME_RATIO_MIN  = 1.5    # 量比門檻

EMA_SHORT  = 10
EMA_MID    = 20
EMA_LONG1  = 60
EMA_LONG2  = 120
EMA_FALLBACK_MIN = 60      # 備援 EMA 模式最少需要的 K 棒數

# 等級門檻（漲幅；保留作向下相容）
GRADE_SS = 7.0
GRADE_S  = 3.5
GRADE_A  = 1.0

# 雙買超門檻
MIN_FOREIGN_SHARE     = 10000
MIN_TRUST_SHARE       = 10000
MIN_INST_SHARE_SINGLE = 100000

# 17:00 觸發一次盤後分析（只在第 0 分；Bot 重啟後由 DB 狀態決定是否要再跑）
ANALYSIS_TRIGGER_TIMES = [(17, 0)]
SCHEDULER_STARTUP_BUFFER_SEC = 90    # Bot 啟動後 N 秒內不觸發排程（避免重啟瞬間誤觸）
