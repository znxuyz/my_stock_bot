import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def clean_sid(series):
    """徹底清除代號中的所有雜質：等號、引號、空格"""
    return series.astype(str).str.replace(r'[=" \t]', '', regex=True).str.strip()

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 取得最後交易日
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        # 2. 抓取法人 (T86)
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers=headers)
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains('^[0-9A-Z]', na=False)].copy() # 只留有代號的
        
        # 3. 抓取行情 (MI_INDEX)
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers=headers)
        p_text = p_res.text
        start_idx = p_text.find('"證券代號"')
        if start_idx == -1: return
        df_p = pd.read_csv(io.StringIO(p_text[start_idx:]), thousands=',').dropna(thresh=5)

        # 4. 關鍵清理：強制對齊代號
        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])

        # 5. 資料合併 (使用 merge 避免索引錯誤)
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        results = []
        for _, row in df.iterrows():
            try:
                # 取得必要數據
                name = row['證券名稱_x'] if '證券名稱_x' in row else row.iloc[1]
                vol = pd.to_numeric(row.iloc[18], errors='coerce') / 1000 # 合計買賣超通常在第19欄
                price = pd.to_numeric(row['收盤價'], errors='coerce')
                diff = pd.to_numeric(row['漲跌價'], errors='coerce')
                sign = str(row['漲跌(+/-)'])
                
                if pd.isna(vol) or vol <= 0 or pd.isna(price) or pd.isna(diff): continue
                if '−' in sign or '-' in sign: diff *= -1
                
                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0

                # 判定分級
                tag = ""
                if change >= 7.0: tag = "🔥【SS 級】"
                elif change >= 3.5: tag = "💎【S 級】"
                else: tag = "📊【測試級】"
                    
                if tag:
                    results.append((change, f"{tag} **[{row['sid_clean']} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張"))
            except: continue

        # 6. 排序並輸出結果
        results.sort(key=lambda x: x[0], reverse=True)
        final_list = [r[1] for r in results[:15]]

        content = f"☀️ **【川投顧：{date_str} 戰果報告】**\n\n" + "\n\n".join(final_list) if final_list else f"📅 {date_str} 盤勢太爛，沒東西好發。"
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統又在鬧：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
