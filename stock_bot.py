import os, requests, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def fetch_twse_data(url):
    """加強版請求：自動重試並偽裝瀏覽器"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for _ in range(3): # 失敗自動重試 3 次
        try:
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                return res.json()
        except:
            time.sleep(5)
    return None

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 抓取最後一個交易日 (考慮週末)
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 1. 抓取行情 (MI_INDEX)
        p_data = fetch_twse_data(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999")
        # 2. 抓取法人 (T86)
        i_data = fetch_twse_data(f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL")

        if not p_data or p_data.get('stat') != 'OK' or not i_data or i_data.get('stat') != 'OK':
            requests.post(WEBHOOK_URL, json={"content": f"📅 報告：{date_str} 證交所資料還在睡覺或結算中，請晚點再叫我。"})
            return

        # --- 動態找行情表 (關鍵修復) ---
        p_list, p_fields = [], []
        for k, v in p_data.items():
            if 'data' in k and v and len(v[0]) > 10: # 只要欄位數夠多就是我們要的行情表
                p_list, p_fields = v, p_data.get(k.replace('data', 'fields'), [])
                break

        df_p = pd.DataFrame(p_list, columns=p_fields)
        df_i = pd.DataFrame(i_data['data'], columns=i_data['fields'])
        df_i['法人買超'] = df_i.iloc[:, -1].str.replace(',', '').astype(float) / 1000

        results = []
        for _, row in df_i.sort_values(by='法人買超', ascending=False).head(100).iterrows():
            sid, name = row['證券代號'], row['證券名稱']
            vol = int(row['法人買超'])
            
            match = df_p[df_p['證券代號'] == sid]
            if match.empty: continue
            
            # 漲跌計算
            p_row = match.iloc[0]
            try:
                price = p_row['收盤價']
                diff = float(p_row['漲跌價'].replace(',', ''))
                if '−' in p_row['漲跌(+/-)'] or '-' in p_row['漲跌(+/-)']: diff *= -1
                prev = float(price.replace(',', '')) - diff
                change = round((diff / prev) * 100, 2)
            except: continue

            # --- 川投顧分級系統 ---
            tag = ""
            if change >= 7.0 and vol > 0: tag = "🔥【SS 級】"
            elif change >= 3.5 and vol > 0: tag = "💎【S 級】"
            elif change >= 1.0: tag = "📈【A 級】"
            
            if tag:
                results.append(f"{tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{vol} 張")

        msg = f"☀️ **【川投顧：{date_str} 三等級全解析】**\n\n" + "\n\n".join(results[:15]) if results else "📅 盤面太廢，沒標的。"
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": msg})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 報警！系統又抽風了：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
