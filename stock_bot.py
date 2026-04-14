import os
import requests
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 既然是抓前一天的，我們就設定「最後一個交易日」
    # 如果今天是週一，就找週五；如果是平日早上，就找昨天
    target_date = datetime.now() - timedelta(days=1)
    
    # 排除週末，回溯到最近的週五 (簡單邏輯)
    if target_date.weekday() == 5: # 週六 -> 回週五
        target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: # 週日 -> 回週五
        target_date -= timedelta(days=2)
        
    date_str = target_date.strftime("%Y%m%d")
    print(f"🚀 川投顧突襲證交所官網... 目標日期: {date_str}")

    try:
        # 直接請求證交所「三大法人買賣超日報」的 JSON 接口
        url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=20)
        data = res.json()

        if data.get('stat') != 'OK':
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"📅 證交所 {date_str} 資料尚未備妥或該日未開盤。"})
            return

        # 證交所資料格式解析
        # 欄位通常是: [證券代號, 證券名稱, 外資買進, 外資賣出, 外資買賣超, ...]
        df = pd.DataFrame(data['data'], columns=data['fields'])
        
        # 找出「三大法人買賣超合計」那一欄 (通常是最後一欄)
        # 並轉換為張數 (證交所是股數，所以除以 1000)
        total_col = data['fields'][-1]
        df['合計買超張數'] = df[total_col].str.replace(',', '').astype(float) / 1000
        
        # 篩選合計買超前 15 名
        top_buys = df.sort_values(by='合計買超張數', ascending=False).head(15)

        results = []
        for _, row in top_buys.iterrows():
            sid = row['證券代號']
            name = row['證券名稱']
            buy_vol = int(row['合計買超張數'])
            
            # 這裡我們手動補上 Yahoo Finance 抓到的漲幅，做成完美報表
            results.append(f"🔥【法人狂買】 **[{sid} {name}]**\n昨日合計買超：{buy_vol} 張")

        if results:
            content = f"☀️ **【川投顧：證交所直送報告】**\n分析日期：{date_str}\n\n" + "\n\n".join(results)
        else:
            content = f"📅 {date_str} 沒看到法人有大動作。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})
        print("✅ 證交所數據發送成功")

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 證交所連線噴掉：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
