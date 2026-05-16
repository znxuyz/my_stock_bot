"""Discord /health 指令測試（cmd_health_core + handler dispatch）。"""
from contextlib import contextmanager
from datetime import date, timedelta

import config
from discord_bot import admin_commands as ac


class _Cur:
    def __init__(self, fetch_map):
        """fetch_map: list[(sql_substring, rows)]，按順序消費。"""
        self._fetch_map = list(fetch_map)
        self._last_rows = []

    def execute(self, sql, params=None):
        # 依照 SQL 含字串挑要回的 rows；沒匹配就吃下個
        for i, (substr, rows) in enumerate(self._fetch_map):
            if substr in sql:
                self._last_rows = rows
                self._fetch_map.pop(i)
                return
        self._last_rows = []

    def fetchall(self):
        return self._last_rows

    def fetchone(self):
        return self._last_rows[0] if self._last_rows else None

    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, cur): self._cur = cur
    def cursor(self, cursor_factory=None): return self._cur
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_conn(monkeypatch, fetch_map):
    cur = _Cur(fetch_map)
    @contextmanager
    def _cm():
        yield _Conn(cur)
    import db.conn
    monkeypatch.setattr(db.conn, 'get_conn', lambda: _cm())
    return cur


def test_health_core_all_healthy(monkeypatch):
    today = date(2026, 5, 15)
    # 過去 10 個交易日（往回工作日）— 全部存在
    expected = []
    cur = today
    while len(expected) < 10:
        if cur.weekday() < 5:
            expected.append(cur)
        cur -= timedelta(days=1)
    fetch_map = [
        ('FROM daily_t86_history',  [(d,) for d in expected]),
        ('FROM daily_scores',        [(42,)]),
        ('FROM screen_records',      [(7,)]),
    ]
    _patch_conn(monkeypatch, fetch_map)
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')
    # 給一個 persistent 路徑（非 /tmp）讓 cache field 通過
    monkeypatch.setattr(config, 'KBAR_CACHE_DIR', '/data/kbar_cache')

    embed = ac.cmd_health_core(today=today)
    assert '✅' in embed['title']
    assert embed['color'] == ac._STATUS_COLOR['success']

    # 5 個 fields（加上 KBAR cache）
    field_names = [f['name'] for f in embed['fields']]
    assert any('SCHEMA_VERSION' in n for n in field_names)
    assert any('daily_t86_history' in n for n in field_names)
    assert any('daily_scores' in n for n in field_names)
    assert any('screen_records' in n for n in field_names)
    assert any('KBAR cache' in n for n in field_names)

    # 完整性訊息應該含「完整」
    t86_field = next(f for f in embed['fields']
                     if 'daily_t86_history' in f['name'])
    assert '完整' in t86_field['value']

    # daily_scores 顯示 42 筆
    ds_field = next(f for f in embed['fields'] if 'daily_scores' in f['name'])
    assert '42' in ds_field['value']

    # screen_records 顯示 7 筆
    sr_field = next(f for f in embed['fields'] if 'screen_records' in f['name'])
    assert '7' in sr_field['value']


def test_health_core_partial_when_t86_missing(monkeypatch):
    today = date(2026, 5, 15)
    expected = []
    cur = today
    while len(expected) < 10:
        if cur.weekday() < 5:
            expected.append(cur)
        cur -= timedelta(days=1)
    # 只回 7 天（缺最近 3 天）
    fetch_map = [
        ('FROM daily_t86_history', [(d,) for d in expected[:7]]),
        ('FROM daily_scores',       [(10,)]),
        ('FROM screen_records',     [(0,)]),
    ]
    _patch_conn(monkeypatch, fetch_map)
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')

    embed = ac.cmd_health_core(today=today)
    assert '⚠️' in embed['title']
    assert embed['color'] == ac._STATUS_COLOR['partial']

    t86_field = next(f for f in embed['fields']
                     if 'daily_t86_history' in f['name'])
    # 應該列出缺漏天數
    assert '缺 3 天' in t86_field['value']


def test_health_core_partial_when_schema_wrong(monkeypatch):
    today = date(2026, 5, 15)
    # 假裝其他全 OK；只有 schema 不對
    expected = []
    cur = today
    while len(expected) < 10:
        if cur.weekday() < 5:
            expected.append(cur)
        cur -= timedelta(days=1)
    fetch_map = [
        ('FROM daily_t86_history', [(d,) for d in expected]),
        ('FROM daily_scores',       [(10,)]),
        ('FROM screen_records',     [(3,)]),
    ]
    _patch_conn(monkeypatch, fetch_map)
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v4-macd-chase')

    embed = ac.cmd_health_core(today=today)
    assert '⚠️' in embed['title']
    schema_field = next(f for f in embed['fields']
                        if 'SCHEMA_VERSION' in f['name'])
    assert '⚠️' in schema_field['value']


def test_health_core_handles_db_failure_gracefully(monkeypatch):
    """DB 完全掛掉時不要 raise，回 partial embed 並把錯誤訊息塞進 fields。"""
    import db.conn

    def _fail():
        raise RuntimeError('db down')
    monkeypatch.setattr(db.conn, 'get_conn', _fail)
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')

    embed = ac.cmd_health_core(today=date(2026, 5, 15))
    # schema OK，但其餘三個 DB 查詢都失敗 → partial
    assert embed['color'] == ac._STATUS_COLOR['partial']
    failed_fields = [f for f in embed['fields'] if '❌' in f['value']]
    # T86 / daily_scores / screen_records 三個都會回失敗
    assert len(failed_fields) >= 1


# ─────────── HTTP handler dispatch（不限管理員） ───────────

def test_handler_defers_and_runs_health_for_any_user(monkeypatch):
    """任何使用者觸發 /health → defer + 背景跑 cmd_health_core。"""
    from discord_bot.handlers import InteractionHandler

    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_calls = []
    h.send_json = lambda code, body, cors=False: h.send_json_calls.append((code, body))

    called = {}
    monkeypatch.setattr('discord_bot.handlers.cmd_health_core',
                        lambda: (called.setdefault('hit', True) or
                                  {'title': 'X', 'color': 0, 'fields': []}))
    monkeypatch.setattr('discord_bot.handlers.threading.Thread',
                        lambda target, daemon=True, args=(): type('T', (),
                            {'start': lambda self: target(*args)})())
    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: (called.setdefault('embed_sent', True) or True))

    body = {'channel_id': 'ch_1'}
    handled = h._handle_admin('health', [], body, token='tok')
    assert handled is True
    assert h.send_json_calls[0][1]['type'] == 5
    assert called.get('hit') is True
    assert called.get('embed_sent') is True


def test_register_includes_admin_commands():
    """確認 register.py 已宣告 /backfill 與 /health。"""
    from discord_bot.register import _COMMANDS
    names = {c['name'] for c in _COMMANDS}
    assert 'backfill' in names
    assert 'health' in names


# ─────────── KBAR cache 持久化檢查 ───────────

def _all_db_present(today):
    """產生「DB 全 OK」的 fetch_map（給只想測 cache 段的測試用）。"""
    expected = []
    cur = today
    while len(expected) < 10:
        if cur.weekday() < 5:
            expected.append(cur)
        cur -= timedelta(days=1)
    return [
        ('FROM daily_t86_history', [(d,) for d in expected]),
        ('FROM daily_scores',       [(0,)]),
        ('FROM screen_records',     [(0,)]),
    ]


def _fake_walk(walk_map):
    """產生 os.walk 的 fake 回值：walk_map = {dir_path: [files...]}"""
    def _walk(root):
        files = walk_map.get(root, [])
        yield (root, [], files)
    return _walk


def test_health_shows_kbar_cache_stats(monkeypatch):
    """KBAR_CACHE_DIR 在 persistent 路徑 + 目錄有檔案 → 顯示路徑 + 檔案數 + 大小 + ✅ persistent。"""
    persistent_path = '/data/kbar_cache'  # 非 /tmp → persistent
    files_in_dir = ['2330_202605.json', '2317_202605.json', '1101_202605.json']
    file_sizes = {
        f'{persistent_path}/2330_202605.json': 1024,
        f'{persistent_path}/2317_202605.json': 2048,
        f'{persistent_path}/1101_202605.json': 512,
    }

    import os as _os
    monkeypatch.setattr(_os.path, 'exists', lambda p: p == persistent_path)
    monkeypatch.setattr(_os.path, 'isdir', lambda p: p == persistent_path)
    monkeypatch.setattr(_os.path, 'getsize', lambda p: file_sizes.get(p, 0))
    monkeypatch.setattr(_os, 'walk', _fake_walk({persistent_path: files_in_dir}))

    today = date(2026, 5, 15)
    _patch_conn(monkeypatch, _all_db_present(today))
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')
    monkeypatch.setattr(config, 'KBAR_CACHE_DIR', persistent_path)

    embed = ac.cmd_health_core(today=today)
    kbar_field = next(f for f in embed['fields'] if f['name'] == 'KBAR cache')
    assert persistent_path in kbar_field['value']
    assert '✅ persistent' in kbar_field['value']
    assert '3' in kbar_field['value']                              # 3 個檔案
    assert 'KB' in kbar_field['value']                             # 顯示為 KB
    # 整體仍 ✅（cache persistent + DB 全 OK）
    assert '✅' in embed['title']


def test_health_warns_when_cache_in_tmp(monkeypatch):
    """KBAR_CACHE_DIR 在 /tmp 下 → ⚠️ persistent 警告，整體標題降為 ⚠️。"""
    tmp_cache = '/tmp/stockbot_test_kbar_cache'

    import os as _os
    # 目錄即使不存在也應該識別出 /tmp 而標 ⚠️
    monkeypatch.setattr(_os.path, 'exists', lambda p: False)

    today = date(2026, 5, 15)
    _patch_conn(monkeypatch, _all_db_present(today))
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')
    monkeypatch.setattr(config, 'KBAR_CACHE_DIR', tmp_cache)

    embed = ac.cmd_health_core(today=today)
    kbar_field = next(f for f in embed['fields'] if f['name'] == 'KBAR cache')
    assert '/tmp' in kbar_field['value']
    assert '重啟會清空' in kbar_field['value']
    # 整體標題降為 ⚠️ partial（提醒 deploy 不是 persistent）
    assert '⚠️' in embed['title']


def test_health_handles_missing_cache_dir(monkeypatch):
    """KBAR_CACHE_DIR 指向不存在的 persistent 路徑 → 顯示「目錄尚未建立」+ ✅ persistent。"""
    missing = '/data/kbar_cache_never_made'  # 不在 /tmp → persistent

    import os as _os
    monkeypatch.setattr(_os.path, 'exists', lambda p: False)

    today = date(2026, 5, 15)
    _patch_conn(monkeypatch, _all_db_present(today))
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')
    monkeypatch.setattr(config, 'KBAR_CACHE_DIR', missing)

    embed = ac.cmd_health_core(today=today)
    kbar_field = next(f for f in embed['fields'] if f['name'] == 'KBAR cache')
    assert missing in kbar_field['value']
    # 持久化路徑（不在 /tmp）即使目錄還不存在，也標 persistent
    assert '✅ persistent' in kbar_field['value']
    assert '尚未建立' in kbar_field['value']
    # 整體仍 ✅（首次啟動時這正常）
    assert '✅' in embed['title']


def test_health_handles_empty_cache_dir_env(monkeypatch):
    """KBAR_CACHE_DIR 完全未設 → 顯示「未設定」警示。"""
    today = date(2026, 5, 15)
    _patch_conn(monkeypatch, _all_db_present(today))
    monkeypatch.setattr(config, 'SCHEMA_VERSION', 'v62-pure-stalker-10pt')
    monkeypatch.setattr(config, 'KBAR_CACHE_DIR', '')

    embed = ac.cmd_health_core(today=today)
    kbar_field = next(f for f in embed['fields'] if f['name'] == 'KBAR cache')
    assert '未設定' in kbar_field['value']
    assert '⚠️' in embed['title']


def test_human_bytes_formatting():
    """_human_bytes 各量級。"""
    assert ac._human_bytes(0) == '0 B'
    assert ac._human_bytes(512) == '512 B'
    assert ac._human_bytes(1024) == '1.0 KB'
    assert ac._human_bytes(1024 * 1024) == '1.0 MB'
    assert ac._human_bytes(1024 * 1024 * 1024) == '1.0 GB'
