"""Tests for plex.tv resilience during main-account lookup (issue #197).

Two behaviours are covered:

1. `_get_main_account()` wraps `myPlexAccount()` in `_retry_plextv_call`, so a
   transient DNS failure or connection reset on the first plex.tv call of a run
   no longer marks plex.tv unreachable for the whole run.

2. When watchlist caching is disabled, a plex.tv failure must not set the
   "watchlist data incomplete" flag. That flag gates array restore, and with
   watchlist off no cached file is held by the watchlist, so blocking the
   restore is a false positive.
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
    'plexapi.library', 'plexapi.exceptions',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core.plex_api import PlexManager, PLEXTV_MAX_RETRIES


# The exact error urllib3/requests raises when DNS returns no address, as
# reported in issue #197 ([Errno -5] No address associated with hostname).
DNS_FAILURE = requests.ConnectionError(
    "HTTPSConnectionPool(host='plex.tv', port=443): Max retries exceeded with "
    "url: /api/v2/user (Caused by NameResolutionError(\"HTTPSConnection("
    "host='plex.tv', port=443): Failed to resolve 'plex.tv' "
    "([Errno -5] No address associated with hostname)\"))"
)


def _bare_api(watchlist_enabled=True):
    """Construct a PlexManager without running __init__ (avoids network auth)."""
    api = PlexManager.__new__(PlexManager)
    api.plex_url = "http://localhost:32400"
    api.plex_token = "ADMIN_TOKEN"
    api.watchlist_enabled = watchlist_enabled
    api._user_tokens = {}
    api._plex_tv_reachable = True
    api._watchlist_data_complete = True
    api._ondeck_data_complete = True
    api._token_lock = threading.Lock()
    api._rate_limited_api_call = MagicMock()
    api.plex = MagicMock()
    return api


class TestMainAccountRetry:
    """_get_main_account() retry wiring."""

    def test_transient_dns_failure_then_success(self):
        """DNS blip on the first attempt, success on the second → account returned."""
        api = _bare_api()
        account = MagicMock()
        account.title = "Brandon"
        api.plex.myPlexAccount.side_effect = [DNS_FAILURE, account]

        with patch("core.plex_api.time.sleep"):
            result = api._get_main_account("main")

        assert result is account
        assert api.plex.myPlexAccount.call_count == 2
        # A recovered blip must leave both health flags untouched.
        assert api._plex_tv_reachable is True
        assert api._watchlist_data_complete is True

    def test_retries_are_bounded_then_gives_up(self):
        """Sustained DNS failure exhausts retries and reports plex.tv unreachable."""
        api = _bare_api()
        api.plex.myPlexAccount.side_effect = DNS_FAILURE

        with patch("core.plex_api.time.sleep"):
            result = api._get_main_account("main")

        assert result is None
        assert api.plex.myPlexAccount.call_count == PLEXTV_MAX_RETRIES
        assert api._plex_tv_reachable is False

    def test_success_on_first_attempt_makes_one_call(self):
        """No retry overhead on the happy path."""
        api = _bare_api()
        account = MagicMock()
        account.title = "Brandon"
        api.plex.myPlexAccount.return_value = account

        assert api._get_main_account("Brandon") is account
        assert api.plex.myPlexAccount.call_count == 1

    def test_auth_error_is_not_retried(self):
        """Non-transient errors fail fast rather than burning retry budget."""
        api = _bare_api()
        api.plex.myPlexAccount.side_effect = ValueError("401 Unauthorized")

        with patch("core.plex_api.time.sleep"):
            assert api._get_main_account("main") is None

        assert api.plex.myPlexAccount.call_count == 1
        assert api._plex_tv_reachable is False


class TestWatchlistFlagRespectsToggle:
    """plex.tv failure should only flag watchlist data when watchlist is in use."""

    def test_watchlist_enabled_flags_incomplete(self):
        api = _bare_api(watchlist_enabled=True)
        api.plex.myPlexAccount.side_effect = DNS_FAILURE

        with patch("core.plex_api.time.sleep"):
            api._get_main_account("main")

        assert api._plex_tv_reachable is False
        assert api._watchlist_data_complete is False
        assert api.is_watchlist_data_complete() is False

    def test_watchlist_disabled_leaves_data_complete(self):
        """With watchlist off, array restore must not be blocked by a plex.tv outage."""
        api = _bare_api(watchlist_enabled=False)
        api.plex.myPlexAccount.side_effect = DNS_FAILURE

        with patch("core.plex_api.time.sleep"):
            api._get_main_account("main")

        # plex.tv is still genuinely unreachable...
        assert api._plex_tv_reachable is False
        # ...but nothing watchlist-shaped is incomplete, so restore stays allowed.
        assert api._watchlist_data_complete is True
        assert api.is_watchlist_data_complete() is True

    def test_constructor_defaults_watchlist_enabled_true(self):
        """Existing callers that don't pass the flag keep the old guard behaviour."""
        with patch("core.plex_api.UserTokenCache"):
            api = PlexManager(plex_url="http://localhost:32400", plex_token="T")
        assert api.watchlist_enabled is True
