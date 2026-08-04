"""One-time backfill sweep for pre-fix empty folders (issue #196).

Per-file cleanup only ever sees folders it empties itself, so installs upgrading
from a version where eviction skipped cleanup carry a backlog it can never
reach. `_backfill_empty_folder_cleanup()` sweeps the cache trees once, records a
marker in data/migrations.json, and never runs again.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app import PlexCacheApp
from core.system_utils import sweep_empty_folders


class _Mapping:
    def __init__(self, cache_path, enabled=True, cacheable=True):
        self.cache_path = str(cache_path)
        self.enabled = enabled
        self.cacheable = cacheable


def _make_app(tmp_path, mappings, cleanup=True, excluded=None, dry_run=False):
    """Build a PlexCacheApp shell with just the config the backfill reads."""
    app = PlexCacheApp.__new__(PlexCacheApp)
    app.dry_run = dry_run

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    config_manager = MagicMock()
    config_manager.cache.cleanup_empty_folders = cleanup
    config_manager.cache.excluded_folders = excluded or []
    config_manager.paths.path_mappings = mappings
    config_manager.paths.cache_dir = ""
    config_manager.get_data_folder.return_value = data_dir
    app.config_manager = config_manager

    return app, data_dir


def _stage_backlog(cache_root):
    """Empty folders of the kind pre-fix eviction left behind."""
    (cache_root / "Movies" / "Evicted (2020)").mkdir(parents=True)
    (cache_root / "Movies" / "Also Evicted (2021)").mkdir(parents=True)
    (cache_root / "TV Shows" / "Old Show" / "Season 01").mkdir(parents=True)

    kept = cache_root / "Movies" / "Still Cached (2022)"
    kept.mkdir(parents=True)
    (kept / "Film.mkv").write_bytes(b"\0" * 16)
    return kept


@pytest.fixture
def cache_root(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    return root


class TestBackfillSweep:
    def test_clears_backlog_and_keeps_populated_folders(self, tmp_path, cache_root):
        kept = _stage_backlog(cache_root)
        app, _ = _make_app(tmp_path, [_Mapping(cache_root)])

        app._backfill_empty_folder_cleanup()

        assert not (cache_root / "Movies" / "Evicted (2020)").exists()
        assert not (cache_root / "Movies" / "Also Evicted (2021)").exists()
        assert not (cache_root / "TV Shows").exists()
        assert kept.exists()
        assert (kept / "Film.mkv").exists()
        assert cache_root.exists()

    def test_writes_marker_after_running(self, tmp_path, cache_root):
        _stage_backlog(cache_root)
        app, data_dir = _make_app(tmp_path, [_Mapping(cache_root)])

        app._backfill_empty_folder_cleanup()

        marker = data_dir / "migrations.json"
        assert marker.exists()
        recorded = json.loads(marker.read_text(encoding="utf-8"))
        assert recorded["empty_folder_backfill"]

    def test_marker_written_even_when_nothing_removed(self, tmp_path, cache_root):
        app, data_dir = _make_app(tmp_path, [_Mapping(cache_root)])

        app._backfill_empty_folder_cleanup()

        recorded = json.loads((data_dir / "migrations.json").read_text(encoding="utf-8"))
        assert recorded["empty_folder_backfill"]

    def test_does_not_run_twice(self, tmp_path, cache_root):
        app, data_dir = _make_app(tmp_path, [_Mapping(cache_root)])
        (data_dir / "migrations.json").write_text(
            json.dumps({"empty_folder_backfill": "2026-08-01T00:00:00"}, indent=2),
            encoding="utf-8",
        )
        stale = cache_root / "Movies" / "Left Alone (2020)"
        stale.mkdir(parents=True)

        app._backfill_empty_folder_cleanup()

        assert stale.exists(), "already-migrated install must not re-sweep"

    def test_skipped_on_dry_run(self, tmp_path, cache_root):
        _stage_backlog(cache_root)
        app, data_dir = _make_app(tmp_path, [_Mapping(cache_root)], dry_run=True)

        app._backfill_empty_folder_cleanup()

        assert (cache_root / "Movies" / "Evicted (2020)").exists()
        assert not (data_dir / "migrations.json").exists()

    def test_skipped_when_setting_disabled(self, tmp_path, cache_root):
        _stage_backlog(cache_root)
        app, data_dir = _make_app(tmp_path, [_Mapping(cache_root)], cleanup=False)

        app._backfill_empty_folder_cleanup()

        assert (cache_root / "Movies" / "Evicted (2020)").exists()
        assert not (data_dir / "migrations.json").exists()

    def test_disabled_and_non_cacheable_mappings_untouched(self, tmp_path, cache_root):
        other = cache_root.parent / "other_pool"
        (other / "Movies" / "Untouched").mkdir(parents=True)
        _stage_backlog(cache_root)

        app, _ = _make_app(tmp_path, [
            _Mapping(cache_root),
            _Mapping(other, enabled=False),
        ])
        app._backfill_empty_folder_cleanup()

        assert (other / "Movies" / "Untouched").exists()
        assert not (cache_root / "Movies" / "Evicted (2020)").exists()

    def test_sweeps_every_enabled_pool(self, tmp_path, cache_root):
        second = cache_root.parent / "ssd_cache"
        (second / "Movies" / "Evicted (2019)").mkdir(parents=True)
        _stage_backlog(cache_root)

        app, _ = _make_app(tmp_path, [_Mapping(cache_root), _Mapping(second)])
        app._backfill_empty_folder_cleanup()

        assert not (second / "Movies" / "Evicted (2019)").exists()
        assert not (cache_root / "Movies" / "Evicted (2020)").exists()

    def test_logs_summary_line(self, tmp_path, cache_root, caplog):
        import logging
        _stage_backlog(cache_root)
        app, _ = _make_app(tmp_path, [_Mapping(cache_root)])

        with caplog.at_level(logging.INFO):
            app._backfill_empty_folder_cleanup()

        assert any("[CLEANUP] Removed" in rec.message for rec in caplog.records)

    def test_excluded_folder_names_are_respected(self, tmp_path, cache_root):
        (cache_root / "Movies" / "KeepMe").mkdir(parents=True)
        app, _ = _make_app(tmp_path, [_Mapping(cache_root)], excluded=["KeepMe"])

        app._backfill_empty_folder_cleanup()

        assert (cache_root / "Movies" / "KeepMe").exists()


class TestSweepEmptyFolders:
    """Direct coverage for the shared sweep helper."""

    def test_skips_hidden_directories(self, cache_root):
        (cache_root / ".Trash").mkdir()
        (cache_root / "Movies" / "Gone").mkdir(parents=True)

        removed = sweep_empty_folders([str(cache_root)])

        assert (cache_root / ".Trash").exists()
        assert not (cache_root / "Movies" / "Gone").exists()
        assert removed >= 1

    def test_collapses_nested_empty_chain_in_one_pass(self, cache_root):
        (cache_root / "TV" / "Show" / "Season 01").mkdir(parents=True)

        removed = sweep_empty_folders([str(cache_root)])

        assert removed == 3
        assert not (cache_root / "TV").exists()

    def test_never_removes_the_root(self, cache_root):
        sweep_empty_folders([str(cache_root)])
        assert cache_root.exists()

    def test_missing_root_is_skipped(self, tmp_path):
        assert sweep_empty_folders([str(tmp_path / "nope")]) == 0

    def test_empty_input_is_safe(self):
        assert sweep_empty_folders([]) == 0
        assert sweep_empty_folders(None) == 0
