"""
TWSE HTTP 抓資料的共用工具：safe_get / CSV 解析 / 欄位查找。
"""
import io
import logging
import time

import pandas as pd
import requests
import urllib3

import config

logger = logging.getLogger(__name__)

# Note: TWSE 偶爾出現 SSL 憑證錯誤，預設關閉驗證；可用 TWSE_VERIFY_SSL=1 強制開啟。
if not config.TWSE_VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def safe_get(url, params=None, timeout=25, retries=3, wait=15):
    """
    GET TWSE API，逾時 / RequestException 自動重試 retries 次。
    回傳 Response 或 None。
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                url, params=params, headers=config.TWSE_HEADERS,
                timeout=timeout, verify=config.TWSE_VERIFY_SSL,
            )
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout as e:
            last_exc = e
            logger.warning('[逾時] %s（第%d次）', url, attempt)
        except requests.exceptions.RequestException as e:
            last_exc = e
            logger.warning('[失敗] %s（第%d次）：%s', url, attempt, e)
        if attempt < retries:
            time.sleep(wait)
    if last_exc:
        logger.error('[safe_get] 最終放棄：%s', url)
    return None


def safe_read_csv(text, label, skiprows=0, thousands=',', min_cols=2):
    """容錯版 read_csv：解析失敗或欄位不足時回 empty DataFrame，並 log 部分內容供 debug。"""
    try:
        df = pd.read_csv(
            io.StringIO(text), skiprows=skiprows,
            thousands=thousands, on_bad_lines='skip',
        )
    except Exception as e:
        logger.warning('[%s] 解析失敗：%s\n前400字：\n%s', label, e, text[:400])
        return pd.DataFrame()
    if df.shape[1] < min_cols:
        logger.warning('[%s] 欄位不足(%d)，前400字：\n%s', label, df.shape[1], text[:400])
        return pd.DataFrame()
    return df


def find_col(df, *keywords):
    """找第一個欄位名稱包含全部 keyword 的欄。找不到回 None。"""
    for c in df.columns:
        if all(k in str(c) for k in keywords):
            return c
    return None


def clean_sid(series):
    """把證券代號常見的 '=', '"', 空白等雜訊去掉。"""
    return series.astype(str).str.replace(r'[=\" \t]', '', regex=True).str.strip()
