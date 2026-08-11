"""Tests for RecentlyAddedService enrichment + summary logic.

``_enrich`` is pure given its inputs (raw items, a path converter, the tracker
key sets, and the file-existence probe), so these tests exercise it directly
without a live Plex server or real filesystem.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

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

from core.plex_api import RecentlyAddedItem
from web.services.recently_added_service import RecentlyAddedService, RecentlyAddedRow


class FakePathModifier:
    """Maps /data/... -> /mnt/user/... -> /mnt/cache/... predictably."""

    def convert_plex_to_real(self, plex):
        if plex and plex.startswith("/data"):
            return ("/mnt/user" + plex[len("/data"):], None)
        return (None, None)

    def convert_real_to_cache(self, real):
        if real and real.startswith("/mnt/user"):
            return ("/mnt/cache" + real[len("/mnt/user"):], None)
        return (None, None)


def _item(rating_key="1", title="Movie", media_type="movie",
          plex_path="/data/movies/Movie.mkv", size=1000, episode_info=None):
    return RecentlyAddedItem(
        file_path=plex_path,
        rating_key=rating_key,
        title=title,
        media_type=media_type,
        added_at=datetime.now() - timedelta(hours=2),
        library_title="Movies",
        library_section_id=1,
        size=size,
        episode_info=episode_info,
    )


def _service(on_disk):
    """Service whose file-existence probe returns True only for `on_disk`."""
    svc = RecentlyAddedService()
    svc._file_exists = lambda path, _set=set(on_disk): path in _set
    return svc


def _enrich(svc, items, pinned=None, ondeck=None, watchlist=None, timestamps=None):
    return svc._enrich(
        items,
        FakePathModifier(),
        pinned_keys=set(pinned or []),
        ondeck_keys=set(ondeck or []),
        watchlist_keys=set(watchlist or []),
        timestamps_keys=set(timestamps or []),
    )


class TestEnrichState:
    def test_on_cache_not_pinned_is_actionable_state(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        rows = _enrich(svc, [_item()])
        assert len(rows) == 1
        r = rows[0]
        assert r.location == "cache"
        assert r.state == "on_cache_not_pinned"
        assert r.protected_by == []

    def test_on_array_when_only_array_file_exists(self):
        svc = _service(on_disk={"/mnt/user0/movies/Movie.mkv"})
        rows = _enrich(svc, [_item()])
        assert rows[0].location == "array"
        assert rows[0].state == "on_array"

    def test_pinned_takes_precedence_over_location(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        rows = _enrich(svc, [_item(rating_key="42")], pinned=["42"])
        assert rows[0].is_pinned is True
        assert rows[0].state == "pinned"
        assert rows[0].protected_by == ["Pinned"]

    def test_ondeck_membership_keyed_by_real_path(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        rows = _enrich(svc, [_item()], ondeck=["/mnt/user/movies/Movie.mkv"])
        assert rows[0].is_ondeck is True
        assert rows[0].state == "protected"
        assert rows[0].protected_by == ["OnDeck"]

    def test_watchlist_membership_keyed_by_plex_path(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        rows = _enrich(svc, [_item()], watchlist=["/data/movies/Movie.mkv"])
        assert rows[0].is_watchlist is True
        assert rows[0].state == "protected"
        assert rows[0].protected_by == ["Watchlist"]

    def test_unknown_location_for_unmapped_path(self):
        svc = _service(on_disk=set())
        rows = _enrich(svc, [_item(plex_path="/somewhere/else/x.mkv")])
        assert rows[0].location == "unknown"
        assert rows[0].state == "unknown"

    def test_cache_tracked_flag_from_timestamps(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        rows = _enrich(svc, [_item()], timestamps=["/mnt/cache/movies/Movie.mkv"])
        assert rows[0].is_cache_tracked is True

    def test_episode_pin_type_and_metadata(self):
        svc = _service(on_disk={"/mnt/cache/tv/Show/Ep.mkv"})
        ep = _item(media_type="episode", plex_path="/data/tv/Show/Ep.mkv",
                   episode_info={"show": "Show", "season": 1, "episode": 3})
        rows = _enrich(svc, [ep])
        assert rows[0].pin_type == "episode"
        assert rows[0].episode_info == {"show": "Show", "season": 1, "episode": 3}

    def test_display_fields_formatted(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        rows = _enrich(svc, [_item(size=28_000_000_000)])
        assert rows[0].size_display.endswith("GB")
        assert rows[0].added_display  # non-empty relative age


def _make_row(rating_key="1", title="X", media_type="movie", library_title="Movies",
              size=1000, location="cache", state="on_cache_not_pinned",
              is_pinned=False, episode_info=None):
    return RecentlyAddedRow(
        rating_key=rating_key, title=title, media_type=media_type,
        library_title=library_title, file_path=f"/data/{title}.mkv",
        size=size, size_display="1.00 KB", added_at=datetime.now(),
        added_display="now", location=location, state=state, is_pinned=is_pinned,
        pin_type="episode" if media_type == "episode" else "movie",
        episode_info=episode_info,
    )


class TestGroupRowsForDisplay:
    def test_multi_episode_show_collapses_to_one_group(self):
        rows = [
            _make_row("1", "Ep One", "episode", "TV Shows", 100,
                      episode_info={"show": "The Last of Us", "season": 2, "episode": 1}),
            _make_row("2", "Ep Two", "episode", "TV Shows", 200,
                      episode_info={"show": "The Last of Us", "season": 2, "episode": 2}),
            _make_row("3", "Ep Three", "episode", "TV Shows", 300, location="array",
                      state="on_array",
                      episode_info={"show": "The Last of Us", "season": 2, "episode": 3}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        assert len(display) == 1
        g = display[0]
        assert g["kind"] == "show"
        assert g["show"] == "The Last of Us"
        assert g["season"] == 2
        assert g["episode_count"] == 3
        assert g["total_size"] == 600
        # Mixed cache/array → both locations present
        assert set(g["locations"]) == {"cache", "array"}
        assert g["not_pinned_count"] == 2
        # Episodes sorted by episode number
        assert [e.episode_info["episode"] for e in g["episodes"]] == [1, 2, 3]

    def test_single_episode_show_stays_a_row(self):
        rows = [_make_row("1", "Solo", "episode", "TV Shows",
                          episode_info={"show": "Severance", "season": 1, "episode": 1})]
        display = RecentlyAddedService.group_rows_for_display(rows)
        assert len(display) == 1
        assert display[0]["kind"] == "row"

    def test_movies_stay_rows_and_order_preserved(self):
        rows = [
            _make_row("1", "Dune", "movie"),
            _make_row("2", "EpA", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
            _make_row("3", "EpB", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
            _make_row("4", "Civil War", "movie"),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        # movie, show-group (anchored at first episode), movie
        assert [d["kind"] for d in display] == ["row", "show", "row"]
        assert display[0]["row"].title == "Dune"
        assert display[1]["episode_count"] == 2
        assert display[2]["row"].title == "Civil War"

    def test_seasons_merge_into_one_show_group(self):
        rows = [
            _make_row("1", "S1E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
            _make_row("2", "S1E2", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
            _make_row("3", "S2E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 2, "episode": 1}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        # A season rollover must not split one show into two adjacent rows.
        assert [d["kind"] for d in display] == ["show"]
        g = display[0]
        assert g["episode_count"] == 3
        assert g["seasons"] == [1, 2]
        assert g["season_display"] == "Seasons 1–2"
        # No single season to name → the plain `season` field is None
        assert g["season"] is None
        # Sorted by (season, episode) so the rollover reads in order
        assert [(e.episode_info["season"], e.episode_info["episode"]) for e in g["episodes"]] == [
            (1, 1), (1, 2), (2, 1)
        ]

    def test_single_season_group_names_that_season(self):
        rows = [
            _make_row("1", "E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 3, "episode": 1}),
            _make_row("2", "E2", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 3, "episode": 2}),
        ]
        g = RecentlyAddedService.group_rows_for_display(rows)[0]
        assert g["season"] == 3
        assert g["season_display"] == "Season 3"

    def test_same_show_in_two_libraries_stays_separate(self):
        rows = [
            _make_row("1", "E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
            _make_row("2", "E2", "episode", "Anime",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        assert [d["kind"] for d in display] == ["row", "row"]

    def test_group_added_display_is_newest_episode(self):
        # Rows arrive newest-first, so the group's age is the first member's.
        rows = [
            _make_row("1", "E2", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
            _make_row("2", "E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
        ]
        rows[0].added_display = "1 hr ago"
        rows[1].added_display = "9 hr ago"
        g = RecentlyAddedService.group_rows_for_display(rows)[0]
        assert g["added_display"] == "1 hr ago"


class TestScanAssociatedFiles:
    def test_matches_subtitles_and_sidecars(self, tmp_path):
        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Dune.mkv"
        video.write_bytes(b"x")
        (d / "Dune.en.srt").write_bytes(b"sub")
        (d / "Dune.nfo").write_bytes(b"nfo")
        (d / "Dune-poster.jpg").write_bytes(b"img")
        (d / "Unrelated.txt").write_bytes(b"no")

        svc = RecentlyAddedService()
        found = svc._scan_associated_files(str(video))
        names = {f["filename"] for f in found}
        assert names == {"Dune.en.srt", "Dune.nfo", "Dune-poster.jpg"}
        # Each carries a size display
        assert all(f["size"] for f in found)

    def test_missing_directory_returns_empty(self):
        svc = RecentlyAddedService()
        assert svc._scan_associated_files("/nope/does/not/exist/Movie.mkv") == []

    def test_enrich_only_scans_cache_resident_items(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        svc._scan_associated_files = lambda p: [{"filename": "Movie.en.srt", "size": "1 KB"}]
        rows = _enrich(svc, [_item()])
        assert rows[0].location == "cache"
        assert rows[0].associated_files == [{"filename": "Movie.en.srt", "size": "1 KB"}]

    def test_enrich_skips_scan_for_array_items(self):
        svc = _service(on_disk={"/mnt/user0/movies/Movie.mkv"})
        svc._scan_associated_files = lambda p: [{"filename": "should-not-appear", "size": "1 KB"}]
        rows = _enrich(svc, [_item()])
        assert rows[0].location == "array"
        assert rows[0].associated_files == []


class TestGetRecentlyAddedEndToEnd:
    """Exercise the real get_recently_added() path (config load → connect →
    fetch → enrich), which the mocked-service route tests don't cover. This is
    the regression guard for the MultiPathModifier import (a wrong class name
    here surfaces as a 500 in the browser)."""

    def _settings(self, tmp_path):
        data = {
            "PLEX_URL": "http://x", "PLEX_TOKEN": "t", "number_episodes": 5,
            "valid_sections": [1], "days_to_monitor": 7, "users_toggle": True,
            "watchlist_toggle": True, "watchlist_episodes": 3, "watched_move": True,
            "cache_dir": "/mnt/cache/", "max_concurrent_moves_array": 2,
            "max_concurrent_moves_cache": 2, "prefetch_minimum_minutes": 0,
            "path_mappings": [{
                "name": "Movies", "plex_path": "/data/", "real_path": "/mnt/user/",
                "cache_path": "/mnt/cache/", "enabled": True, "cacheable": True,
                "section_id": 1,
            }],
        }
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_enriches_real_items_without_error(self, tmp_path):
        from core.plex_api import RecentlyAddedItem

        items = [
            RecentlyAddedItem(file_path="/data/movies/Dune.mkv", rating_key="101",
                              title="Dune", media_type="movie",
                              added_at=datetime.now() - timedelta(hours=2),
                              library_title="Movies", library_section_id="1", size=28000),
            RecentlyAddedItem(file_path="/data/tv/Show/S01E01.mkv", rating_key="201",
                              title="Ep1", media_type="episode", added_at=None,
                              library_title="TV", library_section_id="1", size=5000,
                              episode_info={"show": "Show", "season": 1, "episode": 1}),
        ]
        svc = RecentlyAddedService()
        svc.settings_file = str(self._settings(tmp_path))

        fake_pm = MagicMock()
        fake_pm.connect.return_value = None
        fake_pm.get_recently_added_media.return_value = items

        with patch("core.plex_api.PlexManager", return_value=fake_pm):
            res = svc.get_recently_added(days=7, max_items=100)

        assert res["available"] is True
        assert res["error"] is None
        assert len(res["rows"]) == 2
        # The episode with added_at=None must still enrich (no crash on None age).
        ep = next(r for r in res["rows"] if r.media_type == "episode")
        assert ep.added_display is None


class TestSummary:
    def test_summary_counts(self):
        svc = _service(on_disk={
            "/mnt/cache/movies/A.mkv",
            "/mnt/cache/movies/B.mkv",
            "/mnt/user0/movies/C.mkv",
        })
        rows = _enrich(svc, [
            _item(rating_key="1", title="A", plex_path="/data/movies/A.mkv"),          # cache, not pinned
            _item(rating_key="2", title="B", plex_path="/data/movies/B.mkv"),          # cache, pinned
            _item(rating_key="3", title="C", plex_path="/data/movies/C.mkv"),          # array
        ], pinned=["2"])

        summary = svc._summary(rows)
        assert summary["total"] == 3
        assert summary["on_cache"] == 2            # A + B physically on cache
        assert summary["on_cache_not_pinned"] == 1  # only A is actionable
        assert summary["on_array"] == 1            # C
