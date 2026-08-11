"""Tests for PlexCache's tracked-file inventory (_get_plexcache_tracked_size).

The inventory is the single feed for two things: the plexcache_quota check in
_apply_cache_limit() and the eviction candidate pool in _run_smart_eviction().
It used to read the Unraid mover exclude file alone. It now leads with the
timestamp tracker and unions the exclude file in, matching what
CacheService.get_cached_files_list() already does on the web side.

The distinction matters because the exclude file answers "what should the
Unraid mover keep its hands off" — a narrower question than "what is PlexCache
managing". A released file is deliberately dropped from the exclude file but
must stay reachable by the quota and by eviction.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# core.app pulls in plexapi/requests transitively; they may not be installed.
for _mod_name in [
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex', 'requests',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from conftest import create_test_file  # noqa: E402


# ============================================================================
# Helpers
# ============================================================================

def _build_app(tmp_path, exclude_lines=None, tracker=None, host_to_container=None,
               exclude_readable=True):
    """Build a minimal PlexCacheApp wired for _get_plexcache_tracked_size().

    Args:
        exclude_lines: Lines to write into the exclude file, or None for no
                       exclude file at all.
        tracker: Object exposing get_all_tracked_paths(), or None to simulate
                 an app whose timestamp_tracker was never constructed.
        host_to_container: Optional callable used as the Docker path
                           translation for exclude-file entries.
        exclude_readable: When False the exclude file exists but raises OSError
                          on open, simulating a permissions problem.
    """
    from core.app import PlexCacheApp

    config_manager = MagicMock()

    exclude_path = tmp_path / "plexcache_cached_files.txt"
    if exclude_lines is not None:
        exclude_path.write_text("\n".join(exclude_lines) + "\n", encoding="utf-8")
    config_manager.get_cached_files_file.return_value = exclude_path

    app = object.__new__(PlexCacheApp)
    app.config_manager = config_manager

    if tracker is not None:
        app.timestamp_tracker = tracker

    if host_to_container is not None:
        file_filter = MagicMock()
        file_filter._translate_from_host_path.side_effect = host_to_container
        app.file_filter = file_filter
    else:
        app.file_filter = None

    if not exclude_readable:
        app._force_exclude_error = True

    return app


class _FakeTracker:
    """Stands in for CacheTimestampTracker's inventory surface."""

    def __init__(self, paths):
        self._paths = set(paths)

    def get_all_tracked_paths(self):
        return set(self._paths)


# ============================================================================
# Inventory sources
# ============================================================================

class TestInventorySources:
    """Which stores feed the inventory, and how they combine."""

    def test_exclude_file_only_still_works(self, tmp_path):
        """Backwards compatibility: an install with no tracker entries."""
        f = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=100)
        app = _build_app(tmp_path, exclude_lines=[f], tracker=_FakeTracker([]))

        total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 100

    def test_tracker_only_is_counted(self, tmp_path):
        """A file the tracker knows about counts even with no exclude file.

        This is the released-file case: the exclude line is gone, but PlexCache
        is still managing the file and must be able to evict it.
        """
        f = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=250)
        app = _build_app(tmp_path, exclude_lines=None, tracker=_FakeTracker([f]))

        total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 250

    def test_union_of_both_stores(self, tmp_path):
        """Drifted stores lose nothing — the union keeps both sides."""
        only_tracker = create_test_file(str(tmp_path / "cache" / "A.mkv"), size_bytes=10)
        only_exclude = create_test_file(str(tmp_path / "cache" / "B.mkv"), size_bytes=20)
        app = _build_app(
            tmp_path, exclude_lines=[only_exclude], tracker=_FakeTracker([only_tracker])
        )

        total, files = app._get_plexcache_tracked_size()

        assert sorted(files) == sorted([only_tracker, only_exclude])
        assert total == 30

    def test_missing_tracker_attribute_falls_back_to_exclude(self, tmp_path):
        """_get_plexcache_tracked_size can run before the tracker is built."""
        f = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=64)
        app = _build_app(tmp_path, exclude_lines=[f], tracker=None)

        assert not hasattr(app, "timestamp_tracker")

        total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 64

    def test_both_stores_empty(self, tmp_path):
        app = _build_app(tmp_path, exclude_lines=None, tracker=_FakeTracker([]))

        assert app._get_plexcache_tracked_size() == (0, [])


# ============================================================================
# Deduplication
# ============================================================================

class TestDeduplication:
    """A path present twice must be counted once."""

    def test_same_file_in_both_stores_counted_once(self, tmp_path):
        f = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=500)
        app = _build_app(tmp_path, exclude_lines=[f], tracker=_FakeTracker([f]))

        total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 500

    def test_duplicate_exclude_lines_counted_once(self, tmp_path):
        """MaintenanceService.add_to_exclude appends without a dedup check, so
        the exclude file can legitimately hold the same path twice. That must
        not inflate the quota."""
        f = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=500)
        app = _build_app(tmp_path, exclude_lines=[f, f, f], tracker=_FakeTracker([]))

        total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 500


# ============================================================================
# Sidecars
# ============================================================================

class TestSidecars:
    """Associated files are not top-level tracker keys and must not be lost."""

    def test_associated_files_are_included(self, tmp_path, temp_dir):
        """associate_files() deletes a sidecar's standalone entry and folds it
        into the parent's associated_files list. Walking _timestamps.keys()
        alone would drop every sidecar from the inventory."""
        from core.file_operations import CacheTimestampTracker

        video = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=1000)
        subtitle = create_test_file(str(tmp_path / "cache" / "Movie.srt"), size_bytes=7)

        tracker = CacheTimestampTracker(os.path.join(temp_dir, "timestamps.json"))
        tracker.record_cache_time(video, "ondeck")
        tracker.record_cache_time(subtitle, "ondeck")
        tracker.associate_files({video: [subtitle]})

        # Precondition: the sidecar is no longer a top-level key, it lives
        # only inside the parent's associated_files list.
        assert tracker.get_entry(subtitle) is None
        assert subtitle in tracker.get_associated_files(video)

        app = _build_app(tmp_path, exclude_lines=None, tracker=tracker)
        total, files = app._get_plexcache_tracked_size()

        assert sorted(files) == sorted([video, subtitle])
        assert total == 1007


# ============================================================================
# Filesystem reality
# ============================================================================

class TestFilesystemReality:
    """Only files that actually exist on the cache are counted."""

    def test_missing_files_excluded_from_both_size_and_list(self, tmp_path):
        present = create_test_file(str(tmp_path / "cache" / "Here.mkv"), size_bytes=42)
        absent = str(tmp_path / "cache" / "Gone.mkv")

        app = _build_app(
            tmp_path, exclude_lines=[absent], tracker=_FakeTracker([present, absent])
        )

        total, files = app._get_plexcache_tracked_size()

        assert files == [present]
        assert total == 42

    def test_result_is_deterministically_ordered(self, tmp_path):
        """Sorted output keeps eviction logs and tests stable run to run."""
        b = create_test_file(str(tmp_path / "cache" / "B.mkv"), size_bytes=1)
        a = create_test_file(str(tmp_path / "cache" / "A.mkv"), size_bytes=1)
        c = create_test_file(str(tmp_path / "cache" / "C.mkv"), size_bytes=1)

        app = _build_app(tmp_path, exclude_lines=[c, b], tracker=_FakeTracker([a]))

        _, files = app._get_plexcache_tracked_size()

        assert files == sorted([a, b, c])


# ============================================================================
# Docker path translation
# ============================================================================

class TestDockerPathTranslation:
    """The exclude file holds host paths; the tracker holds container paths."""

    def test_exclude_entries_are_translated_tracker_entries_are_not(self, tmp_path):
        container_root = tmp_path / "mnt" / "cache"
        host_prefix = "/mnt/cache_downloads"

        from_exclude = create_test_file(str(container_root / "FromExclude.mkv"), size_bytes=11)
        from_tracker = create_test_file(str(container_root / "FromTracker.mkv"), size_bytes=22)

        host_line = from_exclude.replace(str(container_root), host_prefix)

        def translate(host_path):
            return host_path.replace(host_prefix, str(container_root))

        app = _build_app(
            tmp_path,
            exclude_lines=[host_line],
            tracker=_FakeTracker([from_tracker]),
            host_to_container=translate,
        )

        total, files = app._get_plexcache_tracked_size()

        assert sorted(files) == sorted([from_exclude, from_tracker])
        assert total == 33

    def test_translation_collision_counted_once(self, tmp_path):
        """Host and container forms of one file must collapse to one entry."""
        container_root = tmp_path / "mnt" / "cache"
        host_prefix = "/mnt/cache_downloads"

        f = create_test_file(str(container_root / "Movie.mkv"), size_bytes=800)
        host_line = f.replace(str(container_root), host_prefix)

        def translate(host_path):
            return host_path.replace(host_prefix, str(container_root))

        app = _build_app(
            tmp_path,
            exclude_lines=[host_line],
            tracker=_FakeTracker([f]),
            host_to_container=translate,
        )

        total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 800


# ============================================================================
# Error handling
# ============================================================================

class TestErrorHandling:
    """An unreadable exclude file must not wipe out the tracker's inventory."""

    def test_unreadable_exclude_file_keeps_tracker_entries(self, tmp_path):
        f = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=99)
        app = _build_app(tmp_path, exclude_lines=["whatever"], tracker=_FakeTracker([f]))

        real_open = open

        def exploding_open(path, *args, **kwargs):
            if str(path).endswith("plexcache_cached_files.txt"):
                raise OSError("permission denied")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=exploding_open):
            total, files = app._get_plexcache_tracked_size()

        assert files == [f]
        assert total == 99

    def test_unreadable_exclude_file_with_no_tracker_returns_empty(self, tmp_path):
        app = _build_app(tmp_path, exclude_lines=["whatever"], tracker=_FakeTracker([]))

        real_open = open

        def exploding_open(path, *args, **kwargs):
            if str(path).endswith("plexcache_cached_files.txt"):
                raise OSError("permission denied")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=exploding_open):
            assert app._get_plexcache_tracked_size() == (0, [])
