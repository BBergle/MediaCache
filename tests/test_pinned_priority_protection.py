"""Phase 2b safety tests: pinned items cannot be evicted or filtered out.

Locks the critical data-contract from FUTURE_ENHANCEMENTS.md §6 point 4:
> Smart eviction MUST skip any file whose rating_key is in the pinned set.

Tests in this file operate on the real CachePriorityManager with real trackers
and touch real files on disk (tmp_path), so they exercise the full eviction
path including os.path.exists() / os.path.getsize() probes.
"""

import os

import pytest

from core.file_operations import (
    CachePriorityManager,
    CacheTimestampTracker,
    OnDeckTracker,
    WatchlistTracker,
)


@pytest.fixture
def trackers(tmp_path):
    ts = CacheTimestampTracker(str(tmp_path / "timestamps.json"))
    od = OnDeckTracker(str(tmp_path / "ondeck.json"))
    wl = WatchlistTracker(str(tmp_path / "watchlist.json"))
    return ts, od, wl


@pytest.fixture
def priority_mgr(trackers):
    ts, od, wl = trackers
    return CachePriorityManager(
        timestamp_tracker=ts,
        watchlist_tracker=wl,
        ondeck_tracker=od,
        eviction_min_priority=60,
        number_episodes=5,
    )


def _touch(path, size=1024 * 1024):
    """Create a file of a given size on disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    return path


# ---------------------------------------------------------------------------
# calculate_priority()
# ---------------------------------------------------------------------------


class TestCalculatePriorityPinnedShortCircuit:
    def test_pinned_cache_path_returns_100(self, priority_mgr, tmp_path, trackers):
        """A pinned cache path returns 100, skipping every scoring factor."""
        ts, _, _ = trackers
        path = str(tmp_path / "pinned.mkv")
        # Even with NO tracker entry (normally scores 50), pinning lifts to 100
        priority_mgr.active_pinned_paths = {path}
        assert priority_mgr.calculate_priority(path) == 100

    def test_non_pinned_path_uses_normal_scoring(self, priority_mgr, tmp_path):
        path = str(tmp_path / "normal.mkv")
        priority_mgr.active_pinned_paths = {str(tmp_path / "other.mkv")}
        # No tracker entry → base 50 + default user bonus logic
        score = priority_mgr.calculate_priority(path)
        assert score < 100

    def test_none_active_pinned_paths_is_noop(self, priority_mgr, tmp_path):
        """active_pinned_paths=None (the default) means no pinned protection."""
        path = str(tmp_path / "file.mkv")
        priority_mgr.active_pinned_paths = None
        score = priority_mgr.calculate_priority(path)
        assert score < 100

    def test_empty_set_is_noop(self, priority_mgr, tmp_path):
        """Empty set = no paths pinned, but non-None sentinel."""
        path = str(tmp_path / "file.mkv")
        priority_mgr.active_pinned_paths = set()
        score = priority_mgr.calculate_priority(path)
        assert score < 100

    def test_pinned_beats_low_watchlist_score(self, priority_mgr, tmp_path, trackers):
        """A pinned file that would otherwise score low (old watchlist) still returns 100."""
        ts, _, wl = trackers
        path = str(tmp_path / "old.mkv")

        # Set up an old watchlist entry (>60d = -10 penalty), no ondeck, no users
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=90)).isoformat()
        wl._data[path] = {
            "users": [],
            "watchlisted_at": old_date,
        }

        # Normal score would be <60
        priority_mgr.active_pinned_paths = None
        assert priority_mgr.calculate_priority(path) < 100

        # Pinned score is always 100
        priority_mgr.active_pinned_paths = {path}
        assert priority_mgr.calculate_priority(path) == 100


# ---------------------------------------------------------------------------
# get_eviction_candidates()
# ---------------------------------------------------------------------------


class TestEvictionCandidatesPinnedSkip:
    def test_pinned_never_returned_as_candidate(self, priority_mgr, tmp_path):
        """Even under extreme budget pressure, pinned files are not evicted."""
        pinned = _touch(str(tmp_path / "pinned.mkv"), size=1024 * 1024 * 100)  # 100 MB
        unpinned = _touch(str(tmp_path / "normal.mkv"), size=1024 * 1024 * 100)

        priority_mgr.active_pinned_paths = {pinned}

        # Request enough bytes that everything would need to go
        target = 1024 * 1024 * 1024  # 1 GB
        candidates = priority_mgr.get_eviction_candidates([pinned, unpinned], target)

        assert pinned not in candidates
        # unpinned has no tracker entry → base score 50 + 5 user bonus (conservative)
        # which is < eviction_min_priority of 60 by default in our fixture
        # So it should be eligible
        assert unpinned in candidates

    def test_mixed_set_respects_pin(self, priority_mgr, tmp_path):
        """Three files: pinned high-scoring, unpinned high-scoring, unpinned low-scoring."""
        pinned = _touch(str(tmp_path / "pinned.mkv"))
        high_score = _touch(str(tmp_path / "high.mkv"))
        low_score = _touch(str(tmp_path / "low.mkv"))

        # No tracker entries: all score 50 (below eviction_min_priority=60)
        priority_mgr.active_pinned_paths = {pinned}

        candidates = priority_mgr.get_eviction_candidates(
            [pinned, high_score, low_score], target_bytes=1024 * 1024 * 500
        )

        assert pinned not in candidates
        # The other two are eligible at score 50
        assert high_score in candidates
        assert low_score in candidates

    def test_pinned_not_in_candidates_even_if_only_file(self, priority_mgr, tmp_path):
        pinned = _touch(str(tmp_path / "only.mkv"))
        priority_mgr.active_pinned_paths = {pinned}
        candidates = priority_mgr.get_eviction_candidates(
            [pinned], target_bytes=1024 * 1024 * 1024
        )
        assert candidates == []

    def test_empty_pinned_set_evicts_normally(self, priority_mgr, tmp_path):
        """Regression: empty pinned set doesn't interfere with normal eviction."""
        f1 = _touch(str(tmp_path / "f1.mkv"))
        f2 = _touch(str(tmp_path / "f2.mkv"))
        priority_mgr.active_pinned_paths = set()
        candidates = priority_mgr.get_eviction_candidates(
            [f1, f2], target_bytes=1024 * 1024 * 500
        )
        # Both score 50 < 60, so both eligible
        assert set(candidates) <= {f1, f2}
        assert len(candidates) >= 1

    def test_none_pinned_evicts_normally(self, priority_mgr, tmp_path):
        """Regression: None sentinel doesn't block eviction."""
        f1 = _touch(str(tmp_path / "f1.mkv"))
        priority_mgr.active_pinned_paths = None
        candidates = priority_mgr.get_eviction_candidates(
            [f1], target_bytes=1024 * 1024 * 500
        )
        assert f1 in candidates
