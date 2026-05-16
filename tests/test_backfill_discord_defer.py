"""驗證 /backfill 一進來就立刻 defer + flush 的測試。

Discord interaction 有 3 秒超時：handler 必須在 3 秒內回 type=5 ACK，
否則前端會卡在 "Thinking…" 永遠等不到結果。本檔測：

  - send_json({'type': 5}) 在 spawn 背景 thread 前就被呼叫
  - send_json 之後有 wfile.flush()（避免 buffering 導致 ACK 延遲）
  - 背景 thread 是 daemon 不阻塞 handler return
  - /backfill 的耗時 work 確實在背景 thread 內、不在主 handler

對應 discord.py 的 `await interaction.response.defer()` + `asyncio.to_thread`，
這個 bot 用 BaseHTTPRequestHandler + ThreadingHTTPServer 達到同樣效果。
"""
from unittest.mock import MagicMock


def _make_handler():
    """建一個未綁 socket 的 InteractionHandler，可以直接呼叫 _handle_admin。"""
    from discord_bot.handlers import InteractionHandler
    h = InteractionHandler.__new__(InteractionHandler)
    h.send_json_call_order = []   # (timestamp_seq, body)
    h.flush_called = []
    h.wfile = MagicMock()
    h.wfile.flush.side_effect = lambda: h.flush_called.append(True)
    return h


def test_defer_happens_before_thread_spawn(monkeypatch):
    """關鍵：send_json(type=5) 必須在 threading.Thread 之前發出。

    這是 hang on "Thinking…" 的核心防護——如果先 spawn thread 才 defer，
    Discord 3 秒沒收到 ACK 就會把這次 interaction 標 failed。
    """
    h = _make_handler()
    events = []

    def fake_send_json(code, body, cors=False):
        events.append(('send_json', body.get('type')))

    def fake_thread(target, daemon=True, args=()):
        events.append(('thread_spawn',))
        # 不真正起 thread；只記錄被呼叫
        return type('T', (), {'start': lambda self: events.append(('thread_start',))})()

    h.send_json = fake_send_json
    monkeypatch.setattr('discord_bot.handlers.threading.Thread', fake_thread)
    monkeypatch.setattr('discord_bot.handlers.cmd_backfill_core',
                        lambda days: {'title': 'x', 'color': 0, 'fields': []})

    body = {'channel_id': 'ch_1', 'member': {'user': {'id': 'u_1'}}}
    handled = h._handle_admin('backfill',
                              [{'name': 'days', 'value': 10}],
                              body, token='tok')
    assert handled is True

    # 順序必須是：send_json(type=5) → thread_spawn → thread_start
    assert events[0] == ('send_json', 5)
    assert events[1] == ('thread_spawn',)
    assert events[2] == ('thread_start',)


def test_defer_also_applies_to_health(monkeypatch):
    """/health 同樣要先 defer。"""
    h = _make_handler()
    events = []

    h.send_json = lambda code, body, cors=False: events.append(('send_json', body.get('type')))
    monkeypatch.setattr('discord_bot.handlers.threading.Thread',
                        lambda target, daemon=True, args=(): (events.append(('thread',)) or
                            type('T', (), {'start': lambda self: None})()))
    monkeypatch.setattr('discord_bot.handlers.cmd_health_core',
                        lambda: {'title': 'x', 'color': 0, 'fields': []})

    body = {'channel_id': 'ch_1'}
    h._handle_admin('health', [], body, token='tok')

    assert events[0] == ('send_json', 5)
    assert events[1] == ('thread',)


def test_send_json_flushes_wfile_after_write(monkeypatch):
    """send_json 寫完 data 必須 flush 一次，否則 buffering 會延遲 ACK。"""
    from discord_bot.handlers import InteractionHandler
    h = InteractionHandler.__new__(InteractionHandler)

    sent = []

    def fake_send_response(code):
        sent.append(('response', code))

    def fake_send_header(name, val):
        sent.append(('header', name, val))

    def fake_end_headers():
        sent.append(('end_headers',))

    h.send_response = fake_send_response
    h.send_header = fake_send_header
    h.end_headers = fake_end_headers

    flushed = []
    written = []

    class _WF:
        def write(self, data):
            written.append(data)

        def flush(self):
            flushed.append(True)

    h.wfile = _WF()

    h.send_json(200, {'type': 5})

    # 寫了 body
    assert len(written) == 1
    # 必須 flush 過
    assert flushed == [True], '送 type=5 後沒 flush 會讓 Discord 收不到 ACK'


def test_backfill_dispatch_does_not_block_on_long_work(monkeypatch):
    """確認 cmd_backfill_core 是在背景 thread 跑、不在主 handler。"""
    h = _make_handler()

    handler_done_at = []
    work_finished_at = []
    captured_target = []

    def fake_send_json(code, body, cors=False):
        handler_done_at.append('defer_sent')

    def slow_work(days):
        # 模擬 8 秒任務（但這應該不會阻塞 _handle_admin return）
        import time as _t
        _t.sleep(0.05)
        work_finished_at.append('work_done')
        return {'title': 'x', 'color': 0, 'fields': []}

    def fake_thread(target, daemon=True, args=()):
        captured_target.append((target, args))
        return type('T', (), {'start': lambda self: None})()

    h.send_json = fake_send_json
    monkeypatch.setattr('discord_bot.handlers.threading.Thread', fake_thread)
    monkeypatch.setattr('discord_bot.handlers.cmd_backfill_core', slow_work)

    body = {'channel_id': 'ch_1'}
    h._handle_admin('backfill', [{'name': 'days', 'value': 10}], body, token='tok')

    # _handle_admin 已 return；work 尚未跑（因為 thread 沒 start）
    assert handler_done_at == ['defer_sent']
    assert work_finished_at == [], 'cmd_backfill_core 不應該在主 handler 內被呼叫'
    # 但 thread 被 spawn，且 target 包了 _bg_admin_run（不是直接 cmd_backfill_core）
    assert len(captured_target) == 1
    target_fn, target_args = captured_target[0]
    assert target_fn.__name__ == '_bg_admin_run'


def test_bg_admin_run_catches_exception_and_reports(monkeypatch):
    """背景任務 raise 應該被 catch、用 _patch 報錯，而不是 thread 默默死掉。"""
    from discord_bot.handlers import _bg_admin_run

    patches = []
    monkeypatch.setattr('discord_bot.handlers._patch',
                        lambda token, content: patches.append(('patch', content)) or True)
    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: patches.append(('embed', embed)) or True)
    monkeypatch.setattr('discord_bot.handlers._channel_send_embed',
                        lambda channel_id, embed: patches.append(('channel', embed)) or True)

    def core():
        raise RuntimeError('boom')

    _bg_admin_run('/backfill', token='tok', channel_id='ch_1', core_fn=core)

    # 必須至少有一個 patch 訊息送出（內含 'boom'）
    found = any('boom' in str(p[1]) for p in patches)
    assert found, f'例外沒有被報出來：{patches}'


def test_bg_admin_run_falls_back_to_channel_when_followup_fails(monkeypatch):
    """followup PATCH 拿到 4xx → 嘗試 channel API fallback。"""
    from discord_bot.handlers import _bg_admin_run

    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: False)  # PATCH 失敗
    channel_sent = []
    monkeypatch.setattr('discord_bot.handlers._channel_send_embed',
                        lambda cid, embed: channel_sent.append((cid, embed)) or True)

    embed = {'title': 'OK', 'color': 0, 'fields': []}
    _bg_admin_run('/backfill', token='tok', channel_id='ch_42',
                  core_fn=lambda: embed)

    assert channel_sent == [('ch_42', embed)]


def test_bg_admin_run_uses_channel_when_elapsed_exceeds_token_ttl(monkeypatch):
    """若 core_fn 耗時超過 14 分鐘，token 已過期，直接走 channel API。"""
    from discord_bot.handlers import _bg_admin_run, FOLLOWUP_TOKEN_TTL_SEC

    # 模擬 time.time() 一前一後相差 > FOLLOWUP_TOKEN_TTL_SEC
    fake_now = [0.0]

    def slow_time():
        fake_now[0] += FOLLOWUP_TOKEN_TTL_SEC  # 每次呼叫多一個 TTL
        return fake_now[0]

    import time as _t
    monkeypatch.setattr(_t, 'time', slow_time)
    # 注意：_bg_admin_run 內部 import time as _time，所以也要 patch 那個

    patches_called = []
    channel_called = []
    monkeypatch.setattr('discord_bot.handlers._patch_embed',
                        lambda token, embed: patches_called.append(embed) or True)
    monkeypatch.setattr('discord_bot.handlers._channel_send_embed',
                        lambda cid, embed: channel_called.append((cid, embed)) or True)

    embed = {'title': 'late', 'color': 0, 'fields': []}
    _bg_admin_run('/backfill', token='tok', channel_id='ch_99',
                  core_fn=lambda: embed)

    # 因為 elapsed > TTL，應該走 channel API 而非 followup
    assert len(channel_called) == 1
    assert channel_called[0][0] == 'ch_99'
    assert patches_called == []
