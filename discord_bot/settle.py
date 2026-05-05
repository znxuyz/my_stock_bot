"""
週五 18:00 結算（settle_weekly）。
從 actual_entry_date ~ settle_date 抓 K 棒，掃 high/low 判斷是否觸 target/stop。
"""
import logging
import time

import requests

import config
import db
from matching import get_period_kbars

logger = logging.getLogger(__name__)


def settle_weekly(settle_date, round_num, guild_id='default'):
    """
    結算 settle_date 這天到期的第 round_num 次結算。
    只結算 fill_status='filled'；報酬以 actual_entry_price 為基準；
    若觸停損 → settle_pct 強制視為 -5%；否則用 settle_close。
    """
    webhook = config.DISCORD_WEBHOOK
    if not webhook and not config.DATABASE_URL:
        return

    records = db.get_pending_settle(settle_date, round_num, guild_id)
    if not records:
        logger.info('[結算] %s 第%d次：無待結算記錄', settle_date, round_num)
        return

    round_label = f'第{"一" if round_num == 1 else "二"}次結算（{"1週" if round_num == 1 else "2週"}）'
    screen_dates = sorted(set(str(r['screen_date']) for r in records))
    week_range   = f'{screen_dates[0]} ~ {screen_dates[-1]}'

    results = []
    for r in records:
        actual_entry      = float(r['actual_entry_price'])
        actual_entry_date = r['actual_entry_date']
        t1 = float(r['actual_target1'])    if r['actual_target1']    else None
        t2 = float(r['actual_target2'])    if r['actual_target2']    else None
        sl = float(r['actual_stop_loss']) if r['actual_stop_loss'] else None

        df = get_period_kbars(r['sid'], actual_entry_date, settle_date)
        if df.empty:
            logger.warning('[結算] %s 抓不到 K 棒，跳過', r['sid'])
            continue
        last_row     = df.iloc[-1]
        settle_close = float(last_row['close'])

        hit_t1 = hit_t2 = hit_sl = False
        hit_t1_date = hit_t2_date = hit_sl_date = None
        for row in df.itertuples(index=False):
            d  = row.date
            hi = float(row.high)
            lo = float(row.low)
            if t1 and not hit_t1 and hi >= t1: hit_t1, hit_t1_date = True, d
            if t2 and not hit_t2 and hi >= t2: hit_t2, hit_t2_date = True, d
            if sl and not hit_sl and lo <= sl: hit_sl, hit_sl_date = True, d

        if hit_sl:
            settle_pct = round((sl - actual_entry) / actual_entry * 100, 2)
        else:
            settle_pct = round((settle_close - actual_entry) / actual_entry * 100, 2)

        db.update_settle(
            r['id'], round_num, settle_close,
            hit_t1, hit_t2, hit_sl,
            hit_t1_date, hit_t2_date, hit_sl_date,
            settle_pct=settle_pct,
        )
        results.append({
            'sid': r['sid'], 'name': r.get('name', ''),
            'grade': r['grade'], 'base': actual_entry,
            'cur': settle_close, 'pct': settle_pct,
            't1': t1, 't2': t2, 'sl': sl,
            'hit_t1': hit_t1, 'hit_t2': hit_t2, 'hit_sl': hit_sl,
            'pos_pct': float(r['position_pct']) if r['position_pct'] else 0,
        })

    if not results:
        return

    lines = [
        f'📊 **【週報結算】{round_label}**',
        f'篩選週：{week_range}　結算日：{settle_date}',
        '━━━━━━━━━━━━━━━━━━━━',
    ]
    wins = losses = flats = 0
    sim_pnl = 0.0
    capital = 100000

    for r in results:
        sign  = '+' if r['pct'] >= 0 else ''
        emoji = '✅' if r['pct'] > 0 else ('❌' if r['pct'] < -3 else '➖')
        if r['pct'] > 0:   wins   += 1
        elif r['pct'] < 0: losses += 1
        else:               flats  += 1

        invest  = capital * r['pos_pct'] / 100
        pnl     = invest * r['pct'] / 100
        sim_pnl += pnl
        pnl_str = f'+{pnl:,.0f}' if pnl >= 0 else f'{pnl:,.0f}'

        t1_str = f'目標一 {r["t1"]:,.1f} {"✅" if r["hit_t1"] else "❌"}　' if r['t1'] else ''
        t2_str = f'目標二 {r["t2"]:,.1f} {"✅" if r["hit_t2"] else "❌"}　' if r['t2'] else ''
        sl_str = f'停損 {r["sl"]:,.1f} {"⚠️觸及" if r["hit_sl"] else "✅未觸"}' if r['sl'] else ''
        lines.append(
            f'{emoji} **{r["sid"]} {r["name"]}**｜{r["grade"]} 級\n'
            f'   實際進場 {r["base"]:,.1f} → 結算價 {r["cur"]:,.1f}（{sign}{r["pct"]}%）\n'
            f'   {t1_str}{t2_str}{sl_str}\n'
            f'   建議倉位 {r["pos_pct"]}%　假設投入 {invest:,.0f} 元 → **{pnl_str} 元**'
        )

    total = len(results)
    wr    = round(wins / total * 100, 1) if total else 0
    sim_sign = '+' if sim_pnl >= 0 else ''
    lines += [
        '━━━━━━━━━━━━━━━━━━━━',
        '📈 **績效摘要**',
        f'篩選支數：{total}　獲利：{wins}支　虧損：{losses}支　持平：{flats}支',
        f'勝率：**{wr}%**',
        f'模擬總損益（資金10萬）：**{sim_sign}{sim_pnl:,.0f} 元**',
    ]

    grade_rows, bias_rows, _ = db.get_cumulative_stats(guild_id)
    total_n = db.get_total_screened(guild_id)
    lines.append('━━━━━━━━━━━━━━━━━━━━')
    lines.append(f'🔍 **邏輯檢討**（累積 {total_n} 筆）')

    if grade_rows:
        lines.append('各等級勝率：')
        for g in grade_rows:
            w1 = int(g['win1'] or 0); t1_ = int(g['total'])
            wr_ = round(w1 / t1_ * 100, 1) if t1_ else 0
            ar  = round(float(g['avg_ret1'] or 0), 2)
            sign_ = '+' if ar >= 0 else ''
            lines.append(f'  {g["grade"]} 級：{wr_}%（{w1}/{t1_}）　平均 {sign_}{ar}%')

    if bias_rows:
        lines.append('乖離率勝率：')
        for b in bias_rows:
            w_ = int(b['win'] or 0); t_ = int(b['total'])
            wr_ = round(w_ / t_ * 100, 1) if t_ else 0
            ar  = round(float(b['avg_ret'] or 0), 2)
            sign_ = '+' if ar >= 0 else ''
            lines.append(f'  {b["bias_zone"]}：{wr_}%　平均 {sign_}{ar}%')

    anomalies = []
    for g in grade_rows:
        w1 = int(g['win1'] or 0); t1_ = int(g['total'])
        if t1_ >= 10:
            wr_ = w1 / t1_ * 100
            ar  = float(g['avg_ret1'] or 0)
            if wr_ < 45:
                anomalies.append(f'・{g["grade"]} 級勝率偏低（{wr_:.0f}%），建議提高門檻或移除')
            if ar < 0:
                anomalies.append(f'・{g["grade"]} 級平均報酬為負（{ar:.1f}%），建議調整')
    for b in bias_rows:
        w_ = int(b['win'] or 0); t_ = int(b['total'])
        if t_ >= 10 and b['bias_zone'] == '過高(>8%)':
            wr_ = w_ / t_ * 100
            if wr_ < 40:
                anomalies.append('・乖離率 >8% 勝率過低，建議加入硬過濾（不發出信號）')
    if anomalies:
        lines.append('⚠️ **異常偵測：**')
        lines += anomalies

    suggestions = []
    for g in grade_rows:
        t1_ = int(g['total'])
        if t1_ >= 10:
            wr_ = int(g['win1'] or 0) / t1_ * 100
            if g['grade'] == 'A'  and wr_ < 50: suggestions.append('1. 考慮將 A 級門檻從 1% 提高至 2%')
            if g['grade'] == 'SS' and wr_ > 70: suggestions.append('1. SS 級勝率良好，可考慮提高 SS 級倉位至 30%')
    for b in bias_rows:
        t_ = int(b['total'])
        if t_ >= 10 and b['bias_zone'] == '過高(>8%)':
            if int(b['win'] or 0) / t_ * 100 < 40:
                suggestions.append('2. 加入乖離率 >8% 硬過濾，直接排除不發信號')
    if suggestions:
        lines.append('📋 **修正建議（供討論）：**')
        lines += suggestions

    msg = '\n'.join(lines)
    chunks, buf = [], ''
    for line in msg.splitlines(keepends=True):
        if len(buf) + len(line) > 1900 and buf:
            chunks.append(buf); buf = ''
        buf += line
    if buf:
        chunks.append(buf)

    guild_wh = db.get_guild_webhook(guild_id)
    send_wh  = guild_wh or webhook
    if not send_wh:
        logger.info('[結算] guild=%s 無 webhook，僅完成 DB 寫入', guild_id)
        return
    for chunk in chunks:
        try:
            requests.post(send_wh, json={'content': chunk}, timeout=15)
        except Exception as e:
            logger.warning('[結算] 發送失敗：%s', e)
        time.sleep(0.5)
    logger.info('[結算] %s 第%d次結算完成，%d 筆', settle_date, round_num, len(results))
