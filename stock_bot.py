import os
import requests
import pandas as pd
from FinMind.data import DataLoader
from datetime import datetime, timedelta

# 從 GitHub 安全設定中抓取 Webhook
WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    api = DataLoader()
    # 抓取今天日期
    today = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # 使用最底層 fetch_data，完全避開函數名稱變動問題
        # 抓取當日成交資訊排行
        df_price = api.fetch_data(dataset="TaiwanStockDailyRanking", data_id="")
        # 抓取今日法人買賣超
        df_inst = api.fetch_data(dataset="TaiwanStockInstitutionalInvestorsAll", data_id="")
        
        results = []
        # 過濾前 200 檔活躍股
        for _, row in df_price.head(200).iterrows():
            change_rate = row.get('change_rate', 0)
            volume = row.get('volume', 0)
            stock_id = row.get('stock_id', '')
            
            # 你的核心條件：漲幅 > 3.5% 且 成交量 > 1000張
            if change_rate >= 3.5 and volume > 1000:
                inst_match = df_inst[df_inst['stock_id'] == stock_id]
                if not inst_match.empty and inst_match['buy'].sum() > 0:
                    results.append(f"🚀 **[{stock_id} {row.get('stock_name','')}]**\n收盤：{row.get('close')} (+{change_rate}%)\n量能：{int(volume)} 張")

        if results:
            msg = f"📊 **【雲端 AI 動能偵測】**\n日期：{today}\n\n" + "\n\n".join(results[:10])
        else:
            msg = f"📅 {today} 掃描完成，未發現符合標的。"

        requests.post(WEBHOOK_URL, json={"content": msg})
    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 執行出錯: {str(e)}"})

if __name__ == "__main__":
    run_analysis()
