import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def get_market_info(date_str):
    """獲取大盤加權指數與三大法人大盤買賣超"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. 大盤點數 (MI_INDEX)
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=IND", headers=headers)
        df_m = pd.read_csv(io.StringIO(p_res.text), skiprows=1, thousands=',')
        # 尋找「發行量加權股價指數」
        taiex = df_m[df_m.iloc[:, 0] == '發行量加權股價指數'].iloc[0]
        idx_price = taiex.iloc[1]
        idx_sign = str(taiex.iloc[2])
        idx_diff = pd.to_numeric(taiex.iloc[3], errors='coerce')
        if '−' in idx_sign or '-' in idx_sign: idx_diff *= -1
        idx_change = round((idx_diff / (idx_price - idx_diff)) * 100, 2)

        # 2. 三大法人大盤買賣超金額 (BFI82U)
        f_res = requests.get(f"https://www.twse.com.tw/fund/BFI82U?response=csv&dayDate={date_str}&type=day", headers=headers)
        df_f = pd.read_csv(io.StringIO(f_res.text), skiprows=1, thousands=',')
        total_buy = pd.to_numeric(df_f.iloc[-1, 3], errors='coerce') / 100000000 # 轉億元
        
        return f"📊 **加權指數：{idx_price} ({'+' if idx_diff>0 else ''}{idx_diff} / {'+' if idx_change>0 else ''}{idx_change}%)**\n💰 **三大法人大盤合計：{'+' if total_buy>0 else ''}{round(total_buy, 2)} 億**"
    except:
        return "📊 大盤數據：證交所尚未更新或解析失敗。"

def run_analysis():
    if not WEBHOOK_URL: return
    now = datetime.now() + timedelta(hours=8)
    is_morning = now.hour < 12
    target_date = now if not is_morning else now - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")
    
    report_type = "【盤前複習】" if is_morning else "【盤後結算】"
    market_header = get_market_info(date_str)

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers)
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers)

        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains('^[0-9A-Z]', na=False)].copy()
        start_idx = p_res.text.find('"證券代號"')
        df_p = pd.read_csv(io.StringIO(p_res.text[start_idx:]), thousands=',').dropna(thresh=5)

        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        results = []
        for _, row in df.iterrows():
            try:
                name = row.iloc[1]
                vol = pd.to_numeric(row.iloc[18], errors='coerce') / 1000
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                sign = str(row['漲跌(+/-)'])
                if '−' in sign or '-' in sign: diff *= -1
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                tag = ""
                if change >= 7.0 and vol > 0: tag = "🔥【SS 級】"
                elif change >= 3.5 and vol > 0: tag = "💎【S 級】"
                elif change >= 1.0 and vol > 0: tag = "📈【A 級】"
                elif vol > 0: tag = "🔍【X 級】"
                
                if tag:
                    results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:20]]

        content = f"☀️ **川投顧 {report_type}**\n日期：{date_str}\n{market_header}\n{'-'*30}\n\n" + "\n\n".join(final_list)
        requests.post(WEBHOOK_URL, json={"username": "川投顧量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 運作異常：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
