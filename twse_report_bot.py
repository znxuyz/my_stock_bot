"""
台灣股市量化戰報機器人
======================
數據來源：台灣證交所 (TWSE) Open API
發送管道：Discord Webhook
排程方式：GitHub Actions

個股分級邏輯（核心需法人買超 > 0）:
  🔥 SS 級：漲幅 >= 7%
  💎 S  級：漲幅 >= 3.5%
  📈 A  級：漲幅 >= 1%
  🔍 X  級：跌幅 0%~-3%，但法人逆勢買超（獨立顯示於「潛在起漲區」）
"""

import os
import re
import io
import time
import logging
import requests
from datetime import datetime, timedelta

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
REQUEST_TIMEOUT     = 20          # 每次 HTTP 請求的 timeout（秒）
MARKET_RETRY_MAX    = 3           # 大盤法人數據最大重試次數
MARKET_RETRY_WAIT   = 15          # 每次重試間隔（秒）
DISCORD_CHUNK_LIMIT = 1900        # Discord 單則訊息上限留緩衝

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 交易日判定
# ─────────────────────────────────────────
def get_trade_date(mode: str = "close") -> str:
    """
    回傳應抓取的交易日字串（YYYYMMDD）。

    mode:
      "close" → 當日（盤後 16:00–18:00 使用）
      "preview" → 前一交易日（盤前 08:00 複習使用）
    """
    today = datetime.now()

    if mode == "preview":
        # 往前找上一個交易日（跳過週末）
        delta = 1
        if today.weekday() == 0:   # 週一 → 回溯週五
            delta = 3
        elif today.weekday() == 6: # 週日 → 回溯週五
            delta = 2
        target = today - timedelta(days=delta)
    else:
        # 盤後結算：若今天是週六/週日，回溯至週五
        if today.weekday() == 5:
            target = today - timedelta(days=1)
        elif today.weekday() == 6:
            target = today - timedelta(days=2)
        else:
            target = today

    return target.strftime("%Y%m%d")


# ─────────────────────────────────────────
# 數據抓取工具
# ─────────────────────────────────────────
def _safe_get(url: str, params: dict = None) -> requests.Response | None:
    """帶 timeout 的 GET，失敗回傳 None。"""
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.exceptions.Timeout:
        log.warning("請求逾時：%s", url)
    except requests.exceptions.RequestException as e:
        log.warning("請求失敗：%s → %s", url, e)
    return None


def _find_header_row(lines: list[str], keyword: str) -> int:
    """在 CSV 行列表中找出含有 keyword 的表頭行索引，找不到回傳 -1。"""
    for i, line in enumerate(lines):
        if keyword in line:
            return i
    return -1


def _parse_number(s: str) -> float:
    """移除千分位逗號後轉 float，失敗回傳 0.0。"""
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


# ─────────────────────────────────────────
# 個股數據：MI_INDEX（當日行情）
# ─────────────────────────────────────────
def fetch_stock_prices(date_str: str) -> dict[str, dict]:
    """
    抓取 MI_INDEX CSV，回傳 {股票代號: {name, price, change_pct}} 字典。
    """
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    resp = _safe_get(url, params={"response": "csv", "date": date_str, "type": "ALLBUT0999"})
    if resp is None:
        return {}

    if "查詢無資料" in resp.text:
        log.info("MI_INDEX 查詢無資料（%s）", date_str)
        return {}

    lines = resp.text.splitlines()
    header_idx = _find_header_row(lines, '"證券代號"')
    if header_idx == -1:
        log.warning("MI_INDEX 找不到表頭")
        return {}

    stocks = {}
    import csv
    reader = csv.reader(lines[header_idx:])
    headers = next(reader, None)
    if headers is None:
        return {}

    # 去掉雙引號，找出欄位索引
    h = [c.strip().strip('"') for c in headers]
    try:
        idx_code    = h.index("證券代號")
        idx_name    = h.index("證券名稱")
        idx_close   = h.index("收盤價")
        idx_chg     = h.index("漲跌價差")   # 有些版本叫「漲跌(+/-) 」
    except ValueError:
        # 嘗試備用欄名
        try:
            idx_code  = h.index("股票代號")
            idx_name  = h.index("股票名稱")
            idx_close = h.index("收盤")
            idx_chg   = h.index("漲跌")
        except ValueError:
            log.warning("MI_INDEX 表頭欄位不符，headers=%s", h[:10])
            return {}

    for row in reader:
        if len(row) <= max(idx_code, idx_name, idx_close, idx_chg):
            continue
        code  = row[idx_code].strip().strip('"')
        name  = row[idx_name].strip().strip('"')
        close = _parse_number(row[idx_close])
        chg   = _parse_number(row[idx_chg])
        if close == 0:
            continue
        change_pct = round(chg / (close - chg) * 100, 2) if (close - chg) != 0 else 0.0
        stocks[code] = {"name": name, "close": close, "change_pct": change_pct}

    log.info("MI_INDEX 抓到 %d 檔股票", len(stocks))
    return stocks


# ─────────────────────────────────────────
# 法人買賣超：T86（個股三大法人）
# ─────────────────────────────────────────
def fetch_institutional_buying(date_str: str) -> dict[str, float]:
    """
    抓取 T86 CSV，回傳 {股票代號: 外資+投信+自營商合計買賣超(張)} 字典。
    """
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"
    resp = _safe_get(url, params={"response": "csv", "date": date_str, "selectType": "ALLBUT0999"})
    if resp is None:
        return {}

    if "查詢無資料" in resp.text:
        log.info("T86 查詢無資料（%s）", date_str)
        return {}

    lines = resp.text.splitlines()
    header_idx = _find_header_row(lines, '"證券代號"')
    if header_idx == -1:
        log.warning("T86 找不到表頭")
        return {}

    import csv
    reader = csv.reader(lines[header_idx:])
    headers = next(reader, None)
    if headers is None:
        return {}

    h = [c.strip().strip('"') for c in headers]
    try:
        idx_code  = h.index("證券代號")
        # 三大法人合計買賣超：外資買賣超 + 投信買賣超 + 自營商買賣超
        # 欄位名稱依日期可能略有差異，嘗試多種
        net_cols = []
        for col_name in ["三大法人買賣超股數", "合計買賣超股數", "合計"]:
            if col_name in h:
                net_cols.append(h.index(col_name))
                break
        # 若找不到合計欄，嘗試加總三欄
        if not net_cols:
            for col_name in ["外資買賣超股數", "投信買賣超股數", "自營商買賣超股數"]:
                if col_name in h:
                    net_cols.append(h.index(col_name))
    except ValueError:
        log.warning("T86 欄位解析失敗，headers=%s", h[:12])
        return {}

    if not net_cols:
        log.warning("T86 找不到買賣超欄位")
        return {}

    buying = {}
    for row in reader:
        if not row or len(row) <= idx_code:
            continue
        code = row[idx_code].strip().strip('"')
        if not re.match(r"^\d{4,6}$", code):
            continue
        total = sum(_parse_number(row[c]) for c in net_cols if c < len(row))
        buying[code] = total  # 股 → 之後除以 1000 換算「張」在顯示端做

    log.info("T86 抓到 %d 檔法人數據", len(buying))
    return buying


# ─────────────────────────────────────────
# 大盤指數與法人合計：BFI82U（非強制）
# ─────────────────────────────────────────
def fetch_market_info(date_str: str) -> dict:
    """
    抓取加權指數收盤與三大法人大盤合計買賣超（億元）。
    此函式為「非強制」—— 若資料未出或逾時，回傳空 dict，不阻斷主程式。
    內建重試機制。
    """
    result = {}

    # ── 加權指數 (MI_INDEX 類型 = 大盤) ──
    url_taiex = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    resp = _safe_get(url_taiex, params={"response": "json", "date": date_str, "type": "MS"})
    if resp:
        try:
            data = resp.json()
            # 找「加權股價指數」那一列
            for row in data.get("data", []):
                if "加權股價指數" in str(row):
                    result["taiex_close"]  = _parse_number(str(row[1]))
                    result["taiex_change"] = _parse_number(str(row[2]))
                    result["taiex_pct"]    = _parse_number(str(row[3]).replace("%", ""))
                    break
        except Exception as e:
            log.warning("解析大盤指數失敗：%s", e)

    # ── 三大法人大盤合計 BFI82U（含重試）──
    url_inst = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    for attempt in range(1, MARKET_RETRY_MAX + 1):
        resp = _safe_get(url_inst, params={"response": "csv", "dayDate": date_str, "type": "day"})
        if resp is None:
            log.info("BFI82U 第 %d 次嘗試失敗，%ds 後重試", attempt, MARKET_RETRY_WAIT)
            time.sleep(MARKET_RETRY_WAIT)
            continue
        if "查詢無資料" in resp.text:
            log.info("BFI82U 查詢無資料（%s）", date_str)
            break

        lines = resp.text.splitlines()
        # 找「三大法人合計」那一列
        for line in lines:
            if "三大法人" in line and "合計" in line:
                parts = line.replace('"', '').split(',')
                # 格式通常：分類,買進金額,賣出金額,買賣超金額
                if len(parts) >= 4:
                    net_val = _parse_number(parts[-1])
                    result["inst_net_billion"] = round(net_val / 1e8, 2)
                break
        if "inst_net_billion" in result:
            log.info("BFI82U 成功（第 %d 次）", attempt)
            break
        log.info("BFI82U 第 %d 次：找不到合計列，%ds 後重試", attempt, MARKET_RETRY_WAIT)
        time.sleep(MARKET_RETRY_WAIT)

    return result


# ─────────────────────────────────────────
# 個股分級
# ─────────────────────────────────────────
GRADE_RULES = [
    ("SS", "🔥", 7.0),
    ("S",  "💎", 3.5),
    ("A",  "📈", 1.0),
]

def grade_stock(change_pct: float, inst_shares: float) -> str | None:
    """
    回傳個股等級字串（"SS"/"S"/"A"/"X"），或 None 表示不入選。
    inst_shares 單位：股（正數 = 買超）。
    """
    if inst_shares <= 0:
        return None  # 核心條件：法人買超 > 0

    for grade, _, threshold in GRADE_RULES:
        if change_pct >= threshold:
            return grade

    # X 級：小跌但法人逆勢買超
    if -3.0 <= change_pct < 0:
        return "X"

    return None


def classify_stocks(
    prices: dict[str, dict],
    buying: dict[str, float],
) -> tuple[list[dict], list[dict]]:
    """
    回傳 (hot_stocks, x_stocks)。
    hot_stocks: SS/S/A 級，依漲幅降序排列。
    x_stocks:   X 級，依法人買超降序排列。
    """
    hot, x_list = [], []

    for code, info in prices.items():
        inst = buying.get(code, 0.0)
        chg  = info["change_pct"]
        grade = grade_stock(chg, inst)
        if grade is None:
            continue

        entry = {
            "code":       code,
            "name":       info["name"],
            "close":      info["close"],
            "change_pct": chg,
            "inst_lots":  round(inst / 1000, 0),  # 股轉換為張
            "grade":      grade,
        }

        if grade == "X":
            x_list.append(entry)
        else:
            hot.append(entry)

    hot.sort(key=lambda e: e["change_pct"], reverse=True)
    x_list.sort(key=lambda e: e["inst_lots"], reverse=True)
    return hot, x_list


# ─────────────────────────────────────────
# 訊息格式化
# ─────────────────────────────────────────
GRADE_EMOJI = {"SS": "🔥", "S": "💎", "A": "📈", "X": "🔍"}

def _fmt_stock_line(s: dict) -> str:
    sign    = "+" if s["change_pct"] >= 0 else ""
    chg_str = f"{sign}{s['change_pct']:.2f}%"
    inst_k  = int(s["inst_lots"])
    inst_str = f"+{inst_k:,}張" if inst_k >= 0 else f"{inst_k:,}張"
    emoji   = GRADE_EMOJI[s["grade"]]
    return (
        f"{emoji} [{s['grade']}] {s['code']} {s['name']}　"
        f"{s['close']} ({chg_str})　法人:{inst_str}"
    )


def format_message(
    date_str: str,
    mode: str,
    hot_stocks: list[dict],
    x_stocks: list[dict],
    market: dict,
) -> str:
    """
    組裝 Discord 純文字訊息。
    mode: "close"（盤後結算）或 "preview"（盤前複習）
    """
    dt = datetime.strptime(date_str, "%Y%m%d")
    date_label = dt.strftime("%Y/%m/%d")
    mode_label = "【盤後結算】" if mode == "close" else "【盤前複習】"

    lines = []
    lines.append(f"☀️ 川投顧 {mode_label}　{date_label}")
    lines.append("=" * 30)

    # 大盤區塊（選擇性）
    if market:
        taiex_close = market.get("taiex_close")
        taiex_pct   = market.get("taiex_pct")
        inst_net    = market.get("inst_net_billion")

        if taiex_close is not None:
            sign = "+" if (taiex_pct or 0) >= 0 else ""
            lines.append(
                f"📊 加權指數：{taiex_close:,.2f}　"
                f"({sign}{taiex_pct:.2f}%)"
            )
        if inst_net is not None:
            sign = "+" if inst_net >= 0 else ""
            lines.append(f"💰 三大法人大盤合計：{sign}{inst_net:.2f} 億元")
        lines.append("")

    # 強勢股主列表
    if hot_stocks:
        lines.append("─ 強勢標的 ─")
        for s in hot_stocks:
            lines.append(_fmt_stock_line(s))
    else:
        lines.append("（今日無符合強勢條件標的）")

    # X 級獨立區塊
    if x_stocks:
        lines.append("")
        lines.append("─ 潛在起漲區（小跌+法人逆勢買） ─")
        for s in x_stocks:
            lines.append(_fmt_stock_line(s))

    lines.append("=" * 30)
    return "\n".join(lines)


# ─────────────────────────────────────────
# Discord 發送
# ─────────────────────────────────────────
def send_to_discord(text: str) -> None:
    """
    將訊息分段發送至 Discord Webhook（每段 < DISCORD_CHUNK_LIMIT 字元）。
    """
    if not DISCORD_WEBHOOK_URL:
        log.error("DISCORD_WEBHOOK_URL 未設定，無法發送")
        print(text)   # CI 環境仍印出供偵錯
        return

    chunks = _split_message(text)
    for i, chunk in enumerate(chunks, 1):
        payload = {"content": chunk}
        try:
            resp = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            log.info("Discord 發送成功（第 %d/%d 段）", i, len(chunks))
        except requests.exceptions.RequestException as e:
            log.error("Discord 發送失敗（第 %d 段）：%s", i, e)
        if i < len(chunks):
            time.sleep(1)   # 避免觸發 Discord rate limit


def _split_message(text: str) -> list[str]:
    """依 DISCORD_CHUNK_LIMIT 分割訊息，優先在換行處切分。"""
    if len(text) <= DISCORD_CHUNK_LIMIT:
        return [text]

    chunks, current = [], []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > DISCORD_CHUNK_LIMIT and current:
            chunks.append("".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


# ─────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────
def main() -> None:
    # 判定模式：由環境變數 RUN_MODE 決定，預設 "close"
    mode = os.environ.get("RUN_MODE", "close").strip().lower()
    if mode not in ("close", "preview"):
        mode = "close"

    date_str = get_trade_date(mode)
    log.info("執行模式：%s，交易日：%s", mode, date_str)

    # ── Step 1：並行（循序）抓取，大盤為非強制 ──
    log.info("抓取個股行情（MI_INDEX）...")
    prices = fetch_stock_prices(date_str)

    log.info("抓取個股法人（T86）...")
    buying = fetch_institutional_buying(date_str)

    log.info("抓取大盤資訊（BFI82U，非強制）...")
    market = fetch_market_info(date_str)

    # ── Step 2：個股過濾與分級 ──
    if not prices:
        log.warning("個股行情為空，中止發送（可能為假日或數據尚未更新）")
        return

    hot_stocks, x_stocks = classify_stocks(prices, buying)
    log.info(
        "分級結果：強勢股 %d 檔，X 級 %d 檔，大盤資訊 %s",
        len(hot_stocks),
        len(x_stocks),
        "取得" if market else "未取得（跳過）",
    )

    # ── Step 3：組裝訊息並發送 ──
    message = format_message(date_str, mode, hot_stocks, x_stocks, market)
    log.info("準備發送 Discord 戰報（%d 字）", len(message))
    send_to_discord(message)
    log.info("完成")


if __name__ == "__main__":
    main()
