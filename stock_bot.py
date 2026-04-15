import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')
HEADERS     = {'User-Agent': 'Mozilla/5.0'}

# ══════════════════════════════════════════════════════════
# 篩選參數（短線 1~3 週策略）── 只改這裡就能調整條件
# ══════════════════════════════════════════════════════════
MIN_PRICE        = 10      # 收盤價下限（元）
MIN_INST_SHARE   = 50000   # 法人合計買超最低股數（50張 = 50,000股）
VOLUME_RATIO_MIN = 1.5     # 當日量 ÷ 近5日均量

EMA_SHORT = 20
EMA_MID   = 60
EMA_LONG  = 120

GRADE_SS   =  7.0
GRADE_S    =  3.5
GRADE_A    =  1.0
GRADE_X_LO = -3.0
GRADE_X_HI =  0.0

# ══════════════════════════════════════════════════════════
# 工具函式
# ══════════════════════════════════════════════════════════
def clean_sid(series):
    return series.astype(str).str.replace(r'[=\" \t]', '', regex=True).str.strip()

def safe_get(url, params=None, timeout=20, retries=3, wait=15):
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

def safe_read_csv(text, label, skiprows=0, thousands=',', min_cols=2):
    """
    安全解析 CSV，失敗時印出前 500 字供 debug，回傳空 DataFrame。
    """
    try:
        df = pd.read_csv(
            io.StringIO(text),
            skiprows=skiprows,
            thousands=thousands,
            on_bad_lines='skip'
        )
        if df.shape[1] < min_cols:
            print(f"[{label}] 欄位數不足（{df.shape[1]}），前500字：\n{text[:500]}")
            return pd.DataFrame()
        return df
    except Exception as e:
        print(f"[{label}] CSV 解析失敗：{e}\n前500字：\n{text[:500]}")
        return pd.DataFrame()

def find_col(df, *keywords):
    for c in df.columns:
        if all(k in str(c) for k in keywords):
            return c
    return None

def fmt_share(n):
    sign = '+' if n >= 0 else ''
    return f"{sign}{int(n):,}"

# ══════════════════════════════════════════════════════════
# 交易日判定
# ══════════════════════════════════════════════════════════
def get_target_date(run_mode):
    now  = datetime.utcnow() + timedelta(hours=8)
    base = now.date()
    if run_mode == 'preview':
        delta = 3 if base.weekday() == 0 else 1
        base -= timedelta(days=delta)
    else:
        if   base.weekday() == 5: base -= timedelta(days=1)
        elif base.weekday() == 6: base -= timedelta(days=2)
    return base.strftime('%Y%m%d')

# ══════════════════════════════════════════════════════════
# 大盤概況（非強制）
# ══════════════════════════════════════════════════════════
def get_market_info(date_str):
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'IND'},
        timeout=20, retries=2, wait=10
    )
    if r is None or '查詢無資料' in r.text:
        return None
    try:
        df = safe_read_csv(r.text, 'MI_INDEX-IND', skiprows=1)
        if df.empty:
            return None
        row = df[df.iloc[:, 0].astype(str).str.contains('發行量加權股價指數', na=False)]
        if row.empty:
            print(f"[大盤] 找不到加權指數列，欄位：{list(df.columns)}")
            return None
        row = row.iloc[0]
        idx_p    = float(str(row.iloc[1]).replace(',', ''))
        idx_diff = pd.to_numeric(str(row.iloc[3]).replace(',', ''), errors='coerce')
        if '−' in str(row.iloc[2]) or str(row.iloc[2]).strip().startswith('-'):
            idx_diff = -abs(idx_diff)
        else:
            idx_diff =  abs(idx_diff)
        idx_chg = round((idx_diff / (idx_p - idx_diff)) * 100, 2) if (idx_p - idx_diff) != 0 else 0
        return {'close': idx_p, 'diff': idx_diff, 'pct': idx_chg}
    except Exception as e:
        print(f"[大盤解析失敗] {e}")
        return None

# ══════════════════════════════════════════════════════════
# 歷史 K 棒 → EMA + 量比
# ══════════════════════════════════════════════════════════
def fetch_monthly_ohlcv(sid, yyyymm):
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=5
    )
    if r is None or '查詢無資料' in r.text:
        return pd.DataFrame()
    try:
        text = r.text
        # 找表頭（民國年日期格式 或 "日期" 關鍵字）
        idx = text.find('"日期"')
        if idx == -1:
            # 備援：找民國年格式的第一列
            for yr_prefix in ['114/', '113/', '112/']:
                idx = text.find(yr_prefix)
                if idx != -1:
                    idx = text.rfind('\n', 0, idx) + 1
                    break
        if idx == -1:
            print(f"[月K] {sid}/{yyyymm} 找不到資料起始點，前300字：\n{text[:300]}")
            return pd.DataFrame()

        df = safe_read_csv(text[idx:], f'STOCK_DAY-{sid}', min_cols=7)
        if df.empty:
            return pd.DataFrame()

        # 過濾出民國年格式的列
        df = df[df.iloc[:, 0].astype(str).str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)].copy()
        if df.empty:
            return pd.DataFrame()

        def roc_to_date(s):
            y, m, d = str(s).split('/')
            return datetime(int(y) + 1911, int(m), int(d)).date()

        df['date']   = df.iloc[:, 0].apply(roc_to_date)
        df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',', ''), errors='coerce')
        df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',', ''), errors='coerce')
        return df[['date', 'close', 'volume']].dropna().reset_index(drop=True)
    except Exception as e:
        print(f"[月K解析失敗] {sid}/{yyyymm}: {e}")
        return pd.DataFrame()

def build_history(sid, target_date_str):
    target = datetime.strptime(target_date_str, '%Y%m%d').date()
    frames = []
    d = target.replace(day=1)
    for _ in range(7):
        yyyymm = d.strftime('%Y%m')
        df_m = fetch_monthly_ohlcv(sid, yyyymm)
        if not df_m.empty:
            frames.append(df_m)
        d = (d - timedelta(days=1)).replace(day=1)
        time.sleep(0.15)
    if not frames:
        return pd.DataFrame()
    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    return df_all[df_all['date'] <= target].reset_index(drop=True)

def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def check_ema_bull(df):
    if len(df) < EMA_LONG:
        return False
    closes = df['close'].astype(float)
    ema20  = calc_ema(closes, EMA_SHORT).iloc[-1]
    ema60  = calc_ema(closes, EMA_MID).iloc[-1]
    ema120 = calc_ema(closes, EMA_LONG).iloc[-1]
    return ema20 > ema60 > ema120

def calc_volume_ratio(df):
    if len(df) < 6:
        return 0.0
    today_vol = df['volume'].iloc[-1]
    avg5      = df['volume'].iloc[-6:-1].mean()
    return round(today_vol / avg5, 2) if avg5 > 0 else 0.0

# ══════════════════════════════════════════════════════════
# 主分析
# ══════════════════════════════════════════════════════════
def run_analysis():
    if not WEBHOOK_URL:
        print('[錯誤] 未設定 DISCORD_WEBHOOK 環境變數')
        return

    run_mode    = os.environ.get('RUN_MODE', 'close').strip().lower()
    date_str    = get_target_date(run_mode)
    report_type = '盤前複習' if run_mode == 'preview' else '盤後結算'
    print(f"[執行] 模式={run_mode}，日期={date_str}")

    market = get_market_info(date_str)

    # ── T86 法人 ──
    r_inst = safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )
    # ── MI_INDEX 行情 ──
    r_price = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )

    if r_inst is None or r_price is None:
        requests.post(WEBHOOK_URL, json={'content': f'❌ 無法取得個股資料（{date_str}），請稍後重試。'}, timeout=15)
        return
    if '查詢無資料' in r_inst.text or '查詢無資料' in r_price.text:
        requests.post(WEBHOOK_URL, json={'content': f'ℹ️ {date_str} 查無資料（可能為假日或尚未更新）。'}, timeout=15)
        return

    try:
        # ── 解析 T86 ──
        print(f"[T86] 回應前300字：\n{r_inst.text[:300]}\n")
        df_i = safe_read_csv(r_inst.text, 'T86', skiprows=1)
        if df_i.empty:
            requests.post(WEBHOOK_URL, json={'content': f'❌ T86 解析失敗（{date_str}），請查看 Actions log。'}, timeout=15)
            return

        df_i = df_i[df_i.iloc[:, 0].astype(str).str.contains(r'^[0-9A-Z]', na=False)].copy()
        print(f"[T86] 有效列數：{len(df_i)}，欄位：{list(df_i.columns[:8])}")

        col_foreign = find_col(df_i, '外資', '買賣超')
        col_trust   = find_col(df_i, '投信', '買賣超')
        col_dealer  = find_col(df_i, '自營商', '買賣超')
        col_total   = find_col(df_i, '合計', '買賣超')
        print(f"[T86] 欄位對應 外資={col_foreign} 投信={col_trust} 自營={col_dealer} 合計={col_total}")

        if not col_total:
            avail = [c for c in [col_foreign, col_trust, col_dealer] if c]
            if avail:
                df_i['_total'] = df_i[avail].apply(pd.to_numeric, errors='coerce').sum(axis=1)
                col_total = '_total'
            else:
                raise ValueError(f"找不到任何法人欄位，現有：{list(df_i.columns)}")

        for col in [col_foreign, col_trust, col_dealer, col_total]:
            if col:
                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0)

        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])

        # ── 解析 MI_INDEX ──
        print(f"[MI_INDEX] 回應前300字：\n{r_price.text[:300]}\n")
        price_text = r_price.text
        start_idx  = price_text.find('"證券代號"')
        if start_idx == -1:
            start_idx = price_text.find('證券代號')
        if start_idx == -1:
            requests.post(WEBHOOK_URL, json={
                'content': f'❌ MI_INDEX 找不到表頭（{date_str}）\n前300字：{price_text[:300]}'
            }, timeout=15)
            return

        df_p = safe_read_csv(price_text[start_idx:], 'MI_INDEX-PRICE', min_cols=5)
        if df_p.empty:
            requests.post(WEBHOOK_URL, json={'content': f'❌ MI_INDEX 行情解析失敗（{date_str}）。'}, timeout=15)
            return

        df_p = df_p.dropna(thresh=5)
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])
        print(f"[MI_INDEX] 有效列數：{len(df_p)}，欄位：{list(df_p.columns[:8])}")

        # ── 合併 ──
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')
        print(f"[合併] 共 {len(df)} 檔")

        col_close = next((c for c in df.columns if '收盤' in str(c)), None)
        col_diff  = next((c for c in df.columns if '漲跌價差' in str(c) or
                         ('漲跌' in str(c) and '差' in str(c))), None)
        col_sign  = next((c for c in df.columns if '漲跌(+/-)' in str(c) or
                         '漲跌符號' in str(c)), None)
        print(f"[欄位] 收盤={col_close} 漲跌差={col_diff} 符號={col_sign}")

        if not all([col_close, col_diff]):
            raise ValueError(f"找不到收盤/漲跌欄，現有欄位：{list(df.columns)}")

        # ── 第一輪：基本條件過濾 ──
        candidates = []
        for _, row in df.iterrows():
            try:
                sid   = row['sid_clean']
                name  = str(row.get('證券名稱', row.iloc[1])).strip()
                price = pd.to_numeric(str(row[col_close]).replace(',', ''), errors='coerce')
                diff  = pd.to_numeric(str(row[col_diff]).replace(',', ''),  errors='coerce')

                if pd.isna(price) or pd.isna(diff) or price < MIN_PRICE:
                    continue

                if col_sign:
                    sign_str = str(row[col_sign])
                    diff = -abs(diff) if ('−' in sign_str or sign_str.strip() == '-') else abs(diff)

                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0.0

                if not (change >= GRADE_A or (GRADE_X_LO <= change < GRADE_X_HI)):
                    continue

                inst_row = df_i[df_i['sid_clean'] == sid]
                if inst_row.empty:
                    continue

                foreign = float(inst_row[col_foreign].values[0]) if col_foreign else 0.0
                trust   = float(inst_row[col_trust].values[0])   if col_trust   else 0.0
                dealer  = float(inst_row[col_dealer].values[0])  if col_dealer  else 0.0
                total   = foreign + trust + dealer

                if total < MIN_INST_SHARE:
                    continue

                candidates.append({
                    'sid': sid, 'name': name,
                    'price': price, 'change': change,
                    'foreign': int(foreign), 'trust': int(trust),
                    'dealer':  int(dealer),  'total': int(total),
                })
            except:
                continue

        print(f"[過濾] 基本條件通過：{len(candidates)} 檔，開始抓歷史資料...")

        # ── 第二輪：EMA + 量比 ──
        ss_list, s_list, a_list, x_list = [], [], [], []

        for entry in candidates:
            sid = entry['sid']
            try:
                df_hist = build_history(sid, date_str)
                if df_hist.empty:
                    print(f"  [跳過] {sid} 歷史資料空白")
                    continue
                if not check_ema_bull(df_hist):
                    print(f"  [濾除] {sid} EMA 非多頭排列")
                    continue
                vol_ratio = calc_volume_ratio(df_hist)
                if vol_ratio < VOLUME_RATIO_MIN:
                    print(f"  [濾除] {sid} 量比 {vol_ratio:.2f} < {VOLUME_RATIO_MIN}")
                    continue

                entry['vol_ratio'] = vol_ratio
                change = entry['change']

                if   change >= GRADE_SS:                   ss_list.append(entry)
                elif change >= GRADE_S:                    s_list.append(entry)
                elif change >= GRADE_A:                    a_list.append(entry)
                elif GRADE_X_LO <= change < GRADE_X_HI:   x_list.append(entry)

                print(f"  [入選] {sid} {entry['name']} 漲{change}% 量比{vol_ratio:.2f}")
            except Exception as e:
                print(f"  [錯誤] {sid}: {e}")
                continue

        for lst in [ss_list, s_list, a_list]:
            lst.sort(key=lambda e: e['change'], reverse=True)
        x_list.sort(key=lambda e: e['total'], reverse=True)
        print(f"[結果] SS={len(ss_list)} S={len(s_list)} A={len(a_list)} X={len(x_list)}")

        # ── 組裝訊息 ──
        def stock_block(e, emoji_open, grade_label, emoji_close):
            sign = '+' if e['change'] >= 0 else ''
            return (
                f"{emoji_open}【{grade_label}】{e['sid']} {e['name']}{emoji_close}\n"
                f"🔹收盤價格:{e['price']} ({sign}{e['change']}%)　量比:{e.get('vol_ratio', 0):.1f}x\n"
                f"🔹外資:{fmt_share(e['foreign'])}股　投信:{fmt_share(e['trust'])}股　自營商:{fmt_share(e['dealer'])}股"
            )

        if market:
            sign_d = '+' if market['diff'] >= 0 else ''
            sign_p = '+' if market['pct']  >= 0 else ''
            mkt_line = f"加權指數：{market['close']:,.2f}　({sign_d}{market['diff']:.2f} / {sign_p}{market['pct']:.2f}%)"
        else:
            mkt_line = "加權指數：資料未取得"

        header = (
            f"🔶【{report_type}】🔶\n"
            f"日期：{date_str}\n"
            f"{mkt_line}\n\n"
            f"{'='*25}"
        )

        sections = []
        if ss_list:
            sections += [stock_block(e, '🔥', 'SS 級', '🔥') for e in ss_list[:10]]
        if s_list:
            sections += [stock_block(e, '💎', 'S 級',  '💎') for e in  s_list[:10]]
        if a_list:
            sections += [stock_block(e, '📈', 'A 級',  '📈') for e in  a_list[:10]]
        if x_list:
            sections.append('─── 🔍 潛在起漲區（小跌+法人逆勢買）───')
            sections += [stock_block(e, '🔍', 'X 級', '🔍') for e in  x_list[:10]]
        if not sections:
            sections.append('（今日無符合所有條件之標的）')

        full_message = header + '\n\n' + '\n\n'.join(sections)

        # 分段發送
        chunks, buf = [], ''
        for line in full_message.splitlines(keepends=True):
            if len(buf) + len(line) > 1900 and buf:
                chunks.append(buf)
                buf = ''
            buf += line
        if buf:
            chunks.append(buf)

        for i, chunk in enumerate(chunks):
            requests.post(
                WEBHOOK_URL,
                json={'username': '川投顧量化系統', 'content': chunk},
                timeout=15
            )
            if i < len(chunks) - 1:
                time.sleep(1)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[主程式錯誤]\n{tb}")
        requests.post(WEBHOOK_URL, json={'content': f'❌ 系統錯誤：{e}'}, timeout=15)

if __name__ == '__main__':
    run_analysis()
