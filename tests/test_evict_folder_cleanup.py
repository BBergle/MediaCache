"""CacheService.evict_file() removes folders it empties (issue #196).

Manual eviction from the Cached Files page deleted the file and stopped, so the
movie folder (and the show/season chain for TV) stayed on the cache drive. The
normal move-to-array path had always cleaned up; the evict paths never did.

Exercises the real evict_file() against a temp filesystem — the .plexcached
backup is restored, the cache copy removed, and the emptied folders go with it.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest.py handles the fcntl mock and sys.path setup


def _settings(cache_root, array_root, cleanup=True):
    # Build prefixes with the OS separator so evict_file's startswith() prefix
    # match behaves the same on Windows as on Linux.
    return {
        "path_mappings": [
            {
                "name": "Movies",
                "plex_path": "/plex/movies",
                "real_path": str(array_root / "Movies"),
                "cache_path": str(cache_root / "Movies"),
                "cacheable": True,
                "enabled": True,
            },
            {
                "name": "TV Shows",
                "plex_path": "/plex/tv",
                "real_path": str(array_root / "TV Shows"),
                "cache_path": str(cache_root / "TV Shows"),
                "cacheable": True,
                "enabled": True,
            },
        ],
        "cache_dir": str(cache_root),
        "cleanup_empty_folders": cleanup,
        "cache_eviction_mode": "smart",
    }


def _make_service(tmp_path, settings):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    with patch("web.services.cache_service.SETTINGS_FILE", settings_file), \
         patch("web.services.cache_service.CONFIG_DIR", tmp_path), \
         patch("web.services.cache_service.DATA_DIR", data_dir):
        from web.services.cache_service import CacheService
        svc = CacheService()

    svc.settings_file = settings_file
    svc.exclude_file = tmp_path / "plexcache_cached_files.txt"
    svc.timestamps_file = data_dir / "timestamps.json"
    svc._get_pinned_cache_paths = lambda: set()
    svc._get_pinned_cache_path_map = lambda: {}
    return svc


def _stage(cache_file, array_file):
    """Put a cache copy in place with its .plexcached backup on the array."""
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    os.makedirs(os.path.dirname(array_file), exist_ok=True)
    Path(cache_file).write_bytes(b"\0" * 1024)
    Path(array_file + ".plexcached").write_bytes(b"\0" * 1024)


@pytest.fixture
def roots(tmp_path):
    cache_root = tmp_path / "cache"
    array_root = tmp_path / "array"
    cache_root.mkdir()
    array_root.mkdir()
    return cache_root, array_root


class TestEvictRemovesEmptyFolders:
    def test_movie_folder_removed_after_evict(self, roots):
        cache_root, array_root = roots
        settings = _settings(cache_root, array_root)
        svc = _make_service(cache_root.parent, settings)

        movie_dir = cache_root / "Movies" / "Sonic the Hedgehog 3 (2024)"
        cache_file = str(movie_dir / "Sonic.mkv")
        array_file = str(array_root / "Movies" / "Sonic the Hedgehog 3 (2024)" / "Sonic.mkv")
        _stage(cache_file, array_file)
        svc.exclude_file.write_text(cache_file + "\n", encoding="utf-8")

        result = svc.evict_file(cache_file)

        assert result["success"], result["message"]
        assert not os.path.exists(cache_file)
        assert not movie_dir.exists(), "movie folder left behind on cache (issue #196)"
        assert os.path.exists(array_file), "array copy must survive"

    def test_season_and_show_folders_removed_for_last_episode(self, roots):
        cache_root, array_root = roots
        settings = _settings(cache_root, array_root)
        svc = _make_service(cache_root.parent, settings)

        season_dir = cache_root / "TV Shows" / "Slow Horses" / "Season 01"
        cache_file = str(season_dir / "S01E01.mkv")
        array_file = str(array_root / "TV Shows" / "Slow Horses" / "Season 01" / "S01E01.mkv")
        _stage(cache_file, array_file)
        svc.exclude_file.write_text(cache_file + "\n", encoding="utf-8")

        result = svc.evict_file(cache_file)

        assert result["success"], result["message"]
        assert not season_dir.exists()
        assert not (cache_root / "TV Shows" / "Slow Horses").exists()

    def test_season_folder_kept_when_other_episodes_remain(self, roots):
        cache_root, array_root = roots
        settings = _settings(cache_root, array_root)
        svc = _make_service(cache_root.parent, settings)

        season_dir = cache_root / "TV Shows" / "Slow Horses" / "Season 01"
        cache_file = str(season_dir / "S01E01.mkv")
        array_file = str(array_root / "TV Shows" / "Slow Horses" / "Season 01" / "S01E01.mkv")
        _stage(cache_file, array_file)
        sibling = season_dir / "S01E02.mkv"
        sibling.write_bytes(b"\0" * 1024)
        svc.exclude_file.write_text(cache_file + "\n", encoding="utf-8")

        result = svc.evict_file(cache_file)

        assert result["success"], result["message"]
        assert season_dir.exists()
        assert sibling.exists()

    def test_cache_root_itself_is_never_removed(self, roots):
        cache_root, array_root = roots
        settings = _settings(cache_root, array_root)
        svc = _make_service(cache_root.parent, settings)

        movie_dir = cache_root / "Movies" / "Only (2024)"
        cache_file = str(movie_dir / "Only.mkv")
        array_file = str(array_root / "Movies" / "Only (2024)" / "Only.mkv")
        _stage(cache_file, array_file)
        svc.exclude_file.write_text(cache_file + "\n", encoding="utf-8")

        svc.evict_file(cache_file)

        assert cache_root.exists()

    def test_cleanup_setting_off_leaves_folders(self, roots):
        cache_root, array_root = roots
        settings = _settings(cache_root, array_root, cleanup=False)
        svc = _make_service(cache_root.parent, settings)

        movie_dir = cache_root / "Movies" / "Kept (2024)"
        cache_file = str(movie_dir / "Kept.mkv")
        array_file = str(array_root / "Movies" / "Kept (2024)" / "Kept.mkv")
        _stage(cache_file, array_file)
        svc.exclude_file.write_text(cache_file + "\n", encoding="utf-8")

        result = svc.evict_file(cache_file)

        assert result["success"], result["message"]
        assert not os.path.exists(cache_file)
        assert movie_dir.exists(), "cleanup_empty_folders=false must be honoured"

    def test_bulk_evict_cleans_every_folder(self, roots):
        cache_root, array_root = roots
        settings = _settings(cache_root, array_root)
        svc = _make_service(cache_root.parent, settings)

        paths = []
        for title in ("Alpha (2020)", "Beta (2021)", "Gamma (2022)"):
            cache_file = str(cache_root / "Movies" / title / f"{title}.mkv")
            array_file = str(array_root / "Movies" / title / f"{title}.mkv")
            _stage(cache_file, array_file)
            paths.append(cache_file)
        svc.exclude_file.write_text("\n".join(paths) + "\n", encoding="utf-8")

        result = svc.evict_files(paths)

        assert result["evicted_count"] == 3, result["errors"]
        for title in ("Alpha (2020)", "Beta (2021)", "Gamma (2022)"):
            assert not (cache_root / "Movies" / title).exists()
        # The mapping's own cache_path is the boundary — emptying it must not
        # delete it, or the next caching run has no destination folder.
        assert (cache_root / "Movies").exists()
        assert cache_root.exists()
