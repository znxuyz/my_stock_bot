import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 自動回溯日期邏輯 (確保抓到有開盤的日子)
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers)
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains('^[0-9A-Z]', na=False)].copy()
        
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers)
        start_idx = p_res.text.find('"證券代號"')
        df_p = pd.read_csv(io.StringIO(p_res.text[start_idx:]), thousands=',').dropna(thresh=5)

        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])

        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        results = []
        for _, row in df.iterrows():
            try:
                name = row.iloc[1]
                # 合計買賣超通常在第 19 欄 (索引 18)
                vol = pd.to_numeric(row.iloc[18], errors='coerce') / 1000
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                sign = str(row['漲跌(+/-)'])
                
                if pd.isna(price) or pd.isna(diff): continue
                if '−' in sign or '-' in sign: diff *= -1
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                tag = ""
                # --- 門檻大放水 ---
                if change >= 7.0 and vol > 0:
                    tag = "🔥【SS 級：噴發神股】"
                elif change >= 3.5 and vol > 0:
                    tag = "💎【S 級：強勢標的】"
                elif change >= 1.0 and vol > 0:
                    tag = "📈【A 級：技術轉強】"
                elif vol > 0: # 只要法人買超過 0 張，通通列為 X
                    tag = "🔍【X 級：潛在動能】"
                
                if tag:
                    results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        # 排序：SS/S/A 優先，按漲幅排
        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:25]] # 顯示數量增加到 25 檔

        if final_list:
            content = f"☀️ **【川投顧：{date_str} 究極放水報告】**\n目前匹配 {len(df)} 檔，連 X 級（買超 > 0）都挖出來了：\n\n" + "\n\n".join(final_list)
        else:
            content = f"📅 {date_str} 市場真的死透了，連一檔買超大於 0 的都找不到。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統崩潰中：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
