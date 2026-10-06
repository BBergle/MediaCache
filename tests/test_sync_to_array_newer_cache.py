"""
Move to Array (MaintenanceService.sync_to_array) must keep the cache copy when
it differs from what is on the array.

The cache copy is what Sonarr/Radarr, Tdarr or any other tool last wrote, so
when its size differs from the .plexcached backup or the same-name array file,
it is the newer version. The caching run already treats it that way (in-place
upgrade in core/file_operations.py); Move to Array must not restore the old
array version and delete the new one.

Uses real files in tmp_path, run through both the sequential and the parallel
code paths.
"""

import json
import os
from unittest.mock import patch

import pytest


OLD = b"old version from the array" * 10
NEW = b"new version written to cache by another tool" * 10


def _make_service(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps({"path_mappings": []}), encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)

    with patch("web.services.maintenance_service.SETTINGS_FILE", settings_file), \
         patch("web.services.maintenance_service.CONFIG_DIR", tmp_path), \
         patch("web.services.maintenance_service.DATA_DIR", tmp_path / "data"):
        from web.services.maintenance_service import MaintenanceService
        svc = MaintenanceService()

    svc.settings_file = settings_file
    svc.exclude_file = tmp_path / "plexcache_cached_files.txt"
    svc.timestamps_file = tmp_path / "data" / "timestamps.json"

    cache_root = tmp_path / "cache" / "Movies"
    array_root = tmp_path / "user0" / "Movies"
    cache_root.mkdir(parents=True)
    array_root.mkdir(parents=True)
    svc._get_paths = lambda: ([str(cache_root)], [str(array_root)])
    return svc, cache_root, array_root


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


WORKERS = [pytest.param(1, id="sequential"), pytest.param(2, id="parallel")]


@pytest.mark.parametrize("max_workers", WORKERS)
def test_backup_differs_keeps_cache_version(tmp_path, max_workers):
    """A .plexcached backup older than the cache copy must not be restored over it."""
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film (2020)" / "Film.mkv"
    backup = array_root / "Film (2020)" / "Film.mkv.plexcached"
    _write(cache_file, NEW)
    _write(backup, OLD)

    result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    array_file = array_root / "Film (2020)" / "Film.mkv"
    assert result.affected_count == 1, result.errors
    assert array_file.read_bytes() == NEW
    assert not backup.exists()
    assert not cache_file.exists()


@pytest.mark.parametrize("max_workers", WORKERS)
def test_array_copy_differs_keeps_cache_version(tmp_path, max_workers):
    """A same-name array file older than the cache copy must be replaced, not kept."""
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film (2020)" / "Film.mkv"
    array_file = array_root / "Film (2020)" / "Film.mkv"
    _write(cache_file, NEW)
    _write(array_file, OLD)

    result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    assert result.affected_count == 1, result.errors
    assert array_file.read_bytes() == NEW
    assert not cache_file.exists()


@pytest.mark.parametrize("max_workers", WORKERS)
def test_backup_same_size_renames_without_copy(tmp_path, max_workers):
    """Identical backup: the existing fast path (rename back, drop cache) still applies."""
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film (2020)" / "Film.mkv"
    backup = array_root / "Film (2020)" / "Film.mkv.plexcached"
    _write(cache_file, NEW)
    _write(backup, NEW)

    with patch.object(svc, "_copy_with_progress") as mock_copy:
        result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    array_file = array_root / "Film (2020)" / "Film.mkv"
    assert result.affected_count == 1, result.errors
    mock_copy.assert_not_called()
    assert array_file.read_bytes() == NEW
    assert not backup.exists()
    assert not cache_file.exists()


@pytest.mark.parametrize("max_workers", WORKERS)
def test_array_copy_same_size_deletes_cache_without_copy(tmp_path, max_workers):
    """Identical array copy: the existing fast path (drop cache) still applies."""
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film (2020)" / "Film.mkv"
    array_file = array_root / "Film (2020)" / "Film.mkv"
    _write(cache_file, NEW)
    _write(array_file, NEW)

    with patch.object(svc, "_copy_with_progress") as mock_copy:
        result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    assert result.affected_count == 1, result.errors
    mock_copy.assert_not_called()
    assert array_file.read_bytes() == NEW
    assert not cache_file.exists()


# ---------------------------------------------------------------------------
# The other direction: the array copy was changed after the cache copy.
#
# Caching keeps the original modification time on both sides (copystat on the
# cache copy, rename for the backup), so an untouched pair has matching mtimes.
# When a tool rewrites the array side directly, the cache copy is the stale one
# and must not be copied over it.
# ---------------------------------------------------------------------------

STALE_CACHE = b"stale copy left on cache" * 10
PROCESSED_ARRAY = b"processed on the array by another tool" * 10
BASE_TIME = 1_700_000_000


def _set_mtime(path, seconds):
    os.utime(path, (seconds, seconds))


@pytest.mark.parametrize("max_workers", WORKERS)
@pytest.mark.parametrize("array_side", ["same-name", "backup"])
def test_newer_array_copy_is_left_alone(tmp_path, max_workers, array_side):
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film (2020)" / "Film.mkv"
    name = "Film.mkv" if array_side == "same-name" else "Film.mkv.plexcached"
    array_side_file = array_root / "Film (2020)" / name
    _write(cache_file, STALE_CACHE)
    _write(array_side_file, PROCESSED_ARRAY)
    _set_mtime(cache_file, BASE_TIME)
    _set_mtime(array_side_file, BASE_TIME + 3600)

    result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    assert result.affected_count == 0
    assert any("newer than the cache copy" in e for e in result.errors), result.errors
    assert array_side_file.read_bytes() == PROCESSED_ARRAY
    assert cache_file.read_bytes() == STALE_CACHE
    assert not (array_root / "Film (2020)" / "Film.mkv.pc-part").exists()
    if array_side == "backup":
        assert not (array_root / "Film (2020)" / "Film.mkv").exists()


@pytest.mark.parametrize("max_workers", WORKERS)
def test_newer_cache_copy_still_replaces_array(tmp_path, max_workers):
    """Explicit mtimes for the #222 direction: the cache copy was rewritten later."""
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film.mkv"
    array_file = array_root / "Film.mkv"
    _write(cache_file, NEW)
    _write(array_file, OLD)
    _set_mtime(array_file, BASE_TIME)
    _set_mtime(cache_file, BASE_TIME + 3600)

    result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    assert result.affected_count == 1, result.errors
    assert array_file.read_bytes() == NEW
    assert not cache_file.exists()


@pytest.mark.parametrize("max_workers", WORKERS)
@pytest.mark.parametrize("array_offset", [0, 1], ids=["same-mtime", "within-tolerance"])
def test_matching_mtimes_keep_the_cache_version(tmp_path, max_workers, array_offset):
    """A tool that keeps the original mtime gives no signal; the cache copy wins as in #222."""
    svc, cache_root, array_root = _make_service(tmp_path)
    cache_file = cache_root / "Film.mkv"
    backup = array_root / "Film.mkv.plexcached"
    _write(cache_file, NEW)
    _write(backup, OLD)
    _set_mtime(cache_file, BASE_TIME)
    _set_mtime(backup, BASE_TIME + array_offset)

    result = svc.sync_to_array([str(cache_file)], dry_run=False, max_workers=max_workers)

    assert result.affected_count == 1, result.errors
    assert (array_root / "Film.mkv").read_bytes() == NEW
    assert not backup.exists()
    assert not cache_file.exists()
