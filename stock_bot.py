import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def get_market_info(date_str):
    """獲取大盤概況，加入 timeout 防止 GitHub Actions 卡死"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 1. 大盤點數
        p_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=IND"
        p_res = requests.get(p_url, headers=headers, timeout=10) # 10秒沒回應就斷開
        if "查詢無資料" in p_res.text: return "📊 大盤數據：今日休市或尚未更新。"
        
        df_m = pd.read_csv(io.StringIO(p_res.text), skiprows=1, thousands=',')
        taiex = df_m[df_m.iloc[:, 0].str.contains('發行量加權股價指數', na=False)].iloc[0]
        idx_price = float(taiex.iloc[1])
        idx_sign = str(taiex.iloc[2])
        idx_diff = pd.to_numeric(taiex.iloc[3], errors='coerce')
        if '−' in idx_sign or '-' in idx_sign: idx_diff *= -1
        idx_change = round((idx_diff / (idx_price - idx_diff)) * 100, 2)

        # 2. 三大法人大盤買賣超
        f_url = f"https://www.twse.com.tw/fund/BFI82U?response=csv&dayDate={date_str}&type=day"
        f_res = requests.get(f_url, headers=headers, timeout=10)
        df_f = pd.read_csv(io.StringIO(f_res.text), skiprows=1, thousands=',')
        total_buy = pd.to_numeric(df_f.iloc[-1, 3], errors='coerce') / 100000000 # 轉億元
        
        return f"📊 **加權指數：{idx_price} ({'+' if idx_diff>0 else ''}{idx_diff} / {'+' if idx_change>0 else ''}{idx_change}%)**\n💰 **三大法人大盤合計：{'+' if total_buy>0 else ''}{round(total_buy, 2)} 億**"
    except:
        return "📊 大盤數據：獲取中或解析異常。"

def run_analysis():
    if not WEBHOOK_URL: return
    now = datetime.now() + timedelta(hours=8)
    is_morning = now.hour < 12
    target_date = now if not is_morning else now - timedelta(days=1)
    
    # 周末過濾邏輯
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")
    
    report_type = "【盤前複習】" if is_morning else "【盤後結算】"
    market_header = get_market_info(date_str)

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # 下載法人資料與個股資料，加入 timeout
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers, timeout=15)
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers, timeout=15)

        if "查詢無資料" in i_res.text:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 查無資料，系統停止運行。"})
            return

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
                # 更新 X 級邏輯：法人買超且跌幅在 0% ~ -3% (跌幅收斂潛在起漲)
                if vol > 0:
                    if change >= 7.0: tag = "🔥【SS 級】"
                    elif change >= 3.5: tag = "💎【S 級】"
                    elif change >= 1.0: tag = "📈【A 級】"
                    elif -3.0 <= change <= 0: tag = "🔍【X 級：逆勢起漲潛力】"
                
                if tag:
                    results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:20]]

        content = f"☀️ **川投顧 {report_type}**\n日期：{date_str}\n{market_header}\n{'-'*30}\n\n" + "\n\n".join(final_list)
        requests.post(WEBHOOK_URL, json={"username": "川投顧量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統炸裂：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
