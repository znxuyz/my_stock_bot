import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def get_market_info(date_str):
    """嘗試獲取大盤概況，失敗則優雅跳過"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        p_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=IND"
        p_res = requests.get(p_url, headers=headers, timeout=8)
        if "查詢無資料" in p_res.text: return ""
        
        df_m = pd.read_csv(io.StringIO(p_res.text), skiprows=1, thousands=',')
        taiex = df_m[df_m.iloc[:, 0].str.contains('發行量加權股價指數', na=False)].iloc[0]
        idx_p = float(taiex.iloc[1])
        idx_diff = pd.to_numeric(taiex.iloc[3], errors='coerce')
        if '−' in str(taiex.iloc[2]) or '-' in str(taiex.iloc[2]): idx_diff *= -1
        idx_chg = round((idx_diff / (idx_p - idx_diff)) * 100, 2)
        return f"📊 **加權指數：{idx_p} ({'+' if idx_diff>0 else ''}{idx_diff} / {'+' if idx_chg>0 else ''}{idx_chg}%)**\n"
    except: return ""

def run_analysis():
    if not WEBHOOK_URL: return
    now = datetime.now() + timedelta(hours=8)
    is_morning = now.hour < 12
    target_date = now if not is_morning else now - timedelta(days=1)
    if target_date.weekday() >= 5: target_date -= timedelta(days=target_date.weekday()-4)
    date_str = target_date.strftime("%Y%m%d")
    
    market_header = get_market_info(date_str)
    report_type = "【盤前複習】" if is_morning else "【盤後結算】"

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers, timeout=12)
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers, timeout=12)

        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains('^[0-9A-Z]', na=False)].copy()
        df_p = pd.read_csv(io.StringIO(p_res.text[p_res.text.find('"證券代號"'):]), thousands=',').dropna(thresh=5)

        df_i['sid_clean'], df_p['sid_clean'] = clean_sid(df_i.iloc[:, 0]), clean_sid(df_p.iloc[:, 0])
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        hot_stocks = [] # SS, S, A 級
        x_stocks = []   # X 級起漲潛力

        for _, row in df.iterrows():
            try:
                name, price = row.iloc[1], pd.to_numeric(row['收盤價'], errors='coerce')
                vol = pd.to_numeric(row.iloc[18], errors='coerce') / 1000 # 法人買超張數
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                if '−' in str(row['漲跌(+/-)']) or '-' in str(row['漲跌(+/-)']): diff *= -1
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                info = f"**[{row['sid_clean']} {name}]** 價格:{price} ({'+' if change>0 else ''}{change}%) 法人:{int(vol)}張"
                
                if vol > 0: # 核心條件：法人必須是買超
                    if change >= 7.0: hot_stocks.append((change, f"🔥【SS 級】 {info}"))
                    elif change >= 3.5: hot_stocks.append((change, f"💎【S 級】 {info}"))
                    elif change >= 1.0: hot_stocks.append((change, f"📈【A 級】 {info}"))
                    elif -3.0 <= change <= 0: # X 級：小跌但法人買
                        x_stocks.append((change, f"🔍【X 級：逆勢起漲】 {info}"))
            except: continue

        # 排序與組合
        hot_stocks.sort(key=lambda x: x[0], reverse=True)
        x_stocks.sort(key=lambda x: x[0], reverse=True) # 越接近平盤的排越前面
        
        final_list = [s[1] for s in hot_stocks[:15]] + ["--- 潛在起漲區 ---"] + [s[1] for s in x_stocks[:10]]

        content = f"☀️ **川投顧 {report_type}**\n日期：{date_str}\n{market_header}{'='*25}\n\n" + "\n".join(final_list)
        requests.post(WEBHOOK_URL, json={"username": "川投顧量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統暫時無法取得個股資料：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
