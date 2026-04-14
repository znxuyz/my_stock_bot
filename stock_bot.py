import os, requests, io, time
from datetime import datetime, timedelta
import pandas as pd
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def send_debug_msg(msg):
    """偵錯專用：直接發送純文字到 Discord，確保我們知道程式跑到了哪"""
    if WEBHOOK_URL:
        requests.post(WEBHOOK_URL, json={"content": f"🛠 [DEBUG]: {msg}"}, timeout=10)

def get_secure_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.twse.com.tw/zh/afterTrading/MI_INDEX.html'
    })
    retries = Retry(total=5, backoff_factor=3, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def run_analysis():
    if not WEBHOOK_URL:
        print("Webhook URL 缺失")
        return
    
    session = get_secure_session()
    now_tw = datetime.utcnow() + timedelta(hours=8)
    is_morning = now_tw.hour < 12
    target_date = now_tw - timedelta(days=1) if is_morning else now_tw
    
    # 週末回溯
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    send_debug_msg(f"開始執行 {date_str} ({'早報' if is_morning else '晚報'}) 任務...")

    try:
        # 1. 抓取法人資料
        i_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=csv"
        i_res = session.get(i_url, timeout=30)
        if len(i_res.text) < 1000 or "查詢無資料" in i_res.text:
            send_debug_msg(f"❌ 法人資料 (T86) 尚未準備好或抓取失敗。內容長度: {len(i_res.text)}")
            return

        # 2. 抓取收盤資料
        p_url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=csv"
        p_res = session.get(p_url, timeout=30)
        if len(p_res.text) < 1000:
            send_debug_msg(f"❌ 收盤資料 (MI_INDEX) 抓取失敗。")
            return

        send_debug_msg("✅ 資料下載成功，開始解析...")

        # 3. 解析與合併
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].astype(str).str.contains('^[0-9A-Z]', na=False)].copy()
        
        col_name = df_i.columns[1]
        col_vol = [c for c in df_i.columns if '三大法人' in c and '合計' in c][0]

        start_idx = p_res.text.find('"證券代號"')
        df_p = pd.read_csv(io.StringIO(p_res.text[start_idx:]), thousands=',').dropna(thresh=5)

        df_i['sid'] = df_i.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df_p['sid'] = df_p.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        
        df = pd.merge(df_i, df_p, on='sid', how='inner')
        send_debug_msg(f"成功匹配 {len(df)} 檔標的，進行分級篩選...")

        hot_list, x_list = [], []
        for _, row in df.iterrows():
            try:
                v = pd.to_numeric(row[col_vol], errors='coerce') / 1000
                p = pd.to_numeric(row['收盤價'], errors='coerce')
                d = pd.to_numeric(row['漲跌價'], errors='coerce')
                if '−' in str(row['漲跌(+/-)']) or '-' in str(row['漲跌(+/-)']): d *= -1
                chg = round((d / (p - d)) * 100, 2) if (p - d) != 0 else 0

                if v > 0:
                    info = f"**[{row['sid']} {row[col_name]}]** {p} ({'+' if chg>0 else ''}{chg}%) {int(v)}張"
                    if chg >= 7.0: hot_list.append((chg, f"🔥 SS級 {info}"))
                    elif chg >= 3.5: hot_list.append((chg, f"💎 S級 {info}"))
                    elif chg >= 1.0: hot_list.append((chg, f"📈 A級 {info}"))
                    elif -3.0 <= chg <= 0: x_list.append((chg, f"🔍 X級起漲 {info}"))
            except: continue

        # 4. 發送最終成果
        hot_list.sort(key=lambda x: x[0], reverse=True)
        x_list.sort(key=lambda x: x[0], reverse=True)

        payload = {
            "embeds": [{
                "title": f"☀️ 川投顧戰報 ({date_str})",
                "color": 15158332,
                "fields": [
                    {"name": "🚀 強勢區", "value": "\n".join([s[1] for s in hot_list[:15]]) or "無符合標的", "inline": False},
                    {"name": "🔍 潛在起漲區 (X)", "value": "\n".join([s[1] for s in x_list[:10]]) or "無符合標的", "inline": False}
                ]
            }]
        }
        res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code != 204:
            send_debug_msg(f"Webhook 發送失敗，狀態碼: {res.status_code}")

    except Exception as e:
        send_debug_msg(f"🚨 程式運行中崩潰！錯誤原因: {str(e)}")

if __name__ == "__main__":
    run_analysis()
