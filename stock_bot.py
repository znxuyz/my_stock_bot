import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 決定目標日期 (抓最後一個交易日)
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 2. 從證交所抓法人大數據
        twse_url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
        res = requests.get(twse_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        data = res.json()

        if data.get('stat') != 'OK':
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"📅 證交所 {date_str} 資料尚未準備好。"})
            return

        df_inst = pd.DataFrame(data['data'], columns=data['fields'])
        
        # 3. 篩選法人有買超的標的 (前 50 名)
        df_inst['合計買超張數'] = df_inst.iloc[:, -1].str.replace(',', '').astype(float) / 1000
        top_buys = df_inst.sort_values(by='合計買超張數', ascending=False).head(50)

        results = []
        
        # 4. 針對法人買超股，去 Yahoo 抓漲幅來分級
        for _, row in top_buys.iterrows():
            sid = row['證券代號']
            name = row['證券名稱']
            inst_vol = int(row['合計買超張數'])
            
            # yfinance 格式：台股要加 .TW
            ticker = yf.Ticker(f"{sid}.TW")
            hist = ticker.history(period="2d")
            
            if len(hist) < 2: continue
            
            close_now = hist['Close'].iloc[-1]
            close_prev = hist['Close'].iloc[-2]
            change = round(((close_now - close_prev) / close_prev) * 100, 2)
            
            # --- 川投顧三等級邏輯 ---
            rank_tag = ""
            if change >= 7.0:
                rank_tag = "🔥【SS 級：噴發神股】"
            elif change >= 3.5:
                rank_tag = "💎【S 級：優選強勢】"
            elif change >= 1.0:
                rank_tag = "📈【A 級：技術轉強】"
            
            if rank_tag:
                results.append(f"{rank_tag} **[{sid} {name}]**\n昨日漲幅：{change}%\n法人買超：{inst_vol} 張")

        # 5. 發送 Discord 訊息
        if results:
            content = f"☀️ **【川投顧：三等級全自動分析】**\n分析日期：{date_str}\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {date_str} 雖然有法人買，但沒一檔漲超過 1%，太廢了不予推薦。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})
        print(f"✅ {date_str} 三等級報告發送成功！")

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統炸了：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
