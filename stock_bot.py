import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')
HEADERS = {
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer':         'https://www.twse.com.tw/',
}

# ══════════════════════════════════════════════════════════
# 篩選參數 ── 只改這裡就能調整條件
# ══════════════════════════════════════════════════════════
MIN_PRICE        = 10      # 1. 收盤價下限（元）
# 2. 漲跌幅區間：漲幅 >= GRADE_A 或 跌幅 GRADE_X_LO~GRADE_X_HI（程式內判斷）
MIN_INST_SHARE   = 50000   # 3. 法人合計買超最低股數（50張 = 50,000股）
MAX_CANDIDATES   = 50      # 4. 候選數量保護上限（取法人買超最多的前N名）
VOLUME_RATIO_MIN = 1.5     # 5. 量比：當日量 ÷ 近5日均量
# 6. EMA 多頭排列（程式內判斷，含備援邏輯）

EMA_SHORT  = 10
EMA_MID    = 20
EMA_LONG1  = 60    # 主要：20EMA > 60EMA > 120EMA
EMA_LONG2  = 120
# 備援：資料不足120筆時改用 10EMA > 20EMA > 60EMA
EMA_FALLBACK_MIN = 60   # 備援模式最少需要幾筆資料

GRADE_SS   =  7.0
GRADE_S    =  3.5
GRADE_A    =  1.0
GRADE_X_LO = -1.5   # 縮小 X 級跌幅範圍（原 -3.0），讓 X 級更精準
GRADE_X_HI =  0.0

# 雙買超門檻：外資和投信各自的最低要求
MIN_FOREIGN_SHARE      = 10000   # 外資至少 10,000 股
MIN_TRUST_SHARE        = 10000   # 投信至少 10,000 股
MIN_INST_SHARE_SINGLE  = 100000  # 只有單方買超時，合計需達 100,000 股才入選

DATA_READY_HOUR = 17   # 台灣時間幾點後才有當天資料

# ══════════════════════════════════════════════════════════
# 工具函式
# ══════════════════════════════════════════════════════════
def clean_sid(series):
    return series.astype(str).str.replace(r'[=\" \t]', '', regex=True).str.strip()

def safe_get(url, params=None, timeout=25, retries=3, wait=15):
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
    try:
        df = pd.read_csv(
            io.StringIO(text), skiprows=skiprows,
            thousands=thousands, on_bad_lines='skip'
        )
        if df.shape[1] < min_cols:
            print(f"[{label}] 欄位不足({df.shape[1]})，前400字：\n{text[:400]}")
            return pd.DataFrame()
        return df
    except Exception as e:
        print(f"[{label}] 解析失敗：{e}\n前400字：\n{text[:400]}")
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
    hour = now.hour

    if run_mode == 'preview':
        delta = 3 if base.weekday() == 0 else 1
        base -= timedelta(days=delta)
    elif run_mode == 'close':
        if   base.weekday() == 5: base -= timedelta(days=1)
        elif base.weekday() == 6: base -= timedelta(days=2)
    else:  # auto
        if hour < DATA_READY_HOUR:
            delta = 3 if base.weekday() == 0 else (
                    2 if base.weekday() == 6 else 1)
            base -= timedelta(days=delta)
        else:
            if   base.weekday() == 5: base -= timedelta(days=1)
            elif base.weekday() == 6: base -= timedelta(days=2)

    return base.strftime('%Y%m%d')

def prev_months(date_str, n=7):
    target = datetime.strptime(date_str, '%Y%m%d')
    months, d = [], target.replace(day=1)
    for _ in range(n):
        months.append(d.strftime('%Y%m'))
        d = (d - timedelta(days=1)).replace(day=1)
    return months

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
# 股市趨勢新聞（Google News RSS，台股相關）
# ══════════════════════════════════════════════════════════
def fetch_stock_news(count=10):
    """
    從 Google News RSS 抓取台股相關新聞標題。
    回傳最新 count 則新聞的清單 [{'title': ..., 'source': ...}]。
    """
    import xml.etree.ElementTree as ET
    rss_url = 'https://news.google.com/rss/search?q=台股+股市&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
    r = safe_get(rss_url, timeout=15, retries=2, wait=5)
    if r is None:
        print("[新聞] 抓取失敗")
        return []
    try:
        root = ET.fromstring(r.content)
        items = root.findall('.//item')
        news = []
        for item in items[:count]:
            title  = item.findtext('title', '').strip()
            source = item.findtext('source', '').strip()
            # 去掉標題末尾的來源名稱（Google News 格式：標題 - 來源）
            if ' - ' in title:
                title, source = title.rsplit(' - ', 1)
            news.append({'title': title.strip(), 'source': source.strip()})
        print(f"[新聞] 取得 {len(news)} 則")
        return news
    except Exception as e:
        print(f"[新聞] 解析失敗：{e}")
        return []

# ══════════════════════════════════════════════════════════
# T86 法人解析（用固定欄位索引，不依賴欄位名稱字串）
# T86 固定欄位順序：
#   idx 0  : 證券代號
#   idx 1  : 證券名稱
#   idx 4  : 外資及陸資買賣超股數  ← _foreign
#   idx 10 : 投信買賣超股數        ← _trust
#   idx 18 : 三大法人買賣超股數    ← _total
# ══════════════════════════════════════════════════════════
def parse_t86(text):
    print(f"[T86] 前300字：\n{text[:300]}\n{'─'*40}")
    lines = text.splitlines()

    # 找表頭行
    header_idx = -1
    for i, line in enumerate(lines):
        if '證券代號' in line:
            header_idx = i
            break
    if header_idx == -1:
        print("[T86] 找不到表頭，發送原始內容供 debug")
        return pd.DataFrame()

    print(f"[T86] 表頭第 {header_idx} 行：{lines[header_idx][:120]}")
    df = safe_read_csv('\n'.join(lines[header_idx:]), 'T86', min_cols=11)
    if df.empty:
        return pd.DataFrame()

    # 印出全部欄位名稱
    print(f"[T86] 共 {len(df.columns)} 欄：{list(df.columns)}")

    # 過濾有效股票列
    df = df[df.iloc[:, 0].astype(str).str.match(r'^[0-9A-Z]{4,6}$', na=False)].copy()
    if df.empty:
        print("[T86] 過濾後無有效股票列")
        return pd.DataFrame()

    df['sid_clean'] = clean_sid(df.iloc[:, 0])
    n = len(df.columns)

    # 用索引取值
    if n >= 19:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = pd.to_numeric(df.iloc[:, 18], errors='coerce').fillna(0)
        print(f"[T86] 標準19欄格式，外資idx=4 投信idx=10 合計idx=18")
    elif n >= 11:
        df['_foreign'] = pd.to_numeric(df.iloc[:, 4],  errors='coerce').fillna(0)
        df['_trust']   = pd.to_numeric(df.iloc[:, 10], errors='coerce').fillna(0)
        df['_total']   = df['_foreign'] + df['_trust']
        print(f"[T86] 備援{n}欄格式，外資idx=4 投信idx=10")
    else:
        print(f"[T86] 欄位數不足({n})，無法解析")
        return pd.DataFrame()

    print(f"[T86] 有效股票：{len(df)} 檔，外資非零：{(df['_foreign']!=0).sum()}，投信非零：{(df['_trust']!=0).sum()}")
    return df

# ══════════════════════════════════════════════════════════
# 歷史 K 棒（單股單月，快速模式）
# ══════════════════════════════════════════════════════════
def fetch_stock_day_fast(sid, yyyymm):
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=12, retries=2, wait=5
    )
    if r is None or '查詢無資料' in r.text:
        return pd.DataFrame()
    try:
        text = r.text
        idx = text.find('"日期"')
        if idx == -1:
            for yr in ['114/', '113/', '112/']:
                idx = text.find(yr)
                if idx != -1:
                    idx = text.rfind('\n', 0, idx) + 1
                    break
        if idx == -1:
            return pd.DataFrame()

        df = safe_read_csv(text[idx:], f'STOCK_DAY-{sid}', min_cols=7)
        if df.empty:
            return pd.DataFrame()

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
    except:
        return pd.DataFrame()

def build_history_fast(sid, months):
    """
    逐月抓歷史K棒，並快取到磁碟（同一天多次執行直接讀快取，確保結果一致）。
    快取路徑：/tmp/stock_cache_{date}/{sid}.json
    """
    import json, os

    # 快取目錄以月份清單第一個月（=最新月）命名，確保每天獨立
    cache_dir = f"/tmp/stock_cache_{months[0]}"
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = f"{cache_dir}/{sid}.json"

    # 有快取就直接讀，跳過 API 請求
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                records = json.load(f)
            if records:
                df = pd.DataFrame(records)
                df['date'] = pd.to_datetime(df['date']).dt.date
                return df
        except:
            pass  # 快取損壞就重新抓

    frames = []
    for yyyymm in months:
        df_m = fetch_stock_day_fast(sid, yyyymm)
        if df_m.empty:
            time.sleep(1)
            df_m = fetch_stock_day_fast(sid, yyyymm)
        if not df_m.empty:
            frames.append(df_m)
        time.sleep(0.08)

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)

    # 寫入快取
    try:
        records = df_all.copy()
        records['date'] = records['date'].astype(str)
        with open(cache_file, 'w') as f:
            json.dump(records.to_dict('records'), f)
    except:
        pass

    return df_all

def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def check_ema_bull(df):
    """
    主要：20EMA > 60EMA > 120EMA（需 >= 120 筆資料）
    備援：10EMA > 20EMA > 60EMA（需 >= 60 筆資料）
    """
    if len(df) < EMA_FALLBACK_MIN:
        return False, 'insufficient'

    closes = df['close'].astype(float)

    if len(df) >= EMA_LONG2:
        # 主要模式
        ema20  = calc_ema(closes, EMA_MID).iloc[-1]
        ema60  = calc_ema(closes, EMA_LONG1).iloc[-1]
        ema120 = calc_ema(closes, EMA_LONG2).iloc[-1]
        return ema20 > ema60 > ema120, 'full'
    else:
        # 備援模式（60~119筆）
        ema10 = calc_ema(closes, EMA_SHORT).iloc[-1]
        ema20 = calc_ema(closes, EMA_MID).iloc[-1]
        ema60 = calc_ema(closes, EMA_LONG1).iloc[-1]
        return ema10 > ema20 > ema60, 'fallback'

def calc_volume_ratio(df, target_date):
    df = df[df['date'] <= target_date].reset_index(drop=True)
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

    run_mode = os.environ.get('RUN_MODE', 'auto').strip().lower()
    date_str = get_target_date(run_mode)
    now_tw   = datetime.utcnow() + timedelta(hours=8)

    if run_mode == 'preview':
        report_type = '盤前複習'
    elif run_mode == 'close':
        report_type = '盤後結算'
    else:
        report_type = '盤後結算' if now_tw.hour >= DATA_READY_HOUR else '盤前複習'

    print(f"[執行] 模式={run_mode}，日期={date_str}，台灣時間={now_tw.strftime('%H:%M')}")
    t_start = time.time()

    # ── 平行抓取大盤、T86、MI_INDEX ──
    market  = get_market_info(date_str)

    r_inst = safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=40, retries=5, wait=20
    )
    r_price = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'ALLBUT0999'},
        timeout=40, retries=5, wait=20
    )

    if r_inst is None or r_price is None:
        which = 'T86(法人)' if r_inst is None else 'MI_INDEX(價格)'
        requests.post(WEBHOOK_URL, json={
            'content': (
                f'❌ 無法取得個股資料（{date_str}）\n'
                f'失敗來源：{which}\n'
                f'已重試 5 次，可能是 TWSE 暫時封鎖海外 IP，請稍後用 /run 手動重試。'
            )
        }, timeout=15)
        return
    if '查詢無資料' in r_inst.text or '查詢無資料' in r_price.text:
        requests.post(WEBHOOK_URL, json={'content': f'ℹ️ {date_str} 查無資料（假日或尚未更新）。'}, timeout=15)
        return

    try:
        # ── 解析 T86 ──
        df_i = parse_t86(r_inst.text)
        if df_i.empty:
            requests.post(WEBHOOK_URL, json={
                'content': f'❌ T86 解析失敗（{date_str}）\n前300字：\n{r_inst.text[:300]}'
            }, timeout=15)
            return

        # parse_t86 已用固定索引解析，欄位名稱固定為 _foreign/_trust/_total
        # 先確認欄位存在
        for required_col in ['_foreign', '_trust', '_total']:
            if required_col not in df_i.columns:
                requests.post(WEBHOOK_URL, json={
                    'content': (
                        f'❌ T86 欄位 {required_col} 不存在（{date_str}）\n'
                        f'現有欄位：{list(df_i.columns)}\n'
                        f'T86前300字：\n{r_inst.text[:300]}'
                    )
                }, timeout=15)
                return

        col_foreign = '_foreign'
        col_trust   = '_trust'
        col_total   = '_total'
        print(f"[T86] 外資非零：{(df_i['_foreign']!=0).sum()} 投信非零：{(df_i['_trust']!=0).sum()}")

        # 數據驗證：外資和投信不應同時全為0
        if (df_i['_foreign'] == 0).all() and (df_i['_trust'] == 0).all():
            requests.post(WEBHOOK_URL, json={
                'content': f'❌ T86 法人數據異常（外資+投信全為0），請查看 Actions log。'
            }, timeout=15)
            return

        # ── 解析 MI_INDEX ──
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
            requests.post(WEBHOOK_URL, json={'content': f'❌ MI_INDEX 解析失敗（{date_str}）。'}, timeout=15)
            return

        df_p = df_p.dropna(thresh=5)
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])
        print(f"[MI_INDEX] {len(df_p)} 檔")

        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')
        print(f"[合併] {len(df)} 檔")

        col_close = next((c for c in df.columns if '收盤' in str(c)), None)
        col_diff  = next((c for c in df.columns if '漲跌價差' in str(c) or
                         ('漲跌' in str(c) and '差' in str(c))), None)
        col_sign  = next((c for c in df.columns if '漲跌(+/-)' in str(c) or '漲跌符號' in str(c)), None)

        if not all([col_close, col_diff]):
            raise ValueError(f"找不到收盤/漲跌欄：{list(df.columns)}")

        # ══════════════════════════════════════
        # 第一輪：基本條件（1.收盤價 2.漲跌幅 3.法人買超）
        # ══════════════════════════════════════
        candidates = []
        for _, row in df.iterrows():
            try:
                sid   = row['sid_clean']
                name  = str(row.get('證券名稱', row.iloc[1])).strip()
                price = pd.to_numeric(str(row[col_close]).replace(',', ''), errors='coerce')
                diff  = pd.to_numeric(str(row[col_diff]).replace(',', ''),  errors='coerce')

                # 1. 收盤價下限
                if pd.isna(price) or pd.isna(diff) or price < MIN_PRICE:
                    continue

                if col_sign:
                    s = str(row[col_sign])
                    diff = -abs(diff) if ('−' in s or s.strip() == '-') else abs(diff)

                change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0.0

                # 2. 漲跌幅區間
                if not (change >= GRADE_A or (GRADE_X_LO <= change < GRADE_X_HI)):
                    continue

                inst_row = df_i[df_i['sid_clean'] == sid]
                if inst_row.empty:
                    continue

                foreign = float(inst_row[col_foreign].values[0]) if col_foreign else 0.0
                trust   = float(inst_row[col_trust].values[0])   if col_trust   else 0.0
                total   = foreign + trust

                # 3. 法人買超下限（雙買超優先，單方買超門檻提高）
                both_buy   = foreign >= MIN_FOREIGN_SHARE and trust >= MIN_TRUST_SHARE
                single_buy = total >= MIN_INST_SHARE_SINGLE
                if not (both_buy or single_buy):
                    continue

                candidates.append({
                    'sid': sid, 'name': name,
                    'price': price, 'change': change,
                    'foreign': int(foreign), 'trust': int(trust),
                    'total': int(total),
                })
            except:
                continue

        print(f"[過濾1] 基本條件通過：{len(candidates)} 檔")

        # 4. 候選數量保護
        if len(candidates) > MAX_CANDIDATES:
            candidates.sort(key=lambda e: e['total'], reverse=True)
            candidates = candidates[:MAX_CANDIDATES]
            print(f"[過濾4] 截斷至前 {MAX_CANDIDATES} 名（依法人買超）")

        # ══════════════════════════════════════
        # 第二輪：量比（5）→ EMA（6）
        # 量比用當日資料即可，先過濾，減少需要抓歷史K棒的數量
        # ══════════════════════════════════════
        months      = prev_months(date_str, n=7)
        target_date = datetime.strptime(date_str, '%Y%m%d').date()
        print(f"[EMA] 月份清單：{months}")

        ss_list, s_list, a_list, x_list = [], [], [], []

        for idx_c, entry in enumerate(candidates):
            sid = entry['sid']
            try:
                t0 = time.time()
                df_hist = build_history_fast(sid, months)
                elapsed = time.time() - t0

                # 5. 量比（用歷史資料計算，含當日）
                vol_ratio = calc_volume_ratio(df_hist, target_date)
                if vol_ratio < VOLUME_RATIO_MIN:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} 量比{vol_ratio:.2f} ✗ {elapsed:.1f}s")
                    continue

                # 6. EMA 多頭排列（含備援）
                is_bull, ema_mode = check_ema_bull(df_hist)
                if not is_bull:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} EMA{ema_mode} ✗ {elapsed:.1f}s")
                    continue

                entry['vol_ratio'] = vol_ratio
                entry['ema_mode']  = ema_mode
                change = entry['change']

                if   change >= GRADE_SS:                  ss_list.append(entry)
                elif change >= GRADE_S:                    s_list.append(entry)
                elif change >= GRADE_A:                    a_list.append(entry)
                elif GRADE_X_LO <= change < GRADE_X_HI:   x_list.append(entry)

                print(f"  [{idx_c+1}/{len(candidates)}] {sid} {entry['name']} ✓ 漲{change}% 量比{vol_ratio:.2f} EMA:{ema_mode} {elapsed:.1f}s")

            except Exception as e:
                print(f"  [{idx_c+1}/{len(candidates)}] {sid} 錯誤：{e}")

        for lst in [ss_list, s_list, a_list]:
            lst.sort(key=lambda e: e['change'], reverse=True)
        x_list.sort(key=lambda e: e['total'], reverse=True)

        total_elapsed = time.time() - t_start
        print(f"[完成] SS={len(ss_list)} S={len(s_list)} A={len(a_list)} X={len(x_list)}，總耗時={total_elapsed:.0f}秒")

        # ══════════════════════════════════════
        # 組裝 Discord 訊息
        # ══════════════════════════════════════
        def stock_block(e, emoji_open, grade_label, emoji_close):
            sign   = '+' if e['change'] >= 0 else ''
            # EMA備援模式標註
            ema_tag = '(備援EMA)' if e.get('ema_mode') == 'fallback' else ''
            return (
                f"{emoji_open}【{grade_label}】{e['sid']} {e['name']}{emoji_close}\n"
                f"🔹收盤價格:{e['price']}\n"
                f"🔹今日漲幅{sign}{e['change']}%    量比:{e.get('vol_ratio', 0):.1f}x{ema_tag}\n"
                f"🔹外資:{fmt_share(e['foreign'])}股　投信:{fmt_share(e['trust'])}股"
            )

        if market:
            sd = '+' if market['diff'] >= 0 else ''
            sp = '+' if market['pct']  >= 0 else ''
            mkt_line = f"加權指數：{market['close']:,.2f}　({sd}{market['diff']:.2f} / {sp}{market['pct']:.2f}%)"
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

        # ── 股市趨勢新聞（股票清單發送完畢後獨立發送）──
        news_list = fetch_stock_news(count=10)
        if news_list:
            news_lines = ['📰 **【台股趨勢新聞】**\n' + '─'*25]
            for i, n in enumerate(news_list, 1):
                source_tag = f"　_{n['source']}_" if n['source'] else ''
                news_lines.append(f"{i}. {n['title']}{source_tag}")
            news_msg = '\n'.join(news_lines)
            requests.post(
                WEBHOOK_URL,
                json={'username': '川投顧量化系統', 'content': news_msg},
                timeout=15
            )
        else:
            print("[新聞] 無資料，跳過發送")

    except Exception as e:
        import traceback
        print(f"[主程式錯誤]\n{traceback.format_exc()}")
        requests.post(WEBHOOK_URL, json={'content': f'❌ 系統錯誤：{e}'}, timeout=15)

if __name__ == '__main__':
    run_analysis()
