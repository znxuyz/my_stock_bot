import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')
HEADERS     = {'User-Agent': 'Mozilla/5.0'}

# ══════════════════════════════════════════════════════════
# 篩選參數 ── 只改這裡就能調整條件
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
            print(f"[{label}] 欄位數不足({df.shape[1]})，前400字：\n{text[:400]}")
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
    """回傳從 date_str 往前 n 個月的 YYYYMM 清單（含當月）。"""
    target = datetime.strptime(date_str, '%Y%m%d')
    months = []
    d = target.replace(day=1)
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
# T86 法人解析
# ══════════════════════════════════════════════════════════
def parse_t86(text):
    print(f"[T86] 前400字：\n{text[:400]}\n{'─'*40}")
    lines = text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if '證券代號' in line:
            header_idx = i
            break
    if header_idx == -1:
        print("[T86] 找不到表頭，嘗試 skiprows=1")
        df = safe_read_csv(text, 'T86-fallback', skiprows=1, min_cols=5)
    else:
        print(f"[T86] 表頭第 {header_idx} 行")
        df = safe_read_csv('\n'.join(lines[header_idx:]), 'T86', min_cols=5)
    if df.empty:
        return pd.DataFrame()
    df = df[df.iloc[:, 0].astype(str).str.match(r'^[0-9A-Z]{4,6}$', na=False)].copy()
    df['sid_clean'] = clean_sid(df.iloc[:, 0])
    print(f"[T86] 有效股票：{len(df)} 檔，欄位：{list(df.columns[:10])}")
    return df

# ══════════════════════════════════════════════════════════
# ★ 核心優化：STOCK_DAY_ALL 批次抓取全市場日K
#   一次請求 = 當月所有股票，7個月只需 7 次請求
#   取代原本「每股 × 7個月」= 420+ 次請求
# ══════════════════════════════════════════════════════════
def fetch_all_stocks_month(yyyymm):
    """
    抓取當月全市場每日收盤與成交量。
    回傳 dict：{sid: [(date, close, volume), ...]}
    """
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL',
        params={'response': 'csv', 'date': yyyymm + '01'},
        timeout=30, retries=2, wait=10
    )
    if r is None or '查詢無資料' in r.text:
        return {}

    try:
        text = r.text
        # 找表頭
        idx = text.find('"證券代號"')
        if idx == -1:
            idx = text.find('證券代號')
        if idx == -1:
            print(f"[STOCK_DAY_ALL] {yyyymm} 找不到表頭，前300字：\n{text[:300]}")
            return {}
        # 往前找到行首
        idx = text.rfind('\n', 0, idx) + 1

        df = safe_read_csv(text[idx:], f'STOCK_DAY_ALL-{yyyymm}', min_cols=5)
        if df.empty:
            return {}

        # 欄位：證券代號, 證券名稱, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 本益比
        # STOCK_DAY_ALL 是月彙總（每股一列），不含每日資料
        # 改用單月日K：STOCK_DAY 但加上 date 欄位
        # → 實際上 STOCK_DAY_ALL 只有月彙總，無法算 EMA，需要改策略
        print(f"[STOCK_DAY_ALL] {yyyymm} 欄位：{list(df.columns[:8])}")
        return df

    except Exception as e:
        print(f"[STOCK_DAY_ALL] {yyyymm} 解析失敗：{e}")
        return pd.DataFrame()


def fetch_month_daily(yyyymm):
    """
    抓取單月全市場「每日」行情彙總（MI_INDEX 日資料）。
    回傳 DataFrame：index=date，columns=sid，values=close
    另外回傳 volume_df：index=date，columns=sid，values=volume
    """
    # TWSE 沒有單一API能一次給全市場每日K棒
    # 最快方法：用 STOCK_DAY 逐股抓，但這就是慢的原因
    # ─ 實際可行的批次方案 ─
    # 用 MI_INDEX type=ALLBUT0999 只給當日收盤，不含歷史
    # 真正的歷史批次只能用 STOCK_DAY 逐股，或用第三方資料源
    # 結論：在 TWSE 限制下，EMA 計算無法避免逐股請求
    # → 改為：限制候選股數量上限，並大幅縮短 sleep 時間
    pass


def fetch_stock_day_fast(sid, yyyymm):
    """單股單月日K，timeout 縮短、sleep 縮短。"""
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=10, retries=1, wait=3   # 快速模式：1次重試，失敗直接跳過
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
    """逐月抓歷史K棒，快速模式（失敗直接跳過不重試）。"""
    frames = []
    for yyyymm in months:
        df_m = fetch_stock_day_fast(sid, yyyymm)
        if not df_m.empty:
            frames.append(df_m)
        time.sleep(0.08)   # 縮短 sleep：0.15 → 0.08
    if not frames:
        return pd.DataFrame()
    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    return df_all


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

def calc_volume_ratio(df, target_date):
    """當日量 ÷ 前5日均量。"""
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

    market = get_market_info(date_str)

    r_inst = safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )
    r_price = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'response': 'csv', 'date': date_str, 'type': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )

    if r_inst is None or r_price is None:
        requests.post(WEBHOOK_URL, json={'content': f'❌ 無法取得個股資料（{date_str}），請稍後重試。'}, timeout=15)
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

        col_foreign = find_col(df_i, '外資', '買賣超')
        col_trust   = find_col(df_i, '投信', '買賣超')
        col_dealer  = find_col(df_i, '自營商', '買賣超')
        col_total   = find_col(df_i, '合計', '買賣超')
        print(f"[T86] 外資={col_foreign} 投信={col_trust} 自營={col_dealer} 合計={col_total}")

        if not col_total:
            avail = [c for c in [col_foreign, col_trust, col_dealer] if c]
            if avail:
                df_i['_total'] = df_i[avail].apply(pd.to_numeric, errors='coerce').sum(axis=1)
                col_total = '_total'
            else:
                raise ValueError(f"找不到法人欄位，現有：{list(df_i.columns)}")

        for col in [col_foreign, col_trust, col_dealer, col_total]:
            if col:
                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0)

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
                    s = str(row[col_sign])
                    diff = -abs(diff) if ('−' in s or s.strip() == '-') else abs(diff)

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

        print(f"[過濾] 基本條件通過：{len(candidates)} 檔")

        # ── 候選數量保護：超過 40 檔時只取法人買超最多的前 40 ──
        # 避免 EMA 抓取時間過長（每檔約 7 秒，40檔約 280 秒）
        MAX_CANDIDATES = 40
        if len(candidates) > MAX_CANDIDATES:
            candidates.sort(key=lambda e: e['total'], reverse=True)
            candidates = candidates[:MAX_CANDIDATES]
            print(f"[保護] 候選超過 {MAX_CANDIDATES} 檔，取法人買超前 {MAX_CANDIDATES} 名")

        # ── 預先載入7個月清單（所有候選共用） ──
        months = prev_months(date_str, n=7)
        target_date = datetime.strptime(date_str, '%Y%m%d').date()
        print(f"[EMA] 開始抓 {len(candidates)} 檔歷史資料，月份：{months}")

        # ── 第二輪：EMA + 量比 ──
        ss_list, s_list, a_list, x_list = [], [], [], []

        for idx_c, entry in enumerate(candidates):
            sid = entry['sid']
            try:
                t0 = time.time()
                df_hist = build_history_fast(sid, months)
                elapsed = time.time() - t0

                if df_hist.empty or len(df_hist) < EMA_LONG:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} 資料不足({len(df_hist)}列) {elapsed:.1f}s")
                    continue

                if not check_ema_bull(df_hist):
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} EMA非多頭 {elapsed:.1f}s")
                    continue

                vol_ratio = calc_volume_ratio(df_hist, target_date)
                if vol_ratio < VOLUME_RATIO_MIN:
                    print(f"  [{idx_c+1}/{len(candidates)}] {sid} 量比{vol_ratio:.2f} {elapsed:.1f}s")
                    continue

                entry['vol_ratio'] = vol_ratio
                change = entry['change']

                if   change >= GRADE_SS:                  ss_list.append(entry)
                elif change >= GRADE_S:                    s_list.append(entry)
                elif change >= GRADE_A:                    a_list.append(entry)
                elif GRADE_X_LO <= change < GRADE_X_HI:   x_list.append(entry)

                print(f"  [{idx_c+1}/{len(candidates)}] {sid} {entry['name']} ✓ 漲{change}% 量比{vol_ratio:.2f} {elapsed:.1f}s")

            except Exception as e:
                print(f"  [{idx_c+1}/{len(candidates)}] {sid} 錯誤：{e}")

        for lst in [ss_list, s_list, a_list]:
            lst.sort(key=lambda e: e['change'], reverse=True)
        x_list.sort(key=lambda e: e['total'], reverse=True)

        total_elapsed = time.time() - t_start
        print(f"[完成] SS={len(ss_list)} S={len(s_list)} A={len(a_list)} X={len(x_list)}，總耗時={total_elapsed:.0f}秒")

        # ── 組裝訊息 ──
        def stock_block(e, emoji_open, grade_label, emoji_close):
            sign = '+' if e['change'] >= 0 else ''
            return (
                f"{emoji_open}【{grade_label}】{e['sid']} {e['name']}{emoji_close}\n"
                f"🔹收盤價格:{e['price']} ({sign}{e['change']}%)　量比:{e.get('vol_ratio', 0):.1f}x\n"
                f"🔹外資:{fmt_share(e['foreign'])}股　投信:{fmt_share(e['trust'])}股　自營商:{fmt_share(e['dealer'])}股"
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

    except Exception as e:
        import traceback
        print(f"[主程式錯誤]\n{traceback.format_exc()}")
        requests.post(WEBHOOK_URL, json={'content': f'❌ 系統錯誤：{e}'}, timeout=15)

if __name__ == '__main__':
    run_analysis()
