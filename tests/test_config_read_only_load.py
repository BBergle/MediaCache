"""`ConfigManager.load_config(persist_updates=False)` must not touch the disk.

The Recently Added service loads config on a GET, and the dashboard widget fires
one on every dashboard visit. The default load path ends in `_save_updated_config()`
— a truncating rewrite of plexcache_settings.json — which races any concurrent
reader of that file (the hazard `web/services/settings_service.py` documents and
routes around via `save_json_atomically()`).
"""

import json
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
sys.modules.setdefault('plexapi', MagicMock())
sys.modules.setdefault('plexapi.server', MagicMock())

from core.config import ConfigManager


def _minimal_settings(tmp_path):
    """A settings file complete enough for load_config() to validate."""
    cache_dir = tmp_path / "cache"
    real_dir = tmp_path / "media"
    for d in (cache_dir, real_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "PLEX_URL": "http://localhost:32400",
        "PLEX_TOKEN": "tok",
        "number_episodes": 5,
        "valid_sections": [1],
        "days_to_monitor": 99,
        "users_toggle": False,
        "watchlist_toggle": False,
        "watchlist_episodes": 3,
        "watched_move": True,
        "max_concurrent_moves_array": 2,
        "max_concurrent_moves_cache": 5,
        "cache_dir": str(cache_dir),
        "real_source": str(real_dir),
        "plex_source": "/data/",
        "path_mappings": [{
            "name": "Movies",
            "plex_path": "/data/movies",
            "real_path": str(real_dir),
            "cache_path": str(cache_dir),
            "enabled": True,
        }],
    }


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "plexcache_settings.json"
    path.write_text(json.dumps(_minimal_settings(tmp_path), indent=2), encoding="utf-8")
    return path


class TestReadOnlyLoad:
    def test_read_only_load_does_not_rewrite_the_file(self, settings_file):
        before_bytes = settings_file.read_bytes()
        before_mtime = settings_file.stat().st_mtime_ns

        cfg = ConfigManager(str(settings_file))
        cfg.load_config(persist_updates=False)

        assert settings_file.read_bytes() == before_bytes
        assert settings_file.stat().st_mtime_ns == before_mtime

    def test_read_only_load_still_populates_config(self, settings_file):
        cfg = ConfigManager(str(settings_file))
        cfg.load_config(persist_updates=False)

        assert cfg.plex.plex_url == "http://localhost:32400"
        assert cfg.plex.plex_token == "tok"
        assert cfg.plex.valid_sections == [1]
        assert len(cfg.paths.path_mappings) == 1

    def test_read_only_load_still_normalizes_path_mappings(self, settings_file):
        # The trailing-slash normalization is what MultiPathModifier relies on,
        # so skipping the write must not skip this. Asserted on plex_path, which
        # is POSIX on every platform (real_path here is an OS-native tmp path,
        # which _add_trailing_slashes deliberately leaves alone on Windows).
        cfg = ConfigManager(str(settings_file))
        cfg.load_config(persist_updates=False)

        assert cfg.paths.path_mappings[0].plex_path == "/data/movies/"

    def test_default_load_still_persists(self, settings_file):
        # The CLI path must keep writing back migrations/normalizations.
        cfg = ConfigManager(str(settings_file))
        cfg.load_config()
        assert settings_file.exists()
        reloaded = json.loads(settings_file.read_text(encoding="utf-8"))
        assert reloaded["PLEX_TOKEN"] == "tok"
