import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def get_market_info(date_str):
    """抓取大盤與三大法人大盤買賣超 (RWD 版路徑)"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. 大盤點數
        p_url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=IND&response=csv"
        p_res = requests.get(p_url, headers=headers, timeout=20)
        df_m = pd.read_csv(io.StringIO(p_res.text), skiprows=1, thousands=',')
        taiex = df_m[df_m.iloc[:, 0].str.contains('發行量加權股價指數', na=False)].iloc[0]
        idx_p = float(taiex.iloc[1])
        idx_diff = pd.to_numeric(taiex.iloc[3], errors='coerce')
        if '−' in str(taiex.iloc[2]) or '-' in str(taiex.iloc[2]): idx_diff *= -1
        idx_chg = round((idx_diff / (idx_p - idx_diff)) * 100, 2)

        # 2. 三大法人大盤合計
        f_url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={date_str}&type=day&response=csv"
        f_res = requests.get(f_url, headers=headers, timeout=20)
        df_f = pd.read_csv(io.StringIO(f_res.text), skiprows=1, thousands=',')
        total_buy = pd.to_numeric(df_f.iloc[-1, 3], errors='coerce') / 100000000
        
        return {
            "title": f"📊 加權指數：{idx_p} ({'+' if idx_diff>0 else ''}{idx_diff} / {'+' if idx_chg>0 else ''}{idx_chg}%)",
            "description": f"💰 三大法人大盤合計：{'+' if total_buy>0 else ''}{round(total_buy, 2)} 億"
        }
    except:
        return {"title": "📊 大盤數據：獲取失敗", "description": "證交所資料可能尚未更新"}

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 修正時區邏輯：明確判斷早晚報
    now_tw = datetime.utcnow() + timedelta(hours=8)
    # 若在台灣時間 12:00 前執行，視為「早報」，抓前一交易日
    is_morning = now_tw.hour < 12
    target_date = now_tw - timedelta(days=1) if is_morning else now_tw
    
    # 週末自動回溯
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # 使用 RWD 現代化接口
        i_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=csv"
        p_url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=csv"
        
        i_res = requests.get(i_url, headers=headers, timeout=25)
        p_res = requests.get(p_url, headers=headers, timeout=25)

        if "查詢無資料" in i_res.text:
            return

        # 資料解析
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].astype(str).str.contains('^[0-9A-Z]', na=False)].copy()
        
        # 動態欄位定位
        col_name = df_i.columns[1]
        col_vol = [c for c in df_i.columns if '三大法人買賣超股數' in c or '合計' in c][0]

        df_p = pd.read_csv(io.StringIO(p_res.text[p_res.text.find('"證券代號"'):]), thousands=',').dropna(thresh=5)
        
        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        hot_stocks, x_stocks = [], []
        for _, row in df.iterrows():
            try:
                vol = pd.to_numeric(row[col_vol], errors='coerce') / 1000
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                if '−' in str(row['漲跌(+/-)']) or '-' in str(row['漲跌(+/-)']): diff *= -1
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                if vol > 0:
                    info = f"**[{row['sid_clean']} {row[col_name]}]** {price} ({'+' if change>0 else ''}{change}%) {int(vol)}張"
                    if change >= 7.0: hot_stocks.append((change, f"🔥 SS級 {info}"))
                    elif change >= 3.5: hot_stocks.append((change, f"💎 S級 {info}"))
                    elif change >= 1.0: hot_stocks.append((change, f"📈 A級 {info}"))
                    elif -3.0 <= change <= 0: x_stocks.append((change, f"🔍 X級 {info}"))
            except: continue

        # 準備 Discord Embed 訊息
        market = get_market_info(date_str)
        hot_stocks.sort(key=lambda x: x[0], reverse=True)
        x_stocks.sort(key=lambda x: x[0], reverse=True)

        payload = {
            "username": "川投顧量化系統",
            "embeds": [{
                "title": f"☀️ 川投顧戰報 ({'盤前複習' if is_morning else '盤後結算'})",
                "description": f"日期：{date_str}\n{market['title']}\n{market['description']}",
                "color": 15158332 if not is_morning else 3447003, # 晚報紅色, 早報藍色
                "fields": [
                    {
                        "name": "🚀 強勢噴發區 (SS/S/A)",
                        "value": "\n".join([s[1] for s in hot_stocks[:15]]) if hot_stocks else "無符合標的",
                        "inline": False
                    },
                    {
                        "name": "🔍 逆勢起漲區 (X)",
                        "value": "\n".join([s[1] for s in x_stocks[:10]]) if x_stocks else "無符合標的",
                        "inline": False
                    }
                ],
                "footer": {"text": "數據來源：臺灣證券交易所"}
            }]
        }

        requests.post(WEBHOOK_URL, json=payload, timeout=10)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_analysis()
