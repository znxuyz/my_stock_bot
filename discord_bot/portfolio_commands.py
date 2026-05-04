"""
持倉相關指令：/holding /buy /sell /leaderboard
"""
import db
from time_utils import tw_now

from discord_bot.stock_commands import get_latest_price


def cmd_holding(uid, uname, guild_id='dm'):
    holdings = db.get_holdings(guild_id, uid)
    if not holdings:
        return f'💼 **{uname} 的持倉**\n\n目前尚無持倉記錄。'

    lines = [f'💼 **{uname} 的持倉**\n']
    total_cost = total_mkt = total_unreal = 0
    for h in holdings:
        cost = float(h['price']) * int(h['shares'])
        cur  = get_latest_price(h['sid'])
        if cur:
            mkt    = cur * int(h['shares'])
            unreal = mkt - cost
            sign   = '+' if unreal >= 0 else ''
            emoji  = '🟢' if unreal >= 0 else '🔴'
            pct    = (cur - float(h['price'])) / float(h['price']) * 100
            cur_str = f'{cur:,.1f} 元　{emoji} {sign}{unreal:,.0f}（{sign}{pct:.1f}%）'
            total_mkt    += mkt
            total_unreal += unreal
        else:
            cur_str = '（無法取得最新價格）'
        total_cost += cost
        lines.append(
            f'**{h["sid"]}**　成本 {float(h["price"]):,.1f} 元 × {int(h["shares"]):,} 股\n'
            f'　　　現價 {cur_str}\n'
            f'　　　買入日：{h["buy_date"]}'
        )

    st = '+' if total_unreal >= 0 else ''
    et = '🟢' if total_unreal >= 0 else '🔴'
    lines.append(
        f'\n━━━━━━━━━━━━━━━━\n'
        f'總成本：**{total_cost:,.0f} 元**\n'
        f'市值：**{total_mkt:,.0f} 元**\n'
        f'{et} 未實現損益：**{st}{total_unreal:,.0f} 元**'
    )
    total_pnl = db.get_pnl(guild_id, uid)
    sp = '+' if total_pnl >= 0 else ''
    lines.append(f'💰 已實現損益：**{sp}{total_pnl:,.0f} 元**')
    return '\n'.join(lines)


def cmd_buy(uid, uname, sid, price, shares, guild_id='dm'):
    sid = sid.strip().upper()
    try:
        db.add_holding(guild_id, uid, sid, price, int(shares), tw_now().date())
    except Exception as e:
        return f'❌ 記錄失敗：{e}'
    cost = price * int(shares)
    return (
        f'🛒 **買入記錄成功**\n'
        f'股票：{sid}　價格：{price} 元　股數：{int(shares):,} 股\n'
        f'投入金額：**{cost:,.0f} 元**'
    )


def cmd_sell(uid, uname, sid, price, shares, guild_id='dm'):
    sid = sid.strip().upper()
    realized, err = db.remove_holding(guild_id, uid, sid, price, int(shares))
    if err:
        return f'❌ {err}'
    total_pnl = db.get_pnl(guild_id, uid)
    emoji = '🟢' if realized >= 0 else '🔴'
    sign  = '+' if realized >= 0 else ''
    sp    = '+' if total_pnl >= 0 else ''
    return (
        f'💸 **賣出記錄成功**\n'
        f'股票：{sid}　價格：{price} 元　股數：{int(shares):,} 股\n'
        f'{emoji} 本次損益：**{sign}{realized:,.0f} 元**\n'
        f'累計損益：**{sp}{total_pnl:,.0f} 元**'
    )


def cmd_leaderboard(guild_id='dm'):
    rows = db.get_leaderboard(guild_id)
    if not rows:
        return '🏆 **損益排行榜**\n\n目前尚無任何交易記錄。'
    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    lines  = ['🏆 **損益排行榜**\n']
    for i, row in enumerate(rows):
        total = float(row['total_pnl'])
        sign  = '+' if total >= 0 else ''
        medal = medals[i] if i < len(medals) else f'{i + 1}.'
        lines.append(f'{medal} <@{row["user_id"]}>　{sign}{total:,.0f} 元')
    return '\n'.join(lines)
