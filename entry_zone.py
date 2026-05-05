"""
建議進場區間：根據追漲模式 + 等級決定 [zone_low, zone_high]。
這個檔案是策略門檻變更的單一入口，DB 寫入與 UI 顯示都從這裡拿。

策略 v5（2026-05 更新）：
  normal mode
    SS 級    → [close × 0.97, close × 1.03]   ← 容忍 3% 跳空（最強標的也接得到）
    其他等級 → [close × 0.97, close × 1.02]   ← 容忍 2% 小跳空（補強原本一律 1.00 太保守）
  strong_chase → [close × 1.00, close × 1.07]
  watch        → (None, None)                  ← 不撮合
"""

# 上下緣倍率（precision=2，給 DB 寫入；UI 由 caller 負責顯示精度）
_ZONE_MULTIPLIERS = {
    'strong_chase': (1.00, 1.07),
    'normal_ss':    (0.97, 1.03),
    'normal_other': (0.97, 1.02),
}


def calc_entry_zone(close, chase_mode, grade=None, precision=2):
    """
    回傳 (zone_low, zone_high)；watch 模式回 (None, None)。

    參數：
      close      ── T 日收盤價
      chase_mode ── 'normal' / 'strong_chase' / 'watch' / 'reject'
      grade      ── 等級字串（'SS'/'S'/'A'/None）。strong_chase 與 watch 忽略。
      precision  ── 小數點位數（DB 用 2，UI 顯示常用 1）
    """
    if chase_mode == 'strong_chase':
        lo_mul, hi_mul = _ZONE_MULTIPLIERS['strong_chase']
    elif chase_mode in ('watch', 'reject'):
        return None, None
    else:  # normal
        if grade == 'SS':
            lo_mul, hi_mul = _ZONE_MULTIPLIERS['normal_ss']
        else:
            lo_mul, hi_mul = _ZONE_MULTIPLIERS['normal_other']
    return round(close * lo_mul, precision), round(close * hi_mul, precision)
