import os
import requests
import pandas as pd
from datetime import datetime

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL: return

    print("🚀 川投顧啟動：切換至 Fugle 富果資料源...")
    
    try:
        # 1. 抓取今日台股盤後資訊 (富果公開資訊)
        # 這裡改用富果的公開 API
        res = requests.get("https://api.fugle.tw/marketdata/v1.0/stock/snapshot/market/TSE")
        data = res.json().get('data', [])
        
        if not data:
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": "📅 目前資料庫更新中，請稍後再試。"})
            return

        results = []
        # 2. 篩選與評級
        for item in data:
            sid = item.get('symbol')
            name = item.get('name')
            change = item.get('changePercent', 0)
            price = item.get('closePrice')
            vol = item.get('volume', 0) / 1000 # 換算成張數

            # 測試門檻：漲幅 > 1% 且 成交量 > 10張
            if change >= 1.0 and vol >= 10:
                # 這裡暫時以漲幅做 A 級判斷，確保你一定能看到通知
                results.append(f"📈【A 級】 **[{sid} {name}]**\n價格：{price} (+{change}%)\n量能：{int(vol)} 張")

        if results:
            content = f"☀️ **【早安！川投顧新水源報告】**\n分析來源：Fugle Market Data\n\n" + "\n\n".join(results[:15])
        else:
            content = "📅 掃描完畢，今天市場沒個能打的標的。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})
        print("✅ 報告已送出")

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統又炸了：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
