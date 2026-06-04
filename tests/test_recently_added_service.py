"""Tests for RecentlyAddedService enrichment + summary logic.

``_enrich`` is pure given its inputs (raw items, a path converter, the tracker
key sets, and the file-existence probe), so these tests exercise it directly
without a live Plex server or real filesystem.
"""

import os
import sys
from datetime import datetime, timedelta
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

    def test_different_seasons_are_separate_groups(self):
        rows = [
            _make_row("1", "S1E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
            _make_row("2", "S1E2", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
            _make_row("3", "S2E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 2, "episode": 1}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        # Season 1 (2 eps) groups; Season 2 (1 ep) stays a row
        kinds = sorted(d["kind"] for d in display)
        assert kinds == ["row", "show"]


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
