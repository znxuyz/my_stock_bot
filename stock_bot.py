import os, requests, io, time
from datetime import datetime, timedelta
import pandas as pd
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def get_secure_session():
    """建立一個不容易被證交所擋掉的連線會話"""
    session = requests.Session()
    # 關鍵：偽裝成正常的瀏覽器操作，加上 Referer
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.twse.com.tw/zh/afterTrading/MI_INDEX.html',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7'
    })
    # 設定重試邏輯：如果失敗，間隔 5, 10, 20 秒自動重試
    retries = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def run_analysis():
    if not WEBHOOK_URL: return
    session = get_secure_session()
    
    # 時區與日期判定
    now_tw = datetime.utcnow() + timedelta(hours=8)
    is_morning = now_tw.hour < 12
    target_date = now_tw - timedelta(days=1) if is_morning else now_tw
    if target_date.weekday() >= 5: target_date -= timedelta(days=target_date.weekday()-4)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 使用 RWD 最新接口
        i_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=csv"
        p_url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=csv"

        i_res = session.get(i_url, timeout=30)
        p_res = session.get(p_url, timeout=30)

        # 檢查資料長度，避免抓到空的網頁
        if len(i_res.text) < 500 or "查詢無資料" in i_res.text:
            print(f"[{date_str}] 證交所尚未釋出 CSV 資料。")
            return

        # --- 以下進入資料處理與分級邏輯 (同前版) ---
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].astype(str).str.contains('^[0-9A-Z]', na=False)].copy()
        col_name = df_i.columns[1]
        col_vol = [c for c in df_i.columns if '三大法人' in c and '合計' in c][0]

        df_p = pd.read_csv(io.StringIO(p_res.text[p_res.text.find('"證券代號"'):]), thousands=',').dropna(thresh=5)
        
        # 清理並合併
        df_i['sid'] = df_i.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df_p['sid'] = df_p.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df = pd.merge(df_i, df_p, on='sid', how='inner')

        hot_list, x_list = [], []
        for _, row in df.iterrows():
            try:
                v = pd.to_numeric(row[col_vol], errors='coerce') / 1000
                p = pd.to_numeric(row['收盤價'], errors='coerce')
                d = pd.to_numeric(row['漲跌價'], errors='coerce')
                if '−' in str(row['漲跌(+/-)']) or '-' in str(row['漲跌(+/-)']): d *= -1
                chg = round((d / (p - d)) * 100, 2) if (p - d) != 0 else 0

                if v > 0: # 法人買超
                    info = f"**[{row['sid']} {row[col_name]}]** {p} ({'+' if chg>0 else ''}{chg}%) {int(v)}張"
                    if chg >= 7.0: hot_list.append((chg, f"🔥 SS級 {info}"))
                    elif chg >= 3.5: hot_list.append((chg, f"💎 S級 {info}"))
                    elif chg >= 1.0: hot_list.append((chg, f"📈 A級 {info}"))
                    elif -3.0 <= chg <= 0: x_list.append((chg, f"🔍 X級起漲 {info}"))
            except: continue

        # 發送 Discord Embed
        hot_list.sort(key=lambda x: x[0], reverse=True)
        x_list.sort(key=lambda x: x[0], reverse=True)

        payload = {
            "embeds": [{
                "title": f"☀️ 川投顧戰報 ({'早報' if is_morning else '晚報'})",
                "description": f"日期：{date_str} (資料已成功對接)",
                "color": 15158332 if not is_morning else 3447003,
                "fields": [
                    {"name": "🚀 強勢區", "value": "\n".join([s[1] for s in hot_list[:15]]) or "無", "inline": False},
                    {"name": "🔍 潛在起漲區 (X)", "value": "\n".join([s[1] for s in x_list[:10]]) or "無", "inline": False}
                ]
            }]
        }
        requests.post(WEBHOOK_URL, json=payload, timeout=10)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_analysis()
