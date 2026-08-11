"""Tests for releasing watched files to the Unraid mover instead of relocating them.

A file with no .plexcached backup was never lifted off the array by PlexCache.
Relocating it writes to the array for the first time and spins a disk the user
did not ask to spin — Unraid's own mover reclaims it on the user's schedule once
the exclude line is gone. So PlexCache releases it: drop the exclude line, keep
the tracker entry, move no bytes.

The tracker entry is deliberately kept. The mover owns relocation now, but if
the share is cache:prefer or cache:only the mover will never act, and PlexCache
has to be able to reach the file again. That is what
_reclaim_released_if_constrained() is for. Its trigger is measured harm — the
cache having no room for content we wanted — rather than a pool-usage
threshold, so it needs no constant and scales across any pool size.

Coverage:
- release moves no bytes and drops only the exclude line
- every gate that forces the old relocate behaviour
- the exclude-write failure path leaves tracking intact
- released files stay visible to the quota and to eviction
- reclaim fires only when the cache actually ran out of room
- the audit treats released-and-gone as success, not staleness
"""

import json
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

for _mod_name in [
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex', 'requests',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from conftest import create_test_file  # noqa: E402


CACHE_ROOT = "/mnt/cache/media/Movies"
ARRAY_ROOT = "/mnt/user/media/Movies"


# ============================================================================
# Helpers
# ============================================================================

class _Mapping:
    """Minimal path mapping for cache <-> array translation."""

    def __init__(self, cache_root, array_root):
        self.cache_path = cache_root
        self.real_path = array_root
        self.enabled = True
        self.cacheable = True


class _PathModifier:
    def __init__(self, cache_root, array_root):
        self.cache_root = cache_root
        self.array_root = array_root

    def convert_real_to_cache(self, real_path):
        if real_path.startswith(self.array_root):
            return real_path.replace(self.array_root, self.cache_root, 1), None
        return None, None

    def convert_cache_to_real(self, cache_path):
        if cache_path.startswith(self.cache_root):
            return cache_path.replace(self.cache_root, self.array_root, 1), None
        return None, None


def _build_app(tmp_path, is_unraid=True, dry_run=False, tracker=None,
               cache_root=None, array_root=None):
    """Build a PlexCacheApp wired for the release paths."""
    from core.app import PlexCacheApp

    cache_root = cache_root or str(tmp_path / "mnt" / "cache" / "media" / "Movies")
    array_root = array_root or str(tmp_path / "mnt" / "user" / "media" / "Movies")
    os.makedirs(cache_root, exist_ok=True)
    os.makedirs(array_root, exist_ok=True)

    config_manager = MagicMock()
    config_manager.paths.cache_dir = cache_root
    config_manager.cache.cache_drive_size_bytes = 0
    config_manager.cache.watched_move = True

    app = object.__new__(PlexCacheApp)
    app.config_manager = config_manager
    app.dry_run = dry_run
    app.media_to_array = []
    app.system_detector = MagicMock()
    app.system_detector.is_unraid = is_unraid
    app.file_path_modifier = _PathModifier(cache_root, array_root)
    app.file_filter = MagicMock()
    app.file_filter.remove_files_from_exclude_list.return_value = True
    app.timestamp_tracker = tracker
    app.ondeck_tracker = MagicMock()
    app.watchlist_tracker = MagicMock()
    app.released_count = 0
    app.released_bytes = 0
    app.readopted_count = 0
    app._record_activity = False

    return app, cache_root, array_root


def _real_tracker(temp_dir):
    from core.file_operations import CacheTimestampTracker
    return CacheTimestampTracker(os.path.join(temp_dir, "timestamps.json"))


def _passthrough_array_direct(path):
    """get_array_direct_path stand-in: /mnt/user/ -> /mnt/user0/ under tmp."""
    if "/mnt/user/" in path.replace("\\", "/"):
        return path.replace("mnt/user/", "mnt/user0/").replace("mnt\\user\\", "mnt\\user0\\")
    return path


# ============================================================================
# The partition: which files are eligible for release
# ============================================================================

class TestPartitionReleaseCandidates:

    def test_plain_cache_native_file_is_released(self, tmp_path):
        app, cache_root, array_root = _build_app(tmp_path)
        array_path = os.path.join(array_root, "Movie.mkv")
        create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)

        with patch.object(app, "_share_has_array_side", return_value=True):
            to_release, to_relocate = app._partition_release_candidates([array_path])

        assert to_release == [array_path]
        assert to_relocate == []

    def test_non_unraid_always_relocates(self, tmp_path):
        """Without Unraid there is no mover, so nothing would ever reclaim."""
        app, cache_root, array_root = _build_app(tmp_path, is_unraid=False)
        array_path = os.path.join(array_root, "Movie.mkv")
        create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)

        to_release, to_relocate = app._partition_release_candidates([array_path])

        assert to_release == []
        assert to_relocate == [array_path]

    def test_share_without_array_side_relocates(self, tmp_path):
        """ZFS pool-only share: no array behind it, so release is meaningless."""
        app, cache_root, array_root = _build_app(tmp_path)
        array_path = os.path.join(array_root, "Movie.mkv")
        create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)

        with patch.object(app, "_share_has_array_side", return_value=False):
            to_release, to_relocate = app._partition_release_candidates([array_path])

        assert to_release == []
        assert to_relocate == [array_path]

    def test_renamed_plexcached_backup_relocates(self, tmp_path):
        """A quality upgrade leaves a backup under the OLD filename.

        _move_to_array has to delete that backup. Releasing would walk away and
        orphan it permanently.
        """
        app, cache_root, array_root = _build_app(tmp_path)
        array_path = os.path.join(array_root, "Movie.2020.2160p.mkv")
        cache_path = create_test_file(
            os.path.join(cache_root, "Movie.2020.2160p.mkv"), size_bytes=10
        )

        with patch.object(app, "_share_has_array_side", return_value=True), \
             patch("core.app.get_array_direct_path", side_effect=lambda p: p), \
             patch("core.app.find_matching_plexcached",
                   return_value=os.path.join(array_root, "Movie.2020.1080p.mkv.plexcached")):
            to_release, to_relocate = app._partition_release_candidates([array_path])

        assert to_release == []
        assert to_relocate == [array_path]
        assert os.path.isfile(cache_path)

    def test_hardlinked_file_relocates(self, tmp_path):
        """An actively-seeding file: the mover breaks the link on relocation."""
        app, cache_root, array_root = _build_app(tmp_path)
        array_path = os.path.join(array_root, "Movie.mkv")
        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)

        real_stat = os.stat

        class _MultiLinked:
            """Real stat_result with st_nlink overridden.

            Everything else delegates, because os.path.isfile() also goes
            through os.stat() and reads st_mode on POSIX. A stub carrying only
            st_nlink passes on Windows (Python 3.12+ uses a fast-path
            nt._path_isfile that skips os.stat) and fails on Linux.
            """

            def __init__(self, real):
                self._real = real
                self.st_nlink = 2

            def __getattr__(self, name):
                return getattr(self._real, name)

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            return _MultiLinked(result) if path == cache_path else result

        with patch.object(app, "_share_has_array_side", return_value=True), \
             patch("core.app.find_matching_plexcached", return_value=None), \
             patch("core.app.os.stat", side_effect=fake_stat):
            to_release, to_relocate = app._partition_release_candidates([array_path])

        assert to_release == []
        assert to_relocate == [array_path]

    def test_missing_cache_file_relocates(self, tmp_path):
        """Nothing on cache to release — fall through to the existing path."""
        app, _, array_root = _build_app(tmp_path)
        array_path = os.path.join(array_root, "Gone.mkv")

        with patch.object(app, "_share_has_array_side", return_value=True):
            to_release, to_relocate = app._partition_release_candidates([array_path])

        assert to_release == []
        assert to_relocate == [array_path]

    def test_sidecar_skips_the_upgrade_lookup(self, tmp_path):
        """Non-video sidecars are not upgrades of each other."""
        app, cache_root, array_root = _build_app(tmp_path)
        array_path = os.path.join(array_root, "Movie.srt")
        create_test_file(os.path.join(cache_root, "Movie.srt"), size_bytes=5)

        with patch.object(app, "_share_has_array_side", return_value=True), \
             patch("core.app.find_matching_plexcached") as mock_find:
            to_release, _ = app._partition_release_candidates([array_path])

        assert to_release == [array_path]
        mock_find.assert_not_called()


class TestShareHasArraySide:

    def test_user_path_with_distinct_array_direct(self, tmp_path):
        app, _, _ = _build_app(tmp_path)
        with patch("core.app.get_array_direct_path", side_effect=_passthrough_array_direct):
            assert app._share_has_array_side("/mnt/user/media/Movies/A.mkv") is True

    def test_zfs_pool_only_returns_unchanged(self, tmp_path):
        """get_array_direct_path returns the input for ZFS pool-only shares."""
        app, _, _ = _build_app(tmp_path)
        with patch("core.app.get_array_direct_path", side_effect=lambda p: p):
            assert app._share_has_array_side("/mnt/user/media/Movies/A.mkv") is False

    def test_already_array_direct_path(self, tmp_path):
        """An install configured with /mnt/user0/ real paths still qualifies."""
        app, _, _ = _build_app(tmp_path)
        assert app._share_has_array_side("/mnt/user0/media/Movies/A.mkv") is True

    def test_unrelated_path(self, tmp_path):
        app, _, _ = _build_app(tmp_path)
        assert app._share_has_array_side("/srv/media/A.mkv") is False


# ============================================================================
# The release action
# ============================================================================

class TestReleaseFiles:

    def test_moves_no_bytes(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=1234)
        array_path = os.path.join(array_root, "Movie.mkv")
        tracker.record_cache_time(cache_path, "ondeck")

        released = app._release_files([array_path])

        assert released == 1
        assert os.path.isfile(cache_path)
        assert os.path.getsize(cache_path) == 1234
        assert not os.path.exists(array_path)
        assert not os.path.exists(array_path + ".plexcached")

    def test_drops_exclude_line_and_keeps_tracker_entry(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        array_path = os.path.join(array_root, "Movie.mkv")
        tracker.record_cache_time(cache_path, "ondeck")

        app._release_files([array_path])

        app.file_filter.remove_files_from_exclude_list.assert_called_once_with([cache_path])
        # The entry survives so quota and eviction can still reach the file
        assert tracker.get_entry(cache_path) is not None
        assert tracker.is_released(cache_path) is True

    def test_marks_uncached_in_demand_trackers(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        tracker.record_cache_time(cache_path, "ondeck")

        app._release_files([os.path.join(array_root, "Movie.mkv")])

        app.ondeck_tracker.mark_uncached.assert_called_once_with(cache_path)
        app.watchlist_tracker.mark_uncached.assert_called_once_with(cache_path)

    def test_exclude_write_failure_keeps_everything_tracked(self, tmp_path, temp_dir):
        """If the exclude line cannot be dropped the file is still mover-protected.

        Clearing tracking anyway would leave it protected but unmanaged.
        """
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)
        app.file_filter.remove_files_from_exclude_list.return_value = False

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        tracker.record_cache_time(cache_path, "ondeck")

        released = app._release_files([os.path.join(array_root, "Movie.mkv")])

        assert released == 0
        assert tracker.is_released(cache_path) is False
        app.ondeck_tracker.mark_uncached.assert_not_called()

    def test_dry_run_writes_nothing(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker, dry_run=True)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        tracker.record_cache_time(cache_path, "ondeck")

        released = app._release_files([os.path.join(array_root, "Movie.mkv")])

        assert released == 0
        app.file_filter.remove_files_from_exclude_list.assert_not_called()
        assert tracker.is_released(cache_path) is False

    def test_counts_and_bytes_accumulate(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)

        paths = []
        for name, size in [("A.mkv", 100), ("B.mkv", 250)]:
            create_test_file(os.path.join(cache_root, name), size_bytes=size)
            paths.append(os.path.join(array_root, name))
            tracker.record_cache_time(os.path.join(cache_root, name), "ondeck")

        app._release_files(paths)

        assert app.released_count == 2
        assert app.released_bytes == 350

    def test_empty_input_is_a_noop(self, tmp_path):
        app, _, _ = _build_app(tmp_path)
        assert app._release_files([]) == 0
        app.file_filter.remove_files_from_exclude_list.assert_not_called()

    def test_logs_in_the_web_activity_capture_format(self, tmp_path, temp_dir, caplog):
        """The web OperationRunner builds its activity feed by regex-matching
        "  [Action] name (size)" INFO lines out of the log stream.

        Matched against the runner's own compiled pattern, not a copy of it, so
        the two cannot drift apart without this failing.
        """
        from web.services.operation_runner import ACTION_ENTRY_PATTERN

        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)
        create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=2048)
        tracker.record_cache_time(os.path.join(cache_root, "Movie.mkv"), "ondeck")

        with caplog.at_level("INFO"):
            app._release_files([os.path.join(array_root, "Movie.mkv")])

        matched = [
            m for m in (ACTION_ENTRY_PATTERN.match(line) for line in caplog.messages) if m
        ]

        assert len(matched) == 1, f"no activity line found in {caplog.messages}"
        assert matched[0].group(1) == "Released"
        assert matched[0].group(3) == "2.00 KB"

    def test_released_bytes_are_not_counted_as_restored(self):
        """Released files move no bytes, so they must not inflate the
        restored-bytes counters the way Restored/Moved do."""
        from web.services.operation_runner import ACTION_ENTRY_PATTERN

        m = ACTION_ENTRY_PATTERN.match("  [Released] Movie.mkv (2.00 KB)")
        assert m is not None
        assert m.group(1) not in ("Restored", "Moved", "Cached")


# ============================================================================
# Tracker released-state surface
# ============================================================================

class TestTrackerReleasedState:

    def test_sidecar_inherits_parent_release(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        video = "/mnt/cache/media/Movies/Movie.mkv"
        sub = "/mnt/cache/media/Movies/Movie.srt"

        tracker.record_cache_time(video, "ondeck")
        tracker.record_cache_time(sub, "ondeck")
        tracker.associate_files({video: [sub]})

        tracker.mark_released(video)

        assert tracker.is_released(video) is True
        assert tracker.is_released(sub) is True
        assert sub in tracker.get_released_paths()

    def test_releasing_via_sidecar_stamps_the_parent(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        video = "/mnt/cache/media/Movies/Movie.mkv"
        sub = "/mnt/cache/media/Movies/Movie.srt"

        tracker.record_cache_time(video, "ondeck")
        tracker.record_cache_time(sub, "ondeck")
        tracker.associate_files({video: [sub]})

        assert tracker.mark_released(sub) is True
        assert tracker.is_released(video) is True

    def test_clear_released_round_trip(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        video = "/mnt/cache/media/Movies/Movie.mkv"
        tracker.record_cache_time(video, "ondeck")

        tracker.mark_released(video)
        assert tracker.clear_released(video) is True
        assert tracker.is_released(video) is False
        assert tracker.clear_released(video) is False

    def test_unknown_path_is_not_released(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        assert tracker.is_released("/mnt/cache/nope.mkv") is False
        assert tracker.mark_released("/mnt/cache/nope.mkv") is False

    def test_released_at_persists_to_disk(self, tmp_path, temp_dir):
        from core.file_operations import CacheTimestampTracker

        path = os.path.join(temp_dir, "timestamps.json")
        tracker = CacheTimestampTracker(path)
        video = "/mnt/cache/media/Movies/Movie.mkv"
        tracker.record_cache_time(video, "ondeck")
        tracker.mark_released(video)

        reloaded = CacheTimestampTracker(path)
        assert reloaded.is_released(video) is True
        assert reloaded.get_released_at(video) is not None

    def test_release_does_not_disturb_other_fields(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        video = "/mnt/cache/media/Movies/Movie.mkv"
        tracker.record_cache_time(video, "ondeck", media_type="movie", rating_key="123")

        tracker.mark_released(video)

        entry = tracker.get_entry(video)
        assert entry["source"] == "ondeck"
        assert entry["media_type"] == "movie"
        assert entry["rating_key"] == "123"
        assert "cached_at" in entry


# ============================================================================
# Released files stay reachable by quota and eviction
# ============================================================================

class TestReleasedFilesStayManaged:

    def test_released_file_still_counted_by_inventory(self, tmp_path, temp_dir):
        """The whole reason the tracker entry is kept.

        The exclude line is gone, so an exclude-only inventory would lose the
        file and eviction could never reclaim it.
        """
        from core.app import PlexCacheApp

        tracker = _real_tracker(temp_dir)
        cache_path = create_test_file(str(tmp_path / "cache" / "Movie.mkv"), size_bytes=777)
        tracker.record_cache_time(cache_path, "ondeck")
        tracker.mark_released(cache_path)

        config_manager = MagicMock()
        exclude_path = tmp_path / "plexcache_cached_files.txt"  # never created
        config_manager.get_cached_files_file.return_value = exclude_path

        app = object.__new__(PlexCacheApp)
        app.config_manager = config_manager
        app.timestamp_tracker = tracker
        app.file_filter = None

        total, files = app._get_plexcache_tracked_size()

        assert files == [cache_path]
        assert total == 777


# ============================================================================
# Pressure-based re-adoption
# ============================================================================

class TestReclaimWhenConstrained:
    """The backstop fires on measured harm, not on a pool-usage threshold.

    _apply_cache_limit() reporting that it skipped caching something for space
    reasons is the signal. It scales from a 250GB SSD to a 20TB pool with no
    constant to tune, and inherits whatever cache_limit / min_free_space /
    plexcache_quota the user configured.
    """

    def _prepare(self, tmp_path, temp_dir, constrained):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)
        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        tracker.record_cache_time(cache_path, "ondeck")
        tracker.mark_released(cache_path)
        app._cache_space_constrained = constrained
        app._safe_move_files = MagicMock()
        return app, tracker, cache_path, os.path.join(array_root, "Movie.mkv")

    def test_room_to_spare_leaves_the_file_with_the_mover(self, tmp_path, temp_dir):
        """A Mover Tuning user holds media on cache on purpose. Leave it."""
        app, tracker, cache_path, _ = self._prepare(tmp_path, temp_dir, constrained=False)

        assert app._reclaim_released_if_constrained() == 0
        app._safe_move_files.assert_not_called()
        assert tracker.is_released(cache_path) is True

    def test_no_room_takes_the_file_back(self, tmp_path, temp_dir):
        """cache:prefer / cache:only: the mover never acts, so PlexCache does."""
        app, tracker, cache_path, array_path = self._prepare(tmp_path, temp_dir, constrained=True)

        assert app._reclaim_released_if_constrained() == 1
        app._safe_move_files.assert_called_once_with([array_path], 'array')
        assert tracker.is_released(cache_path) is False
        assert app.readopted_count == 1

    def test_warns_and_explains_why(self, tmp_path, temp_dir, caplog):
        """The log has to explain itself or this becomes a debugging mystery."""
        app, _, _, _ = self._prepare(tmp_path, temp_dir, constrained=True)

        with caplog.at_level("WARNING"):
            app._reclaim_released_if_constrained()

        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "cache:prefer" in warnings[0]
        assert "Moving them to the array now" in warnings[0]

    def test_file_already_gone_is_not_reclaimed(self, tmp_path, temp_dir):
        """The mover did its job, so there is nothing to take back."""
        tracker = _real_tracker(temp_dir)
        app, cache_root, _ = _build_app(tmp_path, tracker=tracker)
        cache_path = os.path.join(cache_root, "Moved.mkv")  # never created
        tracker.record_cache_time(cache_path, "ondeck")
        tracker.mark_released(cache_path)
        app._cache_space_constrained = True
        app._safe_move_files = MagicMock()

        assert app._reclaim_released_if_constrained() == 0
        app._safe_move_files.assert_not_called()

    def test_dry_run_reclaims_nothing(self, tmp_path, temp_dir):
        app, tracker, cache_path, _ = self._prepare(tmp_path, temp_dir, constrained=True)
        app.dry_run = True

        assert app._reclaim_released_if_constrained() == 0
        app._safe_move_files.assert_not_called()
        assert tracker.is_released(cache_path) is True

    def test_no_released_files_is_a_noop(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, _, _ = _build_app(tmp_path, tracker=tracker)
        app._cache_space_constrained = True
        app._safe_move_files = MagicMock()

        assert app._reclaim_released_if_constrained() == 0
        app._safe_move_files.assert_not_called()

    def test_reclaims_every_released_file_at_once(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)
        for name in ("A.mkv", "B.mkv"):
            p = create_test_file(os.path.join(cache_root, name), size_bytes=10)
            tracker.record_cache_time(p, "ondeck")
            tracker.mark_released(p)
        app._cache_space_constrained = True
        app._safe_move_files = MagicMock()

        assert app._reclaim_released_if_constrained() == 2
        moved = app._safe_move_files.call_args.args[0]
        assert sorted(os.path.basename(p) for p in moved) == ["A.mkv", "B.mkv"]


class TestConstraintSignal:
    """_apply_cache_limit() raises the flag the backstop reads."""

    def test_flag_is_not_set_when_everything_fits(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, _, _ = _build_app(tmp_path, tracker=tracker)
        app._cache_space_constrained = False

        assert app._cache_space_constrained is False

    def test_skipping_for_space_raises_the_flag(self, tmp_path, temp_dir, caplog):
        """Driven through the real warning path so the two stay wired together."""
        tracker = _real_tracker(temp_dir)
        app, cache_root, _ = _build_app(tmp_path, tracker=tracker)
        app._cache_space_constrained = False

        # One 100-byte file against a 10-byte limit: cannot fit.
        big = create_test_file(os.path.join(cache_root, "Big.mkv"), size_bytes=100)
        app.config_manager.cache.cache_limit_bytes = 10
        app.config_manager.cache.min_free_space_bytes = 0
        app.config_manager.cache.plexcache_quota_bytes = 0
        app.config_manager.cache.cache_drive_size_bytes = 0
        app.media_info_map = {}
        app.file_filter = MagicMock()

        from core.system_utils import DiskUsage
        with patch("core.app.get_disk_usage",
                   return_value=DiskUsage(total=1000, used=0, free=1000)), \
             patch.object(app, "_get_plexcache_tracked_size", return_value=(0, [])), \
             caplog.at_level("WARNING"):
            app._apply_cache_limit([big], cache_root)

        assert app._cache_space_constrained is True
        assert any("Cache limit reached" in r.message for r in caplog.records)


# ============================================================================
# Audit behaviour
# ============================================================================

class TestAuditTreatsReleasedAsSuccess:

    def _service(self, tmp_path, timestamps):
        from web.services.maintenance_service import MaintenanceService

        ts_path = tmp_path / "timestamps.json"
        ts_path.write_text(json.dumps(timestamps, indent=2), encoding="utf-8")

        service = object.__new__(MaintenanceService)
        service.timestamps_file = ts_path
        return service

    def test_released_entries_are_collected(self, tmp_path):
        service = self._service(tmp_path, {
            "/mnt/cache/A.mkv": {"cached_at": "x", "source": "ondeck", "released_at": "y"},
            "/mnt/cache/B.mkv": {"cached_at": "x", "source": "ondeck"},
        })
        assert service.get_released_files() == {"/mnt/cache/A.mkv"}

    def test_released_sidecars_are_expanded(self, tmp_path):
        service = self._service(tmp_path, {
            "/mnt/cache/A.mkv": {
                "cached_at": "x",
                "released_at": "y",
                "associated_files": ["/mnt/cache/A.srt", "/mnt/cache/A.nfo"],
            },
        })
        assert service.get_released_files() == {
            "/mnt/cache/A.mkv", "/mnt/cache/A.srt", "/mnt/cache/A.nfo",
        }

    def test_missing_timestamps_file_is_empty(self, tmp_path):
        from web.services.maintenance_service import MaintenanceService

        service = object.__new__(MaintenanceService)
        service.timestamps_file = tmp_path / "nope.json"
        assert service.get_released_files() == set()

    def test_corrupt_timestamps_file_is_empty(self, tmp_path):
        ts_path = tmp_path / "timestamps.json"
        ts_path.write_text("{not json", encoding="utf-8")

        from web.services.maintenance_service import MaintenanceService

        service = object.__new__(MaintenanceService)
        service.timestamps_file = ts_path
        assert service.get_released_files() == set()

    def test_legacy_string_entries_are_ignored(self, tmp_path):
        """Pre-v3 timestamps were bare ISO strings, not dicts."""
        service = self._service(tmp_path, {"/mnt/cache/A.mkv": "2026-01-01T00:00:00"})
        assert service.get_released_files() == set()


class TestFullAuditWithReleasedFiles:
    """Drives the real run_full_audit() with its filesystem collaborators stubbed."""

    def _audit(self, cache_files, exclude_files, timestamp_files, released_files):
        from web.services.maintenance_service import MaintenanceService

        service = object.__new__(MaintenanceService)

        with patch.object(MaintenanceService, "get_cache_files", return_value=cache_files), \
             patch.object(MaintenanceService, "get_exclude_files", return_value=exclude_files), \
             patch.object(MaintenanceService, "get_timestamp_files", return_value=timestamp_files), \
             patch.object(MaintenanceService, "get_released_files", return_value=released_files), \
             patch.object(MaintenanceService, "_get_orphaned_plexcached",
                          return_value=([], [], set(), set())), \
             patch.object(MaintenanceService, "_cache_to_array_path", return_value=None), \
             patch.object(MaintenanceService, "_get_pinned_cache_paths", return_value=set()), \
             patch.object(MaintenanceService, "_group_unprotected_by_directory", return_value=[]):
            return service.run_full_audit()

    def test_mover_relocated_a_released_file_is_not_stale(self):
        """The mover doing its job must not read as tracking debris.

        Without this the Maintenance page goes amber on every run after a
        release — the shape of issue #176.
        """
        results = self._audit(
            cache_files=set(),
            exclude_files=set(),
            timestamp_files={"/mnt/cache/Released.mkv", "/mnt/cache/Genuine.mkv"},
            released_files={"/mnt/cache/Released.mkv"},
        )

        assert results.stale_timestamp_entries == ["/mnt/cache/Genuine.mkv"]

    def test_released_file_still_on_cache_is_not_unprotected(self, tmp_path):
        """A released file is absent from the exclude list on purpose.

        Listing it as unprotected recommends sync_to_array — the exact array
        write release exists to avoid — and exposes it to the bulk
        "Add to Exclude" action.
        """
        released = create_test_file(str(tmp_path / "Released.mkv"), size_bytes=10)
        genuine = create_test_file(str(tmp_path / "Genuine.mkv"), size_bytes=10)

        results = self._audit(
            cache_files={released, genuine},
            exclude_files=set(),
            timestamp_files=set(),
            released_files={released},
        )

        flagged = {f.cache_path for f in results.unprotected_files}
        assert flagged == {genuine}

    def test_released_files_do_not_affect_health(self, tmp_path):
        results = self._audit(
            cache_files=set(),
            exclude_files=set(),
            timestamp_files={"/mnt/cache/Released.mkv"},
            released_files={"/mnt/cache/Released.mkv"},
        )

        assert results.stale_timestamp_entries == []
        assert results.total_issues == 0
        assert results.health_status == "healthy"


class TestMoveFilesWiring:
    """_move_files() must route released files away from _safe_move_files()."""

    @staticmethod
    def _array_moves(mock_move):
        """Only the destination='array' calls; Step 3 always makes a 'cache' call."""
        return [c for c in mock_move.call_args_list if c.args[1] == 'array']

    def _app_for_move(self, tmp_path, temp_dir):
        tracker = _real_tracker(temp_dir)
        app, cache_root, array_root = _build_app(tmp_path, tracker=tracker)
        app.file_mover = MagicMock()
        app.file_mover._successful_array_moves = []
        app._move_back_exclude_paths = []
        app.media_to_cache = []
        app.all_active_media = []
        app.sibling_map = {}
        app.media_info_map = {}
        app.files_to_skip = []
        app.evicted_count = 0
        app.evicted_bytes = 0
        app.restored_count = 0
        app.restored_bytes = 0
        app._stop_requested = False
        return app, tracker, cache_root, array_root

    def test_released_file_never_reaches_safe_move_files(self, tmp_path, temp_dir):
        app, tracker, cache_root, array_root = self._app_for_move(tmp_path, temp_dir)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        array_path = os.path.join(array_root, "Movie.mkv")
        tracker.record_cache_time(cache_path, "ondeck")
        app.media_to_array = [array_path]

        with patch.object(app, "_build_restore_sibling_map"), \
             patch.object(app, "_share_has_array_side", return_value=True), \
             patch.object(app, "_safe_move_files") as mock_move, \
             patch.object(app, "_run_smart_eviction", return_value=(0, 0)):
            app._move_files()

        assert self._array_moves(mock_move) == []
        assert app.media_to_array == []
        assert app.released_count == 1
        assert tracker.is_released(cache_path) is True
        assert os.path.isfile(cache_path)

    def test_backed_up_file_still_relocates(self, tmp_path, temp_dir):
        """A file with a .plexcached backup must keep the existing behaviour."""
        app, tracker, cache_root, array_root = self._app_for_move(tmp_path, temp_dir)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        array_path = os.path.join(array_root, "Movie.mkv")
        create_test_file(array_path + ".plexcached", size_bytes=10)
        tracker.record_cache_time(cache_path, "ondeck")
        app.media_to_array = [array_path]

        with patch.object(app, "_build_restore_sibling_map"), \
             patch.object(app, "_share_has_array_side", return_value=True), \
             patch.object(app, "_safe_move_files") as mock_move, \
             patch.object(app, "_run_smart_eviction", return_value=(0, 0)):
            app._move_files()

        assert self._array_moves(mock_move) == [call([array_path], 'array')]
        assert app.released_count == 0
        assert tracker.is_released(cache_path) is False

    def test_mixed_batch_splits_correctly(self, tmp_path, temp_dir):
        app, tracker, cache_root, array_root = self._app_for_move(tmp_path, temp_dir)

        backed_up_cache = create_test_file(os.path.join(cache_root, "Backed.mkv"), size_bytes=10)
        backed_up_array = os.path.join(array_root, "Backed.mkv")
        create_test_file(backed_up_array + ".plexcached", size_bytes=10)

        native_cache = create_test_file(os.path.join(cache_root, "Native.mkv"), size_bytes=10)
        native_array = os.path.join(array_root, "Native.mkv")

        tracker.record_cache_time(backed_up_cache, "ondeck")
        tracker.record_cache_time(native_cache, "ondeck")
        app.media_to_array = [backed_up_array, native_array]

        with patch.object(app, "_build_restore_sibling_map"), \
             patch.object(app, "_share_has_array_side", return_value=True), \
             patch.object(app, "_safe_move_files") as mock_move, \
             patch.object(app, "_run_smart_eviction", return_value=(0, 0)):
            app._move_files()

        assert self._array_moves(mock_move) == [call([backed_up_array], 'array')]
        assert app.released_count == 1
        assert tracker.is_released(native_cache) is True
        assert tracker.is_released(backed_up_cache) is False

    def test_released_files_are_not_counted_as_failed_moves(self, tmp_path, temp_dir, caplog):
        """The deferred-exclude accounting must not treat a release as a
        failed relocation."""
        app, tracker, cache_root, array_root = self._app_for_move(tmp_path, temp_dir)

        cache_path = create_test_file(os.path.join(cache_root, "Movie.mkv"), size_bytes=10)
        array_path = os.path.join(array_root, "Movie.mkv")
        tracker.record_cache_time(cache_path, "ondeck")
        app.media_to_array = [array_path]
        app._move_back_exclude_paths = [cache_path]

        with patch.object(app, "_build_restore_sibling_map"), \
             patch.object(app, "_share_has_array_side", return_value=True), \
             patch.object(app, "_safe_move_files"), \
             patch.object(app, "_run_smart_eviction", return_value=(0, 0)):
            with caplog.at_level("WARNING"):
                app._move_files()

        assert "failed to move to array" not in caplog.text
