import os
import requests
import pandas as pd
import time
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def get_data_with_retry(url, max_retries=3):
    """加入重試機制的抓取函數"""
    for i in range(max_retries):
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data: return data
            print(f"第 {i+1} 次抓取無資料，休息後重試...")
        except Exception as e:
            print(f"抓取錯誤: {e}")
        time.sleep(5) # 失敗就等 5 秒再試
    return []

def run_analysis():
    if not WEBHOOK_URL: return

    today_dt = datetime.now()
    start_date = (today_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        # 1. 抓取行情排行 (加上重試)
        data_price = get_data_with_retry("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDailyRanking")
        
        if not data_price:
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": "❌ 連試三次都抓不到行情資料，API 真的掛了，明天再說吧。"})
            return

        df_price = pd.DataFrame(data_price)
        report_date = df_price['date'].iloc[0]

        # 2. 抓取法人資料 (加上重試)
        url_inst = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsAll&start_date={start_date}"
        data_inst = get_data_with_retry(url_inst)
        
        df_inst = pd.DataFrame(data_inst) if data_inst else pd.DataFrame()
        if not df_inst.empty:
            df_inst = df_inst[df_inst['date'] == report_date]

        results = []
        for _, row in df_price.head(200).iterrows():
            change = row.get('change_rate', 0)
            vol = row.get('volume', 0)
            sid = row.get('stock_id')
            name = row.get('stock_name')
            price = row.get('close')

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
            content = f"☀️ **【早安！今日量化分析報告】**\n分析日期：{report_date}\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {report_date} 掃描完畢，目前沒發現好標的。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統炸了：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
