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

    embed = ac.cmd_health_core(today=today)
    assert '✅' in embed['title']
    assert embed['color'] == ac._STATUS_COLOR['success']

    # 4 個 fields
    field_names = [f['name'] for f in embed['fields']]
    assert any('SCHEMA_VERSION' in n for n in field_names)
    assert any('daily_t86_history' in n for n in field_names)
    assert any('daily_scores' in n for n in field_names)
    assert any('screen_records' in n for n in field_names)

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


# ─────────── HTTP handler dispatch ───────────

def test_handler_non_owner_rejected(monkeypatch):
    from discord_bot.handlers import InteractionHandler
    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', 'owner_42')

    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_calls = []
    h.send_json = lambda code, body, cors=False: h.send_json_calls.append((code, body))

    handled = h._handle_admin('health', [], 'random_user', token='tok')
    assert handled is True
    _, body = h.send_json_calls[0]
    assert '此指令僅限管理員使用' in body['data']['content']
    assert body['data'].get('flags') == 64


def test_handler_owner_defers_and_runs_health(monkeypatch):
    from discord_bot.handlers import InteractionHandler
    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', 'owner_42')

    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_calls = []
    h.send_json = lambda code, body, cors=False: h.send_json_calls.append((code, body))

    called = {}
    monkeypatch.setattr('discord_bot.handlers.cmd_health_core',
                        lambda: (called.setdefault('hit', True) or
                                  {'title': 'X', 'color': 0, 'fields': []}))
    monkeypatch.setattr('discord_bot.handlers.threading.Thread',
                        lambda target, daemon=True: type('T', (),
                            {'start': lambda self: target()})())
    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: called.setdefault('embed_sent', True))

    handled = h._handle_admin('health', [], 'owner_42', token='tok')
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
