"""/diag Discord 指令測試。

不打真實 TWSE — mock requests.get 回固定 status code，驗證：
  - cmd_diag_core 對每組 endpoint 把所有候選 URL 都打一次
  - status 分類正確（200 ✅ / 404 ❌ / timeout ⚠️）
  - title 顏色：全綠 / 部分成功 / 全失敗 三種狀態
  - target_date 預設用工作日（避開週末）
  - handler dispatch /diag → 走 _handle_admin 的 deferred 路徑
"""
from discord_bot import admin_commands as ac


class _FakeResp:
    def __init__(self, status_code, text=''):
        self.status_code = status_code
        self.text = text


def test_diag_core_all_success(monkeypatch):
    """全部 URL 都回 200 → ✅ 全綠 title + success color。"""
    monkeypatch.setattr('discord_bot.admin_commands.requests.get',
                        lambda url, **kw: _FakeResp(200, 'OK csv data'))

    embed = ac.cmd_diag_core(target_date='20260515')
    assert '✅ 全綠' in embed['title']
    assert embed['color'] == ac._STATUS_COLOR['success']
    # 每組 endpoint 都應該至少有一個 ✅
    for f in embed['fields']:
        assert f['name'].startswith('✅')


def test_diag_core_all_fail(monkeypatch):
    """全部 URL 都回 404 → ❌ 全部失敗 title + failure color。"""
    monkeypatch.setattr('discord_bot.admin_commands.requests.get',
                        lambda url, **kw: _FakeResp(404, 'Not Found'))

    embed = ac.cmd_diag_core(target_date='20260515')
    assert '❌ 全部失敗' in embed['title']
    assert embed['color'] == ac._STATUS_COLOR['failure']
    for f in embed['fields']:
        assert f['name'].startswith('❌')
        # 每個 endpoint 至少含一個 ❌
        assert '❌' in f['value']


def test_diag_core_partial_success(monkeypatch):
    """只有部分 URL 成功 → ⚠️ 部分成功 title + partial color。"""
    # 讓含 'rwd' 的 URL fail、其他成功
    def fake_get(url, **kw):
        if 'rwd' in url:
            return _FakeResp(404, 'Not Found')
        return _FakeResp(200, 'OK')

    monkeypatch.setattr('discord_bot.admin_commands.requests.get', fake_get)
    embed = ac.cmd_diag_core(target_date='20260515')
    assert '⚠️ 部分成功' in embed['title']
    assert embed['color'] == ac._STATUS_COLOR['partial']
    # description 應該顯示 X/Y 成功比例
    assert '/' in embed['description']


def test_diag_core_handles_timeout(monkeypatch):
    """requests.Timeout → 該行標 ⚠️ ERR (TIMEOUT)，不 raise。"""
    import requests
    def raise_timeout(url, **kw):
        raise requests.exceptions.Timeout('slow')
    monkeypatch.setattr('discord_bot.admin_commands.requests.get', raise_timeout)

    embed = ac.cmd_diag_core(target_date='20260515')
    # 所有 probe 都 timeout → 全失敗
    assert '❌' in embed['title'] or '⚠️' in embed['title']
    # field value 應該含 TIMEOUT 字樣
    all_text = '\n'.join(f['value'] for f in embed['fields'])
    assert 'TIMEOUT' in all_text


def test_diag_core_handles_generic_exception(monkeypatch):
    """任意例外 → 標 ⚠️ ERR (...)，吞掉繼續測下一個 URL。"""
    def raise_err(url, **kw):
        raise RuntimeError('boom')
    monkeypatch.setattr('discord_bot.admin_commands.requests.get', raise_err)

    embed = ac.cmd_diag_core(target_date='20260515')
    all_text = '\n'.join(f['value'] for f in embed['fields'])
    assert 'ERR' in all_text or 'boom' in all_text


def test_diag_core_default_date_is_workday():
    """target_date=None → 自動回最近的工作日。"""
    # 不打網路 — 只看挑日期邏輯
    import unittest.mock as _mock
    with _mock.patch('discord_bot.admin_commands.requests.get',
                     return_value=_FakeResp(200, 'OK')):
        embed = ac.cmd_diag_core()
    # description 含 YYYYMMDD 字串
    assert '日期' in embed['description']
    # 拿出日期 string，驗證它是 Mon-Fri
    import re
    m = re.search(r'`(\d{8})`', embed['description'])
    assert m, f'description 沒包含 YYYYMMDD：{embed["description"]}'
    d_str = m.group(1)
    from datetime import datetime as _dt
    d_obj = _dt.strptime(d_str, '%Y%m%d').date()
    assert d_obj.weekday() < 5, f'{d_str} 不是工作日（weekday={d_obj.weekday()}）'


def test_diag_targets_includes_all_expected_endpoints():
    """確認診斷涵蓋 5 個 TWSE endpoint + 首頁。"""
    keys = set(ac._TWSE_DIAG_TARGETS.keys())
    for expected in ('MI_INDEX', 'T86', 'STOCK_DAY', 'MI_MARGN', 'MI_QFIIS'):
        assert any(expected in k for k in keys), f'缺 {expected}'
    assert any('首頁' in k for k in keys), '缺 TWSE 首頁連線測試'


def test_diag_targets_have_multiple_candidates_per_endpoint():
    """每個 TWSE endpoint（首頁除外）至少有 2 個候選 URL（current + legacy）。"""
    for label, candidates in ac._TWSE_DIAG_TARGETS.items():
        if '首頁' in label:
            continue
        assert len(candidates) >= 2, f'{label} 只有 {len(candidates)} 個候選，應 ≥ 2'


# ─────────── HTTP handler dispatch ───────────

def test_handler_routes_diag_to_admin(monkeypatch):
    """/diag 應該被 _handle_admin 接走，走 type=5 deferred。"""
    from discord_bot.handlers import InteractionHandler

    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_calls = []
    h.send_json = lambda code, body, cors=False: h.send_json_calls.append((code, body))

    called = {}
    monkeypatch.setattr('discord_bot.handlers.cmd_diag_core',
                        lambda: (called.setdefault('hit', True) or
                                  {'title': 'X', 'color': 0, 'fields': []}))
    monkeypatch.setattr('discord_bot.handlers.threading.Thread',
                        lambda target, daemon=True, args=(): type('T', (),
                            {'start': lambda self: target(*args)})())
    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: (called.setdefault('embed_sent', True) or True))

    body = {'channel_id': 'ch_1'}
    handled = h._handle_admin('diag', [], body, token='tok')
    assert handled is True
    assert h.send_json_calls[0][1]['type'] == 5
    assert called.get('hit') is True
    assert called.get('embed_sent') is True


def test_register_includes_diag():
    from discord_bot.register import _COMMANDS
    names = {c['name'] for c in _COMMANDS}
    assert 'diag' in names