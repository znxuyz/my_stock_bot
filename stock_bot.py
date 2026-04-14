import os
import requests
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 抓取前一交易日
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 2. 獲取行情 (處理 data9 報錯問題)
        p_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999"
        p_res = requests.get(p_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
        
        # 自動尋找行情資料表 (不再寫死 data9)
        p_data_list = []
        p_fields = []
        for key in p_res.keys():
            if 'data' in key and p_res[key] and len(p_res[key][0]) > 10:
                p_data_list = p_res[key]
                p_fields = p_res.get(key.replace('data', 'fields'), [])
                break
        
        # 3. 獲取法人資料
        i_url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
        i_res = requests.get(i_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()

        if not p_data_list or i_res.get('stat') != 'OK':
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"📅 {date_str} 資料尚未準備妥當。"})
            return

        # 4. 處理資料
        df_p = pd.DataFrame(p_data_list, columns=p_fields)
        df_i = pd.DataFrame(i_res['data'], columns=i_res['fields'])
        df_i['合計買超張數'] = df_i.iloc[:, -1].str.replace(',', '').astype(float) / 1000

        results = []
        # 5. 合併與分級 (邏輯優化)
        for _, row in df_i.sort_values(by='合計買超張數', ascending=False).head(100).iterrows():
            sid = row['證券代號']
            name = row['證券名稱']
            inst_vol = int(row['合計買超張數'])
            
            match = df_p[df_p['證券代號'] == sid]
            if match.empty: continue
            
            p_row = match.iloc[0]
            price = p_row['收盤價']
            
            # 漲跌計算
            try:
                sign = p_row['漲跌(+/-)']
                diff = float(p_row['漲跌價'].replace(',', ''))
                if '−' in sign or '-' in sign: diff *= -1
                
                prev = float(price.replace(',', '')) - diff
                change = round((diff / prev) * 100, 2)
            except: change = 0.0

            # --- 川投顧三等級標準 ---
            rank_tag = ""
            if change >= 7.0 and inst_vol > 0: rank_tag = "🔥【SS 級】"
            elif change >= 3.5 and inst_vol > 0: rank_tag = "💎【S 級】"
            elif change >= 1.0: rank_tag = "📈【A 級】"
            
            if rank_tag:
                results.append(f"{rank_tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人買超：{inst_vol} 張")

        # 6. 送出報告
        if results:
            content = f"☀️ **【川投顧：{date_str} 三等級報告】**\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {date_str} 盤面太悶，法人沒在動。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統炸裂：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
