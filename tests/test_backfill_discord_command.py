"""Discord /backfill 指令測試。

涵蓋：
  - is_owner 權限判定（DISCORD_OWNER_ID 未設定 / 不符 / 相符）
  - cmd_backfill_core 呼叫底層 backfill() 並組出 Embed
  - days 參數 clamp 到 [1, 60]
  - backfill() 例外 → 回 failure embed 而不是 raise
  - HTTP handler 對非 owner 立即回 "⛔ 此指令僅限管理員使用"
  - HTTP handler 對 owner 走 defer (type=5)
  - CLI 與 Discord 共用同一個 backfill 主函式（介面相容）
"""
import json
from unittest.mock import MagicMock

import config
from discord_bot import admin_commands as ac
from tools import backfill_t86_history as bf


# ─────────── is_owner ───────────

def test_is_owner_returns_false_when_env_unset(monkeypatch):
    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', '')
    assert ac.is_owner('123') is False


def test_is_owner_returns_false_for_other_user(monkeypatch):
    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', '99999')
    assert ac.is_owner('123') is False


def test_is_owner_returns_true_for_matching_id(monkeypatch):
    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', '99999')
    assert ac.is_owner('99999') is True
    # 也接受 int / str 混用
    assert ac.is_owner(99999) is True


# ─────────── cmd_backfill_core ───────────

def test_backfill_core_calls_backfill_and_returns_embed(monkeypatch):
    """cmd_backfill_core 應該呼叫 backfill()，把回傳值組成 embed dict。"""
    called = {}

    def fake_backfill(days, **kw):
        called['days'] = days
        return {
            'requested_days': days, 'end_date': '20260515',
            'written_dates': ['20260514', '20260515'],
            'holiday_dates': [], 'failed_dates': [], 'total_rows': 100,
        }

    monkeypatch.setattr(ac, 'backfill', fake_backfill)
    embed = ac.cmd_backfill_core(days=5)
    assert called['days'] == 5
    assert 'title' in embed and '✅' in embed['title']
    # fields 含寫入日期數 / 總筆數
    field_names = [f['name'] for f in embed['fields']]
    assert any('寫入日期數' in n for n in field_names)
    assert any('總筆數' in n for n in field_names)
    # 最早 / 最晚日期都應該列出
    field_values = {f['name']: f['value'] for f in embed['fields']}
    assert field_values['最早日期'] == '20260514'
    assert field_values['最晚日期'] == '20260515'


def test_backfill_core_clamps_days_to_range(monkeypatch):
    """days 超過 60 → clamp 到 60；< 1 → clamp 到 1。"""
    seen = []

    def fake_backfill(days, **kw):
        seen.append(days)
        return {
            'requested_days': days, 'end_date': '20260515',
            'written_dates': [], 'holiday_dates': [],
            'failed_dates': [], 'total_rows': 0,
        }
    monkeypatch.setattr(ac, 'backfill', fake_backfill)

    ac.cmd_backfill_core(days=999)
    ac.cmd_backfill_core(days=0)
    assert seen == [60, 1]


def test_backfill_core_non_int_falls_back_to_default(monkeypatch):
    seen = []

    def fake_backfill(days, **kw):
        seen.append(days)
        return {
            'requested_days': days, 'end_date': '20260515',
            'written_dates': [], 'holiday_dates': [],
            'failed_dates': [], 'total_rows': 0,
        }
    monkeypatch.setattr(ac, 'backfill', fake_backfill)
    ac.cmd_backfill_core(days='not-a-number')
    assert seen == [10]


def test_backfill_core_shows_holiday_and_failed_sections(monkeypatch):
    monkeypatch.setattr(ac, 'backfill',
                        lambda **kw: {
                            'requested_days': 5, 'end_date': '20260515',
                            'written_dates': ['20260513'],
                            'holiday_dates': ['20260511', '20260512'],
                            'failed_dates': ['20260514'],
                            'total_rows': 50,
                        })
    embed = ac.cmd_backfill_core(days=5)
    names = [f['name'] for f in embed['fields']]
    assert any('假日' in n and '2' in n for n in names)
    assert any('失敗' in n and '1' in n for n in names)
    # 1 個失敗 + 1 個成功 → 部分成功
    assert '⚠️' in embed['title']


def test_backfill_core_handles_exception(monkeypatch):
    def fake_backfill(**kw):
        raise RuntimeError('boom')
    monkeypatch.setattr(ac, 'backfill', fake_backfill)
    embed = ac.cmd_backfill_core(days=5)
    assert '❌' in embed['title']
    # 失敗顏色
    assert embed['color'] == ac._STATUS_COLOR['failure']


# ─────────── HTTP handler dispatch ───────────

def _build_handler(monkeypatch, owner_uid='owner_42'):
    """建一個未綁 socket 的 InteractionHandler 用於單測 _handle_admin。"""
    from discord_bot.handlers import InteractionHandler
    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', owner_uid)

    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_calls = []

    def fake_send_json(code, body, cors=False):
        h.send_json_calls.append((code, body))

    h.send_json = fake_send_json
    return h


def test_handler_rejects_non_owner_with_ephemeral_deny(monkeypatch):
    h = _build_handler(monkeypatch, owner_uid='owner_42')
    handled = h._handle_admin('backfill', [{'name': 'days', 'value': 10}],
                              'random_user', token='tok')
    assert handled is True
    assert len(h.send_json_calls) == 1
    code, body = h.send_json_calls[0]
    assert code == 200
    assert body['type'] == 4
    assert '此指令僅限管理員使用' in body['data']['content']
    # ephemeral flag
    assert body['data'].get('flags') == 64


def test_handler_owner_defers_and_calls_core(monkeypatch):
    """owner 觸發 → type=5 deferred + 背景呼叫 cmd_backfill_core。"""
    h = _build_handler(monkeypatch, owner_uid='owner_42')

    called = {}
    monkeypatch.setattr('discord_bot.handlers.cmd_backfill_core',
                        lambda days: (called.setdefault('days', days) or
                                       {'title': 'X', 'color': 0, 'fields': []}))
    # 背景 thread 用同步替代以利驗證
    monkeypatch.setattr('discord_bot.handlers.threading.Thread',
                        lambda target, daemon=True: type('T', (),
                            {'start': lambda self: target()})())
    # 攔截 followup patch
    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: called.setdefault('embed_sent', True))

    handled = h._handle_admin('backfill', [{'name': 'days', 'value': 7}],
                              'owner_42', token='tok')
    assert handled is True
    assert h.send_json_calls[0][1]['type'] == 5  # deferred
    assert called.get('days') == 7
    assert called.get('embed_sent') is True


def test_handler_returns_false_for_other_commands(monkeypatch):
    h = _build_handler(monkeypatch, owner_uid='owner_42')
    assert h._handle_admin('help', [], 'owner_42', token='tok') is False
    assert h._handle_admin('run',  [], 'owner_42', token='tok') is False


# ─────────── CLI 與 Discord 共用同一個 backfill ───────────

def test_cli_and_discord_share_backfill_function(monkeypatch):
    """確認重構後 CLI 與 Discord 都從 tools.backfill_t86_history.backfill 呼叫。"""
    # admin_commands 透過 `from tools.backfill_t86_history import backfill` 拿
    from tools.backfill_t86_history import backfill as cli_backfill
    assert ac.backfill is cli_backfill

    # 呼叫一次確認新介面（dict）對兩邊都成立
    import pandas as pd
    monkeypatch.setattr(bf, 'fetch_t86_cached',
                        lambda d: pd.DataFrame([{'sid_clean': '2330',
                                                  '_foreign': 100, '_trust': 0,
                                                  '_dealer': 0}]))
    monkeypatch.setattr(bf, 'save_t86_to_history', lambda d, df: 1)

    r = ac.backfill(days=2, end_date_str='20260515', sleep_between=0)
    assert isinstance(r, dict)
    assert r['total_rows'] == 2


# ─────────── 完整 HTTP body 路徑（防止 dispatch 漏接） ───────────

def test_do_post_routes_backfill_to_admin_handler(monkeypatch):
    """完整模擬 Discord interaction：dispatch 是否走到 _handle_admin。"""
    from discord_bot.handlers import InteractionHandler

    monkeypatch.setattr(config, 'DISCORD_OWNER_ID', 'owner_42')
    # verify_signature 在 handlers.py 已綁定為 module-level 名字，需直接 patch 那裡
    monkeypatch.setattr('discord_bot.handlers.verify_signature',
                        lambda *a, **kw: True)
    # /backfill 應該被 admin handler 接走 → 不會 fallthrough 到 _handle_deferred
    admin_called = {'count': 0}
    real_handle = InteractionHandler._handle_admin

    def spy_admin(self, cmd, opts, uid, token):
        admin_called['count'] += 1
        return real_handle(self, cmd, opts, uid, token)

    monkeypatch.setattr(InteractionHandler, '_handle_admin', spy_admin)

    # 用 mock thread 攔下背景任務，避免真實去 patch followup
    monkeypatch.setattr('discord_bot.handlers.threading.Thread',
                        lambda target, daemon=True: type('T', (),
                            {'start': lambda self: None})())

    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_calls = []
    h.send_json = lambda code, body, cors=False: h.send_json_calls.append((code, body))

    body = {
        'type': 2,
        'data': {'name': 'backfill',
                 'options': [{'name': 'days', 'value': 5}]},
        'token': 'tok',
        'member': {'user': {'id': 'owner_42', 'username': 'me'}},
    }
    raw = json.dumps(body).encode('utf-8')

    h.headers = {'Content-Length': str(len(raw)),
                 'X-Signature-Ed25519': 'x', 'X-Signature-Timestamp': '0'}
    h.rfile = MagicMock()
    h.rfile.read.return_value = raw
    h.do_POST()

    assert admin_called['count'] == 1
    # type=5 deferred (owner 路徑)
    assert h.send_json_calls[0][1]['type'] == 5
