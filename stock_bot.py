import os
import requests
import pandas as pd
from FinMind.data import DataLoader
from datetime import datetime

# 從 GitHub Secrets 抓取
WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    api = DataLoader()
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"開始分析 {today} 資料...")

    try:
        # 1. 使用最基礎的 fetch_data 指令
        # 抓取今日全台股成交資訊
        df_price = api.fetch_data(dataset="TaiwanStockPriceTick", data_id="Full")
        
        # 2. 檢查資料是否為空
        if df_price is None or df_price.empty:
            requests.post(WEBHOOK_URL, json={"content": f"⚠️ {today} 暫無資料 (可能是尚未收盤或 API 維護中)"})
            return

        # 3. 抓取法人資料
        df_inst = api.fetch_data(dataset="TaiwanStockInstitutionalInvestorsAll", data_id="")

        results = []
        # 簡單過濾：漲幅 > 3.5%
        # 注意：不同版本欄位名不同，我們做容錯處理
        for _, row in df_price.iterrows():
            change_p = row.get('change_rate', 0)
            vol = row.get('volume', 0)
            sid = row.get('stock_id', '')

            if change_p >= 3.5 and vol >= 1000:
                # 比對法人
                if not df_inst.empty:
                    inst_buy = df_inst[df_inst['stock_id'] == sid]['buy'].sum()
                    if inst_buy > 0:
                        results.append(f"🚀 **[{sid}]** 收盤: {row.get('close')} (+{change_p}%) 量: {int(vol)}")

        # 4. 發送結果
        if results:
            msg = f"📊 **【AI 動能雷達】**\n" + "\n".join(results[:15])
        else:
            msg = f"📅 {today} 掃描完畢，未發現符合條件標的。"
        
        requests.post(WEBHOOK_URL, json={"content": msg})
        print("發送成功")

    except Exception as e:
        # 萬一真的出錯，把錯誤訊息傳到 Discord 讓我們知道哪裡壞了
        requests.post(WEBHOOK_URL, json={"content": f"❌ 執行中斷: {str(e)}"})

if __name__ == "__main__":
    run_analysis()
