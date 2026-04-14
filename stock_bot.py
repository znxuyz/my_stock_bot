import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def get_market_info(date_str):
    """嘗試獲取大盤數據，若失敗則回傳空字串，不卡死主程式"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 加權指數
        p_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=IND"
        p_res = requests.get(p_url, headers=headers, timeout=8)
        if "查詢無資料" in p_res.text: return ""
        
        df_m = pd.read_csv(io.StringIO(p_res.text), skiprows=1, thousands=',')
        taiex = df_m[df_m.iloc[:, 0].str.contains('發行量加權股價指數', na=False)].iloc[0]
        idx_p, idx_diff = float(taiex.iloc[1]), pd.to_numeric(taiex.iloc[3], errors='coerce')
        if '−' in str(taiex.iloc[2]) or '-' in str(taiex.iloc[2]): idx_diff *= -1
        idx_chg = round((idx_diff / (idx_p - idx_diff)) * 100, 2)

        # 法人合計 (這個最容易卡住，獨立處理)
        try:
            f_url = f"https://www.twse.com.tw/fund/BFI82U?response=csv&dayDate={date_str}&type=day"
            f_res = requests.get(f_url, headers=headers, timeout=8)
            df_f = pd.read_csv(io.StringIO(f_res.text), skiprows=1, thousands=',')
            total_buy = pd.to_numeric(df_f.iloc[-1, 3], errors='coerce') / 100000000
            f_str = f"\n💰 **三大法人大盤合計：{'+' if total_buy>0 else ''}{round(total_buy, 2)} 億**"
        except: f_str = ""

        return f"📊 **加權指數：{idx_p} ({'+' if idx_diff>0 else ''}{idx_diff} / {'+' if idx_chg>0 else ''}{idx_chg}%)**{f_str}\n"
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

        results = []
        for _, row in df.iterrows():
            try:
                name = row.iloc[1]
                vol = pd.to_numeric(row.iloc[18], errors='coerce') / 1000
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                if '−' in str(row['漲跌(+/-)']) or '-' in str(row['漲跌(+/-)']): diff *= -1
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                tag = ""
                if vol > 0: # 法人一定要買超
                    if change >= 7.0: tag = "🔥【SS 級】"
                    elif change >= 3.5: tag = "💎【S 級】"
                    elif change >= 1.0: tag = "📈【A 級】"
                    elif -3.0 <= change <= 0: # 修正 X 級邏輯：跌幅 0~-3% 但法人逆勢買超
                        tag = "🔍【X 級：逆勢起漲潛力】"
                
                if tag:
                    results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:25]]

        content = f"☀️ **川投顧 {report_type}**\n日期：{date_str}\n{market_header}{'-'*30}\n\n" + "\n\n".join(final_list)
        requests.post(WEBHOOK_URL, json={"username": "川投顧量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統暫時無法取得個股資料：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
