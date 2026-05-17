"""
向 Discord 註冊 slash commands。每次 Bot 啟動會跑一次。
"""
import logging
import requests

import config

logger = logging.getLogger(__name__)


_COMMANDS = [
    {'name': 'help',    'description': '📋 顯示所有指令說明'},
    {'name': 'run',     'description': '🚀 手動觸發選股分析',
     'options': [{'name': 'mode', 'description': '執行模式',
                  'type': 3, 'required': False,
                  'choices': [{'name': '自動判斷（auto）',  'value': 'auto'},
                              {'name': '盤後結算（close）', 'value': 'close'},
                              {'name': '盤前複習（preview）', 'value': 'preview'}]}]},
    {'name': 'status',    'description': '📊 查看上次執行時間與狀態'},
    {'name': 'stock',     'description': '🔍 個股技術分析與 0–5 星推薦度',
     'options': [{'name': 'code', 'description': '股票代號（例如：2330）',
                  'type': 3, 'required': True}]},
    {'name': 'fortune',   'description': '🔮 抽取今日股市運勢'},
    {'name': 'roast',     'description': '🗣️ 川投顧用川普語氣評論今日大盤'},
    {'name': 'topbuyer',  'description': '📈 今日外資買超前 10 名'},
    {'name': 'topseller', 'description': '📉 今日外資賣超前 10 名'},
    {'name': 'holding',   'description': '💼 查看持倉紀錄（不填看自己，填 @ 看別人）',
     'options': [{'name': 'target', 'description': '@某人（不填則看自己）',
                  'type': 6, 'required': False}]},
    {'name': 'buy',  'description': '🛒 記錄買入股票',
     'options': [
         {'name': 'code',  'description': '股票代號',          'type': 3,  'required': True},
         {'name': 'price', 'description': '買入價格（元）',    'type': 10, 'required': True},
         {'name': 'lots',  'description': '買入股數（例如：1000）', 'type': 4, 'required': True},
     ]},
    {'name': 'sell', 'description': '💸 記錄賣出股票並計算損益',
     'options': [
         {'name': 'code',  'description': '股票代號',          'type': 3,  'required': True},
         {'name': 'price', 'description': '賣出價格（元）',    'type': 10, 'required': True},
         {'name': 'lots',  'description': '賣出股數（例如：1000）', 'type': 4, 'required': True},
     ]},
    {'name': 'poll', 'description': '🗳️ 發起股票話題投票',
     'options': [
         {'name': 'question', 'description': '投票題目', 'type': 3, 'required': True},
         {'name': 'option1',  'description': '選項一',   'type': 3, 'required': True},
         {'name': 'option2',  'description': '選項二',   'type': 3, 'required': True},
     ]},
    {'name': 'leaderboard', 'description': '🏆 查看伺服器成員損益排行榜'},
    {'name': 'setup', 'description': '⚙️ 設定本伺服器的分析推播頻道（僅管理員）',
     'options': [{'name': 'webhook',
                  'description': 'Webhook URL（輸入 remove 可移除）',
                  'type': 3, 'required': True}]},
    {'name': 'report',    'description': '📊 查看最近結算報告與累積勝率統計'},
    {'name': 'stats',     'description': '📈 查看詳細統計與篩選邏輯修正建議'},
    {'name': 'challenge', 'description': '⚔️ 提交本週選股挑戰，一週後比誰獲利高',
     'options': [{'name': 'code', 'description': '你的挑戰股票代號',
                  'type': 3, 'required': True}]},
    # ─────────── v6.2 系統管理指令（開放） ───────────
    {'name': 'backfill', 'description': '🛠️ 手動補抓 T86 歷史到 daily_t86_history',
     'options': [{'name': 'days', 'description': '往回抓 N 個交易日（1~60，預設 10）',
                  'type': 4, 'required': False}]},
    {'name': 'health',   'description': '🩺 系統健康檢查：schema / 資料表完整性 / 今日筆數'},
    {'name': 'diag',     'description': '🌐 TWSE endpoint 連通性診斷（看哪個 URL 還活著）'},
]


def register_commands():
    if not (config.DISCORD_BOT_TOKEN and config.DISCORD_APP_ID):
        logger.warning('[Bot] 缺少 DISCORD_BOT_TOKEN 或 DISCORD_APP_ID，跳過註冊指令')
        return
    url = f'https://discord.com/api/v10/applications/{config.DISCORD_APP_ID}/commands'
    headers = {
        'Authorization': f'Bot {config.DISCORD_BOT_TOKEN}',
        'Content-Type':  'application/json',
    }
    try:
        r = requests.put(url, headers=headers, json=_COMMANDS, timeout=10)
        ok = r.status_code in (200, 201)
        logger.info('[Bot] Slash Commands 註冊%s', '成功' if ok else f'失敗：{r.status_code}')
    except Exception as e:
        logger.error('[Bot] 註冊指令時出錯：%s', e)
