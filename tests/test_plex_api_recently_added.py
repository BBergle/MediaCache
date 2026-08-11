"""Tests for PlexManager.get_recently_added_media().

Recently-added is a server-wide, library-level fetch (no per-user token
machinery). It iterates enabled library sections, calls section.recentlyAdded(),
filters by an added-within-N-days cutoff, emits one RecentlyAddedItem per media
file, and returns the newest first capped at max_items.
"""

import logging
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library', 'plexapi.exceptions', 'requests',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plex_api import PlexManager, RecentlyAddedItem


# --- fixtures / builders -------------------------------------------------

def _part(file, size=1000):
    return SimpleNamespace(file=file, size=size)


def _media(*parts):
    return SimpleNamespace(parts=list(parts))


def _movie(rating_key, title, added_days_ago, parts=None, added_at=None):
    when = added_at if added_at is not None else datetime.now() - timedelta(days=added_days_ago)
    return SimpleNamespace(
        type='movie', ratingKey=rating_key, title=title, addedAt=when,
        media=[_media(*(parts or [_part(f"/data/movies/{title}.mkv")]))],
    )


def _episode(rating_key, title, show, season, episode, added_days_ago, parts=None):
    return SimpleNamespace(
        type='episode', ratingKey=rating_key, title=title,
        grandparentTitle=show, parentIndex=season, index=episode,
        addedAt=datetime.now() - timedelta(days=added_days_ago),
        media=[_media(*(parts or [_part(f"/data/tv/{show}/{title}.mkv")]))],
    )


def _section(key, title, items):
    return SimpleNamespace(
        key=key, title=title,
        recentlyAdded=lambda maxresults=None, _items=items: list(_items),
    )


def _show_wrapper(rating_key, title):
    """A show-level item (what recentlyAdded() returns for a show library)."""
    return SimpleNamespace(type='show', ratingKey=rating_key, title=title,
                           addedAt=datetime.now(), media=[])


def _show_section(key, title, episodes, shows=None):
    """A show library: recentlyAdded() yields show wrappers, while
    recentlyAddedEpisodes() yields the actual recently-added episodes."""
    return SimpleNamespace(
        key=key, title=title, type='show',
        recentlyAdded=lambda maxresults=None, _s=(shows or []): list(_s),
        recentlyAddedEpisodes=lambda maxresults=None, _e=episodes: list(_e),
    )


def _api(sections):
    api = PlexManager.__new__(PlexManager)
    api.plex = SimpleNamespace(library=SimpleNamespace(sections=lambda: sections))
    return api


# --- tests ---------------------------------------------------------------

class TestGetRecentlyAddedMedia:
    def test_returns_movie_with_metadata(self):
        api = _api([_section(1, "Movies", [
            _movie(101, "Dune", added_days_ago=1, parts=[_part("/data/movies/Dune.mkv", 28_000)]),
        ])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        assert len(items) == 1
        item = items[0]
        assert isinstance(item, RecentlyAddedItem)
        assert item.file_path == "/data/movies/Dune.mkv"
        assert item.rating_key == "101"  # stringified
        assert item.title == "Dune"
        assert item.media_type == "movie"
        assert item.library_title == "Movies"
        assert item.library_section_id == 1
        assert item.size == 28_000
        assert item.episode_info is None

    def test_filters_out_items_older_than_window(self):
        api = _api([_section(1, "Movies", [
            _movie(1, "Fresh", added_days_ago=2),
            _movie(2, "Stale", added_days_ago=40),
        ])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        titles = {i.title for i in items}
        assert titles == {"Fresh"}

    def test_episode_carries_show_season_episode(self):
        api = _api([_section(3, "TV Shows", [
            _episode(50, "Future Days", "The Last of Us", 2, 1, added_days_ago=1),
        ])])
        items = api.get_recently_added_media(valid_sections=[3], days_to_monitor=7)

        assert len(items) == 1
        item = items[0]
        assert item.media_type == "episode"
        assert item.episode_info == {"show": "The Last of Us", "season": 2, "episode": 1}

    def test_respects_valid_sections(self):
        api = _api([
            _section(1, "Movies", [_movie(1, "A", added_days_ago=1)]),
            _section(2, "4K Movies", [_movie(2, "B", added_days_ago=1)]),
        ])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        assert {i.title for i in items} == {"A"}

    def test_empty_valid_sections_includes_all(self):
        api = _api([
            _section(1, "Movies", [_movie(1, "A", added_days_ago=1)]),
            _section(2, "4K Movies", [_movie(2, "B", added_days_ago=1)]),
        ])
        items = api.get_recently_added_media(valid_sections=[], days_to_monitor=7)

        assert {i.title for i in items} == {"A", "B"}

    def test_caps_at_max_items_keeping_newest(self):
        api = _api([_section(1, "Movies", [
            _movie(1, "Oldest", added_days_ago=3),
            _movie(2, "Middle", added_days_ago=2),
            _movie(3, "Newest", added_days_ago=1),
        ])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7, max_items=2)

        assert [i.title for i in items] == ["Newest", "Middle"]

    def test_sorted_newest_first_across_sections(self):
        api = _api([
            _section(1, "Movies", [_movie(1, "Older", added_days_ago=5)]),
            _section(2, "TV Shows", [_episode(2, "Ep", "Show", 1, 1, added_days_ago=1)]),
        ])
        items = api.get_recently_added_media(valid_sections=[1, 2], days_to_monitor=7)

        assert [i.title for i in items] == ["Ep", "Older"]

    def test_skips_non_movie_episode_types(self):
        season_wrapper = SimpleNamespace(
            type='season', ratingKey=9, title="Season 2",
            addedAt=datetime.now(), media=[],
        )
        api = _api([_section(3, "TV Shows", [season_wrapper])])
        items = api.get_recently_added_media(valid_sections=[3], days_to_monitor=7)

        assert items == []

    def test_multi_part_version_is_one_item_covering_both_files(self):
        # cd1/cd2 are parts of ONE version: both are cached together, so they
        # belong to a single item whose size is their sum.
        api = _api([_section(1, "Movies", [
            _movie(1, "Multi", added_days_ago=1, parts=[
                _part("/data/movies/Multi-cd1.mkv", 500),
                _part("/data/movies/Multi-cd2.mkv", 600),
            ]),
        ])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        assert len(items) == 1
        assert items[0].file_path == "/data/movies/Multi-cd1.mkv"
        assert items[0].size == 1100
        assert [f["file_path"] for f in items[0].version_files] == [
            "/data/movies/Multi-cd1.mkv", "/data/movies/Multi-cd2.mkv",
        ]
        assert all(f["is_preferred"] for f in items[0].version_files)

    def test_multi_version_item_emits_one_row_for_the_preferred_version(self):
        # 4K + 1080p of one movie share a ratingKey. PlexCache only ever caches
        # and pins the preferred version, so a second row would put two pin
        # controls on one ratingKey — the second reading "Pin" but unpinning.
        movie = SimpleNamespace(
            type='movie', ratingKey=7, title="Dune",
            addedAt=datetime.now() - timedelta(days=1),
            media=[
                SimpleNamespace(videoResolution='1080', parts=[_part("/data/movies/Dune-1080p.mkv", 8)]),
                SimpleNamespace(videoResolution='4k', parts=[_part("/data/movies/Dune-4K.mkv", 40)]),
            ],
        )
        api = _api([_section(1, "Movies", [movie])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        assert len(items) == 1
        assert items[0].file_path == "/data/movies/Dune-4K.mkv"
        assert items[0].size == 40
        # The 1080p file is still disclosed, flagged as not the managed version.
        assert len(items[0].version_files) == 2
        others = [f for f in items[0].version_files if not f["is_preferred"]]
        assert [f["file_path"] for f in others] == ["/data/movies/Dune-1080p.mkv"]
        assert others[0]["label"] == "1080p"

    def test_version_preference_selects_the_row(self):
        movie = SimpleNamespace(
            type='movie', ratingKey=7, title="Dune",
            addedAt=datetime.now() - timedelta(days=1),
            media=[
                SimpleNamespace(videoResolution='1080', parts=[_part("/data/movies/Dune-1080p.mkv", 8)]),
                SimpleNamespace(videoResolution='4k', parts=[_part("/data/movies/Dune-4K.mkv", 40)]),
            ],
        )
        api = _api([_section(1, "Movies", [movie])])
        items = api.get_recently_added_media(
            valid_sections=[1], days_to_monitor=7, version_preference="lowest"
        )
        assert items[0].file_path == "/data/movies/Dune-1080p.mkv"

    def test_item_with_no_media_is_skipped(self):
        blank = SimpleNamespace(
            type='movie', ratingKey=8, title="Blank",
            addedAt=datetime.now(), media=[],
        )
        api = _api([_section(1, "Movies", [blank])])
        assert api.get_recently_added_media(valid_sections=[1], days_to_monitor=7) == []

    def test_returns_empty_when_sections_unavailable(self):
        api = PlexManager.__new__(PlexManager)

        def _raise():
            raise RuntimeError("not connected")

        api.plex = SimpleNamespace(library=SimpleNamespace(sections=_raise))
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        assert items == []

    def test_show_section_uses_recently_added_episodes(self):
        # recentlyAdded() on a show library returns show wrappers (skipped);
        # the episodes must come from recentlyAddedEpisodes().
        eps = [_episode(50, "Future Days", "The Last of Us", 2, 1, added_days_ago=1)]
        section = _show_section(2, "TV Shows", episodes=eps,
                                shows=[_show_wrapper(9, "The Last of Us")])
        api = _api([section])
        items = api.get_recently_added_media(valid_sections=[2], days_to_monitor=7)

        assert len(items) == 1
        assert items[0].media_type == "episode"
        assert items[0].episode_info == {"show": "The Last of Us", "season": 2, "episode": 1}

    def test_show_section_without_episode_helper_falls_back(self):
        # type == 'show' but no recentlyAddedEpisodes() → fall back to recentlyAdded().
        eps = [_episode(51, "Pilot", "Severance", 1, 1, added_days_ago=1)]
        section = SimpleNamespace(
            key=2, title="TV", type="show",
            recentlyAdded=lambda maxresults=None, _e=eps: list(_e),
        )
        api = _api([section])
        items = api.get_recently_added_media(valid_sections=[2], days_to_monitor=7)

        assert len(items) == 1
        assert items[0].media_type == "episode"

    def test_skips_parts_without_file_path(self):
        api = _api([_section(1, "Movies", [
            _movie(1, "NoFile", added_days_ago=1, parts=[_part(None), _part("/data/movies/Ok.mkv")]),
        ])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        assert [i.file_path for i in items] == ["/data/movies/Ok.mkv"]


class TestPartialObjectResilience:
    """One bad item must not discard every library already collected.

    Section listings return plexapi *partial* objects, which reload on any
    None/[] value — so reading an unnumbered special's `index` fires a live
    metadata GET. When that 5xx'd, the exception escaped
    get_recently_added_media() and the service rendered a full-page error.
    """

    class _RaisesOn:
        """An episode whose named attribute raises, as a failing reload would."""

        def __init__(self, name, rating_key=99, title="Special"):
            self._name = name
            self.type = 'episode'
            self.ratingKey = rating_key
            self.title = title
            self.grandparentTitle = "Show"
            self.grandparentRatingKey = 500
            self.addedAt = datetime.now()
            self.parentIndex = 1
            self.index = 1
            self.media = [_media(_part("/data/tv/Show/Special.mkv"))]

        def __getattribute__(self, item):
            if item != "_name" and item == object.__getattribute__(self, "_name"):
                raise RuntimeError("(503) reload failed")
            return object.__getattribute__(self, item)

    def test_bad_item_does_not_discard_the_section(self, caplog):
        bad = self._RaisesOn('index')
        api = _api([
            _section(1, "TV Shows", [
                _episode(1, "Good1", "Show", 1, 1, added_days_ago=1),
                bad,
                _episode(2, "Good2", "Show", 1, 2, added_days_ago=1),
            ]),
            _section(2, "Movies", [_movie(3, "Dune", added_days_ago=1)]),
        ])

        with caplog.at_level(logging.WARNING):
            items = api.get_recently_added_media(valid_sections=[1, 2], days_to_monitor=7)

        # All four survive — including the raising item, degraded rather than dropped.
        assert len(items) == 4
        degraded = [i for i in items if i.title == "Special"]
        assert len(degraded) == 1
        # A healthy server with a genuinely unnumbered special produces exactly
        # this, so the degraded path is byte-identical to the healthy one.
        assert degraded[0].episode_info is None
        assert any("could not read 'index'" in r.message for r in caplog.records)

    def test_unreadable_media_is_skipped_but_the_rest_survive(self, caplog):
        api = _api([_section(1, "TV Shows", [
            _episode(1, "Good1", "Show", 1, 1, added_days_ago=1),
            self._RaisesOn('media', rating_key=98, title="Unanalyzed"),
            _episode(2, "Good2", "Show", 1, 2, added_days_ago=1),
        ])])

        with caplog.at_level(logging.WARNING):
            items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)

        # No media means no file to show, so this one is correctly skipped —
        # but it takes only itself down. Compared as a set: both survivors share
        # an addedAt, so their relative order is sort noise, not behaviour.
        assert {i.title for i in items} == {"Good1", "Good2"}
        assert any("could not read 'media'" in r.message for r in caplog.records)

    def test_lazy_reload_is_disabled_on_listing_objects(self):
        """Guards the _autoReload opt-out from silently regressing."""
        ep = _episode(1, "Ep", "Show", 1, 1, added_days_ago=1)
        api = _api([_section(1, "TV Shows", [ep])])
        api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)
        assert ep._autoReload is False


class TestShowIdentity:
    def test_show_rating_key_is_captured_as_a_string(self):
        ep = _episode(1, "Ep", "Show", 1, 1, added_days_ago=1)
        ep.grandparentRatingKey = 500          # plexapi casts this to int
        api = _api([_section(1, "TV Shows", [ep])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)
        assert items[0].show_rating_key == "500"

    def test_missing_show_rating_key_degrades_to_empty(self):
        ep = _episode(1, "Ep", "Show", 1, 1, added_days_ago=1)
        api = _api([_section(1, "TV Shows", [ep])])
        items = api.get_recently_added_media(valid_sections=[1], days_to_monitor=7)
        assert items[0].show_rating_key == ""
