import os, requests, io, csv
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')

def safe_float(val):
    """安全轉換浮點數，遇到空值或亂碼回傳 0.0"""
    try:
        if not val or not str(val).strip(): return 0.0
        return float(str(val).replace(',', '').replace('"', '').strip())
    except:
        return 0.0

def run_analysis():
    if not WEBHOOK_URL: return
    
    # 1. 取得前一交易日 (2026-04-14 下午會抓 04-13 的資料)
    target_date = datetime.now() - timedelta(days=1)
    if target_date.weekday() == 5: target_date -= timedelta(days=1)
    elif target_date.weekday() == 6: target_date -= timedelta(days=2)
    date_str = target_date.strftime("%Y%m%d")

    try:
        # 2. 抓取法人資料 CSV
        i_res = requests.get(f"https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL", headers={'User-Agent': 'Mozilla/5.0'})
        i_f = io.StringIO(i_res.text)
        i_rows = [r for r in csv.reader(i_f) if len(r) > 10]
        
        if not i_rows:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 法人 CSV 尚未備妥。"})
            return

        df_i = pd.DataFrame(i_rows[1:], columns=i_rows[0])
        vol_col = df_i.columns[-1] # 最後一欄：合計買賣超

        # 3. 抓取行情資料 CSV
        p_res = requests.get(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date={date_str}&type=ALLBUT0999", headers={'User-Agent': 'Mozilla/5.0'})
        p_f = io.StringIO(p_res.text)
        p_rows = [r for r in csv.reader(p_f) if len(r) >= 15]
        
        if not p_rows:
            requests.post(WEBHOOK_URL, json={"content": f"📅 {date_str} 行情 CSV 尚未備妥。"})
            return

        df_p = pd.DataFrame(p_rows[1:], columns=p_rows[0])
        
        # 定義行情表關鍵欄位
        p_sid_col = [c for c in df_p.columns if "代號" in c][0]
        p_close_col = [c for c in df_p.columns if "收盤價" in c][0]
        p_diff_col = [c for c in df_p.columns if "漲跌價" in c][0]
        p_sign_col = [c for c in df_p.columns if "漲跌" in c and "+/-" in c][0]

        results = []
        # 4. 分析邏輯
        for _, row in df_i.iterrows():
            sid = row[0].strip('=').strip('"')
            name = row[1]
            vol = safe_float(row[vol_col]) / 1000
            
            if vol <= 0: continue # 只看買超
            
            match = df_p[df_p[p_sid_col].str.contains(sid)]
            if match.empty: continue
            
            p_row = match.iloc[0]
            price = safe_float(p_row[p_close_col])
            diff = safe_float(p_row[p_diff_col])
            
            if price == 0: continue
            
            # 判斷正負號
            if '−' in p_row[p_sign_col] or '-' in p_row[p_sign_col]:
                diff *= -1
            
            # 計算漲幅
            prev = price - diff
            change = round((diff / prev) * 100, 2) if prev != 0 else 0.0

            # --- 川投顧三等級標準 ---
            tag = ""
            if change >= 7.0: tag = "🔥【SS 級】"
            elif change >= 3.5: tag = "💎【S 級】"
            elif change >= 1.0: tag = "📈【A 級】"
            
            if tag:
                results.append(f"{tag} **[{sid} {name}]**\n價格：{price} ({'+' if change>0 else ''}{change}%)\n法人：{int(vol)} 張")

        # 5. 排序並送出 (按漲幅排，選前 15 名)
        if results:
            content = f"☀️ **【川投顧：{date_str} 強勢股報告】**\n\n" + "\n\n".join(results[:15])
        else:
            content = f"📅 {date_str} 沒發現符合等級的標的。"

        requests.post(WEBHOOK_URL, json={"username": "川投顧嘴砲量化系統", "content": content})

    except Exception as e:
        requests.post(WEBHOOK_URL, json={"content": f"❌ 系統修復失敗：{str(e)}"})

if __name__ == "__main__":
    run_analysis()
