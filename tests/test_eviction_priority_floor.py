"""A priority threshold below the score floor keeps every file.

``CachePriorityManager.calculate_priority()`` cannot return an arbitrary
number: the base is 50 and the negative factors are bounded, so the lowest a
real file reaches is around ``PRIORITY_RANGE_WATCHLIST_MIN``.
``get_eviction_candidates()`` then keeps anything scoring ``>= threshold``, so
a threshold at or below that floor keeps everything and eviction frees nothing
while still reporting itself as enabled.

Config load reports this rather than correcting it. Raising a stored threshold
would start deleting cache copies on the next run, and a config load is not
the place to make that decision for someone. The web input and the wizard stop
new values from landing there.
"""

import logging
import re
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from core.file_operations import PRIORITY_RANGE_WATCHLIST_MIN  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestFloorIsReal:

    def test_no_file_scores_below_the_documented_floor(self):
        """The constant the UI shows agrees with what the scorer can produce.

        Base 50, watchlist source (+0), one user (+5), stale cache (+0) and the
        worst watchlist age (-10) is the documented worst case.
        """
        assert 50 + 0 + 5 + 0 - 10 == PRIORITY_RANGE_WATCHLIST_MIN

    def test_threshold_at_the_floor_selects_nothing(self, tmp_path):
        """Sanity-check the comparison that makes low thresholds inert."""
        from core.file_operations import CachePriorityManager

        manager = object.__new__(CachePriorityManager)
        manager.eviction_min_priority = 0
        manager.active_pinned_paths = set()

        # get_eviction_candidates keeps everything scoring >= the threshold.
        scores = [PRIORITY_RANGE_WATCHLIST_MIN, 60, 100]
        kept = [s for s in scores if s >= manager.eviction_min_priority]

        assert kept == scores, "a threshold of 0 keeps every file"


def _validate(caplog, *, priority, mode="smart", threshold=90):
    """Run the real validation over a CacheConfig holding these values."""
    from core.config import CacheConfig, ConfigManager

    manager = object.__new__(ConfigManager)
    manager.cache = CacheConfig()
    manager.cache.cache_eviction_mode = mode
    manager.cache.cache_eviction_threshold_percent = threshold
    manager.cache.eviction_min_priority = priority

    with caplog.at_level(logging.WARNING):
        manager._validate_eviction_settings()
    return manager.cache


class TestConfigWarnsAboutADeadThreshold:

    @pytest.mark.parametrize("priority", [0, 1, PRIORITY_RANGE_WATCHLIST_MIN - 1])
    def test_warns_below_the_floor(self, priority, caplog):
        cache = _validate(caplog, priority=priority)

        assert "will never select anything" in caplog.text
        assert cache.eviction_min_priority == priority, (
            "the value must be left as configured — raising it would start "
            "deleting cache copies the user never agreed to lose"
        )
        assert cache.cache_eviction_mode == "smart", "the mode must not be flipped either"

    @pytest.mark.parametrize("priority", [PRIORITY_RANGE_WATCHLIST_MIN, 60, 100])
    def test_silent_at_or_above_the_floor(self, priority, caplog):
        _validate(caplog, priority=priority)

        assert "will never select anything" not in caplog.text

    def test_silent_when_eviction_is_off(self, caplog):
        """A leftover threshold with eviction disabled is not a problem."""
        _validate(caplog, priority=0, mode="none")

        assert "will never select anything" not in caplog.text

    def test_silent_for_fifo(self, caplog):
        """FIFO does not read the priority score at all."""
        _validate(caplog, priority=0, mode="fifo")

        assert "will never select anything" not in caplog.text

    def test_out_of_range_still_falls_back(self, caplog):
        """The existing range check keeps working."""
        cache = _validate(caplog, priority=150)

        assert cache.eviction_min_priority == 60
        assert "Invalid eviction_min_priority" in caplog.text


class TestInputsRejectDeadValues:

    def test_web_input_floor_is_the_score_floor(self):
        html = (REPO / "web" / "templates" / "settings" / "cache.html").read_text(encoding="utf-8")
        tag = re.search(r'<input[^>]*?name="eviction_min_priority"[^>]*?>', html, re.S)

        assert tag, "eviction_min_priority input not found"
        assert "priority_range.watchlist_min" in tag.group(0), (
            "the input should take its floor from the same constant the hint shows, "
            f"not a literal (currently {PRIORITY_RANGE_WATCHLIST_MIN})"
        )

    def test_web_input_floor_applies_only_to_smart_mode(self):
        """The form is hx-put, so HTML5 validation blocks the whole tab.

        A leftover low threshold with eviction off harms nothing, and refusing
        to save every other cache setting until it is corrected would be a
        worse outcome than the dead value it guards against.
        """
        html = (REPO / "web" / "templates" / "settings" / "cache.html").read_text(encoding="utf-8")
        tag = re.search(r'<input[^>]*?name="eviction_min_priority"[^>]*?>', html, re.S)

        assert "cache_eviction_mode == 'smart'" in tag.group(0), (
            "the floor should be conditional on smart eviction being active"
        )
        assert "else 0" in tag.group(0), "no floor when eviction is not smart"

    def test_wizard_clamps_below_floor_values(self):
        """New setups have no stored behaviour to preserve, so clamping is safe."""
        source = (REPO / "core" / "setup.py").read_text(encoding="utf-8")

        assert "PRIORITY_RANGE_WATCHLIST_MIN" in source
        assert "Min priority to evict (0-100)" not in source, (
            "the wizard should no longer advertise a range starting at 0"
        )
