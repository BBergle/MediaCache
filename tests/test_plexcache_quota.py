"""
Tests for PlexCache Quota enforcement in _apply_cache_limit().

Tests the plexcache_quota constraint which limits the total size of
PlexCache-managed files (from the exclude list), independent of total drive usage.
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from collections import namedtuple

# conftest.py handles fcntl mocking and path setup.
for _mod_name in [
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.exceptions', 'requests',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()


DiskUsage = namedtuple('DiskUsage', ['total', 'used', 'free'])

GB = 1024 ** 3


def _build_app(tmp_path, cache_limit_bytes=0, min_free_bytes=0, quota_bytes=0,
               drive_total=1000*GB, drive_used=400*GB, tracked_size=200*GB):
    """Build a minimal PlexCacheApp for _apply_cache_limit() testing."""
    from conftest import create_test_file
    from core.app import PlexCacheApp

    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir, exist_ok=True)

    config_manager = MagicMock()
    config_manager.cache.cache_limit_bytes = cache_limit_bytes
    config_manager.cache.min_free_space_bytes = min_free_bytes
    config_manager.cache.plexcache_quota_bytes = quota_bytes
    config_manager.cache.cache_drive_size_bytes = 0

    exclude_file = tmp_path / "exclude.txt"
    exclude_mock = MagicMock()
    exclude_mock.exists.return_value = False
    config_manager.get_cached_files_file.return_value = exclude_mock

    app = object.__new__(PlexCacheApp)
    app.config_manager = config_manager
    app.dry_run = False
    app.file_filter = None
    app._stop_requested = False

    # Create test media files (each 10GB)
    files = []
    for i in range(5):
        f = os.path.join(cache_dir, f"movie_{i}.mkv")
        create_test_file(f, size_bytes=1024)  # Small files, we mock getsize
        files.append(f)

    # Mock disk_usage and file sizes
    disk = DiskUsage(total=drive_total, used=drive_used, free=drive_total - drive_used)

    return app, cache_dir, files, disk, tracked_size


class TestPlexcacheQuotaEnforcement:
    """Tests for plexcache_quota as a constraint in _apply_cache_limit()."""

    def test_quota_disabled_no_effect(self, tmp_path):
        """When plexcache_quota is empty/0, it has no effect."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path, quota_bytes=0, cache_limit_bytes=0, min_free_bytes=0
        )

        # No constraints at all -> all files pass through
        result = app._apply_cache_limit(files, cache_dir)
        assert result == files

    def test_quota_only_constraint(self, tmp_path):
        """When only quota is set, it limits based on tracked size."""
        app, cache_dir, files, disk, tracked_size = _build_app(
            tmp_path, quota_bytes=250*GB, tracked_size=200*GB
        )

        # Mock disk usage and tracked size
        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # 250GB quota - 200GB tracked = 50GB available
        # Each file is 10GB, so 5 files fit
        assert len(result) == 5

    def test_quota_limits_files(self, tmp_path):
        """When quota has limited space, only fitting files are cached."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path, quota_bytes=220*GB
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # 220GB quota - 200GB tracked = 20GB available
        # Each file is 10GB, so only 2 files fit
        assert len(result) == 2

    def test_quota_exceeded_returns_empty(self, tmp_path):
        """When tracked size already exceeds quota, no files are cached."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path, quota_bytes=150*GB
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # 150GB quota - 200GB tracked = -50GB available -> empty
        assert result == []

    def test_quota_more_restrictive_than_cache_limit(self, tmp_path):
        """When quota is more restrictive than cache_limit, quota wins."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path,
            cache_limit_bytes=800*GB,  # 800GB limit, 400GB used = 400GB available
            quota_bytes=220*GB,  # 220GB quota, 200GB tracked = 20GB available
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # quota is more restrictive: 20GB vs 400GB
        # Only 2 files fit (20GB / 10GB each)
        assert len(result) == 2

    def test_cache_limit_more_restrictive_than_quota(self, tmp_path):
        """When cache_limit is more restrictive than quota, cache_limit wins."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path,
            cache_limit_bytes=410*GB,  # 410GB limit, 400GB used = 10GB available
            quota_bytes=500*GB,  # 500GB quota, 200GB tracked = 300GB available
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # cache_limit is more restrictive: 10GB vs 300GB
        # Only 1 file fits (10GB / 10GB each)
        assert len(result) == 1

    def test_min_free_space_more_restrictive_than_quota(self, tmp_path):
        """When min_free_space is more restrictive than quota, min_free wins."""
        drive_free = 15 * GB
        drive_used = 985 * GB
        disk = DiskUsage(total=1000*GB, used=drive_used, free=drive_free)

        app, cache_dir, files, _, _ = _build_app(
            tmp_path,
            min_free_bytes=10*GB,  # 15GB free - 10GB floor = 5GB available
            quota_bytes=500*GB,    # 500GB quota - 200GB tracked = 300GB available
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # min_free is most restrictive: 5GB available, file is 10GB -> 0 fit
        assert len(result) == 0

    def test_percentage_quota(self, tmp_path):
        """Percentage-based quota is resolved against drive total."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path,
            quota_bytes=-25,  # -25 means 25% of drive
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # 25% of 1000GB = 250GB quota, 200GB tracked = 50GB available
        # 5 files * 10GB = 50GB -> all fit
        assert len(result) == 5

    def test_all_three_constraints_quota_wins(self, tmp_path):
        """When all three constraints are active, the most restrictive wins."""
        app, cache_dir, files, disk, _ = _build_app(
            tmp_path,
            cache_limit_bytes=800*GB,   # 400GB available (800 - 400 used)
            min_free_bytes=100*GB,      # 500GB available (600 free - 100 floor)
            quota_bytes=215*GB,         # 15GB available (215 - 200 tracked)
        )

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', return_value=10*GB):
            result = app._apply_cache_limit(files, cache_dir)

        # quota is most restrictive: 15GB available
        # Only 1 file fits (10GB <= 15GB, then 5GB left < 10GB)
        assert len(result) == 1


class TestNoGapShowOrdering:
    """Tests for #169: episodes of the same show stay contiguous when space is tight.

    Once an episode of show X doesn't fit, every later file mapped to show X
    via media_info_map is skipped — even if it would individually fit. Movies
    and other shows still pack independently into the remaining space.
    """

    def _build_episode_file(self, cache_dir, show, season, episode):
        """Create a placeholder file for an episode and return its path."""
        from conftest import create_test_file
        path = os.path.join(cache_dir, f"{show}_S{season:02d}E{episode:02d}.mkv")
        create_test_file(path, size_bytes=1024)
        return path

    def test_oversized_episode_skips_later_episodes_same_show(self, tmp_path):
        """S04E03 oversized → E04 and E05 skipped even though each would fit."""
        app, cache_dir, _, disk, _ = _build_app(
            tmp_path, quota_bytes=210*GB,  # 10GB available
        )

        e3 = self._build_episode_file(cache_dir, "ShowA", 4, 3)
        e4 = self._build_episode_file(cache_dir, "ShowA", 4, 4)
        e5 = self._build_episode_file(cache_dir, "ShowA", 4, 5)
        files = [e3, e4, e5]

        app.media_info_map = {
            e3: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 3}},
            e4: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 4}},
            e5: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 5}},
        }

        # E03 is 15GB (won't fit in 10GB), E04+E05 are 2GB each (would fit)
        sizes = {e3: 15*GB, e4: 2*GB, e5: 2*GB}

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', side_effect=lambda f: sizes[f]):
            result = app._apply_cache_limit(files, cache_dir)

        # E03 skipped (too big) → E04, E05 skipped to keep show contiguous
        assert result == []

    def test_first_episode_fits_then_oversized_blocks_rest(self, tmp_path):
        """E03 fits, E04 oversized → E05+ skipped even though they'd fit."""
        app, cache_dir, _, disk, _ = _build_app(
            tmp_path, quota_bytes=220*GB,  # 20GB available
        )

        e3 = self._build_episode_file(cache_dir, "ShowA", 4, 3)
        e4 = self._build_episode_file(cache_dir, "ShowA", 4, 4)
        e5 = self._build_episode_file(cache_dir, "ShowA", 4, 5)
        files = [e3, e4, e5]

        app.media_info_map = {
            e3: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 3}},
            e4: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 4}},
            e5: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 5}},
        }

        # E03 = 5GB (fits, 15GB left), E04 = 18GB (won't fit), E05 = 2GB (would fit)
        sizes = {e3: 5*GB, e4: 18*GB, e5: 2*GB}

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', side_effect=lambda f: sizes[f]):
            result = app._apply_cache_limit(files, cache_dir)

        # E03 cached; E04 oversized; E05 skipped (same show as the gap-maker)
        assert result == [e3]

    def test_other_shows_still_pack_after_one_show_at_capacity(self, tmp_path):
        """A show running out of space doesn't starve other shows or movies."""
        app, cache_dir, _, disk, _ = _build_app(
            tmp_path, quota_bytes=215*GB,  # 15GB available
        )

        a_e3 = self._build_episode_file(cache_dir, "ShowA", 4, 3)
        a_e4 = self._build_episode_file(cache_dir, "ShowA", 4, 4)
        b_e1 = self._build_episode_file(cache_dir, "ShowB", 1, 1)
        movie = os.path.join(cache_dir, "movie.mkv")
        from conftest import create_test_file
        create_test_file(movie, size_bytes=1024)

        files = [a_e3, a_e4, b_e1, movie]

        app.media_info_map = {
            a_e3: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 3}},
            a_e4: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 4, "episode": 4}},
            b_e1: {"media_type": "episode", "episode_info": {"show": "ShowB", "season": 1, "episode": 1}},
            movie: {"media_type": "movie", "episode_info": None},
        }

        # ShowA E03 = 20GB (won't fit), ShowA E04 = 3GB, ShowB E01 = 4GB, movie = 5GB
        sizes = {a_e3: 20*GB, a_e4: 3*GB, b_e1: 4*GB, movie: 5*GB}

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', side_effect=lambda f: sizes[f]):
            result = app._apply_cache_limit(files, cache_dir)

        # ShowA E03 skipped → ShowA E04 also skipped (gap protection).
        # ShowB E01 (4GB) fits → 11GB left. Movie (5GB) fits → 6GB left.
        assert result == [b_e1, movie]

    def test_files_without_show_metadata_pack_normally(self, tmp_path):
        """Non-episode files (movies, siblings) without show keys keep first-fit behavior."""
        app, cache_dir, _, disk, _ = _build_app(
            tmp_path, quota_bytes=215*GB,  # 15GB available
        )

        big = os.path.join(cache_dir, "big.mkv")
        small1 = os.path.join(cache_dir, "small1.mkv")
        small2 = os.path.join(cache_dir, "small2.mkv")
        from conftest import create_test_file
        for p in (big, small1, small2):
            create_test_file(p, size_bytes=1024)
        files = [big, small1, small2]

        # No media_info_map entries → no show grouping → first-fit semantics
        app.media_info_map = {}
        sizes = {big: 20*GB, small1: 5*GB, small2: 5*GB}

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', side_effect=lambda f: sizes[f]):
            result = app._apply_cache_limit(files, cache_dir)

        # Without show metadata, the greedy fit keeps the small ones
        assert result == [small1, small2]

    def test_in_order_episodes_all_fit(self, tmp_path):
        """When everything fits in order, no episodes are skipped."""
        app, cache_dir, _, disk, _ = _build_app(
            tmp_path, quota_bytes=300*GB,  # 100GB available
        )

        eps = [self._build_episode_file(cache_dir, "ShowA", 1, i) for i in range(1, 5)]
        app.media_info_map = {
            eps[i]: {"media_type": "episode", "episode_info": {"show": "ShowA", "season": 1, "episode": i + 1}}
            for i in range(4)
        }
        sizes = {ep: 5*GB for ep in eps}

        with patch('core.app.get_disk_usage', return_value=disk), \
             patch.object(app, '_get_plexcache_tracked_size', return_value=(200*GB, [])), \
             patch('os.path.getsize', side_effect=lambda f: sizes[f]):
            result = app._apply_cache_limit(eps, cache_dir)

        assert result == eps


class TestPlexcacheQuotaConfig:
    """Tests for plexcache_quota config parsing."""

    def test_config_default_empty(self):
        """Default plexcache_quota is empty string."""
        from core.config import CacheConfig
        config = CacheConfig()
        assert config.plexcache_quota == ""
        assert config.plexcache_quota_bytes == 0

    def test_parse_quota_gb(self):
        """Parse absolute GB value for quota."""
        from core.config import ConfigManager
        cm = object.__new__(ConfigManager)
        assert cm._parse_cache_limit("500GB") == 500 * GB

    def test_parse_quota_percentage(self):
        """Parse percentage value for quota (returns negative)."""
        from core.config import ConfigManager
        cm = object.__new__(ConfigManager)
        assert cm._parse_cache_limit("50%") == -50

    def test_parse_quota_empty(self):
        """Parse empty string returns 0 (disabled)."""
        from core.config import ConfigManager
        cm = object.__new__(ConfigManager)
        assert cm._parse_cache_limit("") == 0

    def test_parse_quota_zero(self):
        """Parse "0" returns 0 (disabled)."""
        from core.config import ConfigManager
        cm = object.__new__(ConfigManager)
        assert cm._parse_cache_limit("0") == 0


class TestGetEffectivePlexcacheQuota:
    """Tests for _get_effective_plexcache_quota() method."""

    def test_disabled_returns_zero(self, tmp_path):
        """When quota is 0, returns (0, None)."""
        app, cache_dir, _, _, _ = _build_app(tmp_path, quota_bytes=0)
        result = app._get_effective_plexcache_quota(cache_dir)
        assert result == (0, None)

    def test_absolute_value(self, tmp_path):
        """Absolute byte value is returned directly."""
        app, cache_dir, _, disk, _ = _build_app(tmp_path, quota_bytes=500*GB)
        result = app._get_effective_plexcache_quota(cache_dir)
        assert result[0] == 500 * GB
        assert "500.00GB" in result[1]

    def test_percentage_resolved(self, tmp_path):
        """Percentage is resolved against drive total."""
        app, cache_dir, _, disk, _ = _build_app(tmp_path, quota_bytes=-25)

        with patch('core.app.get_disk_usage', return_value=disk):
            result = app._get_effective_plexcache_quota(cache_dir)

        assert result[0] == 250 * GB  # 25% of 1000GB
        assert "25%" in result[1]
