"""
logging_setup 路由測試。

驗證 setup_logging 後：
  DEBUG / INFO        → stdout
  WARNING / ERROR     → stderr
  level=INFO 時 DEBUG 不出現在任何 stream
"""
import io
import logging

import pytest

import logging_setup
from logging_setup import setup_logging


@pytest.fixture
def isolated_logging():
    """每個測試前後備份/還原 root logger handlers，避免污染其他測試。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level    = root.level
    saved_flag     = logging_setup._configured

    for h in saved_handlers:
        root.removeHandler(h)
    logging_setup._configured = False

    yield

    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    logging_setup._configured = saved_flag


def _setup(level, isolated_logging):
    out = io.StringIO()
    err = io.StringIO()
    setup_logging(level=level, force=True, stdout=out, stderr=err)
    return out, err


def test_info_goes_to_stdout_only(isolated_logging):
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('test.routing.info').info('hello info')
    assert 'hello info' in out.getvalue()
    assert 'hello info' not in err.getvalue()


def test_debug_goes_to_stdout_when_level_debug(isolated_logging):
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('test.routing.debug').debug('hello debug')
    assert 'hello debug' in out.getvalue()
    assert 'hello debug' not in err.getvalue()


def test_debug_dropped_when_level_info(isolated_logging):
    out, err = _setup('INFO', isolated_logging)
    logging.getLogger('test.routing.debug2').debug('should not appear')
    assert 'should not appear' not in out.getvalue()
    assert 'should not appear' not in err.getvalue()


def test_warning_goes_to_stderr_only(isolated_logging):
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('test.routing.warn').warning('hello warn')
    assert 'hello warn' in err.getvalue()
    assert 'hello warn' not in out.getvalue()


def test_error_goes_to_stderr_only(isolated_logging):
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('test.routing.err').error('hello err')
    assert 'hello err' in err.getvalue()
    assert 'hello err' not in out.getvalue()


def test_critical_goes_to_stderr_only(isolated_logging):
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('test.routing.crit').critical('boom')
    assert 'boom' in err.getvalue()
    assert 'boom' not in out.getvalue()


def test_format_includes_level_and_logger_name(isolated_logging):
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('myapp.module').info('formatted')
    line = out.getvalue()
    # 預期格式：'YYYY-MM-DD HH:MM:SS [INFO] myapp.module: formatted'
    assert '[INFO]' in line
    assert 'myapp.module' in line
    assert 'formatted' in line


def test_force_resets_handlers(isolated_logging):
    """force=True 應該清掉之前的 handler，避免訊息被重複輸出兩次。"""
    out1, err1 = _setup('DEBUG', isolated_logging)
    out2, err2 = _setup('DEBUG', isolated_logging)  # 第二次 force
    logging.getLogger('test.routing.force').info('once')
    # 第一輪的 buffer 不該再收到（因為 handler 已被換掉）
    assert 'once' not in out1.getvalue()
    # 第二輪只收一次
    assert out2.getvalue().count('once') == 1


def test_third_party_loggers_suppressed_to_warning(isolated_logging):
    """urllib3 / requests 的 INFO 不該洗版。"""
    out, err = _setup('DEBUG', isolated_logging)
    logging.getLogger('urllib3').info('verbose http chatter')
    logging.getLogger('requests').info('verbose http chatter')
    assert 'verbose http chatter' not in out.getvalue()
    assert 'verbose http chatter' not in err.getvalue()
