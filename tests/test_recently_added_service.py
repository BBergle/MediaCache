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


def _enrich(svc, items, pinned=None, ondeck=None, watchlist=None, timestamps=None,
            pin_path_map=..., exclude=None, released=None, watched_move=True):
    # Default the pin map to "resolved, nothing pinned" so existing tests keep
    # exercising the normal path rather than the indeterminate one.
    if pin_path_map is ...:
        pin_path_map = {}
    return svc._enrich(
        items,
        FakePathModifier(),
        pinned_keys=set(pinned or []),
        ondeck_keys=set(ondeck or []),
        watchlist_keys=set(watchlist or []),
        timestamps_keys=set(timestamps or []),
        pin_path_map=pin_path_map,
        exclude_paths=set(exclude) if exclude is not None else None,
        released_paths=set(released) if released is not None else None,
        watched_move=watched_move,
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
              is_pinned=False, episode_info=None, show_rating_key=""):
    return RecentlyAddedRow(
        rating_key=rating_key, title=title, media_type=media_type,
        library_title=library_title, file_path=f"/data/{title}.mkv",
        size=size, size_display="1.00 KB", added_at=datetime.now(),
        added_display="now", location=location, state=state, is_pinned=is_pinned,
        pin_type="episode" if media_type == "episode" else "movie",
        episode_info=episode_info,
        show_rating_key=show_rating_key,
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

    def test_local_extras_are_not_associated_files(self, tmp_path):
        """Plex stores extras beside the feature. Each is its own media item."""
        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Dune.mkv"
        video.write_bytes(b"x")
        (d / "Dune-trailer.mkv").write_bytes(b"x")
        (d / "Dune-behindthescenes.mkv").write_bytes(b"x")
        (d / "Dune.en.srt").write_bytes(b"sub")
        (d / "Dune.nfo").write_bytes(b"nfo")

        svc = RecentlyAddedService()
        names = {f["filename"] for f in svc._scan_associated_files(str(video))}
        # Exact set, so an over-broad filter fails here too.
        assert names == {"Dune.en.srt", "Dune.nfo"}

    def test_plexcached_backups_are_not_associated_files(self, tmp_path):
        """splitext() leaves the real extension behind, so the naive stem test
        accepted these: splitext("Dune.mkv.plexcached")[0] is "Dune.mkv", which
        startswith("Dune."). Both the video and the subtitle backup slipped
        through. Do not simplify the .plexcached clause away."""
        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Dune.mkv"
        video.write_bytes(b"x")
        (d / "Dune.en.srt").write_bytes(b"sub")
        (d / "Dune.mkv.plexcached").write_bytes(b"x")
        (d / "Dune.en.srt.plexcached").write_bytes(b"x")

        assert os.path.splitext("Dune.mkv.plexcached")[0] == "Dune.mkv"
        svc = RecentlyAddedService()
        names = {f["filename"] for f in svc._scan_associated_files(str(video))}
        assert names == {"Dune.en.srt"}

    def test_exotic_sidecars_survive_the_video_filter(self, tmp_path):
        """Guard against the exclusion being too broad. VIDEO_EXTENSIONS and
        SUBTITLE_EXTENSIONS are disjoint and must stay that way."""
        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Dune.mkv"
        video.write_bytes(b"x")
        keep = ["Dune.idx", "Dune.sub", "Dune.sup", "Dune.en.ass",
                "Dune.bif", "Dune.edl", "Dune-clearlogo.png"]
        for n in keep:
            (d / n).write_bytes(b"x")

        svc = RecentlyAddedService()
        names = {f["filename"] for f in svc._scan_associated_files(str(video))}
        assert names == set(keep)

    def test_space_separated_sidecar_is_claimed(self, tmp_path):
        """The shared stem rule accepts any non-alphanumeric boundary, so the
        common "<stem> poster.jpg" naming is picked up. The hand-rolled test
        this replaced only allowed "." and "-" and dropped it."""
        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Dune (2021).mkv"
        video.write_bytes(b"x")
        (d / "Dune (2021) poster.jpg").write_bytes(b"img")

        svc = RecentlyAddedService()
        names = {f["filename"] for f in svc._scan_associated_files(str(video))}
        assert names == {"Dune (2021) poster.jpg"}

    def test_prefix_sharing_movies_do_not_steal_sidecars(self, tmp_path):
        """Boundary-awareness comes from the shared helper: "Movie 10.en.srt"
        must not be claimed by the stem "Movie 1"."""
        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Movie 1.mkv"
        video.write_bytes(b"x")
        (d / "Movie 1.en.srt").write_bytes(b"sub")
        (d / "Movie 10.mkv").write_bytes(b"x")
        (d / "Movie 10.en.srt").write_bytes(b"sub")

        svc = RecentlyAddedService()
        names = {f["filename"] for f in svc._scan_associated_files(str(video))}
        assert names == {"Movie 1.en.srt"}

    def test_is_a_subset_of_what_the_core_pipeline_attaches(self, tmp_path):
        """Cross-module invariant: this display scan must never list a file the
        caching pipeline would not treat as a sidecar. Subset, not equality —
        the scan additionally requires the stem prefix, so it legitimately
        drops directory-level artwork that _find_sibling_files returns."""
        from core.file_operations import SiblingFileFinder

        d = tmp_path / "Movies"
        d.mkdir()
        video = d / "Dune.mkv"
        video.write_bytes(b"x")
        for n in ["Dune-trailer.mkv", "Dune.en.srt", "Dune.nfo",
                  "Dune-poster.jpg", "Dune.mkv.plexcached", "poster.jpg"]:
            (d / n).write_bytes(b"x")

        svc = RecentlyAddedService()
        scanned = {f["filename"] for f in svc._scan_associated_files(str(video))}
        pipeline = {
            os.path.basename(p)
            for p in SiblingFileFinder()._find_sibling_files(str(d), str(video))
        }
        assert scanned <= pipeline, f"listed non-sidecars: {scanned - pipeline}"

    def test_enrich_only_scans_cache_resident_items(self):
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        svc._scan_associated_files = lambda p, *_: [{"filename": "Movie.en.srt", "size": "1 KB"}]
        rows = _enrich(svc, [_item()])
        assert rows[0].location == "cache"
        assert rows[0].associated_files == [{"filename": "Movie.en.srt", "size": "1 KB"}]

    def test_enrich_skips_scan_for_array_items(self):
        svc = _service(on_disk={"/mnt/user0/movies/Movie.mkv"})
        svc._scan_associated_files = lambda p, *_: [{"filename": "should-not-appear", "size": "1 KB"}]
        rows = _enrich(svc, [_item()])
        assert rows[0].location == "array"
        assert rows[0].associated_files == []


class TestScanDirectoryMemo:
    """N rows in one directory must scan it once, without changing any result."""

    def _dir(self, tmp_path, videos, sidecars):
        d = tmp_path / "Movies"
        d.mkdir()
        for n in list(videos) + list(sidecars):
            (d / n).write_bytes(b"x")
        return d

    def test_one_scandir_per_directory_not_per_row(self, tmp_path, monkeypatch):
        d = self._dir(tmp_path, ["A.mkv", "B.mkv", "C.mkv"],
                      ["A.en.srt", "B.en.srt", "C.en.srt"])
        real = os.scandir
        calls = []

        def counting(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(os, "scandir", counting)
        svc = RecentlyAddedService()
        cache = {}
        for name in ("A.mkv", "B.mkv", "C.mkv"):
            svc._scan_associated_files(str(d / name), cache)
        assert len(calls) == 1, f"scanned {len(calls)} times, expected 1"

    def test_without_the_memo_each_row_scans(self, tmp_path, monkeypatch):
        """Pins the behaviour the memo is optimising, so the test above cannot
        pass for the wrong reason."""
        d = self._dir(tmp_path, ["A.mkv", "B.mkv"], ["A.en.srt", "B.en.srt"])
        real = os.scandir
        calls = []
        monkeypatch.setattr(os, "scandir", lambda p: (calls.append(p), real(p))[1])
        svc = RecentlyAddedService()
        for name in ("A.mkv", "B.mkv"):
            svc._scan_associated_files(str(d / name))
        assert len(calls) == 2

    def test_memo_caches_the_listing_not_the_filtered_result(self, tmp_path):
        """The trap: two videos in one directory have different stems, so a
        memo keyed only on the directory must re-filter per row. Caching the
        filtered result would give every row the first row's sidecars."""
        d = self._dir(tmp_path, ["A.mkv", "B.mkv"],
                      ["A.en.srt", "A.nfo", "B.en.srt"])
        svc = RecentlyAddedService()
        cache = {}
        a = {f["filename"] for f in svc._scan_associated_files(str(d / "A.mkv"), cache)}
        b = {f["filename"] for f in svc._scan_associated_files(str(d / "B.mkv"), cache)}
        assert a == {"A.en.srt", "A.nfo"}
        assert b == {"B.en.srt"}

    def test_memoised_output_matches_unmemoised(self, tmp_path):
        d = self._dir(tmp_path, ["A.mkv", "B.mkv"],
                      ["A.en.srt", "A-poster.jpg", "B.en.srt", "stray.txt"])
        svc = RecentlyAddedService()
        cache = {}
        for name in ("A.mkv", "B.mkv"):
            assert (svc._scan_associated_files(str(d / name), cache)
                    == svc._scan_associated_files(str(d / name)))

    def test_sidecar_whose_stat_fails_is_still_listed(self, tmp_path):
        """A sidecar the mover is moving out from under the scan is real; it
        just has no size to show. An implementation that skipped on OSError
        would silently drop it."""
        d = self._dir(tmp_path, ["A.mkv"], ["A.en.srt"])

        class Boom:
            name = "A.nfo"

            def is_file(self):
                return True

            def stat(self):
                raise OSError("EACCES")

        svc = RecentlyAddedService()
        cache = {str(d): list(os.scandir(str(d))) + [Boom()]}
        found = svc._scan_associated_files(str(d / "A.mkv"), cache)
        by_name = {f["filename"]: f["size"] for f in found}
        assert by_name["A.nfo"] == ""
        assert by_name["A.en.srt"]

    def test_missing_directory_is_cached_as_empty(self, tmp_path):
        svc = RecentlyAddedService()
        cache = {}
        missing = str(tmp_path / "nope" / "Movie.mkv")
        assert svc._scan_associated_files(missing, cache) == []
        assert svc._scan_associated_files(missing, cache) == []
        assert cache[os.path.dirname(missing)] == []

    def test_enrich_passes_a_fresh_cache_per_call(self):
        """A cache that outlived the call would go stale — the service is a
        module-level singleton shared across requests."""
        svc = _service(on_disk={"/mnt/cache/movies/Movie.mkv"})
        seen = []
        svc._scan_associated_files = lambda p, c=None: (seen.append(id(c)), [])[1]
        _enrich(svc, [_item()])
        _enrich(svc, [_item()])
        assert len(seen) == 2 and seen[0] != seen[1]


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


class TestPinResolution:
    """Pin state must come from the resolved pin map, not raw key membership.

    `pinned_media.json` holds only the scope's key, so an episode of a pinned
    SHOW never appears there. Keying on the episode's own rating_key made every
    such episode render "Not pinned" with a live Pin button, while /cache showed
    the identical files as Pinned.
    """

    def test_show_pin_covers_its_episodes(self):
        item = _item("9001", "Ep", "episode", "/data/tv/Show/S01E01.mkv",
                     episode_info={"show": "Show", "season": 1, "episode": 1})
        svc = _service(["/mnt/cache/tv/Show/S01E01.mkv"])
        # The show pin (rk 5000) resolves down to this episode's Plex path.
        rows = _enrich(svc, [item],
                       pin_path_map={"/data/tv/Show/S01E01.mkv": ("5000", "show", "Show")},
                       exclude=["/mnt/cache/tv/Show/S01E01.mkv"])

        assert rows[0].is_pinned is True
        assert rows[0].outcome == "held"
        assert rows[0].pin_scope == "show"
        assert rows[0].pin_holder_key == "5000"
        assert rows[0].pin_holder_title == "Show"

    def test_pinned_file_missing_from_the_exclude_list_is_flagged(self):
        # Pinning writes no exclude line, so a file pinned while already on
        # cache is exempt from PlexCache eviction while the Unraid mover can
        # still relocate it. Claiming plain "held" would promise mover
        # protection the pin has not bought.
        item = _item("42", "Dune", "movie", "/data/movies/Dune.mkv")
        svc = _service(["/mnt/cache/movies/Dune.mkv"])
        rows = _enrich(svc, [item],
                       pin_path_map={"/data/movies/Dune.mkv": ("42", "movie", "Dune")},
                       exclude=[])

        assert rows[0].is_pinned is True
        assert rows[0].is_mover_protected is False
        assert rows[0].outcome == "held_mover_gap"

    def test_direct_movie_pin_reports_itself_as_the_holder(self):
        item = _item("42", "Dune", "movie", "/data/movies/Dune.mkv")
        svc = _service(["/mnt/cache/movies/Dune.mkv"])
        rows = _enrich(svc, [item],
                       pin_path_map={"/data/movies/Dune.mkv": ("42", "movie", "Dune")})

        assert rows[0].pin_scope == "movie"
        assert rows[0].pin_holder_key == "42"

    def test_pinned_but_not_on_cache_is_arriving_not_held(self):
        item = _item("42", "Dune", "movie", "/data/movies/Dune.mkv")
        svc = _service(["/mnt/user0/movies/Dune.mkv"])  # array only
        rows = _enrich(svc, [item],
                       pin_path_map={"/data/movies/Dune.mkv": ("42", "movie", "Dune")})

        assert rows[0].is_pinned is True
        assert rows[0].outcome == "arriving"

    def test_unresolvable_pins_render_indeterminate_not_unpinned(self):
        # Rendering "not pinned" here would put a live Pin button on a pinned
        # row; one click unpins it and starts a background eviction.
        item = _item("42", "Dune", "movie", "/data/movies/Dune.mkv")
        svc = _service(["/mnt/cache/movies/Dune.mkv"])
        rows = _enrich(svc, [item], pin_path_map=None)

        assert rows[0].outcome == "pin_unknown"

    def test_unresolvable_pins_still_honour_a_direct_key_match(self):
        # Degraded but true: a direct item pin is knowable from the tracker
        # alone. Only inherited show/season coverage needs Plex.
        item = _item("42", "Dune", "movie", "/data/movies/Dune.mkv")
        svc = _service(["/mnt/cache/movies/Dune.mkv"])
        rows = _enrich(svc, [item], pinned=["42"], pin_path_map=None)

        assert rows[0].is_pinned is True
        assert rows[0].outcome == "pin_unknown"  # still indeterminate overall

    def test_empty_map_means_definitively_nothing_pinned(self):
        item = _item("42", "Dune", "movie", "/data/movies/Dune.mkv")
        svc = _service(["/mnt/cache/movies/Dune.mkv"])
        rows = _enrich(svc, [item], pin_path_map={},
                       exclude=["/mnt/cache/movies/Dune.mkv"])

        assert rows[0].is_pinned is False
        assert rows[0].outcome == "moves_back"


class TestMoverOutcomes:
    def test_cached_file_absent_from_exclude_list_is_the_movers_call(self):
        item = _item("1", "Fresh", "movie", "/data/movies/Fresh.mkv")
        svc = _service(["/mnt/cache/movies/Fresh.mkv"])
        rows = _enrich(svc, [item], exclude=[])

        assert rows[0].is_mover_protected is False
        assert rows[0].outcome == "mover_decides"

    def test_released_file_is_flagged(self):
        item = _item("1", "Rel", "movie", "/data/movies/Rel.mkv")
        svc = _service(["/mnt/cache/movies/Rel.mkv"])
        rows = _enrich(svc, [item], exclude=[], released=["/mnt/cache/movies/Rel.mkv"])

        assert rows[0].is_released is True
        assert rows[0].outcome == "mover_decides"

    def test_watched_move_off_is_reported_on_cached_rows(self):
        item = _item("1", "Held", "movie", "/data/movies/Held.mkv")
        svc = _service(["/mnt/cache/movies/Held.mkv"])
        rows = _enrich(svc, [item], exclude=["/mnt/cache/movies/Held.mkv"],
                       watched_move=False)

        assert rows[0].outcome == "held_by_setting"

    def test_ondeck_cached_file_returns_when_watched(self):
        item = _item("1", "OD", "movie", "/data/movies/OD.mkv")
        svc = _service(["/mnt/cache/movies/OD.mkv"])
        rows = _enrich(svc, [item], ondeck=["/mnt/user/movies/OD.mkv"],
                       exclude=["/mnt/cache/movies/OD.mkv"])

        assert rows[0].outcome == "returns_when_done"


class TestShowIdentityGrouping:
    """Groups key on the show's rating key, not its display title.

    Two distinct shows can share a title — "The Office" (US) and "The Office"
    (UK) — and keying on the title merged them into one group with interleaved
    children and a nonsense season range.
    """

    def test_same_titled_shows_in_one_library_stay_separate(self):
        rows = [
            _make_row("101", "US-E1", "episode", "TV Shows", show_rating_key="10",
                      episode_info={"show": "The Office", "season": 9, "episode": 1}),
            _make_row("102", "US-E2", "episode", "TV Shows", show_rating_key="10",
                      episode_info={"show": "The Office", "season": 9, "episode": 2}),
            _make_row("201", "UK-E5", "episode", "TV Shows", show_rating_key="20",
                      episode_info={"show": "The Office", "season": 2, "episode": 5}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)

        # US groups (2 eps); the lone UK episode stays its own row.
        assert [d["kind"] for d in display] == ["show", "row"]
        assert display[0]["episode_count"] == 2
        # The header shows the title, not the rating key it now groups on.
        assert display[0]["show"] == "The Office"
        # Previously "Seasons 2–9" — the merge's most visible symptom.
        assert display[0]["season_display"] == "Season 9"

    def test_missing_show_rating_key_falls_back_to_the_title(self):
        # A Plex server that omits grandparentRatingKey keeps today's behaviour,
        # so this change can only split merged groups, never merge separate ones.
        rows = [
            _make_row("1", "E1", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
            _make_row("2", "E2", "episode", "TV Shows",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        assert [d["kind"] for d in display] == ["show"]
        assert display[0]["episode_count"] == 2
        assert display[0]["show"] == "Show"

    def test_same_show_key_across_libraries_still_separate(self):
        # library_title remains part of the key.
        rows = [
            _make_row("1", "E1", "episode", "TV Shows", show_rating_key="10",
                      episode_info={"show": "Show", "season": 1, "episode": 1}),
            _make_row("2", "E2", "episode", "Anime", show_rating_key="10",
                      episode_info={"show": "Show", "season": 1, "episode": 2}),
        ]
        display = RecentlyAddedService.group_rows_for_display(rows)
        assert [d["kind"] for d in display] == ["row", "row"]
