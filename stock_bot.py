import os, requests, io, time
from datetime import datetime, timedelta
import pandas as pd
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# 從環境變數讀取 Webhook
WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

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
    
    # 1. 日期邏輯 (確保抓取正確交易日)
    now_tw = datetime.utcnow() + timedelta(hours=8)
    # 如果現在是早上，抓前一天；如果是下午，抓今天
    target_date = now_tw - timedelta(days=1) if now_tw.hour < 12 else now_tw
    # 避開週末
    if target_date.weekday() >= 5: target_date -= timedelta(days=target_date.weekday()-4)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 2. 抓取資料 (RWD 版路徑)
        i_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=csv"
        p_url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=csv"
        
        i_res = session.get(i_url, timeout=30)
        p_res = session.get(p_url, timeout=30)

        if "查詢無資料" in i_res.text:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 證交所資料庫尚未更新或今日休市。"})
            return

        # 3. 解析 T86 (法人資料)
        i_lines = i_res.text.split('\n')
        h_idx = next((i for i, l in enumerate(i_lines) if "證券代號" in l), 0)
        df_i = pd.read_csv(io.StringIO('\n'.join(i_lines[h_idx:])), thousands=',')
        df_i.columns = [str(c).replace(' ', '').replace('\n', '') for c in df_i.columns]

        # --- 欄位鎖定邏輯升級 ---
        # 優先找包含「三大法人」且包含「合計」的欄位
        col_vol = ""
        possible_targets = ['三大法人買賣超股數', '合計買賣超股數', '買賣超合計']
        for pt in possible_targets:
            match = [c for c in df_i.columns if pt in c]
            if match:
                col_vol = match[0]
                break
        
        # 如果還是抓不到，強行使用第 19 欄 (這是 T86 CSV 的標準合計位置)
        if not col_vol:
            col_vol = df_i.columns[18] 

        # 4. 解析 MI_INDEX (價格資料)
        p_lines = p_res.text.split('\n')
        ph_idx = next((i for i, l in enumerate(p_lines) if "證券代號" in l), 0)
        df_p = pd.read_csv(io.StringIO('\n'.join(p_lines[ph_idx:])), thousands=',').dropna(thresh=5)
        df_p.columns = [str(c).replace(' ', '').replace('\n', '') for c in df_p.columns]

        # 5. 清理與合併
        df_i['sid'] = df_i.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df_p['sid'] = df_p.iloc[:, 0].astype(str).str.replace(r'[=" \t]', '', regex=True)
        df = pd.merge(df_i, df_p, on='sid', how='inner')

        # 6. 分級篩選
        ss, s_rank, a_rank, x_rank = [], [], [], []
        
        for _, row in df.iterrows():
            try:
                vol = pd.to_numeric(row[col_vol], errors='coerce') / 1000 # 轉張數
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                sign = str(row.get('漲跌(+/-)', ''))
                if '−' in sign or '-' in sign: diff *= -1
                chg = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                # 核心篩選：必須法人買超 > 0
                if vol > 100: # 稍微提高門檻至 100 張，過濾掉雜訊
                    name = row.iloc[1]
                    info = f"**[{row['sid']} {name}]** 均價:{price} ({'+' if chg>0 else ''}{chg}%) 法人:{int(vol)}張"
                    
                    if chg >= 7.0: ss.append((chg, f"🔥 SS級 {info}"))
                    elif chg >= 3.5: s_rank.append((chg, f"💎 S級 {info}"))
                    elif chg >= 1.0: a_rank.append((chg, f"📈 A級 {info}"))
                    elif -3.0 <= chg <= 0: x_rank.append((chg, f"🔍 X級起漲 {info}"))
            except: continue

        # 7. 排序並輸出
        ss.sort(key=lambda x: x[0], reverse=True)
        s_rank.sort(key=lambda x: x[0], reverse=True)
        a_rank.sort(key=lambda x: x[0], reverse=True)
        x_rank.sort(key=lambda x: x[0], reverse=True)

        hot_list = [v[1] for v in (ss + s_rank + a_rank)]
        potential_list = [v[1] for v in x_rank]

        # 8. 構建 Embed 訊息
        payload = {
            "embeds": [{
                "title": f"📈 川投顧大紅盤戰報 ({date_str})",
                "description": f"今日大盤大漲，系統已匹配 {len(df)} 檔標的。",
                "color": 15158332,
                "fields": [
                    {"name": "🚀 強勢噴發區 (SS/S/A)", "value": "\n".join(hot_list[:15]) or "今日無符合標的", "inline": False},
                    {"name": "🔍 潛在起漲區 (X)", "value": "\n".join(potential_list[:10]) or "今日無符合標的", "inline": False}
                ],
                "footer": {"text": f"使用欄位：{col_vol} | 資料來源：TWSE"}
            }]
        }
        requests.post(WEBHOOK_URL, json=payload, timeout=10)

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"🚨 系統解析異常: {str(e)}"})

if __name__ == "__main__":
    run_analysis()
