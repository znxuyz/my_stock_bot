"""
簡單即時回應的指令：/help /fortune /roast /poll
"""
import random

from discord_bot.content import FORTUNES, ROASTS


def cmd_help():
    return (
        '📋 **川投顧量化系統 ── 指令說明**\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🚀 `/run [模式]` 手動觸發選股分析（盤後/盤前/自動）\n'
        '📊 `/status` 查看上次執行時間與狀態\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🔍 `/stock [代號]` 個股技術分析 + 0–5 星推薦度\n'
        '📈 `/topbuyer` 今日外資買超前 10 名\n'
        '📉 `/topseller` 今日外資賣超前 10 名\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '💼 `/holding [@對象]` 查看持倉紀錄（不填則看自己）\n'
        '🛒 `/buy [代號] [價格] [股數]` 記錄買入\n'
        '💸 `/sell [代號] [價格] [股數]` 記錄賣出並計算損益\n'
        '🏆 `/leaderboard` 伺服器損益排行榜\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🗳️ `/poll [題目] [選項1] [選項2]` 發起投票\n'
        '⚔️ `/challenge [代號]` 發起選股挑戰，一週後比誰獲利高\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🔮 `/fortune` 今日股市運勢\n'
        '🗣️ `/roast` 川投顧用川普語氣評論大盤\n'
    )


def cmd_fortune():
    emoji = random.choice(['🔮', '🎱', '🧧', '🀄', '🎰'])
    return f'{emoji} **今日股市運勢**\n\n{random.choice(FORTUNES)}'


def cmd_roast():
    return f'🗣️ **川投顧語錄**\n\n"{random.choice(ROASTS)}"'


def cmd_poll(question, opt1, opt2):
    return (
        f'🗳️ **{question}**\n\n'
        f'🅰️ {opt1}\n'
        f'🅱️ {opt2}\n\n'
        '請點下方表情回應投票！（🅰️ 或 🅱️）'
    )
