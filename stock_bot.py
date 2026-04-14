import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def run_analysis():
    if not WEBHOOK_URL: return
    
    now = datetime.now() + timedelta(hours=8) # 轉台灣時間
    is_morning = now.hour < 12
    
    # 如果是早上 8 點發送，要抓的是「昨天」的資料
    # 如果是下午 18 點發送，抓的是「今天」的資料
    target_date = now if not is_morning else now - timedelta(days=1)
    
    # 排除週末 (如果是週一早上，要抓上週五的資料)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    
    date_str = target_date.strftime("%Y%m%d")
    report_type = "【盤前複習：今日戰前準備】" if is_morning else "【盤後結算：今日戰報】"

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # 下載資料
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers)
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers)

        if "查詢無資料" in i_res.text:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 查無資料，可能為休市日。"})
            return

        # 資料處理 (沿用之前的究極對齊邏輯)
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
                elif vol > 0: tag = "🔍【X 級】" # 只要買超 > 0 通通出來
                
                if tag:
                    results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:20]]

        content = f"☀️ **川投顧 {report_type}**\n日期：{date_str}\n\n" + "\n\n".join(final_list)
        requests.post(WEBHOOK_URL, json={"username": "川投顧量化系統", "content": content})

    except Exception as e:
        # 如果失敗，噴出更詳細的錯誤，別只噴 (0) 了
        requests.post(WEBHOOK_URL, json={"content": f"❌ 運作異常：{type(e).__name__} - {str(e)}"})

if __name__ == "__main__":
    run_analysis()
