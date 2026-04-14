import os, requests, io, time
from datetime import datetime, timedelta
import pandas as pd
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def send_debug_msg(msg):
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
    if not WEBHOOK_URL: return
    session = get_secure_session()
    now_tw = datetime.utcnow() + timedelta(hours=8)
    # 明確日期判定
    target_date = now_tw - timedelta(days=1) if now_tw.hour < 12 else now_tw
    if target_date.weekday() >= 5: target_date -= timedelta(days=target_date.weekday()-4)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 1. 抓取資料
        i_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=csv"
        p_url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=csv"
        
        i_res = session.get(i_url, timeout=30)
        p_res = session.get(p_url, timeout=30)

        if "查詢無資料" in i_res.text:
            send_debug_msg(f"今日 ({date_str}) 證交所查無交易資料（可能為休市）。")
            return

        # 2. 解析 T86 (法人) - 強化欄位搜尋邏輯
        # 讀取時先不設 skiprows，手動找起始點
        i_lines = i_res.text.split('\n')
        header_line_idx = 0
        for idx, line in enumerate(i_lines):
            if "證券代號" in line:
                header_line_idx = idx
                break
        
        df_i = pd.read_csv(io.StringIO('\n'.join(i_lines[header_line_idx:])), thousands=',')
        # 清洗所有欄位名稱：去空白、去換行
        df_i.columns = [str(c).replace(' ', '').replace('\n', '').replace('\r', '') for c in df_i.columns]
        
        # 尋找目標欄位：只要包含「三大法人」且包含「合計」或「買賣超」
        col_vol_list = [c for c in df_i.columns if ('三大法人' in c and '合計' in c) or ('合計' in c and '買賣超' in c)]
        if not col_vol_list:
            # 如果還是找不到，嘗試找倒數幾個欄位 (通常合計在倒數第二或第三)
            col_vol = df_i.columns[-2]
            send_debug_msg(f"⚠️ 找不到精準合計欄位，自動 fallback 至: {col_vol}")
        else:
            col_vol = col_vol_list[0]

        # 3. 解析 MI_INDEX (收盤價)
        p_lines = p_res.text.split('\n')
        p_header_idx = 0
        for idx, line in enumerate(p_lines):
            if "證券代號" in line:
                p_header_idx = idx
                break
        df_p = pd.read_csv(io.StringIO('\n'.join(p_lines[p_header_idx:])), thousands=',').dropna(thresh=5)
        df_p.columns = [str(c).replace(' ', '').replace('\n', '').replace('\r', '') for c in df_p.columns]

        # 4. 合併與篩選
        df_i['sid'] = df_i.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df_p['sid'] = df_p.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df = pd.merge(df_i, df_p, on='sid', how='inner')

        hot_list, x_list = [], []
        for _, row in df.iterrows():
            try:
                # 使用正確抓到的 col_vol
                v = pd.to_numeric(row[col_vol], errors='coerce') / 1000
                p = pd.to_numeric(row['收盤價'], errors='coerce')
                d = pd.to_numeric(row['漲跌價'], errors='coerce')
                sign = str(row.get('漲跌(+/-)', ''))
                if '−' in sign or '-' in sign: d *= -1
                chg = round((d / (p - d)) * 100, 2) if (p - d) != 0 else 0

                if v > 0:
                    info = f"**[{row['sid']} {row.iloc[1]}]** {p} ({'+' if chg>0 else ''}{chg}%) {int(v)}張"
                    if chg >= 7.0: hot_list.append((chg, f"🔥 SS級 {info}"))
                    elif chg >= 3.5: hot_list.append((chg, f"💎 S級 {info}"))
                    elif chg >= 1.0: hot_list.append((chg, f"📈 A級 {info}"))
                    elif -3.0 <= chg <= 0: x_list.append((chg, f"🔍 X級起漲 {info}"))
            except: continue

        # 5. 發送 Embed
        hot_list.sort(key=lambda x: x[0], reverse=True)
        x_list.sort(key=lambda x: x[0], reverse=True)

        payload = {
            "embeds": [{
                "title": f"☀️ 川投顧戰報 ({date_str})",
                "color": 15158332,
                "fields": [
                    {"name": "🚀 強勢區 (SS/S/A)", "value": "\n".join([s[1] for s in hot_list[:15]]) or "無符合標的", "inline": False},
                    {"name": "🔍 潛在起漲區 (X)", "value": "\n".join([s[1] for s in x_list[:10]]) or "無符合標的", "inline": False}
                ],
                "footer": {"text": f"偵錯資訊：匹配 {len(df)} 檔 / 合計欄位使用: {col_vol}"}
            }]
        }
        requests.post(WEBHOOK_URL, json=payload, timeout=10)

    except Exception as e:
        send_debug_msg(f"🚨 最終防線崩潰！原因: {str(e)}")

if __name__ == "__main__":
    run_analysis()
