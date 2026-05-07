"""
twse_t86.fetch_t86_cached 的 regression test。

關鍵契約（讓 analysis.py 能正確分辨「真假日」與「抓取失敗」）：
  TWSE 回「查詢無資料」  → empty DataFrame（真假日，快取避免重複探查）
  TWSE 回不能 parse 的內容 → None（抓取失敗，caller 應視為 'fail' 通知用戶手動 /run）
  網路失敗 / 全部重試完仍 None → None（同上）

這個 bug 在 2026-05-07 17:00 出現過：TWSE 回了沒有「證券代號」表頭的奇怪內容
（疑似限速錯誤頁），原本 fetch_t86_cached 把這個 empty df 直接回給 analysis.py
被誤判為假日，當天靜默跳過分析，沒通知用戶。
"""
import pytest

import twse_t86


class _FakeResp:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def clean_cache(monkeypatch):
    """每個測試清空 T86 共享快取，避免互相污染。"""
    monkeypatch.setattr(twse_t86, '_T86_CACHE', {})
    yield


def test_holiday_returns_empty_df(monkeypatch, clean_cache):
    """TWSE 明確回「查詢無資料」→ empty DataFrame（caller 應判為 holiday）。"""
    monkeypatch.setattr(
        twse_t86, 'safe_get',
        lambda *a, **kw: _FakeResp('查詢無資料'),
    )
    df = twse_t86.fetch_t86_cached('20260507_holiday')
    assert df is not None, '真假日不該回 None（不然 analysis 會誤判為 fail）'
    assert df.empty


def test_parse_failure_returns_none(monkeypatch, clean_cache):
    """TWSE 有回應但找不到「證券代號」表頭 → None（caller 應判為 fail，不該當假日）。

    這是 2026-05-07 那次 bug 的核心 regression：當時這個 case 回 empty df
    導致 analysis.py 誤判為 holiday 直接跳過。
    """
    monkeypatch.setattr(
        twse_t86, 'safe_get',
        lambda *a, **kw: _FakeResp('<html>503 Service Unavailable</html>'),
    )
    df = twse_t86.fetch_t86_cached('20260507_parsefail')
    assert df is None, 'parse 失敗必須回 None，不能回 empty df 讓 caller 誤當假日'


def test_network_failure_returns_none(monkeypatch, clean_cache):
    """safe_get 重試完仍失敗 → None。"""
    monkeypatch.setattr(twse_t86, 'safe_get', lambda *a, **kw: None)
    df = twse_t86.fetch_t86_cached('20260507_netfail')
    assert df is None


def test_holiday_response_is_cached(monkeypatch, clean_cache):
    """真假日的 empty df 應該被快取（避免同一天反覆探查 TWSE）。"""
    call_count = {'n': 0}

    def fake_get(*a, **kw):
        call_count['n'] += 1
        return _FakeResp('查詢無資料')

    monkeypatch.setattr(twse_t86, 'safe_get', fake_get)
    twse_t86.fetch_t86_cached('20260507_holiday_cache')
    twse_t86.fetch_t86_cached('20260507_holiday_cache')  # 第二次應該命中快取
    assert call_count['n'] == 1, '真假日結果應該被快取'


def test_parse_failure_is_NOT_cached(monkeypatch, clean_cache):
    """parse 失敗不該被快取（給 TWSE 恢復後下次有機會抓到正確資料）。"""
    call_count = {'n': 0}

    def fake_get(*a, **kw):
        call_count['n'] += 1
        return _FakeResp('not a t86 csv')

    monkeypatch.setattr(twse_t86, 'safe_get', fake_get)
    twse_t86.fetch_t86_cached('20260507_pf_cache')
    twse_t86.fetch_t86_cached('20260507_pf_cache')  # 應該還是會打 TWSE
    assert call_count['n'] == 2, 'parse 失敗不該快取，否則 TWSE 恢復後也撈不到'


def test_parse_t86_logs_response_excerpt_when_header_missing(caplog):
    """parse_t86 找不到表頭時，應 log 出回應前段，便於 debug TWSE 實際吐了什麼。"""
    weird_response = 'unexpected weird body without the trigger string'
    with caplog.at_level('WARNING', logger='twse_t86'):
        df = twse_t86.parse_t86(weird_response)
    assert df.empty
    # 確認 warning 內含實際內容片段，不是只說「找不到表頭」
    msgs = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'unexpected weird body' in msgs, \
        f'warning 應包含實際回應內容供 debug，但只看到：{msgs!r}'


def test_parse_t86_normal_csv():
    """正常 T86 CSV → 解析成功，回非空 df 含 _foreign / _trust。"""
    csv = (
        '105年09月13日 三大法人買賣超日報\n'
        '證券代號,證券名稱,外資及陸資(不含外資自營商)買進股數,賣出股數,'
        '外資及陸資(不含外資自營商)買賣超股數,外資自營商買進股數,賣出股數,'
        '外資自營商買賣超股數,投信買進股數,賣出股數,投信買賣超股數,'
        '自營商(自行買賣)買進股數,賣出股數,自營商(自行買賣)買賣超股數,'
        '自營商(避險)買進股數,賣出股數,自營商(避險)買賣超股數,合計\n'
        '"2330","台積電",1000,500,500,0,0,0,200,100,100,0,0,0,0,0,0,600\n'
    )
    df = twse_t86.parse_t86(csv)
    assert not df.empty
    assert '_foreign' in df.columns
    assert '_trust' in df.columns
    assert int(df.iloc[0]['_foreign']) == 500
    assert int(df.iloc[0]['_trust']) == 100
