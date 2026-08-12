"""`PlexManager` takes an explicit connection timeout.

`PlexServer` was constructed with no timeout, so it inherited plexapi's 30s
default implicitly. plexapi keeps the constructor value on the session
(`PlexServer._timeout`, consumed by `query()`), so it governs every subsequent
request, not just the handshake — which is why the web caller pins 30 rather
than the shorter values used by connect-and-validate handshakes elsewhere.

Mock-only: a real black-hole-socket timing test costs ~2 minutes of wall clock
to assert the same contract.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod in [
    'apscheduler', 'apscheduler.schedulers', 'apscheduler.schedulers.background',
    'apscheduler.triggers', 'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library', 'plexapi.exceptions', 'requests',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plex_api import PlexManager


class _FakeServer:
    version = "1.2.3"
    seen = {}

    def __init__(self, url, token, timeout=None):
        _FakeServer.seen = {"url": url, "token": token, "timeout": timeout}


class TestConnectTimeout:
    def test_defaults_to_none_so_plexapi_picks_its_own(self, monkeypatch):
        # The CLI path must stay byte-identical: plexapi does `timeout or TIMEOUT`,
        # so None yields exactly today's behaviour. Hardcoding 30 in the core
        # default would instead pin the value if plexapi ever changes TIMEOUT.
        monkeypatch.setattr("core.plex_api.PlexServer", _FakeServer)
        PlexManager("http://host:32400", "TOKEN").connect()
        assert _FakeServer.seen["timeout"] is None

    def test_forwards_an_explicit_timeout(self, monkeypatch):
        monkeypatch.setattr("core.plex_api.PlexServer", _FakeServer)
        PlexManager("http://host:32400", "TOKEN", timeout=30).connect()
        assert _FakeServer.seen["timeout"] == 30

    def test_timeout_is_stored_on_the_manager(self):
        assert PlexManager("http://h:32400", "T").timeout is None
        assert PlexManager("http://h:32400", "T", timeout=15).timeout == 15


class TestRecentlyAddedUsesTheLongTimeout:
    """Guards against someone 'tidying' this down to match the 10s handshakes."""

    def test_service_constructs_the_manager_with_30s(self, monkeypatch):
        import web.services.recently_added_service as ras

        captured = {}

        class _CapturingManager:
            def __init__(self, url, token, **kwargs):
                captured.update(kwargs)
                captured["url"] = url

            def connect(self):
                return None

        monkeypatch.setattr("core.plex_api.PlexManager", _CapturingManager)

        cfg = MagicMock()
        cfg.plex.plex_url = "http://host:32400"
        cfg.plex.plex_token = "TOKEN"
        cfg.plex.plex_db_path = ""

        ras.RecentlyAddedService()._connect_plex(cfg)

        assert captured["timeout"] == 30, (
            "Recently Added pulls up to recently_added_max_items per library; "
            "plexapi applies this timeout to those sweeps, not just the connect."
        )
