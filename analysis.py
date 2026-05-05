"""
盤後分析主流程 run_analysis。
從 stock_bot.py 抽出，移除原本的死碼（market_env suspend 後的整段重複程式）。
"""
import logging
import os
import time
import traceback
from datetime import date as _date, datetime

import pandas as pd
import requests

import config
import db
from advanced_indicators import calc_advanced_indicators
from chase import check_strong_chase, count_consecutive_limit_ups
from entry_zone import calc_entry_zone
from format_utils import fmt_share_signed as fmt_share
from indicators import (
    calc_bias_and_entry, calc_macd, calc_volume_ratio, check_ema_bull,
)
from matching import fill_pending_t1_entries
from scoring import calc_chip_concentration, calc_market_env, calc_score
from time_utils import get_target_date, prev_months, tw_now
from topflow import extract_top_flow
from twse_http import clean_sid, safe_get, safe_read_csv
from twse_kbar import build_history_fast
from twse_margin import fetch_margin_change
from twse_market import fetch_market_foreign_history, get_market_info
from twse_t86 import fetch_t86_cached

logger = logging.getLogger(__name__)


_MI_INDEX_PRICE_URL = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'

INDICATOR_GUIDE = """
━━━━━━━━━━━━━━━━━━━━━━━━
📖 **【指標說明】**
📐 **乖離率（BIAS）**：股價偏離10日均線的幅度
　0~5% ✅ 理想進場　5~8% ⚠️ 略高　>8% ❌ 過高勿追　負值 🔄 底部

📊 **RSI**：動能強弱指標（0~100）
　>80 短線過熱（飆股可能鈍化）　60~80 ✅ 強勢　50~60 普通　<50 ❌ 動能弱

🏔 **壓力位**：前期高點，股價容易在此遇賣壓
　接近壓力區時分批操作，突破壓力才加碼

📍 **位階**：距近期低點的漲幅，越高追高風險越大
　<20% ✅ 剛起漲　20~50% 中等　50~100% ⚠️ 偏高　>100% ❌ 極高

📦 **OBV（能量潮）**：用成交量確認漲勢是否健康
　量價同步 ✅ 健康　OBV背離 ⚠️ 漲勢可能假突破

⛔ **動態停損（2×ATR）**：根據股票波動幅度計算的停損點
　比固定-5%更精準，跌破此價格建議出場

💡 **建議入場價**：考量乖離率、均線、近期低點後的合理買入區間
━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _get_all_webhooks():
    whs = [config.DISCORD_WEBHOOK] if config.DISCORD_WEBHOOK else []
    try:
        for gw in db.get_all_webhooks():
            if gw['webhook_url'] and gw['webhook_url'] not in whs:
                whs.append(gw['webhook_url'])
    except Exception as e:
        logger.warning('[Webhook 收集] 失敗：%s', e)
    return whs


def _notify_all(msg):
    for wh in _get_all_webhooks():
        try:
            requests.post(wh, json={'username': '川投顧量化系統', 'content': msg}, timeout=10)
        except Exception as e:
            logger.warning('[Webhook 推送] 失敗：%s', e)


def _to_native(v):
    """numpy 型別 → Python 原生（給 DB 寫入用）"""
    import numpy as _np
    if isinstance(v, _np.integer):  return int(v)
    if isinstance(v, _np.floating): return float(v)
    return v


def _normalise_record(e):
    """把 entry dict 轉成可丟給 db.save_screen_records 的純值 dict。"""
    out = {k: _to_native(v) for k, v in e.items() if k != 'bias'}
    if e.get('bias'):
        out['bias'] = {k: _to_native(v) for k, v in e['bias'].items()}
    return out


def _filter_first_round(df, df_i, col_close, col_diff, col_sign):
    """第一輪篩選：收盤價 ≥ MIN_PRICE、漲幅 ≥ GRADE_A、法人買超門檻。

    注意：用 to_dict('records') 而非 itertuples — 後者會把含特殊字元的欄位
    （例如 '漲跌(+/-)'、底線開頭）改名成 _NN 位置索引，導致用原欄位名取值會 KeyError。
    """
    candidates = []
    col_foreign, col_trust = '_foreign', '_trust'
    err_count = 0
    for row_dict in df.to_dict('records'):
        try:
            sid   = row_dict['sid_clean']
            name  = str(row_dict.get('證券名稱', list(row_dict.values())[1])).strip()
            price = pd.to_numeric(str(row_dict[col_close]).replace(',', ''), errors='coerce')
            diff  = pd.to_numeric(str(row_dict[col_diff]).replace(',',  ''), errors='coerce')

            if pd.isna(price) or pd.isna(diff) or price < config.MIN_PRICE:
                continue
            if col_sign:
                s = str(row_dict[col_sign])
                diff = -abs(diff) if ('−' in s or s.strip() == '-') else abs(diff)

            change = round((diff / (price - diff)) * 100, 2) if (price - diff) != 0 else 0.0
            if change < config.GRADE_A:
                continue

            inst_row = df_i[df_i['sid_clean'] == sid]
            if inst_row.empty:
                continue
            foreign = float(inst_row[col_foreign].values[0])
            trust   = float(inst_row[col_trust].values[0])
            total   = foreign + trust

            both_buy   = foreign >= config.MIN_FOREIGN_SHARE and trust >= config.MIN_TRUST_SHARE
            single_buy = total >= config.MIN_INST_SHARE_SINGLE
            if not (both_buy or single_buy):
                continue

            candidates.append({
                'sid': sid, 'name': name,
                'price': price, 'change': change,
                'foreign': int(foreign), 'trust': int(trust),
                'total': int(total),
            })
        except Exception as e:
            err_count += 1
            # 同樣錯誤只 log 前 3 筆 + 最後總數，避免重複訊息塞爆 Railway log 額度
            if err_count <= 3:
                logger.warning('[第一輪] 處理列失敗：%s', e)
    if err_count > 3:
        logger.warning('[第一輪] 共 %d 列處理失敗（已抑制重複訊息）', err_count)
    return candidates


def _enrich_candidate(entry, df_hist, target_date, market_env, date_str):
    """對通過量比 / EMA 的候選股計算所有指標、評分、追漲模式。"""
    sid = entry['sid']

    bias_info = calc_bias_and_entry(df_hist, entry['price'])
    entry['bias'] = bias_info

    adv = calc_advanced_indicators(df_hist, entry['price'])
    entry['adv'] = adv

    entry['market_score'] = market_env.get('score', 0)

    vol_today = int(df_hist['volume'].iloc[-1]) if not df_hist.empty else 0
    chip = calc_chip_concentration(entry['foreign'], entry['trust'], vol_today)
    entry['chip_score'] = chip['score']
    entry['chip_label'] = chip['label']

    entry['consec_score'] = 0
    entry['consec_label'] = ''

    try:
        margin = fetch_margin_change(sid, date_str)
        entry['margin_score'] = margin['score']
        entry['margin_label'] = margin['label']
    except Exception as e:
        entry['margin_score'] = 0
        entry['margin_label'] = ''
        logger.warning('[融資] %s 失敗：%s', sid, e)

    macd_info = calc_macd(df_hist)
    entry['macd_score'] = macd_info['macd_score']
    entry['macd_label'] = macd_info['macd_label']
    entry['macd_info']  = macd_info

    consec = count_consecutive_limit_ups(df_hist)
    entry['consec_limit_up'] = consec
    if consec >= 3:
        chase = check_strong_chase(entry, macd_info, entry['market_score'])
        entry['chase_check'] = chase
        if chase['passed'] >= 5:
            entry['chase_mode'] = 'strong_chase'
        elif chase['passed'] >= 4:
            entry['chase_mode'] = 'watch'
        else:
            entry['chase_mode'] = 'reject'
    else:
        entry['chase_mode'] = 'normal'

    entry['score'] = calc_score(entry)
    return entry


def _classify(entry, ss, s, a, chase, watch):
    mode = entry['chase_mode']
    score = entry['score']
    if mode == 'strong_chase':
        chase.append(entry)
    elif mode == 'watch':
        watch.append(entry)
    elif mode == 'reject':
        return  # 連續漲停但條件不夠，跳過
    elif score >= 85:
        ss.append(entry)
    elif score >= 68:
        s.append(entry)
    elif score >= 52:
        a.append(entry)
    # < 52 淘汰


def _build_stock_block(e):
    sign    = '+' if e['change'] >= 0 else ''
    ema_tag = '(備援EMA)' if e.get('ema_mode') == 'fallback' else ''
    b       = e.get('bias')
    adv     = e.get('adv') or {}
    score_str = f' {e["score"]}分' if 'score' in e else ''
    grade   = e.get('grade', '')
    emoji_open  = e.get('_emoji_open', '')
    emoji_close = e.get('_emoji_close', '')

    lines = [
        f"{emoji_open}【{grade}{score_str}】{e['sid']} {e['name']}{emoji_close}",
        '',
        '🔹基本資料',
        f"收盤價格：{e['price']}　漲幅：{sign}{e['change']}%　量比：{e.get('vol_ratio', 0):.1f}x{ema_tag}",
        f"外資：{fmt_share(e['foreign'])}股　投信：{fmt_share(e['trust'])}股",
    ]
    if b:
        sp = '+' if b['bias_pct'] >= 0 else ''
        lines.append(f"乖離率（10日）：{sp}{b['bias_pct']}%　{b['bias_emoji']} {b['bias_label']}")

    mode = e.get('chase_mode', 'normal')
    zl, zh = calc_entry_zone(e['price'], mode, grade=grade, precision=1)
    if mode == 'strong_chase':
        lines += ['', f"🚀強勢追漲（連續{e.get('consec_limit_up', 0)}日漲停）",
                  f"進場區間：{zl:,.1f} ~ {zh:,.1f} 元（容忍隔日跳空 0~7%）",
                  "➡️ T+1 開盤在此區間以開盤價買；跳空 >7% 則放棄；跳空跌破收盤視為趨勢反轉，不接刀"]
    elif mode == 'watch':
        passed = e.get('chase_check', {}).get('passed', 0)
        lines += ['', f"⚠️觀察名單（連續{e.get('consec_limit_up', 0)}日漲停但條件不足）",
                  f"➡️ 5 項追漲門檻僅通過 {passed}/5，**不買入**只觀察"]
        if e.get('chase_check', {}).get('reasons'):
            lines += [f"  {r}" for r in e['chase_check']['reasons']]
    else:
        gap_label = '3%' if grade == 'SS' else '2%'
        lines += ['', '🎯建議進場區（限價單）',
                  f"進場區間：{zl:,.1f} ~ {zh:,.1f} 元（容忍隔日跳空 0~{gap_label}）",
                  "➡️ 隔日 T+1 觸及才算進場；以實際成交價為準，目標 +5% / +10%、停損 -5%"]

    if adv.get('atr_stop'):
        lines.append(f"參考動態停損（2×ATR）：{adv['atr_stop']:,.1f} 元（{adv['atr_pct']}%）")

    has_adv = any([
        adv.get('rsi_label'), adv.get('resistance_label'),
        adv.get('position_label'), adv.get('obv_label'),
        (e.get('chip_label') and e.get('chip_score', 0) > 0),
        e.get('margin_label'), e.get('macd_label'),
    ])
    if has_adv:
        lines += ['', '📊輔助數據']
        if e.get('macd_label'):       lines.append(f"MACD：{e['macd_label']}")
        if adv.get('rsi_label'):      lines.append(f"RSI：{adv['rsi_label']}")
        if adv.get('resistance_label'): lines.append(f"壓力位：{adv['resistance_label']}")
        if adv.get('position_label'): lines.append(f"位階：{adv['position_label']}")
        if adv.get('obv_label'):      lines.append(f"OBV：{adv['obv_label']}")
        if e.get('chip_label') and e.get('chip_score', 0) > 0:
            lines.append(f"籌碼：{e['chip_label']}")
        if e.get('margin_label'):     lines.append(f"融資：{e['margin_label']}")
    lines.append('─' * 25)
    return '\n'.join(lines)


def _send_message(message):
    """把 message 切成 1900 字內的 chunk 發送到所有 webhook。"""
    chunks, buf = [], ''
    for line in message.splitlines(keepends=True):
        if len(buf) + len(line) > 1900 and buf:
            chunks.append(buf)
            buf = ''
        buf += line
    if buf:
        chunks.append(buf)

    for wh in _get_all_webhooks():
        for i, chunk in enumerate(chunks):
            try:
                requests.post(
                    wh, json={'username': '川投顧量化系統', 'content': chunk},
                    timeout=15,
                )
            except Exception as e:
                logger.warning('[Webhook 發送] 失敗：%s', e)
            if i < len(chunks) - 1:
                time.sleep(0.5)
        time.sleep(1)


def run_analysis(attempt=0, run_mode=None):
    """
    盤後分析主流程。
    回傳狀態字串：'success' / 'holiday' / 'fail'。
    run_mode 沒指定時讀 RUN_MODE 環境變數（向下相容）；建議呼叫端直接傳。
    """
    if not config.DISCORD_WEBHOOK and not config.DATABASE_URL:
        logger.error('[錯誤] 未設定 DISCORD_WEBHOOK 且資料庫未連線，無法發送')
        return 'fail'

    if run_mode is None:
        run_mode = os.environ.get('RUN_MODE', 'auto').strip().lower()
    date_str = get_target_date(run_mode)

    if attempt == 0:
        _notify_all(f'⏳ 量化分析啟動中（{date_str}），預計 10~20 分鐘後發送結果...')
    else:
        logger.info('[排程] 第 %d 次重試（不再發 Discord 通知避免洗版）', attempt)

    now_tw = tw_now()
    if run_mode == 'preview':
        report_type = '盤前複習'
    elif run_mode == 'close':
        report_type = '盤後結算'
    else:
        report_type = '盤後結算' if now_tw.hour >= config.DATA_READY_HOUR else '盤前複習'

    logger.info('[執行] 模式=%s，日期=%s，台灣時間=%s',
                run_mode, date_str, now_tw.strftime('%H:%M'))
    t_start = time.time()

    market = get_market_info(date_str)
    market_foreign_history = fetch_market_foreign_history(date_str, days=3)
    market_env = calc_market_env(market_foreign_history) if market_foreign_history else {
        'score': 0, 'label': '', 'suspend': False,
    }
    if market_env.get('suspend'):
        _notify_all(f'⚠️ {market_env["label"]}')
        return 'success'

    df_i = fetch_t86_cached(date_str)
    r_price = safe_get(
        _MI_INDEX_PRICE_URL,
        params={'response': 'csv', 'date': date_str, 'type': 'ALLBUT0999'},
        timeout=40, retries=5, wait=20,
    )
    if df_i is None:
        _notify_all(
            f'❌ 無法取得 T86 法人資料（{date_str}）\n'
            f'已重試多次，可能是 TWSE 暫時封鎖或限速。\n'
            f'請手動 `/run` 重試（或等明天 17:00 自動排程）。'
        )
        return 'fail'
    if r_price is None:
        _notify_all(f'❌ 無法取得 MI_INDEX 收盤資料（{date_str}）\n已重試多次，請手動 `/run` 重試。')
        return 'fail'
    if df_i.empty or '查詢無資料' in r_price.text:
        logger.info('[假日] %s TWSE 無資料，跳過分析', date_str)
        return 'holiday'

    try:
        for required_col in ('_foreign', '_trust', '_total'):
            if required_col not in df_i.columns:
                _notify_all(
                    f'❌ T86 欄位 {required_col} 不存在（{date_str}）\n'
                    f'現有欄位：{list(df_i.columns)}\n請手動 `/run` 重試。'
                )
                return 'fail'

        if (df_i['_foreign'] == 0).all() and (df_i['_trust'] == 0).all():
            _notify_all(f'❌ T86 法人數據異常（{date_str}：外資+投信全為0）請手動 `/run` 重試。')
            return 'fail'

        price_text = r_price.text
        start_idx  = price_text.find('"證券代號"')
        if start_idx == -1:
            start_idx = price_text.find('證券代號')
        if start_idx == -1:
            _notify_all(f'❌ MI_INDEX 找不到表頭（{date_str}），請手動 `/run` 重試。')
            return 'fail'

        df_p = safe_read_csv(price_text[start_idx:], 'MI_INDEX-PRICE', min_cols=5)
        if df_p.empty:
            _notify_all(f'❌ MI_INDEX 解析失敗（{date_str}），請手動 `/run` 重試。')
            return 'fail'
        df_p = df_p.dropna(thresh=5)
        df_p['sid_clean'] = clean_sid(df_p.iloc[:, 0])
        logger.info('[MI_INDEX] %d 檔', len(df_p))

        df = pd.merge(df_i, df_p, on='sid_clean', how='inner')
        logger.info('[合併] %d 檔', len(df))

        try:
            top_flow_data = extract_top_flow(df, n=10)
            logger.info('[外資榜] 買超 %d / 賣超 %d',
                        len(top_flow_data['buyers']), len(top_flow_data['sellers']))
        except Exception as e:
            top_flow_data = None
            logger.warning('[外資榜] 失敗：%s', e)

        col_close = next((c for c in df.columns if '收盤' in str(c)), None)
        col_diff  = next((c for c in df.columns
                          if '漲跌價差' in str(c) or ('漲跌' in str(c) and '差' in str(c))), None)
        col_sign  = next((c for c in df.columns
                          if '漲跌(+/-)' in str(c) or '漲跌符號' in str(c)), None)

        if not all([col_close, col_diff]):
            raise ValueError(f'找不到收盤/漲跌欄：{list(df.columns)}')

        # 第一輪
        candidates = _filter_first_round(df, df_i, col_close, col_diff, col_sign)
        logger.info('[過濾1] 基本條件通過：%d 檔', len(candidates))
        if len(candidates) > config.MAX_CANDIDATES:
            candidates.sort(key=lambda e: e['total'], reverse=True)
            candidates = candidates[:config.MAX_CANDIDATES]
            logger.info('[過濾4] 截斷至前 %d 名（依法人買超）', config.MAX_CANDIDATES)

        # 第二輪：量比 + EMA + 進階指標 + 評分
        months      = prev_months(date_str, n=7)
        target_date = datetime.strptime(date_str, '%Y%m%d').date()
        logger.info('[EMA] 月份清單：%s', months)

        ss_list, s_list, a_list = [], [], []
        chase_list, watch_list = [], []
        consec_fails = 0

        for idx_c, entry in enumerate(candidates):
            sid = entry['sid']
            try:
                t0 = time.time()
                df_hist = build_history_fast(sid, months)
                elapsed = time.time() - t0

                if df_hist.empty or 'date' not in df_hist.columns or len(df_hist) < 10:
                    consec_fails += 1
                    logger.info('  [%d/%d] %s 歷史資料不足 ✗ %.1fs (連續失敗 %d)',
                                idx_c + 1, len(candidates), sid, elapsed, consec_fails)
                    if consec_fails >= config.RATE_LIMIT_THRESHOLD:
                        logger.warning('  [⏸ 限速退避] 連續 %d 檔抓不到，暫停 %ds 等 TWSE 恢復...',
                                       consec_fails, config.RATE_LIMIT_BACKOFF_SEC)
                        time.sleep(config.RATE_LIMIT_BACKOFF_SEC)
                        consec_fails = 0
                    continue
                consec_fails = 0

                vol_ratio = calc_volume_ratio(df_hist, target_date)
                if vol_ratio < config.VOLUME_RATIO_MIN:
                    logger.info('  [%d/%d] %s 量比%.2f ✗ %.1fs', idx_c + 1, len(candidates), sid, vol_ratio, elapsed)
                    continue

                is_bull, ema_mode = check_ema_bull(df_hist)
                if not is_bull:
                    logger.info('  [%d/%d] %s EMA%s ✗ %.1fs', idx_c + 1, len(candidates), sid, ema_mode, elapsed)
                    continue
                entry['vol_ratio'] = vol_ratio
                entry['ema_mode']  = ema_mode

                _enrich_candidate(entry, df_hist, target_date, market_env, date_str)

                if entry['chase_mode'] == 'reject':
                    consec = entry.get('consec_limit_up', 0)
                    passed = entry.get('chase_check', {}).get('passed', 0)
                    logger.info('  [%d/%d] %s 連續%d日漲停但只過%d/5 項 ✗', idx_c + 1, len(candidates), sid, consec, passed)
                    continue

                _classify(entry, ss_list, s_list, a_list, chase_list, watch_list)
                logger.info("  [%d/%d] %s %s ✓ 漲%s%% 量比%.2f EMA:%s mode:%s %.1fs",
                                idx_c + 1, len(candidates), sid, entry['name'],
                                entry['change'], vol_ratio, ema_mode, entry['chase_mode'], elapsed)
            except Exception as e:
                logger.warning('  [%d/%d] %s 錯誤：%s', idx_c + 1, len(candidates), sid, e)

        for lst in (ss_list, s_list, a_list, chase_list, watch_list):
            lst.sort(key=lambda e: e.get('score', 0), reverse=True)

        # 寫入資料庫（每個 guild 各一份）
        try:
            sd = _date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
            graded = (
                [(e, 'SS')    for e in ss_list]    +
                [(e, 'S')     for e in s_list]     +
                [(e, 'A')     for e in a_list]     +
                [(e, 'CHASE') for e in chase_list] +
                [(e, 'WATCH') for e in watch_list]
            )
            cleaned = []
            for e, g in graded:
                copy = dict(e)
                copy['grade'] = g
                cleaned.append(_normalise_record(copy))
            if cleaned:
                guilds = db.get_all_webhooks()
                for gw in guilds:
                    try:
                        db.save_screen_records(cleaned, sd, gw['guild_id'])
                    except Exception as ge:
                        logger.error('[DB] guild %s 寫入失敗：%s', gw['guild_id'], ge)
                logger.info('[DB] 儲存 %d 筆至 %d 個伺服器', len(cleaned), len(guilds))
        except Exception as e:
            logger.error('[DB] 寫入失敗：%s', e)

        # T+1 撮合
        try:
            today = _date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
            fill_pending_t1_entries(today)
        except Exception as e:
            logger.error('[T+1撮合] 失敗：%s', e)

        # 匯出 dashboard
        try:
            import web_export as _we
            _we.export_dashboard(top_flow=top_flow_data, screen_date_str=date_str)
        except Exception as e:
            logger.error('[Web] Dashboard 匯出失敗：%s', e)

        total_elapsed = time.time() - t_start
        logger.info('[完成] SS=%d S=%d A=%d，總耗時=%.0f秒', len(ss_list), len(s_list), len(a_list), total_elapsed)

        # ── 組裝 Discord 訊息 ──
        if market:
            sd_str = '+' if market['diff'] >= 0 else ''
            sp_str = '+' if market['pct']  >= 0 else ''
            mkt_line = f"加權指數：{market['close']:,.2f}　({sd_str}{market['diff']:.2f} / {sp_str}{market['pct']:.2f}%)"
        else:
            mkt_line = '加權指數：資料未取得'

        header = (
            f'🔶【{report_type}】🔶\n'
            f'日期：{date_str}\n'
            f'{mkt_line}\n\n'
            f'{"=" * 25}'
        )

        sections = []
        for lst, emoji_open, label, emoji_close, limit in (
            (ss_list,    '🔥', 'SS',    '🔥', 10),
            (s_list,     '💎', 'S',     '💎', 10),
            (a_list,     '📈', 'A',     '📈', 10),
            (chase_list, '🚀', 'CHASE', '🚀', 5),
            (watch_list, '⚠️', 'WATCH', '⚠️', 5),
        ):
            for e in lst[:limit]:
                e['grade']        = label
                e['_emoji_open']  = emoji_open
                e['_emoji_close'] = emoji_close
                sections.append(_build_stock_block(e))
        if not sections:
            sections.append('（今日無符合條件之標的）')

        full_message = header + '\n\n' + '\n\n'.join(sections) + '\n\n' + INDICATOR_GUIDE
        _send_message(full_message)
        return 'success'

    except Exception as e:
        logger.error('[主程式錯誤]\n%s', traceback.format_exc())
        _notify_all(f'❌ 系統錯誤：{e}（請手動 `/run` 重試）')
        return 'fail'


if __name__ == '__main__':
    from logging_setup import setup_logging
    setup_logging()
    run_analysis()
