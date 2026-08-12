"""Tests that one bad watchlist item doesn't drop the rest of a user's watchlist.

Observed 2026-07-31: plex.tv returned 503 while plexapi lazily resolved `guids`
on a single watchlist item. The exception escaped the per-user try/except, so
the remaining ~70 of that user's 93 items were never processed, and the run
marked watchlist data incomplete — which blocks array restore for every user.

Each item now resolves inside its own guard: a failure costs that item only.
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

from plexapi.exceptions import BadRequest

from core.plex_api import PlexManager


SERVICE_UNAVAILABLE = BadRequest(
    "(503) service_unavailable; https://metadata.provider.plex.tv/library/"
    "metadata/5d9c086ee98e47001eb0fcba?includeBandwidths=1 upstream connect "
    "error or disconnect/reset before headers. reset reason: connection termination"
)


def _bare_api():
    """Construct a PlexManager without running __init__ (avoids network auth)."""
    api = PlexManager.__new__(PlexManager)
    api.plex_url = "http://localhost:32400"
    api.plex_token = "ADMIN_TOKEN"
    api.watchlist_enabled = True
    api._user_tokens = {}
    api._plex_tv_reachable = True
    api._watchlist_data_complete = True
    api._ondeck_data_complete = True
    api._token_lock = threading.Lock()
    api._rate_limited_api_call = MagicMock()
    api.plex = MagicMock()
    return api


def _watchlist_item(title, guids_error=None):
    """A plex.tv watchlist item whose `guids` may blow up on lazy reload."""
    item = MagicMock()
    item.title = title
    item.type = 'movie'
    if guids_error is not None:
        type(item).guids = property(lambda self: (_ for _ in ()).throw(guids_error))
    else:
        item.guids = []
    return item


def _local_movie(title):
    """A local Plex library match for a watchlist item."""
    movie = MagicMock()
    movie.title = title
    movie.TYPE = 'movie'
    movie.librarySectionID = 1
    return movie


class TestWatchlistItemIsolation:
    """A single failing item must not abort the rest of the watchlist."""

    def _run(self, api, items):
        account = MagicMock()
        account.watchlist.return_value = items
        account.userState.return_value = MagicMock(watchlistedAt=None)

        processed = []

        def fake_process_movie(file, username, watchlisted_at):
            processed.append(file.title)
            yield (f"/data/Movies/{file.title}.mkv", username, watchlisted_at, None, None, "watchlist")

        with patch.object(PlexManager, '_process_watchlist_movie', side_effect=fake_process_movie), \
             patch.object(api, 'search_plex', side_effect=lambda title, **kw: _local_movie(title)), \
             patch('core.plex_api.MyPlexAccount', return_value=account), \
             patch('core.plex_api.requests.Session', MagicMock()), \
             patch('core.plex_api.time.sleep'):
            results = list(api._fetch_user_watchlist(
                user=None, valid_sections=[1], watchlist_episodes=3,
                skip_watchlist=[], rss_url=None, filtered_sections=[1],
            ))
        return processed, results

    def test_failing_item_does_not_drop_later_items(self):
        api = _bare_api()
        items = [
            _watchlist_item("Weeds"),
            _watchlist_item("Motherland", guids_error=SERVICE_UNAVAILABLE),
            _watchlist_item("Slow Horses"),
            _watchlist_item("The Menu"),
        ]

        processed, results = self._run(api, items)

        # The 503 costs exactly one item; the three healthy ones still resolve.
        assert processed == ["Weeds", "Slow Horses", "The Menu"]
        assert len(results) == 3

    def test_failing_item_marks_watchlist_incomplete(self):
        """Dropped items mean the data really is incomplete — restore stays gated."""
        api = _bare_api()
        items = [
            _watchlist_item("Weeds"),
            _watchlist_item("Motherland", guids_error=SERVICE_UNAVAILABLE),
        ]

        self._run(api, items)

        assert api.is_watchlist_data_complete() is False

    def test_clean_watchlist_stays_complete(self):
        api = _bare_api()
        items = [_watchlist_item("Weeds"), _watchlist_item("Slow Horses")]

        processed, results = self._run(api, items)

        assert processed == ["Weeds", "Slow Horses"]
        assert api.is_watchlist_data_complete() is True

    def test_skipped_item_is_logged_with_title_and_user(self, caplog):
        import logging
        api = _bare_api()
        items = [_watchlist_item("Motherland", guids_error=SERVICE_UNAVAILABLE)]

        with caplog.at_level(logging.WARNING):
            self._run(api, items)

        messages = [rec.message for rec in caplog.records]
        assert any("Motherland" in m and "Skipping watchlist item" in m for m in messages)

    def test_transient_503_on_guids_is_retried(self):
        """A 503 that clears on retry costs nothing at all."""
        api = _bare_api()
        item = MagicMock()
        item.title = "Motherland"
        item.type = 'movie'
        calls = {"n": 0}

        def flaky_guids(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SERVICE_UNAVAILABLE
            return []

        type(item).guids = property(flaky_guids)

        processed, results = self._run(api, [item])

        assert processed == ["Motherland"]
        assert calls["n"] == 2
        assert api.is_watchlist_data_complete() is True
