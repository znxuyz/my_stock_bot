import os, requests, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def fetch_json(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for _ in range(3):
        try:
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200: return res.json()
        except: time.sleep(5)
    return None

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 抓取最後一個交易日
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 1. 抓取行情與法人
        p_data = fetch_json(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999")
        i_data = fetch_json(f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL")

        if not p_data or p_data.get('stat') != 'OK' or not i_data or i_data.get('stat') != 'OK':
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 資料尚未準備好，證交所還在結算中。"})
            return

        # 2. 動態解析行情表 (避開 data9 報錯)
        df_p = pd.DataFrame()
        for k, v in p_data.items():
            if 'data' in k and v and len(v[0]) > 10:
                df_p = pd.DataFrame(v)
                break
        
        # 3. 處理法人資料 (避開欄位名稱報錯，改用索引)
        df_i = pd.DataFrame(i_data['data'])
        # 索引說明: [0]代號, [1]名稱, [-1]合計買賣超
        df_i['vol'] = df_i.iloc[:, -1].str.replace(',', '').astype(float) / 1000

        results = []
        # 4. 進行數據合併與分級 (全索引化操作)
        for _, row in df_i.sort_values(by='vol', ascending=False).head(100).iterrows():
            sid, name, vol = row[0], row[1], int(row['vol'])
            
            # 匹配行情表 (行情表 [0] 是代號)
            match = df_p[df_p[0] == sid]
            if match.empty: continue
            
            p_row = match.iloc[0]
            # 行情表索引: [2]名稱, [5]收盤價, [9]漲跌符號, [10]漲跌價
            try:
                price = p_row[5]
                diff_sign = p_row[9]
                diff_val = float(p_row[10].replace(',', ''))
                if '−' in diff_sign or '-' in diff_sign: diff_val *= -1
                
                prev_price = float(price.replace(',', '')) - diff_val
                change = round((diff_val / prev_price) * 100, 2)
            except: continue

            # --- 川投顧三等級判定 ---
            tag = ""
            if change >= 7.0 and vol > 0: tag = "🔥【SS 級】"
            elif change >= 3.5 and vol > 0: tag = "💎【S 級】"
            elif change >= 1.0: tag = "📈【A 級】"
            
            if tag:
                results.append(f"{tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{vol} 張")

        msg = f"☀️ **【川投顧：{date_str} 三等級報告】**\n\n" + "\n\n".join(results[:15]) if results else "📅 今日無符合標的。"
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": msg})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統炸裂：找不到資料欄位，請檢查證交所格式。({str(e)})"})

if __name__ == "__main__":
    run_analysis()
