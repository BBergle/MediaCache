"""Tests for _retry_plextv_call helper.

Verifies that transient plex.tv failures (timeouts, connection errors, 5xx
responses) are retried with backoff, while permanent errors raise immediately.
"""

import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library', 'plexapi.exceptions',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from plexapi.exceptions import BadRequest, NotFound, Unauthorized

from core.plex_api import _retry_plextv_call, PLEXTV_MAX_RETRIES


def _plexapi_error(status: int, codename: str, cls=BadRequest):
    """Build the exact message shape plexapi raises for a non-2xx response."""
    return cls(
        f"({status}) {codename}; https://metadata.provider.plex.tv/library/metadata/abc123 "
        f"upstream connect error or disconnect/reset before headers"
    )


class TestRetryPlexTvCall:
    """Verify retry semantics for the plex.tv retry helper."""

    def test_returns_immediately_on_success(self):
        func = MagicMock(return_value="ok")
        result = _retry_plextv_call(func, label="test")
        assert result == "ok"
        assert func.call_count == 1

    def test_retries_on_read_timeout_then_succeeds(self):
        func = MagicMock(side_effect=[requests.Timeout("read timeout"), "ok"])
        with patch('core.plex_api.time.sleep') as mock_sleep:
            result = _retry_plextv_call(func, label="test")
        assert result == "ok"
        assert func.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_on_connection_error_then_succeeds(self):
        func = MagicMock(side_effect=[requests.ConnectionError("dns fail"), "ok"])
        with patch('core.plex_api.time.sleep'):
            result = _retry_plextv_call(func, label="test")
        assert result == "ok"
        assert func.call_count == 2

    def test_gives_up_after_max_attempts_and_raises(self):
        err = requests.Timeout("read timeout")
        func = MagicMock(side_effect=err)
        with patch('core.plex_api.time.sleep'), pytest.raises(requests.Timeout):
            _retry_plextv_call(func, label="test")
        assert func.call_count == PLEXTV_MAX_RETRIES

    def test_non_retriable_exception_raised_immediately(self):
        """Auth errors, logic bugs, etc. should not be retried."""
        func = MagicMock(side_effect=ValueError("bad token"))
        with pytest.raises(ValueError):
            _retry_plextv_call(func, label="test")
        assert func.call_count == 1

    def test_backoff_is_exponential(self):
        """Wait times should be 2s, 4s (PLEXTV_RETRY_BASE_WAIT ** attempt)."""
        func = MagicMock(side_effect=[
            requests.Timeout("t1"), requests.Timeout("t2"), "ok"
        ])
        with patch('core.plex_api.time.sleep') as mock_sleep:
            _retry_plextv_call(func, label="test")
        wait_times = [c.args[0] for c in mock_sleep.call_args_list]
        assert wait_times == [2, 4]

    def test_respects_custom_max_attempts(self):
        func = MagicMock(side_effect=requests.Timeout("t"))
        with patch('core.plex_api.time.sleep'), pytest.raises(requests.Timeout):
            _retry_plextv_call(func, label="test", max_attempts=2)
        assert func.call_count == 2

    def test_logs_warning_with_label_on_retry(self, caplog):
        import logging
        func = MagicMock(side_effect=[requests.Timeout("oops"), "ok"])
        with patch('core.plex_api.time.sleep'), caplog.at_level(logging.WARNING):
            _retry_plextv_call(func, label="watchlist for Brandon")
        assert any("watchlist for Brandon" in rec.message for rec in caplog.records)
        assert any("1/3" in rec.message for rec in caplog.records)


class TestRetryOnHttpStatus:
    """plexapi collapses non-2xx into BadRequest with the code in the message.

    5xx from plex.tv is a transient upstream hiccup worth retrying; 4xx means
    the request is wrong and never will be.
    """

    def test_retries_on_503_then_succeeds(self):
        """The failure mode from the 2026-07-31 log: a 503 on a watchlist item."""
        func = MagicMock(side_effect=[
            _plexapi_error(503, "service_unavailable"), "ok"
        ])
        with patch('core.plex_api.time.sleep'):
            result = _retry_plextv_call(func, label="guids for 'Weeds'")
        assert result == "ok"
        assert func.call_count == 2

    @pytest.mark.parametrize("status,codename", [
        (500, "internal_server_error"),
        (502, "bad_gateway"),
        (503, "service_unavailable"),
        (504, "gateway_timeout"),
    ])
    def test_retries_every_5xx(self, status, codename):
        func = MagicMock(side_effect=[_plexapi_error(status, codename), "ok"])
        with patch('core.plex_api.time.sleep'):
            assert _retry_plextv_call(func, label="test") == "ok"
        assert func.call_count == 2

    def test_gives_up_after_max_attempts_on_persistent_5xx(self):
        func = MagicMock(side_effect=_plexapi_error(503, "service_unavailable"))
        with patch('core.plex_api.time.sleep'), pytest.raises(BadRequest):
            _retry_plextv_call(func, label="test")
        assert func.call_count == PLEXTV_MAX_RETRIES

    @pytest.mark.parametrize("status,codename,cls", [
        (400, "bad_request", BadRequest),
        (401, "unauthorized", Unauthorized),
        (403, "forbidden", BadRequest),
        (429, "too_many_requests", BadRequest),
    ])
    def test_4xx_raises_immediately(self, status, codename, cls):
        """A bad token or a rejected request won't fix itself — don't burn backoff."""
        func = MagicMock(side_effect=_plexapi_error(status, codename, cls))
        with pytest.raises(BadRequest):
            _retry_plextv_call(func, label="test")
        assert func.call_count == 1

    def test_not_found_raises_immediately(self):
        """NotFound is a sibling of BadRequest in plexapi, not a subclass."""
        func = MagicMock(side_effect=NotFound("(404) not_found; https://plex.tv/x"))
        with pytest.raises(NotFound):
            _retry_plextv_call(func, label="test")
        assert func.call_count == 1

    def test_bad_request_without_status_prefix_raises_immediately(self):
        """Only messages that actually start with a 5xx code are retriable."""
        func = MagicMock(side_effect=BadRequest("something went sideways"))
        with pytest.raises(BadRequest):
            _retry_plextv_call(func, label="test")
        assert func.call_count == 1
