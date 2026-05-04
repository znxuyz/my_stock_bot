"""
選股挑戰：/challenge 與週五自動結算 settle_challenge。
"""
from datetime import timedelta

import requests

import config
import db
from discord_bot.stock_commands import get_latest_price
from time_utils import tw_now


def cmd_challenge(uid, uname, sid, guild_id='dm'):
    sid = sid.strip().upper()
    week_key = tw_now().strftime('%Y-W%W')

    existing = db.get_challenge(guild_id, uid, week_key)
    if existing:
        return (
            f'⚔️ 你本週已提交挑戰股票：**{existing["sid"]}**\n'
            f'起始價格：{float(existing["start_price"]):,.1f} 元\n'
            f'挑戰截止：{existing["end_date"]}'
        )

    start_price = get_latest_price(sid)
    if start_price is None:
        return f'❌ 無法取得 {sid} 的最新價格，請確認股票代號是否正確。'

    now_d = tw_now().date()
    days_to_fri = (4 - now_d.weekday()) % 7
    if days_to_fri == 0:
        days_to_fri = 7
    end_date = (now_d + timedelta(days=days_to_fri)).strftime('%Y/%m/%d')
    db.add_challenge(guild_id, uid, week_key, sid, start_price, end_date)

    all_ch = []
    for ch in db.get_all_challenges(guild_id, week_key):
        all_ch.append(
            f'<@{ch["user_id"]}>　**{ch["sid"]}**　起始 {float(ch["start_price"]):,.1f} 元'
        )

    lines = [
        '⚔️ **本週選股挑戰**\n',
        f'✅ <@{uid}> 加入挑戰！股票：**{sid}**　起始價：{start_price} 元\n',
        f'📅 截止日期：{end_date}\n',
        '**本週參賽名單：**',
    ] + all_ch
    return '\n'.join(lines)


def settle_challenge():
    """週五 21:00 自動結算本週挑戰。"""
    webhook = config.DISCORD_WEBHOOK
    week_key = tw_now().strftime('%Y-W%W')

    guilds = db.get_all_webhooks()

    results = []
    for gw in guilds:
        for ch in db.get_all_challenges(gw['guild_id'], week_key):
            cur = get_latest_price(ch['sid'])
            if cur is None:
                continue
            start = float(ch['start_price'])
            pct   = round((cur - start) / start * 100, 2)
            results.append({
                'uid': ch['user_id'], 'sid': ch['sid'],
                'start': start, 'cur': cur, 'pct': pct,
                'webhook': gw['webhook_url'], 'guild_id': gw['guild_id'],
            })

    if not results:
        if webhook:
            requests.post(
                webhook,
                json={'content': '⚔️ **本週選股挑戰結算**\n\n本週無人參賽。'},
                timeout=10,
            )
        return

    results.sort(key=lambda x: x['pct'], reverse=True)
    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    lines  = ['⚔️ **本週選股挑戰結算！**\n']
    for i, r in enumerate(results):
        sign  = '+' if r['pct'] >= 0 else ''
        emoji = '🟢' if r['pct'] >= 0 else '🔴'
        medal = medals[i] if i < len(medals) else f'{i + 1}.'
        lines.append(
            f'{medal} <@{r["uid"]}> **{r["sid"]}**\n'
            f'   起始 {r["start"]:,.1f} → 現價 {r["cur"]:,.1f} 元\n'
            f'   {emoji} {sign}{r["pct"]}%'
        )

    winner = results[0]
    win_sign = '+' if winner['pct'] >= 0 else ''
    lines.append(
        f'\n🏆 本週冠軍：<@{winner["uid"]}> 的 **{winner["sid"]}**，'
        f'報酬率 {win_sign}{winner["pct"]}%！'
    )
    lines.append('\n⚔️ 下週挑戰已重置，歡迎用 `/challenge` 繼續參賽！')
    if webhook:
        try:
            requests.post(webhook, json={'content': '\n'.join(lines)}, timeout=10)
        except Exception as e:
            print(f'[挑戰結算] webhook 失敗：{e}')

    for gw in guilds:
        try:
            db.clear_challenges(gw['guild_id'], week_key)
        except Exception as e:
            print(f'[挑戰結算] 清空失敗 guild={gw["guild_id"]}：{e}')
    print(f'[挑戰] 週五結算完成並清零，共 {len(results)} 人參賽')
