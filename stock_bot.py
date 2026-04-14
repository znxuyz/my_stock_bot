import os, requests, io
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 取得前一交易日
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
        
        # 2. 抓取法人買賣超 (T86) - 使用 pandas 直接解析 CSV
        i_url = f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL"
        i_res = requests.get(i_url, headers=headers, timeout=30)
        
        # 證交所 CSV 前幾行是說明，要跳過 (skiprows=1)，並處理引號
        df_i = pd.read_csv(io.StringIO(i_res.text), skiprows=1, thousands=',').dropna(thresh=10)
        
        # 3. 抓取行情 (MI_INDEX)
        p_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999"
        p_res = requests.get(p_url, headers=headers, timeout=30)
        
        # 行情表雜訊更多，我們尋找包含「證券代號」的那一行作為起點
        p_text = p_res.text
        start_idx = p_text.find('"證券代號"')
        if start_idx == -1:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 資料來源異常，請稍後再試。"})
            return
            
        df_p = pd.read_csv(io.StringIO(p_text[start_idx:]), thousands=',').dropna(thresh=10)

        # 4. 數據清洗：移除代號中的 = 和 "
        df_i.iloc[:, 0] = df_i.iloc[:, 0].astype(str).str.replace('=', '').str.replace('"', '').str.strip()
        df_p.iloc[:, 0] = df_p.iloc[:, 0].astype(str).str.replace('=', '').str.replace('"', '').str.strip()

        results = []
        # 5. 合併與分級
        for _, row in df_i.iterrows():
            sid = row[0]
            name = row[1]
            # 最後一欄通常是合計買賣超
            vol = pd.to_numeric(str(row.iloc[-1]).replace(',', ''), errors='coerce') / 1000
            
            if pd.isna(vol) or vol <= 0: continue
            
            # 在行情表中找對應
            match = df_p[df_p.iloc[:, 0] == sid]
            if match.empty: continue
            
            p_row = match.iloc[0]
            try:
                price = pd.to_numeric(str(p_row['收盤價']).replace(',', ''), errors='coerce')
                diff = pd.to_numeric(str(p_row['漲跌價']).replace(',', ''), errors='coerce')
                sign = str(p_row['漲跌(+/-)'])
                
                if pd.isna(price) or pd.isna(diff): continue
                if '−' in sign or '-' in sign: diff *= -1
                
                prev = price - diff
                change = round((diff / prev) * 100, 2) if prev != 0 else 0
            except: continue

            # --- SS/S/A 三等級標準 ---
            tag = ""
            if change >= 7.0: tag = "🔥【SS 級】"
            elif change >= 3.5: tag = "💎【S 級】"
            elif change >= 1.0: tag = "📈【A 級】"
            
            if tag:
                results.append(f"{tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張")

        # 6. 送出 Discord
        if results:
            content = f"☀️ **【川投顧：{date_str} 三等級全自動分析】**\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {date_str} 盤勢太悶，法人買超股皆未達標。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 數據解析失敗：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
