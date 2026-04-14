import os
import requests
import pandas as pd
from datetime import datetime, timedelta

# 從 GitHub Secrets 抓取 Discord 網址
WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL:
        print("錯誤：找不到 DISCORD_WEBHOOK 設定")
        return

    # 搜尋區間設定
    today_dt = datetime.now()
    start_date = (today_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        # 1. 抓取行情排行
        res_price = requests.get("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDailyRanking")
        data_price = res_price.json().get('data', [])
        
        if not data_price:
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": "❌ 抓不到行情資料，可能是 API 沒回應。"})
            return

        df_price = pd.DataFrame(data_price)
        report_date = df_price['date'].iloc[0]

        # 2. 抓取三大法人資料
        url_inst = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsAll&start_date={start_date}"
        res_inst = requests.get(url_inst)
        data_inst = res_inst.json().get('data', [])
        
        # 法人資料容錯處理
        df_inst = pd.DataFrame(data_inst) if data_inst else pd.DataFrame()
        
        # 強制對齊日期 (若有法人資料才對齊)
        if not df_inst.empty:
            df_inst = df_inst[df_inst['date'] == report_date]

        results = []

        # 掃描邏輯 (前 200 名)
        for _, row in df_price.head(200).iterrows():
            change = row.get('change_rate', 0)
            vol = row.get('volume', 0)
            sid = row.get('stock_id')
            name = row.get('stock_name')
            price = row.get('close')

            # --- 超低測試門檻 ---
            if change >= 1.0 and vol >= 10:
                # 檢查法人
                has_inst_buy = False
                if not df_inst.empty:
                    inst_match = df_inst[df_inst['stock_id'] == sid]
                    has_inst_buy = not inst_match.empty and inst_match['buy'].sum() > 0
                
                rank_tag = ""
                if change >= 7.0 and has_inst_buy:
                    rank_tag = "🔥【SS 級】"
                elif change >= 3.5 and has_inst_buy:
                    rank_tag = "💎【S 級】"
                elif change >= 1.0:
                    rank_tag = "📈【A 級】"
                
                if rank_tag:
                    results.append(f"{rank_tag} **[{sid} {name}]**\n收盤：{price} (+{change}%)\n量能：{int(vol)} 張")

        # 3. 發送訊息
        if results:
            content = f"☀️ **【早安！量化分析報告】**\n分析日期：{report_date}\n\n" + "\n\n".join(results[:10])
        else:
            content = f"📅 {report_date} 掃描完畢，連 A 級都沒看到，這盤沒救了。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        # 發生任何錯誤都一定要回報到 Discord，不然你會不知道發生什麼事
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統炸了，錯誤訊息：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
