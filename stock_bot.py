import os
import requests
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 抓取目標日期 (前一交易日)
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 2. 獲取證交所當日所有股票行情 (含漲跌幅)
        price_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999"
        p_res = requests.get(price_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        p_data = p_res.json()
        
        # 3. 獲取三大法人買賣超
        inst_url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
        i_res = requests.get(inst_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        i_data = i_res.json()

        if p_data.get('stat') != 'OK' or i_data.get('stat') != 'OK':
            requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"📅 {date_str} 資料尚未齊全。"})
            return

        # 4. 處理行情資料
        # 欄位說明: [0]代號, [1]名稱, [5]收盤價, [9]漲跌(+/-), [10]漲跌價
        df_p = pd.DataFrame(p_data['data9'], columns=p_data['fields9'])
        
        # 5. 處理法人資料
        # 欄位說明: [0]代號, [1]名稱, [-1]三大法人買賣超合計
        df_i = pd.DataFrame(i_data['data'], columns=i_data['fields'])
        df_i['合計買超張數'] = df_i.iloc[:, -1].str.replace(',', '').astype(float) / 1000

        # 6. 合併資料並進行分級
        results = []
        for _, row in df_i.sort_values(by='合計買超張數', ascending=False).head(100).iterrows():
            sid = row['證券代號']
            name = row['證券名稱']
            inst_vol = int(row['合計買超張數'])
            
            # 從行情表找對應股票
            match = df_p[df_p['證券代號'] == sid]
            if match.empty: continue
            
            p_row = match.iloc[0]
            price = p_row['收盤價']
            
            # 計算漲跌幅 (證交所沒直接給%，我們要拿漲跌價除以昨收)
            try:
                diff_sign = p_row['漲跌(+/-)']
                diff_val = float(p_row['漲跌價'])
                if '−' in diff_sign or '-' in diff_sign: diff_val *= -1
                
                prev_price = float(price.replace(',', '')) - diff_val
                change = round((diff_val / prev_price) * 100, 2)
            except:
                change = 0.0

            # --- 川投顧三等級邏輯 ---
            rank_tag = ""
            if change >= 7.0 and inst_vol > 0: rank_tag = "🔥【SS 級】"
            elif change >= 3.5 and inst_vol > 0: rank_tag = "💎【S 級】"
            elif change >= 1.0: rank_tag = "📈【A 級】"
            
            if rank_tag:
                results.append(f"{rank_tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人買超：{inst_vol} 張")

        # 7. 發送
        if results:
            content = f"☀️ **【川投顧：{date_str} 全自動分析】**\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {date_str} 盤勢太爛，沒半檔能看。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": f"❌ 系統錯誤：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
