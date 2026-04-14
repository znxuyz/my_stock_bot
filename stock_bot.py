import os
import requests
import pandas as pd
from datetime import datetime

# 從 GitHub Secrets 抓取
WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL:
        return

    # 1. 直接用 API 抓取「今日排行」
    url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDailyRanking"
    try:
        res = requests.get(url)
        data = res.json().get('data', [])
        df_price = pd.DataFrame(data)

        # 2. 篩選邏輯
        results = []
        if not df_price.empty:
            # 找出漲幅 > 3.5% 且成交量 > 1000張的股票
            # 這裡我們專注在成交量前 150 名的標的，確保流動性
            for _, row in df_price.head(150).iterrows():
                change = row.get('change_rate', 0)
                vol = row.get('volume', 0)
                
                if change >= 3.5 and vol >= 1000:
                    sid = row.get('stock_id')
                    name = row.get('stock_name')
                    price = row.get('close')
                    results.append(f"🔥 **[{sid} {name}]**\n收盤：{price} (+{change}%)\n量能：{int(vol)} 張")

        # 3. 發送訊息
        if results:
            msg = "📊 **【AI 雲端偵測：強勢起漲股】**\n\n" + "\n\n".join(results[:10])
        else:
            msg = f"📅 {datetime.now().strftime('%Y-%m-%d')} 掃描完畢，未發現符合標的。"

        requests.post(WEBHOOK_URL, json={"content": msg})
        print("Done!")
    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 錯誤：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
