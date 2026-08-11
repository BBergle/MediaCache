"""Tests for surfacing the released state in the web UI.

A released file is on cache, out of the exclude list, and waiting on the Unraid
mover. Before this existed it fell into the "Other" bucket, indistinguishable
from a file with no recorded demand, which made a chunk of the cache look
unexplained.

The state comes from the timestamp entry's released_at stamp, which
PlexCacheApp._release_files() writes. No new storage.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

for _mod_name in [
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex', 'requests',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()


def _file(**kw):
    """Build a CachedFile with only the fields these tests care about."""
    from web.services.cache_service import CachedFile
    from datetime import datetime

    defaults = dict(
        path="/mnt/cache/x.mkv",
        filename="x.mkv",
        size=100,
        size_display="100 B",
        cached_at=datetime(2026, 8, 10, 12, 0, 0),
        cache_age_hours=1.0,
        source="unknown",
        priority_score=50,
        users=[],
        is_ondeck=False,
        is_watchlist=False,
    )
    defaults.update(kw)
    return CachedFile(**defaults)


class TestCachedFileField:

    def test_defaults_to_false(self):
        assert _file().is_released is False

    def test_round_trips_through_the_dict_serializer(self):
        from web.services.cache_service import cached_files_to_dicts

        rows = cached_files_to_dicts([_file(is_released=True), _file()])

        assert rows[0]["is_released"] is True
        assert rows[1]["is_released"] is False


class TestFileTotals:
    """calculate_file_totals() drives the counts on the Cached Files page."""

    def _totals(self, files):
        from web.services.cache_service import cached_files_to_dicts, calculate_file_totals
        return calculate_file_totals(cached_files_to_dicts(files))

    def test_released_counted_separately(self):
        totals = self._totals([_file(is_released=True), _file()])

        assert totals["released_count"] == 1
        assert totals["other_count"] == 1

    def test_released_does_not_inflate_other(self):
        """The bug this fixes: released files swelling the Other bucket."""
        totals = self._totals([_file(is_released=True) for _ in range(6)])

        assert totals["released_count"] == 6
        assert totals["other_count"] == 0

    def test_ondeck_file_that_is_released_counts_as_released(self):
        """Release marks files uncached in both trackers, but a stale tracker
        entry must not put a released file back in the OnDeck bucket."""
        totals = self._totals([_file(is_released=True, is_ondeck=True)])

        assert totals["released_count"] == 1
        assert totals["other_count"] == 0


class TestSourceFilter:
    """The ?source= filter on /cache, driven through the real builder."""

    def _build(self, tmp_path, source_filter, released):
        from web.services.cache_service import CacheService
        from conftest import create_test_file
        from datetime import datetime

        path = create_test_file(str(tmp_path / "Movie.mkv"), size_bytes=100)
        entry = {"cached_at": "2026-08-10T12:00:00", "source": "pre-existing"}
        if released:
            entry["released_at"] = "2026-08-10T21:40:00"

        service = object.__new__(CacheService)
        return service._build_cached_file(
            path,
            timestamps={path: entry},
            ondeck={},
            watchlist={},
            settings={},
            pinned_cache_path_map={},
            pinned_cache_paths=set(),
            video_subtitles={},
            video_sidecars={},
            source_filter=source_filter,
            search="",
            now=datetime(2026, 8, 10, 22, 0, 0),
        )

    def test_released_at_sets_the_flag(self, tmp_path):
        assert self._build(tmp_path, "all", released=True).is_released is True
        assert self._build(tmp_path, "all", released=False).is_released is False

    def test_released_filter_selects_released(self, tmp_path):
        assert self._build(tmp_path, "released", released=True) is not None
        assert self._build(tmp_path, "released", released=False) is None

    def test_other_filter_excludes_released(self, tmp_path):
        """The bug this fixes: released files falling into the Other bucket."""
        assert self._build(tmp_path, "other", released=True) is None
        assert self._build(tmp_path, "other", released=False) is not None


class TestBreakdownBuckets:
    """The Cache Breakdown by Source cards, driven through get_storage_stats().

    Locks down the ordering rule: released is evaluated before every other
    bucket, so a released file never double-counts or lands in Other.
    """

    def _breakdown(self, tmp_path, files):
        from web.services.cache_service import CacheService
        from unittest.mock import patch

        service = object.__new__(CacheService)
        with patch.object(CacheService, "get_all_cached_files", return_value=files), \
             patch.object(CacheService, "get_cached_files_list", return_value=[]), \
             patch.object(CacheService, "get_timestamps", return_value={}), \
             patch.object(CacheService, "get_ondeck_tracker", return_value={}), \
             patch.object(CacheService, "get_watchlist_tracker", return_value={}), \
             patch.object(CacheService, "_load_settings", return_value={}), \
             patch.object(CacheService, "_get_cache_dir", return_value=str(tmp_path)):
            return service.get_drive_details()["breakdown"]

    def test_buckets_are_mutually_exclusive(self, tmp_path):
        files = [
            _file(size=10, is_ondeck=True),
            _file(size=20, is_watchlist=True),
            _file(size=30),
            _file(size=40, is_released=True),
        ]
        b = self._breakdown(tmp_path, files)

        assert b["ondeck"]["size"] == 10
        assert b["watchlist"]["size"] == 20
        assert b["other"]["size"] == 30
        assert b["released"]["size"] == 40
        assert sum(v["size"] for v in b.values()) == 100

    def test_released_wins_over_a_stale_ondeck_flag(self, tmp_path):
        b = self._breakdown(tmp_path, [_file(size=50, is_released=True, is_ondeck=True)])

        assert b["released"]["size"] == 50
        assert b["ondeck"]["size"] == 0

    def test_percentages_add_up(self, tmp_path):
        """Cards are shown as a share of PlexCache data, so a file counted
        twice or dropped would make them not total 100%."""
        files = [
            _file(size=25, is_released=True),
            _file(size=25, is_ondeck=True),
            _file(size=50),
        ]
        b = self._breakdown(tmp_path, files)

        assert b["released"]["percent"] == 25.0
        assert b["ondeck"]["percent"] == 25.0
        assert b["other"]["percent"] == 50.0




class TestTemplatesRender:
    """The badges and card must survive Jinja parsing."""

    @pytest.mark.parametrize("template", [
        "web/templates/cache/partials/storage_stats.html",
        "web/templates/cache/partials/file_table.html",
        "web/templates/cache/partials/priorities_content.html",
        "web/templates/cache/list.html",
    ])
    def test_template_parses(self, template):
        import jinja2

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), template
        )
        with open(path, encoding="utf-8") as f:
            jinja2.Environment().parse(f.read(), filename=template)

    def test_released_markup_present(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        stats = open(os.path.join(
            root, "web/templates/cache/partials/storage_stats.html"), encoding="utf-8").read()
        assert "breakdown-released" in stats
        assert "source=released" in stats
        assert "badge-released" in stats

        listing = open(os.path.join(
            root, "web/templates/cache/list.html"), encoding="utf-8").read()
        assert 'value="released"' in listing

    def test_released_badge_styled(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        css = open(os.path.join(
            root, "web/static/css/plex-theme.css"), encoding="utf-8").read()

        assert ".badge-released" in css
        assert ".breakdown-released .breakdown-header" in css
        assert ".breakdown-fill.released" in css
