import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def run_analysis():
    if not WEBHOOK_URL: return
    
    # --- 自動回溯日期邏輯 ---
    data_found = False
    offset = 1
    while not data_found and offset < 7: # 最多回溯 7 天找資料
        target_date = datetime.now() - timedelta(days=offset)
        date_str = target_date.strftime("%Y%m%d")
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers)
        
        if "查詢無資料" not in i_res.text and len(i_res.text) > 500:
            data_found = True
        else:
            offset += 1
    
    if not data_found:
        requests.post(WEBHOOK_URL, json={"content": "❌ 連續 7 天都抓不到證交所資料，這不科學。"})
        return

    try:
        # 1. 讀取法人資料
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains('^[0-9A-Z]', na=False)].copy()
        
        # 2. 抓取行情資料
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers)
        p_text = p_res.text
        start_idx = p_text.find('"證券代號"')
        df_p = pd.read_csv(io.StringIO(p_text[start_idx:]), thousands=',').dropna(thresh=5)

        # 3. 徹底對齊
        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])

        # 4. 合併
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        results = []
        for _, row in df.iterrows():
            try:
                name = row.iloc[1]
                # 這裡把 vol 門檻拿掉，連賣超也看
                vol = pd.to_numeric(row.iloc[18], errors='coerce') / 1000
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                sign = str(row['漲跌(+/-)'])
                
                if '−' in sign or '-' in sign: diff *= -1
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                # --- 測試級分級 (完全鬆綁) ---
                if change >= 7.0: tag = "🔥【SS 級】"
                elif change >= 3.5: tag = "💎【S 級】"
                elif change >= 1.0: tag = "📈【A 級】"
                else: tag = "📊【測試級】"
                
                # 只要有資料就塞進去
                results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        # 按漲幅排序，取前 10 名，這樣一定會有東西
        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:10]]

        content = f"☀️ **【川投顧：{date_str} 測試報告】**\n成功匹配 {len(df)} 檔股票\n\n" + "\n\n".join(final_list)
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 最終調試出錯：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
