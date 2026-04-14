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

    # 設定搜尋區間，確保能抓到最新的交易日資料
    today_dt = datetime.now()
    start_date = (today_dt - timedelta(days=5)).strftime("%Y-%m-%d")
    print(f"🚀 啟動早晨掃描 (搜尋起點: {start_date})...")

    try:
        # 1. 抓取行情排行
        url_price = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDailyRanking"
        res_price = requests.get(url_price)
        df_price = pd.DataFrame(res_price.json().get('data', []))

        # 2. 抓取三大法人資料
        url_inst = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsAll&start_date={start_date}"
        res_inst = requests.get(url_inst)
        df_inst = pd.DataFrame(res_inst.json().get('data', []))
        
        # 同步法人日期
        if not df_inst.empty:
            last_inst_date = df_inst['date'].max()
            df_inst = df_inst[df_inst['date'] == last_inst_date]

        results = []

        if not df_price.empty:
            for _, row in df_price.head(200).iterrows():
                change = row.get('change_rate', 0)
                vol = row.get('volume', 0)
                sid = row.get('stock_id')
                name = row.get('stock_name')
                price = row.get('close')

                # 篩選標準：漲幅 > 3.5% 且 成交量 > 1000張
                if change >= 1 and vol >= 10:
                    inst_match = df_inst[df_inst['stock_id'] == sid]
                    has_inst_buy = not inst_match.empty and inst_match['buy'].sum() > 0
                    
                    # 評級邏輯
                    rank_tag = ""
                    if change >= 7.0 and has_inst_buy:
                        rank_tag = "🔥【SS 級：超強動能】"
                    elif change >= 3.5 and has_inst_buy:
                        rank_tag = "💎【S 級：優選標的】"
                    elif change >= 3.5:
                        rank_tag = "📈【A 級：技術轉強】"
                    
                    if rank_tag:
                        results.append(f"{rank_tag} **[{sid} {name}]**\n收盤：{price} (+{change}%)\n量能：{int(vol)} 張")

        # 3. 組合發送內容
        report_date = df_price['date'].iloc[0] if not df_price.empty else "未知"
        if results:
            content = f"☀️ **【早安！昨日量化分析報告】**\n交易日期：{report_date}\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {report_date} 掃描完畢，市場太冷，沒個能打的標的。"

        # 加入你指定的名字
        payload = {
            "username": "川投顧嘴砲量化系統",
            "content": content
        }

        requests.post(WEBHOOK_URL, json=payload)
        print("✅ 任務成功完成！")

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統炸了：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
