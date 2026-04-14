import os
import requests
import pandas as pd
import time
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def get_data_safely(url, name="資料"):
    """更像人類的抓取方式"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    for i in range(3):
        try:
            # 增加 timeout 到 30 秒，給 API 更多反應時間
            res = requests.get(url, headers=headers, timeout=30)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data: 
                    return data
            print(f"{name} 第 {i+1} 次沒反應，狀態碼: {res.status_code}")
        except Exception as e:
            print(f"{name} 抓取發生錯誤: {e}")
        time.sleep(10) # 失敗休息久一點 (10秒)
    return []

def run_analysis():
    if not WEBHOOK_URL: return

    # 搜尋區間
    today_dt = datetime.now()
    start_date = (today_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        # 1. 抓取行情排行
        data_price = get_data_safely("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDailyRanking", "股價排行")
        
        if not data_price:
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": "💀 API 徹底罷工了。這不是程式的問題，是資料源沒給東西。"})
            return

        df_price = pd.DataFrame(data_price)
        report_date = df_price['date'].iloc[0]

        # 2. 抓取三大法人資料
        url_inst = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsAll&start_date={start_date}"
        data_inst = get_data_safely(url_inst, "法人資料")
        
        df_inst = pd.DataFrame(data_inst) if data_inst else pd.DataFrame()
        if not df_inst.empty:
            df_inst = df_inst[df_inst['date'] == report_date]

        results = []
        # 掃描前 200 檔
        for _, row in df_price.head(200).iterrows():
            change = row.get('change_rate', 0)
            vol = row.get('volume', 0)
            sid = row.get('stock_id')
            name = row.get('stock_name')
            price = row.get('close')

            # --- 測試門檻：1% / 10張 ---
            if change >= 1.0 and vol >= 10:
                has_inst_buy = False
                if not df_inst.empty:
                    inst_match = df_inst[df_inst['stock_id'] == sid]
                    has_inst_buy = not inst_match.empty and inst_match['buy'].sum() > 0
                
                rank_tag = ""
                if change >= 7.0 and has_inst_buy: rank_tag = "🔥【SS 級】"
                elif change >= 3.5 and has_inst_buy: rank_tag = "💎【S 級】"
                elif change >= 1.0: rank_tag = "📈【A 級】"
                
                if rank_tag:
                    results.append(f"{rank_tag} **[{sid} {name}]**\n收盤：{price} (+{change}%)\n量能：{int(vol)} 張")

        if results:
            content = f"☀️ **【早安！量化分析報告】**\n分析日期：{report_date}\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {report_date} 掃描完畢，目前沒發現好標的。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 執行異常：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
