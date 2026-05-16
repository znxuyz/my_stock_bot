"""
盤後分析主流程 run_analysis（v6.2）。

策略內核：純 Stalker 篩選 + 10 分制評分 + 5 段分級。
推播範圍：SETUP+；WATCH / NOISE 仍寫進 daily_scores 給分數動能追蹤。
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
from accumulation import detect_stalker_setup
from format_utils import fmt_status_block
from indicators import (
    calc_bias_and_entry,
    calc_bias_20,
    calc_5d_cumulative_change,
    calc_5d_price_range,
    calc_daily_amount,
    calc_ema,
    calc_macd,
    calc_vol_vs_60d_ratio,
    calc_volume_ratio,
    count_limit_ups_in_window,
)
from matching import fill_pending_t1_entries
from scoring import (
    calc_market_env,
    calc_score_v62,
    status_to_emoji,
)
from time_utils import get_target_date, prev_months, tw_now
from topflow import extract_top_flow
from twse_http import clean_sid, safe_get, safe_read_csv
from twse_kbar import build_history_fast
from twse_market import fetch_market_foreign_history, get_market_info
from twse_t86 import (
    fetch_t86_cached,
    fetch_t86_multi_day,
    get_inst_history,
    save_t86_to_history,
)

logger = logging.getLogger(__name__)


_MI_INDEX_PRICE_URL = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'

INDICATOR_GUIDE = """
━━━━━━━━━━━━━━━━━━━━━━━━
📖 **【v6.2 指標說明】**
🎯 **Flow Score（5 分）**：法人累積強度
　• 5/5 連續買 +2　• 累積淨買 ≥ 500K +2　• 籌碼集中度 ≥ 10% +1

📈 **Trend Score（3 分）**：趨勢確認
　• 收盤 > 10MA +1　• 10MA 5 日上彎 +1　• MACD(8,17,5) 多頭 +1

🌡 **Heat Score（2 分，反向加分）**：市場熱度
　• 5 日漲幅 ≤ 2% +1　• 量/60日 ≤ 1.3 +1　• 乖離 20MA ≤ 2% +1
　（滿足 3 → +2、2 → +1、其餘 0）

🏷 **狀態分級**：
　🔵 9-10 MOMENTUM｜🟢 7-8 ACTIVE（主進場）｜🟡 5-6 SETUP（拉回布局）
　🟠 3-4 WATCH（不交易）｜🔴 0-2 NOISE（不交易）

⚙️ **進場 / 倉位**：T+1 開盤市價（一字漲停 missed），ACTIVE 30% / SETUP 18%
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


def _filter_first_round_v62(df, df_i, col_close, col_diff, col_sign):
    """v6.2 第一輪：價格 ≥ 10 元 + 漲幅 -1%~+3% + 法人單日有買（外資 OR 投信 > 0）。

    注意：用 to_dict('records') 而非 itertuples — 後者會把含特殊字元的欄位
    （例如 '漲跌(+/-)'、底線開頭）改名成 _NN 位置索引，導致 KeyError。
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

            # v6.2 漲幅雙向硬擋：-1%~+3%
            if not (config.STALKER_MIN_TODAY_CHANGE <= change <= config.STALKER_MAX_TODAY_CHANGE):
                continue

            inst_row = df_i[df_i['sid_clean'] == sid]
            if inst_row.empty:
                continue
            foreign = float(inst_row[col_foreign].values[0])
            trust   = float(inst_row[col_trust].values[0])
            # 外資 OR 投信當日有買
            if foreign <= 0 and trust <= 0:
                continue

            candidates.append({
                'sid': sid, 'name': name,
                'price': float(price), 'change': change,
                'foreign': int(foreign), 'trust': int(trust),
                'total': int(foreign + trust),
            })
        except Exception as e:
            err_count += 1
            if err_count <= 3:
                logger.warning('[第一輪] 處理列失敗：%s', e)
    if err_count > 3:
        logger.warning('[第一輪] 共 %d 列處理失敗（已抑制重複訊息）', err_count)
    return candidates


def _enrich_candidate_v62(entry, df_hist, target_date, inst_hist):
    """v6.2 第二輪 11 道過濾 + 評分。任一硬條件不過回 None。"""
    sid = entry['sid']

    # 第二輪 1. 量比（vs 5 日均量）1.0 ~ 1.8
    vol_ratio = calc_volume_ratio(df_hist, target_date)
    if not (config.STALKER_VOL_RATIO_MIN <= vol_ratio <= config.STALKER_VOL_RATIO_MAX):
        logger.debug('  [enrich] %s 量比 %.2f 不在 %.1f~%.1f', sid, vol_ratio,
                     config.STALKER_VOL_RATIO_MIN, config.STALKER_VOL_RATIO_MAX)
        return None
    entry['vol_ratio'] = vol_ratio

    # 第二輪 2. 今日量 / 60 日均量 < 2.0
    vol60 = calc_vol_vs_60d_ratio(df_hist)
    if vol60 is not None and vol60 > config.STALKER_MAX_VOL_VS_60D:
        return None
    entry['vol_vs_60d'] = vol60

    # 第二輪 3. 日成交金額 ≥ 5000 萬
    amount = calc_daily_amount(df_hist)
    if amount < config.MIN_DAILY_AMOUNT:
        return None

    # 第二輪 4. 5 日累積漲幅 -2% ~ +3%
    cum5d = calc_5d_cumulative_change(df_hist)
    if cum5d is None or not (
        config.STALKER_MIN_CUM_CHANGE <= cum5d <= config.STALKER_MAX_CUM_CHANGE
    ):
        return None
    entry['cum_5d_pct'] = cum5d

    # 第二輪 5. 5 日 high/low 振幅 < 5%
    pr = calc_5d_price_range(df_hist)
    if pr is None or pr >= config.STALKER_MAX_PRICE_RANGE:
        return None

    # 第二輪 6. 10 日內漲停次數 = 0
    if count_limit_ups_in_window(df_hist, window=10) > config.STALKER_MAX_LIMIT_UPS_10D:
        return None

    # 第二輪 7. EMA 20 > EMA 60
    closes = df_hist['close'].astype(float)
    if len(df_hist) < 60:
        return None
    if calc_ema(closes, 20).iloc[-1] <= calc_ema(closes, 60).iloc[-1]:
        return None

    # 第二輪 8. 乖離 10MA ≤ 5%
    bias_info = calc_bias_and_entry(df_hist, entry['price'])
    if not bias_info or bias_info['bias_pct'] > config.STALKER_MAX_BIAS_10:
        return None
    entry['bias'] = bias_info

    # 第二輪 9. 乖離 20MA ≤ 3%
    bias20 = calc_bias_20(df_hist)
    if bias20 is None or bias20 > config.STALKER_MAX_BIAS_20:
        return None
    entry['bias_20'] = bias20

    # 第二輪 10. Stalker 累積偵測：5 日法人買 ≥ 4 天 且累積淨買 > 0
    setup = detect_stalker_setup(df_hist, inst_hist)
    if not setup['is_stalker']:
        return None
    entry['acc_buy_days'] = setup['buy_days']
    entry['acc_cum_net']  = setup['cum_net']

    # 第二輪 11. 計算 10 分制評分
    entry['macd_info'] = calc_macd(df_hist)
    score_result = calc_score_v62(entry, df_hist)
    entry.update(score_result)

    return entry


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
    """盤後分析主流程。回傳 'success' / 'holiday' / 'fail'。"""
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
    if df_i.empty:
        logger.info('[假日] %s T86 回「查詢無資料」，跳過分析', date_str)
        return 'holiday'
    if '查詢無資料' in r_price.text:
        logger.info('[假日] %s MI_INDEX 回「查詢無資料」，跳過分析', date_str)
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

        # v6.2 新：抓多日 T86 → 寫 history（給 Stalker 偵測讀）
        try:
            multi = fetch_t86_multi_day(date_str, days=config.STALKER_DAYS)
            saved = 0
            for d_str, d_df in multi.items():
                saved += save_t86_to_history(d_str, d_df)
            logger.info('[T86 history] %d 日抓取、共寫入 %d 筆', len(multi), saved)
        except Exception as e:
            logger.warning('[T86 history] 失敗：%s', e)

        target_date = datetime.strptime(date_str, '%Y%m%d').date()

        # 第一輪
        candidates = _filter_first_round_v62(df, df_i, col_close, col_diff, col_sign)
        logger.info('[過濾1] 基本條件通過：%d 檔', len(candidates))

        # v6.2 排序鍵：5 日累積法人淨買 DESC（Phase 2 起改 velocity）
        def _sort_key(c):
            try:
                h = get_inst_history(c['sid'], days=config.STALKER_DAYS,
                                     end_date=target_date)
                return sum(n for _, n in h) if h else 0
            except Exception:
                return 0
        candidates.sort(key=_sort_key, reverse=True)
        if len(candidates) > config.MAX_CANDIDATES:
            candidates = candidates[:config.MAX_CANDIDATES]
            logger.info('[過濾2] 截斷至前 %d 名（5 日累積法人淨買 DESC）',
                        config.MAX_CANDIDATES)

        # 第二輪 + 評分
        months = prev_months(date_str, n=7)
        logger.info('[EMA] 月份清單：%s', months)
        enriched = []
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

                try:
                    inst_hist = get_inst_history(sid, days=config.STALKER_DAYS,
                                                 end_date=target_date)
                except Exception as e:
                    logger.warning('  [%d/%d] %s 法人歷史讀取失敗：%s',
                                   idx_c + 1, len(candidates), sid, e)
                    inst_hist = []

                result = _enrich_candidate_v62(entry, df_hist, target_date, inst_hist)
                if result is not None:
                    enriched.append(result)
                    logger.info(
                        "  [%d/%d] %s %s ✓ %d/10 %s 累積%d/5 %.1fs",
                        idx_c + 1, len(candidates), sid, entry.get('name', ''),
                        result['total_score'], result['status'],
                        result['acc_buy_days'], elapsed,
                    )
                else:
                    logger.info('  [%d/%d] %s 未通過 v6.2 過濾 %.1fs',
                                idx_c + 1, len(candidates), sid, elapsed)
            except Exception as e:
                logger.warning('  [%d/%d] %s 錯誤：%s', idx_c + 1, len(candidates), sid, e)

        # 5 段分桶
        buckets = {'MOMENTUM': [], 'ACTIVE': [], 'SETUP': [], 'WATCH': [], 'NOISE': []}
        for r in enriched:
            buckets[r['status']].append(r)
        for v in buckets.values():
            v.sort(key=lambda e: e.get('total_score', 0), reverse=True)

        push_list = buckets['MOMENTUM'] + buckets['ACTIVE'] + buckets['SETUP']

        # 寫 daily_scores（所有 enriched candidate 都寫，含 WATCH/NOISE）
        try:
            for r in enriched:
                db.save_daily_score(
                    sid=r['sid'], date=target_date,
                    flow=r['flow_score'], trend=r['trend_score'],
                    heat=r['heat_score'], total=r['total_score'],
                    status=r['status'],
                )
        except Exception as e:
            logger.error('[daily_scores] 寫入失敗：%s', e)

        # 寫 screen_records（只寫 SETUP+，WATCH/NOISE 不進主表）
        try:
            cleaned = [_normalise_record(r) for r in push_list]
            if cleaned:
                guilds = db.get_all_webhooks()
                for gw in guilds:
                    try:
                        db.save_screen_records(cleaned, target_date, gw['guild_id'])
                    except Exception as ge:
                        logger.error('[DB] guild %s 寫入失敗：%s', gw['guild_id'], ge)
                logger.info('[DB] 儲存 %d 筆至 %d 個伺服器',
                            len(cleaned), len(guilds))
        except Exception as e:
            logger.error('[DB] screen_records 寫入失敗：%s', e)

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
        logger.info('[完成] MOMENTUM=%d ACTIVE=%d SETUP=%d (WATCH=%d NOISE=%d)，總耗時=%.0f秒',
                    len(buckets['MOMENTUM']), len(buckets['ACTIVE']),
                    len(buckets['SETUP']), len(buckets['WATCH']),
                    len(buckets['NOISE']), total_elapsed)

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
        for label in ('MOMENTUM', 'ACTIVE', 'SETUP'):
            lst = buckets[label]
            if not lst:
                continue
            sections.append(
                f"\n━━━ {status_to_emoji(label)} {label} ({len(lst)} 檔) ━━━"
            )
            for e in lst[:10]:
                sections.append(fmt_status_block(e))

        if not sections:
            sections.append('（今日無符合 v6.2 Stalker 條件之標的；'
                            '「找不到標的」是 feature——今天沒有好的累積 setup）')

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
