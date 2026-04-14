import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')
HEADERS     = {'User-Agent': 'Mozilla/5.0'}

# ── 工具 ──────────────────────────────────────────────────────────────
def clean_sid(series):
    return series.astype(str).str.replace(r'[=\" \t]', '', regex=True).str.strip()

def safe_get(url, params=None, timeout=20, retries=3, wait=15):
    """帶 timeout 與重試的 GET，全部失敗回傳 None。"""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            print(f"[逾時] {url}（第{attempt}次）")
        except requests.exceptions.RequestException as e:
            print(f"[失敗] {url}（第{attempt}次）：{e}")
        if attempt < retries:
            time.sleep(wait)
    return None

# ── 交易日判定 ────────────────────────────────────────────────────────
def get_target_date(run_mode):
    """
    run_mode = 'close'   → 當日（盤後）
    run_mode = 'preview' → 前一交易日（盤前複習）
    自動跳過週末。
    """
    # GitHub Actions 是 UTC，加 8 小時取得台灣時間
    now = datetime.utcnow() + timedelta(hours=8)
    base = now.date()

    if run_mode == 'preview':
        # 盤前複習：回溯一個交易日（週一→週五、其餘往前一天）
        delta = 3 if base.weekday() == 0 else 1
        base -= timedelta(days=delta)
    else:
        # 盤後結算：若週六/週日，回溯到週五
        if base.weekday() == 5:
            base -= timedelta(days=1)
        elif base.weekday() == 6:
            base -= timedelta(days=2)

    return base.strftime('%Y%m%d')

# ── 大盤概況（非強制）────────────────────────────────────────────────
def get_market_info(date_str):
    """
    抓加權指數。失敗或查無資料回傳空字串，不阻斷主流程。
    使用新版 /rwd/zh/ 路徑。
    """
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'IND'},
        timeout=20, retries=2, wait=10
    )
    if r is None or '查詢無資料' in r.text:
        return ''
    try:
        df = pd.read_csv(io.StringIO(r.text), skiprows=1, thousands=',')
        row = df[df.iloc[:, 0].str.contains('發行量加權股價指數', na=False)].iloc[0]
        idx_p    = float(str(row.iloc[1]).replace(',', ''))
        idx_diff = pd.to_numeric(str(row.iloc[3]).replace(',', ''), errors='coerce')
        # 證交所用全形減號「−」或一般「-」表示下跌
        if '−' in str(row.iloc[2]) or str(row.iloc[2]).strip().startswith('-'):
            idx_diff = abs(idx_diff) * -1
        idx_chg = round((idx_diff / (idx_p - idx_diff)) * 100, 2) if (idx_p - idx_diff) != 0 else 0
        sign = '+' if idx_diff >= 0 else ''
        return f"📊 **加權指數：{idx_p:,.2f}　({sign}{idx_diff} / {sign}{idx_chg}%)**\n"
    except Exception as e:
        print(f"[大盤解析失敗] {e}")
        return ''

# ── 主分析 ────────────────────────────────────────────────────────────
def run_analysis():
    if not WEBHOOK_URL:
        print('[錯誤] 未設定 DISCORD_WEBHOOK 環境變數')
        return

    # 由環境變數決定模式，預設 close（盤後）
    run_mode    = os.environ.get('RUN_MODE', 'close').strip().lower()
    date_str    = get_target_date(run_mode)
    report_type = '【盤前複習】' if run_mode == 'preview' else '【盤後結算】'
    print(f"[執行] 模式={run_mode}，日期={date_str}")

    # 大盤（非強制，失敗不擋個股）
    market_header = get_market_info(date_str)

    # ── 個股法人 T86（新版路徑）──
    r_inst = safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )
    # ── 個股行情 MI_INDEX（新版路徑）──
    r_price = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )

    if r_inst is None or r_price is None:
        requests.post(WEBHOOK_URL, json={'content': f'❌ 無法取得個股資料（{date_str}），請稍後重試。'}, timeout=15)
        return
    if '查詢無資料' in r_inst.text or '查詢無資料' in r_price.text:
        requests.post(WEBHOOK_URL, json={'content': f'ℹ️ {date_str} 查無資料（可能為假日或資料未更新）。'}, timeout=15)
        return

    try:
        # ── 解析法人 T86 ──
        df_i = pd.read_csv(io.StringIO(r_inst.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains(r'^[0-9A-Z]', na=False)].copy()

        # 找「三大法人合計買賣超」欄（名稱可能有微小差異）
        inst_col = next(
            (c for c in df_i.columns if '合計' in str(c) and ('買賣超' in str(c) or '買賣差' in str(c))),
            None
        )
        if inst_col is None:
            # 備援：加總外資＋投信＋自營商三欄
            candidate_cols = [c for c in df_i.columns if any(k in str(c) for k in ['外資', '投信', '自營商']) and '買賣超' in str(c)]
            if candidate_cols:
                df_i['_net'] = df_i[candidate_cols].apply(pd.to_numeric, errors='coerce').sum(axis=1)
                inst_col = '_net'
            else:
                raise ValueError(f"找不到法人買賣超欄位，現有欄位：{list(df_i.columns)}")

        df_i['inst_shares'] = pd.to_numeric(df_i[inst_col], errors='coerce').fillna(0)

        # ── 解析行情 MI_INDEX ──
        price_text = r_price.text
        start_idx  = price_text.find('"證券代號"')
        if start_idx == -1:
            raise ValueError('MI_INDEX 找不到表頭「證券代號」')
        df_p = pd.read_csv(io.StringIO(price_text[start_idx:]), thousands=',').dropna(thresh=5)

        # ── 清洗代號並合併 ──
        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])
        df_p['sid_clean'] = clean_sid(df_p['證券代號'])
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        # ── 找漲跌相關欄位（名稱略有版本差異）──
        col_close = next((c for c in df.columns if '收盤' in str(c)), None)
        col_diff  = next((c for c in df.columns if '漲跌價差' in str(c) or ('漲跌' in str(c) and '差' in str(c))), None)
        col_sign  = next((c for c in df.columns if '漲跌(+/-)' in str(c) or '漲跌符號' in str(c)), None)

        if not all([col_close, col_diff]):
            raise ValueError(f"找不到收盤/漲跌欄位，現有：{list(df.columns)}")

        hot_stocks, x_stocks = [], []

        for _, row in df.iterrows():
            try:
                name   = str(row.get('證券名稱', row.iloc[1])).strip()
                price  = pd.to_numeric(str(row[col_close]).replace(',', ''), errors='coerce')
                diff   = pd.to_numeric(str(row[col_diff]).replace(',', ''), errors='coerce')
                inst   = float(row['inst_shares']) / 1000  # 股 → 張

                if pd.isna(price) or pd.isna(diff) or price == 0:
                    continue

                # 判斷漲跌正負（優先看符號欄，否則看數值本身）
                if col_sign:
                    sign_str = str(row[col_sign])
                    if '−' in sign_str or sign_str.strip() == '-':
                        diff = -abs(diff)
                    else:
                        diff = abs(diff)

                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0.0
                info   = f"**[{row['sid_clean']} {name}]** 價格:{price} ({'+' if change>=0 else ''}{change}%) 法人:{'+' if inst>=0 else ''}{int(inst)}張"

                if inst > 0:  # 核心條件
                    if   change >= 7.0:              hot_stocks.append((change, f"🔥【SS 級】 {info}"))
                    elif change >= 3.5:              hot_stocks.append((change, f"💎【S 級】 {info}"))
                    elif change >= 1.0:              hot_stocks.append((change, f"📈【A 級】 {info}"))
                    elif -3.0 <= change < 0:         x_stocks.append((change,   f"🔍【X 級：逆勢起漲】 {info}"))
            except:
                continue

        hot_stocks.sort(key=lambda x: x[0], reverse=True)
        x_stocks.sort(key=lambda x: x[0], reverse=True)  # 越接近平盤排越前

        lines = [s[1] for s in hot_stocks[:15]]
        if x_stocks:
            lines += ['─── 潛在起漲區（小跌+法人逆勢買）───']
            lines += [s[1] for s in x_stocks[:10]]
        if not lines:
            lines = ['（今日無符合條件標的）']

        content = (
            f"☀️ **川投顧 {report_type}**\n"
            f"日期：{date_str}\n"
            f"{market_header}"
            f"{'='*25}\n\n"
            + '\n'.join(lines)
        )

        # Discord 單則上限 2000 字，超過就分段送
        chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
        for chunk in chunks:
            requests.post(WEBHOOK_URL, json={'username': '川投顧量化系統', 'content': chunk}, timeout=15)
            if len(chunks) > 1:
                time.sleep(1)

    except Exception as e:
        print(f"[主程式錯誤] {e}")
        requests.post(WEBHOOK_URL, json={'content': f'❌ 系統錯誤：{e}'}, timeout=15)

if __name__ == '__main__':
    run_analysis()
