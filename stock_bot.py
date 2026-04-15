import os, requests, io, time
import pandas as pd
from datetime import datetime, timedelta

WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK')
HEADERS     = {'User-Agent': 'Mozilla/5.0'}

# ══════════════════════════════════════════════════════════
# 篩選參數（短線 1~3 週策略）── 只改這裡就能調整條件
# ══════════════════════════════════════════════════════════
MIN_PRICE      = 10     # 收盤價下限（元）
MIN_INST_SHARE = 50000  # 法人合計買超最低「股數」（50張 = 50,000股）

VOLUME_RATIO_MIN = 1.5  # 當日量 ÷ 近5日均量，需 >= 此值

# EMA 多頭排列：20EMA > 60EMA > 120EMA
EMA_SHORT  = 20
EMA_MID    = 60
EMA_LONG   = 120

# 等級漲幅門檻
GRADE_SS    =  7.0   # 🔥 SS 級：漲幅 >= 7%
GRADE_S     =  3.5   # 💎 S  級：漲幅 >= 3.5%
GRADE_A     =  1.0   # 📈 A  級：漲幅 >= 1%
GRADE_X_LOW = -3.0   # 🔍 X  級：跌幅 -3%~0%，法人逆勢買
GRADE_X_HI  =  0.0

# ══════════════════════════════════════════════════════════
# 工具函式
# ══════════════════════════════════════════════════════════
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

def find_col(df, *keywords):
    """找出同時包含所有關鍵字的欄位名稱，找不到回傳 None。"""
    for c in df.columns:
        if all(k in str(c) for k in keywords):
            return c
    return None

def fmt_share(n):
    """格式化股數，加正負號與千分位。"""
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
        df  = pd.read_csv(io.StringIO(r.text), skiprows=1, thousands=',')
        row = df[df.iloc[:, 0].str.contains('發行量加權股價指數', na=False)].iloc[0]
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
# 歷史 K 棒（TWSE 月資料）→ 計算 EMA 與 5 日均量
# ══════════════════════════════════════════════════════════
def fetch_monthly_ohlcv(sid, yyyymm):
    """
    抓取單一股票單月日 K 資料，回傳 DataFrame（date, close, volume）。
    date 欄為 datetime.date，volume 為整數（股）。
    """
    r = safe_get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
        params={'response': 'csv', 'date': yyyymm + '01', 'stockNo': sid},
        timeout=15, retries=2, wait=5
    )
    if r is None or '查詢無資料' in r.text:
        return pd.DataFrame()
    try:
        text = r.text
        # 找表頭行
        idx = text.find('"日期"')
        if idx == -1:
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(text[idx:]), thousands=',').dropna(thresh=3)
        df = df[df.iloc[:, 0].str.match(r'^\d{3}/\d{2}/\d{2}$', na=False)].copy()
        # 民國年 → 西元年
        def roc_to_date(s):
            y, m, d = s.split('/')
            return datetime(int(y) + 1911, int(m), int(d)).date()
        df['date']   = df.iloc[:, 0].apply(roc_to_date)
        df['close']  = pd.to_numeric(df.iloc[:, 6].astype(str).str.replace(',', ''), errors='coerce')
        df['volume'] = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(',', ''), errors='coerce')
        return df[['date', 'close', 'volume']].dropna().reset_index(drop=True)
    except Exception as e:
        print(f"[月K解析失敗] {sid}/{yyyymm}: {e}")
        return pd.DataFrame()

def build_history(sid, target_date_str):
    """
    組合近 7 個月的日 K（確保有足夠資料算 120EMA）。
    回傳按日期排序的 DataFrame，最後一列 = target_date 當天。
    """
    target = datetime.strptime(target_date_str, '%Y%m%d').date()
    frames = []
    d = target.replace(day=1)
    for _ in range(7):
        yyyymm = d.strftime('%Y%m')
        df_m = fetch_monthly_ohlcv(sid, yyyymm)
        if not df_m.empty:
            frames.append(df_m)
        # 往前一個月
        d = (d - timedelta(days=1)).replace(day=1)
        time.sleep(0.15)   # 避免過快打 API

    if not frames:
        return pd.DataFrame()
    df_all = pd.concat(frames).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    # 只保留 <= target_date 的資料
    df_all = df_all[df_all['date'] <= target].reset_index(drop=True)
    return df_all

def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def check_ema_bull(df):
    """
    檢查最後一日是否滿足 20EMA > 60EMA > 120EMA（多頭排列）。
    資料不足時回傳 False（保守處理）。
    """
    if len(df) < EMA_LONG:
        return False
    closes = df['close'].astype(float)
    ema20  = calc_ema(closes, EMA_SHORT).iloc[-1]
    ema60  = calc_ema(closes, EMA_MID).iloc[-1]
    ema120 = calc_ema(closes, EMA_LONG).iloc[-1]
    return ema20 > ema60 > ema120

def calc_volume_ratio(df):
    """
    當日量 ÷ 前 5 日均量（不含當日）。
    資料不足回傳 0。
    """
    if len(df) < 6:
        return 0.0
    today_vol  = df['volume'].iloc[-1]
    avg5       = df['volume'].iloc[-6:-1].mean()
    if avg5 == 0:
        return 0.0
    return round(today_vol / avg5, 2)

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

    # ── 大盤（非強制）──
    market = get_market_info(date_str)

    # ── 個股法人 T86 ──
    r_inst = safe_get(
        'https://www.twse.com.tw/rwd/zh/fund/T86',
        params={'response': 'csv', 'date': date_str, 'selectType': 'ALLBUT0999'},
        timeout=20, retries=3, wait=15
    )
    # ── 個股行情 MI_INDEX ──
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
        # ── 解析 T86 法人 ──
        df_i = pd.read_csv(io.StringIO(r_inst.text), skiprows=1, thousands=',')
        df_i = df_i[df_i.iloc[:, 0].str.contains(r'^[0-9A-Z]', na=False)].copy()

        col_foreign = find_col(df_i, '外資', '買賣超')
        col_trust   = find_col(df_i, '投信', '買賣超')
        col_dealer  = find_col(df_i, '自營商', '買賣超')
        col_total   = find_col(df_i, '合計', '買賣超')

        if not col_total:
            avail = [c for c in [col_foreign, col_trust, col_dealer] if c]
            if avail:
                df_i['_total'] = df_i[avail].apply(pd.to_numeric, errors='coerce').sum(axis=1)
                col_total = '_total'
            else:
                raise ValueError(f"找不到法人欄位：{list(df_i.columns)}")

        for col in [col_foreign, col_trust, col_dealer, col_total]:
            if col:
                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0)

        df_i['sid_clean'] = clean_sid(df_i.iloc[:, 0])

        # ── 解析 MI_INDEX 行情 ──
        price_text = r_price.text
        start_idx  = price_text.find('"證券代號"')
        if start_idx == -1:
            raise ValueError('MI_INDEX 找不到表頭「證券代號」')
        df_p = pd.read_csv(io.StringIO(price_text[start_idx:]), thousands=',').dropna(thresh=5)
        df_p['sid_clean'] = clean_sid(df_p['證券代號'])

        # ── 合併 ──
        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')

        col_close = next((c for c in df.columns if '收盤' in str(c)), None)
        col_diff  = next((c for c in df.columns if '漲跌價差' in str(c) or
                         ('漲跌' in str(c) and '差' in str(c))), None)
        col_sign  = next((c for c in df.columns if '漲跌(+/-)' in str(c) or
                         '漲跌符號' in str(c)), None)

        if not all([col_close, col_diff]):
            raise ValueError(f"找不到收盤/漲跌欄：{list(df.columns)}")

        # ── 第一輪過濾：基本條件（快速，無需歷史資料）──
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

                # 只處理有機會入級的漲跌區間
                if not (change >= GRADE_A or (GRADE_X_LOW <= change < GRADE_X_HI)):
                    continue

                inst_row = df_i[df_i['sid_clean'] == sid]
                if inst_row.empty:
                    continue

                foreign = float(inst_row[col_foreign].values[0]) if col_foreign else 0.0
                trust   = float(inst_row[col_trust].values[0])   if col_trust   else 0.0
                dealer  = float(inst_row[col_dealer].values[0])  if col_dealer  else 0.0
                total   = foreign + trust + dealer

                # 法人買超股數門檻
                if total < MIN_INST_SHARE:
                    continue

                candidates.append({
                    'sid': sid, 'name': name,
                    'price': price, 'change': change,
                    'foreign': int(foreign), 'trust': int(trust),
                    'dealer': int(dealer),   'total': int(total),
                })
            except:
                continue

        print(f"[過濾] 基本條件通過：{len(candidates)} 檔，開始抓歷史資料...")

        # ── 第二輪過濾：EMA 多頭排列 + 量比（需逐一抓歷史 K 棒）──
        ss_list, s_list, a_list, x_list = [], [], [], []

        for entry in candidates:
            sid = entry['sid']
            try:
                df_hist = build_history(sid, date_str)
                if df_hist.empty:
                    print(f"  [跳過] {sid} 歷史資料空白")
                    continue

                # EMA 多頭排列
                if not check_ema_bull(df_hist):
                    print(f"  [濾除] {sid} EMA 非多頭排列")
                    continue

                # 量比 >= 1.5
                vol_ratio = calc_volume_ratio(df_hist)
                if vol_ratio < VOLUME_RATIO_MIN:
                    print(f"  [濾除] {sid} 量比 {vol_ratio:.2f} < {VOLUME_RATIO_MIN}")
                    continue

                entry['vol_ratio'] = vol_ratio
                change = entry['change']

                if   change >= GRADE_SS:              ss_list.append(entry)
                elif change >= GRADE_S:               s_list.append(entry)
                elif change >= GRADE_A:               a_list.append(entry)
                elif GRADE_X_LOW <= change < GRADE_X_HI: x_list.append(entry)

                print(f"  [入選] {sid} {entry['name']} 漲{change}% 量比{vol_ratio:.2f}")

            except Exception as e:
                print(f"  [錯誤] {sid}: {e}")
                continue

        for lst in [ss_list, s_list, a_list]:
            lst.sort(key=lambda e: e['change'], reverse=True)
        x_list.sort(key=lambda e: e['total'], reverse=True)

        print(f"[結果] SS={len(ss_list)} S={len(s_list)} A={len(a_list)} X={len(x_list)}")

        # ══════════════════════════════════════════════════════════
        # 組裝 Discord 訊息
        # ══════════════════════════════════════════════════════════
        def stock_block(e, emoji_open, grade_label, emoji_close):
            sign = '+' if e['change'] >= 0 else ''
            vol_tag = f"　量比:{e.get('vol_ratio', 0):.1f}x"
            return (
                f"{emoji_open}【{grade_label}】{e['sid']} {e['name']}{emoji_close}\n"
                f"🔹收盤價格:{e['price']} ({sign}{e['change']}%){vol_tag}\n"
                f"🔹外資:{fmt_share(e['foreign'])}股"
                f"　投信:{fmt_share(e['trust'])}股"
                f"　自營商:{fmt_share(e['dealer'])}股"
            )

        if market:
            sign_d = '+' if market['diff'] >= 0 else ''
            sign_p = '+' if market['pct']  >= 0 else ''
            mkt_line = (
                f"加權指數：{market['close']:,.2f}　"
                f"({sign_d}{market['diff']:.2f} / {sign_p}{market['pct']:.2f}%)"
            )
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
            sections += [stock_block(e, '💎', 'S 級',  '💎') for e in s_list[:10]]
        if a_list:
            sections += [stock_block(e, '📈', 'A 級',  '📈') for e in a_list[:10]]
        if x_list:
            sections.append('─── 🔍 潛在起漲區（小跌+法人逆勢買）───')
            sections += [stock_block(e, '🔍', 'X 級', '🔍') for e in x_list[:10]]
        if not sections:
            sections.append('（今日無符合所有條件之標的）')

        full_message = header + '\n\n' + '\n\n'.join(sections)

        # 分段發送（Discord 2000 字上限）
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
        print(f"[主程式錯誤] {e}")
        requests.post(WEBHOOK_URL, json={'content': f'❌ 系統錯誤：{e}'}, timeout=15)

if __name__ == '__main__':
    run_analysis()
