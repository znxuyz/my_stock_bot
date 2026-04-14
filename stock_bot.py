import os
import requests
import pandas as pd
from FinMind.data import DataLoader
from datetime import datetime

# 從 GitHub Secrets 抓取
WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    # 加入檢查，避免 URL 為空
    if not WEBHOOK_URL:
        print("錯誤：找不到 DISCORD_WEBHOOK 設定")
        return

    api = DataLoader()
    # 這裡我們不指定日期，讓 FinMind 回傳最新一筆
    print("🚀 開始連線 FinMind 抓取最新數據...")

    try:
        # 使用最單純的指令：直接抓取今日全台股行情
        # 如果當天還沒收盤，會自動回傳前一個交易日的資料
        df_price = api.taiwan_stock_daily_ranking()
        
        # 抓取法人
        df_inst = api.taiwan_stock_institutional_investors_all()

        results = []
        
        # 檢查資料是否完整
        if df_price is not None and not df_price.empty:
            # 只掃描前 200 檔最熱門的股票，增加穩定度
            for _, row in df_price.head(200).iterrows():
                # 取得數值，加入預設值避免報錯
                change_p = row.get('change_rate', 0)
                vol = row.get('volume', 0)
                sid = row.get('stock_id', '')
                name = row.get('stock_name', '未知')
                close = row.get('close', 0)

                # 核心篩選條件：漲幅 > 3.5% 且 成交量 > 1000張
                if change_p >= 3.5 and vol >= 1000:
                    # 簡單檢查法人是否有買（不檢查詳細張數，只要有資料就算）
                    if not df_inst.empty and sid in df_inst['stock_id'].values:
                        results.append(f"🔥 **[{sid} {name}]**\n收盤：{close} (+{change_p}%)\n量能：{int(vol)} 張")

        # 整理發送訊息
        if results:
            content = "📊 **【AI 動能突破雷達 - 雲端版】**\n偵測到符合條件標的：\n\n" + "\n\n".join(results[:15])
        else:
            content = "📅 掃描完畢，今日未發現符合強勢起漲條件的標的。"

        # 正式發送
        res = requests.post(WEBHOOK_URL, json={"content": content})
        if res.status_code == 204:
            print("✅ Discord 訊息發送成功！")
        else:
            print(f"❌ 發送失敗，狀態碼：{res.status_code}")

    except Exception as e:
        # 捕捉任何錯誤並傳回 Discord，這樣你才知道發生什麼事
        error_msg = f"❌ 執行過程中發生錯誤：{str(e)}"
        requests.post(WEBHOOK_URL, json={"content": error_msg})
        print(error_msg)

if __name__ == "__main__":
    run_analysis()
