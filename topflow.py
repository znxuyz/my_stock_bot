"""
從 T86 + MI_INDEX 合併後的 DataFrame 抽出外資買超 / 賣超 Top N。
給 dashboard / /topbuyer 用。
"""
import pandas as pd


def extract_top_flow(df_merged, n=10):
    """
    回傳 {'buyers': [...], 'sellers': [...]}，每筆含
    sid / name / foreign / trust / close / change_pct
    """
    if df_merged is None or df_merged.empty:
        return {'buyers': [], 'sellers': []}

    df = df_merged.copy()
    name_col  = next((c for c in df.columns if '證券名稱' in str(c)), df.columns[1])
    close_col = next((c for c in df.columns if '收盤'     in str(c)), None)
    diff_col  = next((c for c in df.columns if '漲跌價差' in str(c) or
                                              ('漲跌' in str(c) and '差' in str(c))), None)
    sign_col  = next((c for c in df.columns if '漲跌(+/-)' in str(c) or '漲跌符號' in str(c)), None)

    if close_col:
        df['_close'] = pd.to_numeric(
            df[close_col].astype(str).str.replace(',', ''), errors='coerce')
    if diff_col:
        df['_diff'] = pd.to_numeric(
            df[diff_col].astype(str).str.replace(',', ''), errors='coerce')
        if sign_col:
            df['_diff'] = df.apply(
                lambda r: -abs(r['_diff']) if (
                    '−' in str(r[sign_col]) or str(r[sign_col]).strip() == '-'
                ) else abs(r['_diff']),
                axis=1,
            )

    def _to_records(rows):
        # 注意：底線開頭欄位（_close/_foreign/...）若用 itertuples 會被 pandas 改名。
        # 改用 to_dict('records') 完整保留欄位名稱。
        out = []
        for row_dict in rows.to_dict('records'):
            close = row_dict.get('_close') if close_col else None
            close = float(close) if close is not None and not pd.isna(close) else None
            diff  = row_dict.get('_diff')  if diff_col  else None
            diff  = float(diff)  if diff  is not None and not pd.isna(diff)  else None
            chg = (round(diff / (close - diff) * 100, 2)
                   if (close and diff is not None and (close - diff) != 0) else None)
            foreign = row_dict.get('_foreign')
            trust   = row_dict.get('_trust')
            out.append({
                'sid':        str(row_dict.get('sid_clean', '')),
                'name':       str(row_dict.get(name_col, '')).strip(),
                'foreign':    int(foreign) if foreign is not None and not pd.isna(foreign) else 0,
                'trust':      int(trust)   if trust   is not None and not pd.isna(trust)   else 0,
                'close':      close,
                'change_pct': chg,
            })
        return out

    buyers  = _to_records(df[df['_foreign'] > 0].sort_values('_foreign', ascending=False).head(n))
    sellers = _to_records(df[df['_foreign'] < 0].sort_values('_foreign', ascending=True ).head(n))
    return {'buyers': buyers, 'sellers': sellers}
