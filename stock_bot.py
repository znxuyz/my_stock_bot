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

    # 設定搜尋區間，搜尋過去 7 天確保能涵蓋到最後一個交易日
    today_dt = datetime.now()
    start_date = (today_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        # 1. 抓取行情排行
        url_price = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDailyRanking"
        res_price = requests.get(url_price)
        df_price = pd.DataFrame(res_price.json().get('data', []))

        if df_price.empty:
            print("無法抓取行情資料")
            return

        # 取得股價排行資料的實際日期 (例如 2026-04-14)
        report_date = df_price['date'].iloc[0]
        print(f"📊 股價資料日期: {report_date}")

        # 2. 抓取三大法人資料
        url_inst = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsAll&start_date={start_date}"
        res_inst = requests.get(url_inst)
        df_inst = pd.DataFrame(res_inst.json().get('data', []))
        
        # --- 核心修正：強制對齊日期 ---
        if not df_inst.empty:
            # 關鍵！只留下跟股價排行同一天的法人資料
            df_inst = df_inst[df_inst['date'] == report_date]
            print(f"✅ 已對齊 {report_date} 的法人籌碼資料")

        results = []

        # 針對成交排行前 200 名熱門股進行掃描
        for _, row in df_price.head(200).iterrows():
            change = row.get('change_rate', 0)
            vol = row.get('volume', 0)
            sid = row.get('stock_id')
            name = row.get('stock_name')
            price = row.get('close')

            # --- 測試用超低門檻 (漲 1% 就抓) ---
            if change >= 1.0 and vol >= 10:
                
                # 比對該股在同日是否有法人買超
                inst_match = df_inst[df_inst['stock_id'] == sid]
                has_inst_buy = not inst_match.empty and inst_match['buy'].sum() > 0
                
                rank_tag = ""
                if change >= 7.0 and has_inst_buy:
                    rank_tag = "🔥【SS 級：超強動能】"
                elif change >= 3.5 and has_inst_buy:
                    rank_tag = "💎【S 級：優選標的】"
                elif change >= 1.0: 
                    # A 級不強制看法人資料，確保只要有漲 1% 就能在 Discord 看到結果
                    rank_tag = "📈【A 級：技術轉強】"
                
                if rank_tag:
                    results.append(f"{rank_tag} **[{sid} {name}]**\n收盤：{price} (+{change}%)\n量能：{int(vol)} 張")

        # 3. 組合發送內容
        if results:
            # 只取前 15 檔，避免 Discord 訊息過長
            content = f"☀️ **【早安！今日量化分析報告】**\n分析日期：{report_date}\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {report_date} 掃描完畢，目前市場沒一個能打的標的。"

        payload = {
            "username": "川投顧嘴砲量化系統",
            "content": content
        }

        requests.post(WEBHOOK_URL, json=payload)
        print(f"✅ 分析任務成功，日期：{report_date}")

    except Exception as e:
        error_msg = {"username": "川投顧嘴砲量化系統", "content": f"❌ 系統炸了：{str(e)}"}
        requests.post(WEBHOOK_URL, json=error_msg)

if __name__ == "__main__":
    run_analysis()
