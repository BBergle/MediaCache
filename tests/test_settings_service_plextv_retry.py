"""The Settings user list survives a transient plex.tv timeout.

Observed 2026-08-12 15:02 during the hourly cache refresh:

    Refreshing Plex data cache...
    WARNING Could not get main account: HTTPSConnectionPool(host='plex.tv',
        port=443): Read timed out. (read timeout=30)
    INFO Fetched 24 shared users

The admin silently vanished from the Settings user list for that refresh
cycle while the shared users loaded fine — the very next plex.tv call
succeeded. That independence is why the two blocks retry separately instead
of sharing one fetched account.

Retries here are capped at 2 attempts, not the PLEXTV_MAX_RETRIES of 3 used
by the caching engine: this code is reachable from GET /settings/plex/users,
so attempts are paid in page latency, and losing is a stale user list rather
than a skipped array restore.
"""

import os
import sys
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('fcntl', MagicMock())
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

from web.services.settings_service import SettingsService

READ_TIMEOUT = requests.exceptions.ReadTimeout(
    "HTTPSConnectionPool(host='plex.tv', port=443): "
    "Read timed out. (read timeout=30)"
)

UI_ATTEMPTS = 2


def _bare_service():
    """A SettingsService with only the state get_plex_users() touches."""
    svc = SettingsService.__new__(SettingsService)
    svc._cache_lock = threading.Lock()
    svc._plex_users_cache = []
    svc._plex_cache_time = datetime.now()
    svc._last_plex_error = None
    svc._prefetched_users = None
    svc._save_plex_cache_to_file = MagicMock()
    svc._is_plex_cache_valid = MagicMock(return_value=False)
    return svc


def _shared_user(title, home=False):
    user = MagicMock()
    user.title = title
    user.home = home
    return user


def _account(username="Brandon", shared=()):
    account = MagicMock()
    account.username = username
    account.title = username
    account.users.return_value = list(shared)
    return account


def _fetch(svc):
    """Explicit credentials bypass the cache branch and force a live fetch."""
    return svc.get_plex_users(plex_url="http://localhost:32400",
                              plex_token="TOKEN")


class TestTransientTimeoutIsRetried:

    def test_main_account_recovers_on_the_second_attempt(self):
        account = _account(shared=[_shared_user("Paige")])
        plex = MagicMock()
        plex.myPlexAccount.side_effect = [READ_TIMEOUT, account, account]
        svc = _bare_service()

        with patch('plexapi.server.PlexServer', return_value=plex), \
             patch('core.plex_api.time.sleep'):
            users = _fetch(svc)

        assert [u["username"] for u in users] == ["Brandon", "Paige"]
        assert svc.get_last_plex_error() is None

    def test_gives_up_after_two_attempts(self):
        """Capped lower than the engine's three — see the module docstring."""
        account = _account(shared=[_shared_user("Paige")])
        plex = MagicMock()
        # Fail the two main-account attempts, then let the shared-user
        # block's own fetch succeed.
        plex.myPlexAccount.side_effect = [READ_TIMEOUT, READ_TIMEOUT, account]
        svc = _bare_service()

        with patch('plexapi.server.PlexServer', return_value=plex), \
             patch('core.plex_api.time.sleep'):
            users = _fetch(svc)

        assert plex.myPlexAccount.call_count == UI_ATTEMPTS + 1
        assert [u["username"] for u in users] == ["Paige"]

    def test_the_user_list_call_is_retried_too(self):
        """account.users() is the actual shared-user round trip."""
        account = _account(shared=[_shared_user("Paige")])
        account.users.side_effect = [READ_TIMEOUT, [_shared_user("Paige")]]
        plex = MagicMock()
        plex.myPlexAccount.return_value = account
        svc = _bare_service()

        with patch('plexapi.server.PlexServer', return_value=plex), \
             patch('core.plex_api.time.sleep'):
            users = _fetch(svc)

        assert account.users.call_count == 2
        assert [u["username"] for u in users] == ["Brandon", "Paige"]


class TestTheTwoBlocksStayIndependent:
    """The exact shape of the logged incident."""

    def test_a_lost_main_account_does_not_cost_the_shared_users(self):
        account = _account(shared=[_shared_user("Paige"), _shared_user("Alex")])
        plex = MagicMock()
        # Both main-account attempts time out; the shared-user block then
        # fetches its own account successfully, as happened in the log.
        plex.myPlexAccount.side_effect = [READ_TIMEOUT, READ_TIMEOUT, account]
        svc = _bare_service()

        with patch('plexapi.server.PlexServer', return_value=plex), \
             patch('core.plex_api.time.sleep'):
            users = _fetch(svc)

        assert [u["username"] for u in users] == ["Paige", "Alex"]
        assert all(u["is_admin"] is False for u in users)

    def test_total_plextv_failure_reports_an_error(self):
        plex = MagicMock()
        plex.myPlexAccount.side_effect = READ_TIMEOUT
        svc = _bare_service()

        with patch('plexapi.server.PlexServer', return_value=plex), \
             patch('core.plex_api.time.sleep'):
            users = _fetch(svc)

        assert users == []
        assert "Could not get account info" in svc.get_last_plex_error()


class TestNonTransientFailures:

    def test_auth_failure_is_not_retried(self):
        """A revoked token fails the same way every time."""
        plex = MagicMock()
        plex.myPlexAccount.side_effect = ValueError("(401) Unauthorized")
        svc = _bare_service()

        with patch('plexapi.server.PlexServer', return_value=plex), \
             patch('core.plex_api.time.sleep') as mock_sleep:
            _fetch(svc)

        # One attempt per block, no backoff.
        assert plex.myPlexAccount.call_count == 2
        mock_sleep.assert_not_called()
