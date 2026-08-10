"""Tests for empty-folder cleanup after cache files are removed (issue #196).

Reported upstream: evicting files manually from the Cached Files page left the
movie / show / season folders behind on the cache drive. Only the normal
move-to-array path cleaned up; both eviction paths deleted the file and stopped.

Covers the shared helpers in core.system_utils plus the boundary resolution that
keeps a multi-pool setup (/mnt/cache + /mnt/ssd_cache) from being skipped.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules['fcntl'] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.system_utils import cleanup_empty_parent_folders, resolve_cache_boundary


@pytest.fixture
def cache_root(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    return root


class TestCleanupEmptyParentFolders:
    """Walk up from a removed file, stopping at the boundary or a non-empty dir."""

    def test_removes_single_empty_folder(self, cache_root):
        movie_dir = cache_root / "Movies" / "Sonic the Hedgehog 3 (2024)"
        movie_dir.mkdir(parents=True)
        film = movie_dir / "Sonic.mkv"
        film.write_text("x")
        film.unlink()

        removed = cleanup_empty_parent_folders(str(film), str(cache_root))

        assert removed == 2  # the movie folder and the now-empty Movies folder
        assert not movie_dir.exists()
        assert cache_root.exists()

    def test_climbs_multiple_empty_levels(self, cache_root):
        season = cache_root / "TV Shows" / "Slow Horses" / "Season 01"
        season.mkdir(parents=True)
        ep = season / "S01E01.mkv"
        ep.write_text("x")
        ep.unlink()

        removed = cleanup_empty_parent_folders(str(ep), str(cache_root))

        assert removed == 3
        assert not (cache_root / "TV Shows").exists()

    def test_stops_at_non_empty_folder(self, cache_root):
        season = cache_root / "TV Shows" / "Slow Horses" / "Season 01"
        season.mkdir(parents=True)
        gone = season / "S01E01.mkv"
        gone.write_text("x")
        keep = season / "S01E02.mkv"
        keep.write_text("x")
        gone.unlink()

        removed = cleanup_empty_parent_folders(str(gone), str(cache_root))

        assert removed == 0
        assert season.exists()
        assert keep.exists()

    def test_stops_partway_up_when_sibling_remains(self, cache_root):
        show = cache_root / "TV Shows" / "Slow Horses"
        season1 = show / "Season 01"
        season2 = show / "Season 02"
        season1.mkdir(parents=True)
        season2.mkdir(parents=True)
        (season2 / "S02E01.mkv").write_text("x")
        ep = season1 / "S01E01.mkv"
        ep.write_text("x")
        ep.unlink()

        removed = cleanup_empty_parent_folders(str(ep), str(cache_root))

        assert removed == 1  # Season 01 only — the show folder still has Season 02
        assert not season1.exists()
        assert show.exists()

    def test_never_removes_the_boundary_itself(self, cache_root):
        orphan = cache_root / "Movie.mkv"
        orphan.write_text("x")
        orphan.unlink()

        removed = cleanup_empty_parent_folders(str(orphan), str(cache_root))

        assert removed == 0
        assert cache_root.exists()

    def test_file_outside_boundary_is_a_noop(self, tmp_path):
        outside = tmp_path / "elsewhere" / "Movies" / "Film"
        outside.mkdir(parents=True)
        f = outside / "Film.mkv"
        f.write_text("x")
        f.unlink()

        removed = cleanup_empty_parent_folders(str(f), str(tmp_path / "cache"))

        assert removed == 0
        assert outside.exists()

    def test_sibling_prefix_is_not_treated_as_inside(self, tmp_path):
        """/mnt/cache_downloads must not count as living inside /mnt/cache."""
        boundary = tmp_path / "cache"
        boundary.mkdir()
        lookalike = tmp_path / "cache_downloads" / "Movies"
        lookalike.mkdir(parents=True)
        f = lookalike / "Film.mkv"
        f.write_text("x")
        f.unlink()

        removed = cleanup_empty_parent_folders(str(f), str(boundary))

        assert removed == 0
        assert lookalike.exists()

    def test_missing_arguments_are_safe(self):
        assert cleanup_empty_parent_folders("", "/mnt/cache") == 0
        assert cleanup_empty_parent_folders("/mnt/cache/x.mkv", "") == 0


class TestResolveCacheBoundary:
    """Pick the cache root a file belongs to, across multi-pool setups."""

    MAPPINGS = [
        {"cache_path": "/mnt/cache/data/media", "enabled": True},
        {"cache_path": "/mnt/ssd_cache/data/media", "enabled": True},
        {"cache_path": "/mnt/old_cache/data", "enabled": False},
    ]

    def test_matches_the_owning_mapping(self):
        boundary = resolve_cache_boundary(
            "/mnt/ssd_cache/data/media/movies/Film (2020)/Film.mkv", self.MAPPINGS
        )
        assert boundary == os.path.normpath("/mnt/ssd_cache/data/media")

    def test_secondary_pool_is_not_bounded_by_primary(self):
        """The bug this guards: one global cache_dir skips other pools entirely."""
        boundary = resolve_cache_boundary(
            "/mnt/ssd_cache/data/media/movies/Film.mkv",
            self.MAPPINGS,
            fallback_dir="/mnt/cache",
        )
        assert boundary == os.path.normpath("/mnt/ssd_cache/data/media")

    def test_longest_match_wins(self):
        mappings = [
            {"cache_path": "/mnt/cache", "enabled": True},
            {"cache_path": "/mnt/cache/data/media", "enabled": True},
        ]
        boundary = resolve_cache_boundary("/mnt/cache/data/media/movies/F.mkv", mappings)
        assert boundary == os.path.normpath("/mnt/cache/data/media")

    def test_disabled_mapping_is_ignored(self):
        boundary = resolve_cache_boundary("/mnt/old_cache/data/movies/F.mkv", self.MAPPINGS)
        assert boundary is None

    def test_falls_back_to_cache_dir(self):
        boundary = resolve_cache_boundary(
            "/mnt/cache/Movies/F.mkv", [], fallback_dir="/mnt/cache"
        )
        assert boundary == os.path.normpath("/mnt/cache")

    def test_returns_none_when_nothing_contains_the_file(self):
        boundary = resolve_cache_boundary(
            "/mnt/somewhere_else/F.mkv", self.MAPPINGS, fallback_dir="/mnt/cache"
        )
        assert boundary is None

    def test_no_boundary_for_empty_path(self):
        assert resolve_cache_boundary("", self.MAPPINGS) is None
