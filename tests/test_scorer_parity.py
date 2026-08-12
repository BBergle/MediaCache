"""The Priority Report and the eviction engine must produce the same score.

Two implementations exist because they read from different places:
``CachePriorityManager`` from its tracker objects, ``CacheService`` from the
raw JSON the web layer already has loaded. That is a reasonable split, but the
*number* has to match — ``eviction_min_priority`` is calibrated against what
the report shows, and acted on by what the engine computes.

The episode-position factor is the one that had drifted: the web scorer
computed a prefetch window and never compared against it, so every episode with
a number scored +10 where the engine gave it only within half the window.
``core.media_grouping.episodes_ahead_of()`` is now shared by both.
"""

import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    sys.modules.setdefault(_mod_name, MagicMock())

from core.media_grouping import episodes_ahead_of  # noqa: E402

def _cache_path(season, episode):
    return f"/mnt/cache/tv/Show/Show - S{season:02d}E{episode:02d}.mkv"


def _plex_path(season, episode):
    return f"/data/tv/Show/Show - S{season:02d}E{episode:02d}.mkv"


def _engine_score(*, source, users, episode_info, ondeck_positions, number_episodes=6):
    from core.file_operations import CachePriorityManager

    mgr = object.__new__(CachePriorityManager)
    mgr.active_pinned_paths = set()
    mgr.active_ondeck_paths = None
    mgr.number_episodes = number_episodes

    mgr.timestamp_tracker = MagicMock()
    mgr.timestamp_tracker.get_source.return_value = source
    mgr.timestamp_tracker.get_episode_info.return_value = episode_info

    mgr.ondeck_tracker = MagicMock()
    mgr.ondeck_tracker.get_entry.return_value = (
        {"users": users, "first_seen": None} if source == "ondeck" else None)
    mgr.ondeck_tracker.get_episode_info.return_value = episode_info
    mgr.ondeck_tracker.get_earliest_ondeck_position.return_value = (
        min(ondeck_positions) if ondeck_positions else None)

    mgr.watchlist_tracker = MagicMock()
    mgr.watchlist_tracker.get_entry.return_value = None

    mgr._get_hours_since_cached = MagicMock(return_value=500)  # old, no bonus
    target = _cache_path((episode_info or {}).get("season", 1),
                         (episode_info or {}).get("episode", 1))
    return mgr.calculate_priority(target)


def _web_score(*, source, users, episode_info, ondeck_positions, number_episodes=6):
    from web.services.cache_service import CacheService

    svc = object.__new__(CacheService)

    tgt_season = (episode_info or {}).get("season", 1)
    tgt_episode = (episode_info or {}).get("episode", 1)
    target = _cache_path(tgt_season, tgt_episode)

    timestamps = {target: {"source": source,
                           "cached_at": (datetime.now() - timedelta(days=30)).isoformat()}}

    ondeck = {}
    # Whoever is furthest behind sets the reference point. Written first so the
    # target's own entry wins if it happens to be that same episode.
    for season, ep in ondeck_positions:
        ondeck[_plex_path(season, ep)] = {
            "users": ["u"],
            "episode_info": {"show": "Show", "season": season, "episode": ep,
                             "is_current_ondeck": True},
        }
    if source == "ondeck":
        ondeck[_plex_path(tgt_season, tgt_episode)] = {
            "users": users, "episode_info": episode_info}

    return svc.calculate_priority(
        target, timestamps, ondeck, {}, {"number_episodes": number_episodes})


CASES = [
    pytest.param(0, id="current-ondeck-episode"),
    pytest.param(2, id="two-ahead-inside-window"),
    pytest.param(3, id="at-the-window-edge"),
    pytest.param(6, id="far-ahead-outside-window"),
    pytest.param(20, id="very-far-ahead"),
]


@pytest.mark.parametrize("ahead", CASES)
def test_episode_position_scores_agree(ahead):
    """The factor that had drifted, across the whole window."""
    target_ep = 5 + ahead
    episode_info = {"show": "Show", "season": 1, "episode": target_ep,
                    "is_current_ondeck": ahead == 0}
    positions = [(1, 5)]

    engine = _engine_score(source="ondeck", users=["u"],
                           episode_info=episode_info, ondeck_positions=positions)
    web = _web_score(source="ondeck", users=["u"],
                     episode_info=episode_info, ondeck_positions=positions)

    assert engine == web, (
        f"{ahead} episodes ahead: engine scores {engine}, report shows {web}"
    )


def test_far_ahead_episode_gets_no_bonus_in_either():
    """The concrete regression: the report used to read 10 points high."""
    episode_info = {"show": "Show", "season": 1, "episode": 25,
                    "is_current_ondeck": False}
    positions = [(1, 5)]

    web = _web_score(source="ondeck", users=["u"],
                     episode_info=episode_info, ondeck_positions=positions)
    engine = _engine_score(source="ondeck", users=["u"],
                           episode_info=episode_info, ondeck_positions=positions)

    assert episodes_ahead_of(1, 25, 1, 5) == 20, "sanity: well outside the window"
    assert web == engine
    # base 50 + ondeck 15 + one user 5, and no episode bonus
    assert web == 70


def test_watchlist_item_scores_agree():
    engine = _engine_score(source="watchlist", users=[],
                           episode_info=None, ondeck_positions=[])
    web = _web_score(source="watchlist", users=[],
                     episode_info=None, ondeck_positions=[])

    assert engine == web
