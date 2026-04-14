import os, requests, io, csv
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
        # 2. 下載三大法人買賣超 CSV (這個連結比 JSON 穩定十倍)
        i_url = f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL"
        i_res = requests.get(i_url, headers={'User-Agent': 'Mozilla/5.0'})
        
        # 3. 解析 CSV
        f = io.StringIO(i_res.text)
        reader = csv.reader(f)
        rows = [r for r in reader if len(r) > 10] # 只抓有內容的列
        
        if not rows:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 證交所尚未提供 CSV 資料。"})
            return

        # 4. 處理數據 (CSV 的第一行通常是標題)
        df_i = pd.DataFrame(rows[1:], columns=rows[0])
        
        # 5. 強制取得關鍵欄位 (用關鍵字找，不用索引)
        # 我們找包含「代號」、「名稱」和最後一欄「合計」
        sid_col = [c for c in df_i.columns if "代號" in c][0]
        name_col = [c for c in df_i.columns if "名稱" in c][0]
        vol_col = df_i.columns[-1] # 最後一欄一定是合計買賣超

        df_i['vol_num'] = df_i[vol_col].str.replace('"', '').str.replace(',', '').astype(float) / 1000

        # 6. 抓取行情 (一樣改用 CSV)
        p_url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999"
        p_res = requests.get(p_url, headers={'User-Agent': 'Mozilla/5.0'})
        
        # 簡單過濾行情 CSV
        p_f = io.StringIO(p_res.text)
        p_rows = [r for r in csv.reader(p_f) if len(r) >= 16] # 行情表欄位很多
        df_p = pd.DataFrame(p_rows[1:], columns=p_rows[0])
        
        p_sid_col = [c for c in df_p.columns if "代號" in c][0]
        p_close_col = [c for c in df_p.columns if "收盤價" in c][0]
        p_diff_col = [c for c in df_p.columns if "漲跌價" in c][0]
        p_sign_col = [c for c in df_p.columns if "漲跌" in c and "+/-" in c][0]

        results = []
        # 7. 進行分級分析
        for _, row in df_i.sort_values(by='vol_num', ascending=False).head(100).iterrows():
            sid, name, vol = row[sid_col].strip('=').strip('"'), row[name_col], int(row['vol_num'])
            
            match = df_p[df_p[p_sid_col].str.contains(sid)]
            if match.empty: continue
            
            p_row = match.iloc[0]
            try:
                price_str = p_row[p_close_col].replace(',', '')
                diff_str = p_row[p_diff_col].replace(',', '')
                if not price_str or not diff_str: continue
                
                price = float(price_str)
                diff = float(diff_str)
                if '−' in p_row[p_sign_col] or '-' in p_row[p_sign_col]: diff *= -1
                
                change = round((diff / (price - diff)) * 100, 2)
            except: continue

            tag = ""
            if change >= 7.0 and vol > 0: tag = "🔥【SS 級】"
            elif change >= 3.5 and vol > 0: tag = "💎【S 級】"
            elif change >= 1.0: tag = "📈【A 級】"
            
            if tag:
                results.append(f"{tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{vol} 張")

        msg = f"☀️ **【川投顧：{date_str} CSV 核心分析】**\n\n" + "\n\n".join(results[:15]) if results else "📅 今日盤面無符合標的。"
        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": msg})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 終極炸裂：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
