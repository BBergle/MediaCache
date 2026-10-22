"""The main-account watchlist path retries plex.tv like its home-user sibling.

Observed 2026-08-13 20:32 on a single-user install:

    Processing 1 users for watchlist (main + 0 home users)
    [USER:Brandon] Fetching watchlist media
    ERROR [PLEX API] Error (get Plex account for Brandon):
        HTTPSConnectionPool(host='plex.tv', port=443): Read timed out. (read timeout=30)
    WARNING Skipping array restore - watchlist data incomplete (plex.tv unreachable)

`MyPlexAccount(...)` in the `user is None` branch was the one plex.tv entry
point left unwrapped — `_get_main_account()` and the `switchHomeUser` path had
both been given retries already. On an install with no home users that branch is
the only watchlist fetch there is, so "this user failed" and "watchlist data is
incomplete" are the same event, and the run skipped array restore entirely.

Retry semantics themselves live in `test_plex_api_retry.py`; this module only
checks that this call site is wired into the helper.
"""

import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core.plex_api import PlexManager, PLEXTV_MAX_RETRIES

# Verbatim from the log line above — a read timeout, not a connection reset.
# The two arrive as different exception classes and only ReadTimeout was seen
# in production.
READ_TIMEOUT = requests.exceptions.ReadTimeout(
    "HTTPSConnectionPool(host='plex.tv', port=443): "
    "Read timed out. (read timeout=30)"
)


def _bare_api(username="Brandon"):
    """A PlexManager with just the state the main-account branch touches."""
    api = PlexManager.__new__(PlexManager)
    api.plex_url = "http://localhost:32400"
    api.plex_token = "ADMIN_TOKEN"
    api._user_tokens = {}
    api._user_is_home = {}
    api._ondeck_data_complete = True
    api._watchlist_data_complete = True
    api._token_lock = threading.Lock()
    api._rate_limited_api_call = MagicMock()
    api._token_cache = MagicMock()
    # user is None → the username comes from the local server, not plex.tv
    api.plex = MagicMock()
    api.plex.myPlexAccount.return_value.title = username
    api.mark_watchlist_incomplete = MagicMock(
        side_effect=lambda: setattr(api, '_watchlist_data_complete', False)
    )
    return api


def _fetch(api):
    """Drain the generator for the main account (user=None), no RSS."""
    return list(api._fetch_user_watchlist(
        user=None,
        valid_sections=[1],
        watchlist_episodes=3,
        skip_watchlist=[],
        rss_url=None,
        filtered_sections=[1],
    ))


class TestMainAccountRetry:

    def test_transient_timeout_then_success_reaches_the_watchlist(self):
        """The case from the log: one blip must not cost the run."""
        api = _bare_api()
        account = MagicMock()
        account.watchlist.return_value = []

        with patch('core.plex_api.MyPlexAccount',
                   side_effect=[READ_TIMEOUT, account]) as mock_acct, \
             patch('core.plex_api.requests.Session'), \
             patch('core.plex_api.time.sleep'):
            _fetch(api)

        assert mock_acct.call_count == 2
        assert api.mark_watchlist_incomplete.call_count == 0
        assert api.is_watchlist_data_complete() is True
        account.watchlist.assert_called_once()

    def test_three_attempts_before_giving_up(self):
        """Persistent timeout → PLEXTV_MAX_RETRIES tries, then mark incomplete."""
        api = _bare_api()

        with patch('core.plex_api.MyPlexAccount',
                   side_effect=READ_TIMEOUT) as mock_acct, \
             patch('core.plex_api.requests.Session'), \
             patch('core.plex_api.time.sleep'):
            _fetch(api)

        assert mock_acct.call_count == PLEXTV_MAX_RETRIES == 3
        api.mark_watchlist_incomplete.assert_called_once()

    def test_backs_off_between_attempts(self):
        """2s then 4s, so a brief plex.tv blip has time to clear."""
        api = _bare_api()

        with patch('core.plex_api.MyPlexAccount', side_effect=READ_TIMEOUT), \
             patch('core.plex_api.requests.Session'), \
             patch('core.plex_api.time.sleep') as mock_sleep:
            _fetch(api)

        assert [c.args[0] for c in mock_sleep.call_args_list] == [2, 4]

    def test_connection_errors_are_retried_too(self):
        """DNS failures surface as ConnectionError — see issue #197."""
        api = _bare_api()
        account = MagicMock()
        account.watchlist.return_value = []

        with patch('core.plex_api.MyPlexAccount',
                   side_effect=[requests.ConnectionError(
                       "Failed to resolve 'plex.tv'"), account]) as mock_acct, \
             patch('core.plex_api.requests.Session'), \
             patch('core.plex_api.time.sleep'):
            _fetch(api)

        assert mock_acct.call_count == 2
        assert api.mark_watchlist_incomplete.call_count == 0

    def test_auth_failure_is_not_retried(self):
        """A revoked token fails identically on every attempt; retrying only stalls."""
        api = _bare_api()

        with patch('core.plex_api.MyPlexAccount',
                   side_effect=ValueError("(401) Unauthorized")) as mock_acct, \
             patch('core.plex_api.requests.Session'), \
             patch('core.plex_api.time.sleep') as mock_sleep:
            _fetch(api)

        assert mock_acct.call_count == 1
        mock_sleep.assert_not_called()
        api.mark_watchlist_incomplete.assert_called_once()


class TestFailureStillGuardsArrayRestore:
    """Retrying changes how often we give up, never what giving up means."""

    def test_exhausted_retries_still_flag_incomplete_data(self):
        api = _bare_api()

        with patch('core.plex_api.MyPlexAccount', side_effect=READ_TIMEOUT), \
             patch('core.plex_api.requests.Session'), \
             patch('core.plex_api.time.sleep'):
            yielded = _fetch(api)

        assert yielded == []
        assert api.is_watchlist_data_complete() is False, (
            "array restore must stay blocked when the watchlist never loaded"
        )
