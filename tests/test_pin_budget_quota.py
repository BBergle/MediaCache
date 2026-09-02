"""The pin budget measures pinned bytes against a comparable ceiling.

``parse_budget_from_settings()`` has always computed ``plexcache_quota_bytes``,
but ``compute_budget_state()`` was only given ``cache_limit`` and
``min_free_space``. Two consequences:

* An install configured with only ``plexcache_quota`` got no pin guard at all.
  ``effective_budget`` was 0, and both flags are hardcoded False when it is 0.
* Where ``cache_limit`` was set, pinned bytes (a count of PlexCache-managed
  files) were compared against a whole-drive ceiling whose denominator includes
  everything else on the drive.

``plexcache_quota`` bounds the same population that ``current_pinned_bytes``
counts, so it is the comparable ceiling. The whole-drive limit stays as a
fallback and as a co-constraint.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    sys.modules.setdefault(_mod_name, MagicMock())

from core.pinned_media import compute_budget_state  # noqa: E402

GB = 1024 ** 3


class TestQuotaAsPinCeiling:

    def test_quota_alone_now_guards(self):
        """The regression: this configuration previously had no guard."""
        state = compute_budget_state(
            cache_limit_bytes=0,
            min_free_space_bytes=0,
            plexcache_quota_bytes=100 * GB,
            current_pinned_bytes=120 * GB,
        )

        assert state["effective_budget_bytes"] == 100 * GB
        assert state["over_budget"] is True

    def test_quota_alone_blocks_a_pin_that_would_exceed(self):
        state = compute_budget_state(
            cache_limit_bytes=0,
            min_free_space_bytes=0,
            plexcache_quota_bytes=100 * GB,
            current_pinned_bytes=95 * GB,
            additional_bytes=10 * GB,
        )

        assert state["would_exceed"] is True

    def test_quota_alone_allows_a_pin_that_fits(self):
        state = compute_budget_state(
            cache_limit_bytes=0,
            min_free_space_bytes=0,
            plexcache_quota_bytes=100 * GB,
            current_pinned_bytes=50 * GB,
            additional_bytes=10 * GB,
        )

        assert state["would_exceed"] is False
        assert state["over_budget"] is False

    def test_tighter_ceiling_wins_when_both_are_set(self):
        # quota 40GB vs drive ceiling 100-10=90GB
        state = compute_budget_state(
            cache_limit_bytes=100 * GB,
            min_free_space_bytes=10 * GB,
            plexcache_quota_bytes=40 * GB,
            current_pinned_bytes=50 * GB,
        )

        assert state["effective_budget_bytes"] == 40 * GB
        assert state["over_budget"] is True

    def test_drive_ceiling_wins_when_it_is_tighter(self):
        state = compute_budget_state(
            cache_limit_bytes=50 * GB,
            min_free_space_bytes=10 * GB,
            plexcache_quota_bytes=200 * GB,
            current_pinned_bytes=45 * GB,
        )

        assert state["effective_budget_bytes"] == 40 * GB
        assert state["over_budget"] is True


class TestUnchangedBehaviour:

    def test_cache_limit_only_is_untouched(self):
        """Installs without a quota keep exactly the guard they had."""
        state = compute_budget_state(
            cache_limit_bytes=10 * GB,
            min_free_space_bytes=2 * GB,
            current_pinned_bytes=5 * GB,
        )

        assert state["effective_budget_bytes"] == 8 * GB
        assert state["over_budget"] is False

    def test_nothing_configured_never_blocks(self):
        """The guard must not hard-block without an explicit limit."""
        state = compute_budget_state(
            cache_limit_bytes=0,
            min_free_space_bytes=0,
            plexcache_quota_bytes=0,
            current_pinned_bytes=900 * GB,
            additional_bytes=900 * GB,
        )

        assert state["effective_budget_bytes"] == 0
        assert state["over_budget"] is False
        assert state["would_exceed"] is False


class TestDriveSizeOverride:

    def test_percent_budget_uses_the_configured_drive_total(self):
        """ZFS reports the dataset, not the pool, hence the override."""
        from core.pinned_media import get_active_cache_total_bytes

        settings = {
            "cache_drive_size": "4TB",
            "path_mappings": [{"enabled": True, "cache_path": "/mnt/cache/media"}],
        }
        probed = MagicMock(total=500 * GB)

        with patch("core.system_utils.get_disk_usage", return_value=probed) as probe:
            total = get_active_cache_total_bytes(settings)

        assert probe.call_args.args[1] == 4 * 1024 * GB, (
            "the override should be handed to get_disk_usage, as the engine does"
        )
        assert total > 0
