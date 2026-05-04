"""
統計報表：/report /stats
"""
import traceback

import db


def cmd_report(guild_id='dm'):
    """累積統計：進場率 + 賺錢勝率"""
    try:
        grade_rows, bias_rows, _ = db.get_cumulative_stats(guild_id)
        total_n = db.get_total_screened(guild_id)
        if total_n == 0:
            return 'ℹ️ 尚無任何篩選記錄，等每日分析跑完後才會有資料。'

        lines = [f'📊 **累積統計（共 {total_n} 筆）**',
                 '進場率 = T+1 觸及進場區間的比例；賺錢勝率 = 已進場中賺錢的比例\n']
        if grade_rows:
            lines.append('**各等級指標：**')
            for g in grade_rows:
                t  = int(g['total'])
                fi = int(g['filled'] or 0); mi = int(g['missed'] or 0)
                decided = fi + mi
                fr = round(fi / decided * 100, 1) if decided else 0
                w1 = int(g['win1'] or 0); s1 = int(g['settled1'] or 0)
                wr = round(w1 / s1 * 100, 1) if s1 else 0
                ar = round(float(g['avg_ret1'] or 0), 2)
                sgn = '+' if ar >= 0 else ''
                lines.append(
                    f'  {g["grade"]} 級（{t} 筆）：進場率 {fr}%（{fi}/{decided}）　'
                    f'1週賺錢勝率 {wr}%（{w1}/{s1}）　均報酬 {sgn}{ar}%'
                )
        if bias_rows:
            lines.append('\n**依乖離率：**')
            for b in bias_rows:
                fi = int(b['filled'] or 0); mi = int(b['missed'] or 0)
                decided = fi + mi
                fr = round(fi / decided * 100, 1) if decided else 0
                w  = int(b['win'] or 0); st = int(b['settled'] or 0)
                wr = round(w / st * 100, 1) if st else 0
                ar = round(float(b['avg_ret'] or 0), 2)
                sgn = '+' if ar >= 0 else ''
                lines.append(
                    f'  {b["bias_zone"]}：進場率 {fr}%　賺錢勝率 {wr}%　均報酬 {sgn}{ar}%'
                )
        return '\n'.join(lines)
    except Exception as e:
        return f'❌ 查詢失敗：{e}\n```\n{traceback.format_exc()[-500:]}\n```'


def cmd_stats(guild_id='dm'):
    """詳細統計 + 修正建議（含法人組合的進場率與勝率）"""
    try:
        grade_rows, bias_rows, dual_rows = db.get_cumulative_stats(guild_id)
        total_n = db.get_total_screened(guild_id)
        if total_n == 0:
            return 'ℹ️ 尚無任何篩選記錄。'

        lines = [f'📈 **詳細統計（累積 {total_n} 筆）**\n']
        lines.append('**法人組合：**')
        for d in dual_rows:
            t  = int(d['total'])
            if t == 0: continue
            fi = int(d['filled'] or 0); mi = int(d['missed'] or 0)
            decided = fi + mi
            fr = round(fi / decided * 100, 1) if decided else 0
            w  = int(d['win'] or 0); st = int(d['settled'] or 0)
            wr = round(w / st * 100, 1) if st else 0
            ar = round(float(d['avg_ret'] or 0), 2)
            s = '+' if ar >= 0 else ''
            lines.append(
                f'  {d["buy_type"]}（{t} 筆）：'
                f'進場率 {fr}%　賺錢勝率 {wr}%　均報酬 {s}{ar}%'
            )

        lines.append('\n**命中目標 / 觸停損：**')
        for g in grade_rows:
            t = int(g['total'])
            if t == 0: continue
            ht1 = int(g['hit_t1'] or 0)
            ht2 = int(g['hit_t2'] or 0)
            hsl = int(g['hit_sl'] or 0)
            fi  = int(g['filled'] or 0)
            lines.append(
                f'  {g["grade"]} 級：命中目標一 {ht1}/{fi}、'
                f'命中目標二 {ht2}/{fi}、觸停損 {hsl}/{fi}'
            )

        suggestions = []
        for g in grade_rows:
            fi = int(g['filled'] or 0); mi = int(g['missed'] or 0)
            decided = fi + mi
            if decided >= 10:
                fr = fi / decided * 100
                if fr < 40:
                    suggestions.append(
                        f'・{g["grade"]} 級進場率僅 {fr:.0f}% (<40%)，'
                        '建議放寬進場區間（例如改用 close × 0.95~1.00）'
                    )
            s1 = int(g['settled1'] or 0)
            if s1 >= 10:
                wr = int(g['win1'] or 0) / s1 * 100
                ar = float(g['avg_ret1'] or 0)
                if g['grade'] == 'A' and wr < 50:
                    suggestions.append('・A 級賺錢勝率 <50%，考慮提高分數門檻')
                if g['grade'] == 'SS' and wr > 70:
                    suggestions.append('・SS 級勝率良好（>70%），可考慮加大倉位')
                if ar < -1:
                    suggestions.append(f'・{g["grade"]} 級平均報酬為負，需重新評估')
        for b in bias_rows:
            st = int(b['settled'] or 0)
            if st >= 10 and b['bias_zone'] == '過高(>8%)' and int(b['win'] or 0) / st * 100 < 40:
                suggestions.append('・乖離率 >8% 賺錢勝率過低，建議加入硬過濾')
        if suggestions:
            lines.append('\n📋 **修正建議：**')
            lines += suggestions
        else:
            lines.append('\n✅ 目前尚無明顯異常，繼續累積樣本中。')
        return '\n'.join(lines)
    except Exception as e:
        return f'❌ 查詢失敗：{e}'
