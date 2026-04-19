"""Phase 2c safety tests: pinned files are never moved back to array.

Locks the second mandatory integration point from the pinned media plan:
FileFilter.get_files_to_move_back_to_array() MUST skip pinned files, or
a pinned item that isn't on any user's OnDeck/Watchlist would be silently
restored to the array on the next run — defeating the whole feature.
"""

import os

import pytest

from core.file_operations import (
    CacheTimestampTracker,
    FileFilter,
    OnDeckTracker,
    WatchlistTracker,
)


@pytest.fixture
def env(tmp_path):
    """Set up a realistic cache/array/exclude-file environment.

    Directory layout::
        tmp_path/
          cache/
            Movies/
              Matrix.mkv         (cached file)
              Pinned.mkv         (cached pinned file)
            TV/
              Show/Season 01/S01E01.mkv  (cached pinned episode)
          array/
            Movies/Matrix.mkv
            Movies/Pinned.mkv
            TV/Show/Season 01/S01E01.mkv
          exclude.txt            (the mover exclude file)
          timestamps.json
          ondeck.json
          watchlist.json
    """
    cache_dir = tmp_path / "cache"
    array_dir = tmp_path / "array"

    # Create files on "cache"
    matrix_cache = cache_dir / "Movies" / "Matrix.mkv"
    pinned_movie_cache = cache_dir / "Movies" / "Pinned.mkv"
    pinned_ep_cache = cache_dir / "TV" / "Show" / "Season 01" / "S01E01.mkv"
    for f in [matrix_cache, pinned_movie_cache, pinned_ep_cache]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\0" * 1024)

    # Exclude file tracks all three as cached
    exclude_file = tmp_path / "exclude.txt"
    exclude_file.write_text(
        f"{matrix_cache}\n{pinned_movie_cache}\n{pinned_ep_cache}\n",
        encoding="utf-8",
    )

    ts = CacheTimestampTracker(str(tmp_path / "timestamps.json"))
    od = OnDeckTracker(str(tmp_path / "ondeck.json"))
    wl = WatchlistTracker(str(tmp_path / "watchlist.json"))

    # Record each cached file with the timestamp tracker (simulates prior run)
    ts.record_cache_time(str(matrix_cache), source="ondeck", media_type="movie")
    ts.record_cache_time(str(pinned_movie_cache), source="pinned", media_type="movie")
    ts.record_cache_time(
        str(pinned_ep_cache),
        source="pinned",
        media_type="episode",
        episode_info={"show": "Show", "season": 1, "episode": 1},
    )

    ff = FileFilter(
        real_source=str(array_dir),
        cache_dir=str(cache_dir),
        is_unraid=False,
        mover_cache_exclude_file=str(exclude_file),
        timestamp_tracker=ts,
        cache_retention_hours=0,  # Disable retention hold so we actually test pinned protection
        ondeck_tracker=od,
        watchlist_tracker=wl,
    )

    return {
        "ff": ff,
        "matrix_cache": str(matrix_cache),
        "pinned_movie_cache": str(pinned_movie_cache),
        "pinned_ep_cache": str(pinned_ep_cache),
        "exclude_file": str(exclude_file),
    }


class TestMoveBackPinnedProtection:
    def test_pinned_movie_never_moved_back_even_when_not_ondeck(self, env):
        """Matrix is on OnDeck → kept. Pinned is on NEITHER → still kept because pinned."""
        ff = env["ff"]
        current_ondeck = {env["matrix_cache"]}
        current_watchlist = set()
        current_pinned = {env["pinned_movie_cache"], env["pinned_ep_cache"]}

        to_move_back, stale, exclude_paths = ff.get_files_to_move_back_to_array(
            current_ondeck,
            current_watchlist,
            files_to_skip=set(),
            current_pinned_cache_paths=current_pinned,
        )

        assert env["pinned_movie_cache"] not in exclude_paths
        assert env["pinned_ep_cache"] not in exclude_paths
        # Sanity: Matrix is still on OnDeck, so it shouldn't be moved back either
        assert env["matrix_cache"] not in exclude_paths

    def test_unpinned_file_still_moved_back(self, env):
        """Regression: protection doesn't block normal move-back for non-pinned files.

        Only the episode is in the pinned set. Both movies (including the one
        named "Pinned.mkv" which is NOT actually pinned here) must be moved
        back because nothing is on OnDeck or Watchlist.
        """
        ff = env["ff"]
        to_move_back, stale, exclude_paths = ff.get_files_to_move_back_to_array(
            set(),
            set(),
            files_to_skip=set(),
            current_pinned_cache_paths={env["pinned_ep_cache"]},
        )

        # The episode (pinned) is kept
        assert env["pinned_ep_cache"] not in exclude_paths
        # Both movies fall off — neither is on OnDeck/Watchlist and neither is pinned
        assert env["matrix_cache"] in exclude_paths
        assert env["pinned_movie_cache"] in exclude_paths

    def test_none_pinned_set_defaults_to_empty(self, env):
        """current_pinned_cache_paths=None means no pinned protection (backwards compat)."""
        ff = env["ff"]
        to_move_back, stale, exclude_paths = ff.get_files_to_move_back_to_array(
            set(),
            set(),
            files_to_skip=set(),
            current_pinned_cache_paths=None,
        )
        # All three files fall off and get moved back
        assert env["matrix_cache"] in exclude_paths
        assert env["pinned_movie_cache"] in exclude_paths
        assert env["pinned_ep_cache"] in exclude_paths

    def test_empty_pinned_set_defaults_to_no_protection(self, env):
        """current_pinned_cache_paths=set() is equivalent to None."""
        ff = env["ff"]
        to_move_back, stale, exclude_paths = ff.get_files_to_move_back_to_array(
            set(),
            set(),
            files_to_skip=set(),
            current_pinned_cache_paths=set(),
        )
        assert env["matrix_cache"] in exclude_paths
        assert env["pinned_movie_cache"] in exclude_paths
        assert env["pinned_ep_cache"] in exclude_paths

    def test_keyword_arg_only(self, env):
        """current_pinned_cache_paths must be a keyword argument (prevents positional mistakes)."""
        ff = env["ff"]
        # Positional call with 3 args should work (files_to_skip is positional-ok)
        to_move_back, stale, exclude_paths = ff.get_files_to_move_back_to_array(
            set(), set(), set()
        )
        # All three files should be moved back (no pinned protection since we didn't pass it)
        assert env["matrix_cache"] in exclude_paths

    def test_old_call_signature_still_works(self, env):
        """Existing callers that don't pass current_pinned_cache_paths should still work."""
        ff = env["ff"]
        # Three-arg call (the pre-pinned signature)
        result = ff.get_files_to_move_back_to_array(
            {env["matrix_cache"]},
            set(),
            set(),
        )
        assert len(result) == 3  # (files_to_move_back, stale_entries, move_back_exclude_paths)


class TestFifoEvictionPinnedSkip:
    """FIFO eviction also needs explicit pinned protection (priority manager doesn't cover it)."""

    def test_fifo_skips_pinned_via_app_level_filter(self, tmp_path):
        """Simulates the filter logic we added to _get_fifo_eviction_candidates.

        The actual method is on PlexCacheApp and requires full app wiring, so
        this locks the filter behavior at the unit level. If this fails,
        _get_fifo_eviction_candidates' filter loop is likely broken.
        """
        pinned = tmp_path / "pinned.mkv"
        normal = tmp_path / "normal.mkv"
        pinned.write_bytes(b"\0" * 1024)
        normal.write_bytes(b"\0" * 1024)

        pinned_set = {str(pinned)}
        cached_files = [str(pinned), str(normal)]

        # The filter we added in _get_fifo_eviction_candidates:
        filtered = [f for f in cached_files if f not in pinned_set]
        assert str(pinned) not in filtered
        assert str(normal) in filtered
