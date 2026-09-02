"""Size settings accept what the app advertises, and expiry is reported.

``_parse_cache_limit`` rejected two formats the rest of the app accepts, and
because 0 means "no limit" to every consumer, a rejected value silently removed
the cap the user believed they had set:

* ``7.5%`` — the percent branch used ``int()``
* ``3.7T`` — there was no short-suffix branch, unlike ``parse_size_bytes()``

The same function parses cache_limit, min_free_space and plexcache_quota, so
its warnings named cache_limit no matter which one was mistyped.
"""

import logging
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    sys.modules.setdefault(_mod_name, MagicMock())

GB = 1024 ** 3
TB = 1024 ** 4


def _parse(value, key="cache_limit"):
    from core.config import ConfigManager
    return ConfigManager._parse_cache_limit(object.__new__(ConfigManager), value, key)


class TestFormatsTheAppAdvertises:

    @pytest.mark.parametrize("value,expected", [
        ("3.7T", int(3.7 * TB)),
        ("1.5G", int(1.5 * GB)),
        ("500M", 500 * 1024 ** 2),
        ("250GB", 250 * GB),
        ("500MB", 500 * 1024 ** 2),
        ("2", 2 * GB),
    ])
    def test_byte_quantities(self, value, expected):
        assert _parse(value) == expected

    @pytest.mark.parametrize("value,expected", [
        ("7.5%", -7.5),
        ("50%", -50.0),
        ("1%", -1.0),
        ("100%", -100.0),
    ])
    def test_percentages_may_be_fractional(self, value, expected):
        assert _parse(value) == expected

    def test_fractional_percent_resolves_against_the_drive(self):
        """The negative sentinel has to survive _get_effective_limit()."""
        from core.app import PlexCacheApp

        app = object.__new__(PlexCacheApp)
        app.config_manager = MagicMock()
        app.config_manager.cache.cache_drive_size_bytes = 0

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("core.app.get_disk_usage",
                       lambda *a, **k: MagicMock(total=1000 * GB))
            resolved, readable = app._get_effective_limit(_parse("7.5%"), "/mnt/cache", "cache_limit")

        assert resolved == 75 * GB
        assert "7.5%" in readable


class TestRejectionIsLoudAndNamed:

    @pytest.mark.parametrize("value", ["nonsense", "12PB", "GB"])
    def test_unparseable_is_zero_and_warns(self, value, caplog):
        with caplog.at_level(logging.WARNING):
            assert _parse(value) == 0
        assert "Using no limit" in caplog.text

    @pytest.mark.parametrize("value", ["0%", "101%", "-5%"])
    def test_out_of_range_percent_is_rejected(self, value, caplog):
        with caplog.at_level(logging.WARNING):
            assert _parse(value) == 0

    @pytest.mark.parametrize("key", ["cache_limit", "min_free_space", "plexcache_quota"])
    def test_warning_names_the_setting_that_was_mistyped(self, key, caplog):
        with caplog.at_level(logging.WARNING):
            _parse("wrong", key)
        assert key in caplog.text

    def test_parse_size_bytes_no_longer_fails_silently(self, caplog):
        from core.system_utils import parse_size_bytes

        with caplog.at_level(logging.WARNING):
            assert parse_size_bytes("lots") == 0
        assert "Could not read" in caplog.text

    @pytest.mark.parametrize("value", ["", "0", None])
    def test_empty_and_zero_stay_quiet(self, value, caplog):
        """Disabled is not a mistake, so it must not warn."""
        from core.system_utils import parse_size_bytes

        with caplog.at_level(logging.WARNING):
            assert parse_size_bytes(value) == 0
        assert caplog.text == ""


class TestWatchlistExpiryVisibility:

    def test_expiry_summary_logs_at_info(self):
        """DEBUG hid it; the OnDeck twin has always logged at INFO.

        Only the summary is checked. The RSS sub-count stays at DEBUG on
        purpose: it is folded into the same total, so promoting it would
        report the same items twice.
        """
        import inspect
        from core.app import PlexCacheApp

        source = inspect.getsource(PlexCacheApp)
        summary = [ln for ln in source.splitlines()
                   if "watchlist items due to retention expiry" in ln
                   and "retention_days" in ln]

        assert summary, "expiry summary line not found"
        assert "logging.info" in summary[0], f"still not INFO: {summary[0].strip()}"

    def test_rss_subcount_stays_at_debug(self):
        """Guard the deliberate asymmetry so it is not 'fixed' later."""
        import inspect
        from core.app import PlexCacheApp

        source = inspect.getsource(PlexCacheApp)
        sub = [ln for ln in source.splitlines()
               if "RSS watchlist items due to retention expiry" in ln]

        assert sub and "logging.debug" in sub[0]

    def test_disabled_retention_reports_nothing_expiring(self):
        """0 means nothing expires, so the list must be empty, not full."""
        import inspect
        from web.services.cache_service import CacheService

        source = inspect.getsource(CacheService)
        assert 'settings.get("watchlist_retention_days", 14)' not in source, (
            "the expiring-soon list still invents a 14-day retention for users "
            "who never set one"
        )
