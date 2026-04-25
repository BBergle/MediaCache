"""Tests for per-section OnDeck fetching (issue #151).

Verifies that `_fetch_user_on_deck_media` queries each configured library's
`/library/sections/{key}/onDeck` endpoint rather than the global
`/library/onDeck` endpoint, which applies Plex's "Include in home screen"
visibility filter server-side and silently drops hidden libraries.
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library', 'plexapi.exceptions', 'requests',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plex_api import PlexManager


def _make_video(section_key, last_viewed_days_ago=0):
    v = MagicMock()
    v.lastViewedAt = datetime.now() - timedelta(days=last_viewed_days_ago)
    v.title = f"video-sec{section_key}"
    return v


def _make_plex_instance(section_keys, ondeck_by_section):
    plex = MagicMock()
    sections = []
    for k in section_keys:
        s = MagicMock()
        s.key = k
        sections.append(s)
    plex.library.sections.return_value = sections

    def section_by_id(key):
        s = MagicMock()
        s.onDeck.return_value = ondeck_by_section.get(key, [])
        return s

    plex.library.sectionByID.side_effect = section_by_id
    return plex


def _bare_api():
    """Construct a PlexAPI instance without running __init__ (avoids network auth)."""
    api = PlexManager.__new__(PlexManager)
    api._ondeck_data_complete = True
    return api


class TestPerSectionOnDeckFetch:
    """Verify per-section iteration replaces the global /library/onDeck call."""

    def test_queries_each_valid_section(self):
        """sectionByID().onDeck() is called once per section in valid_sections."""
        api = _bare_api()
        plex = _make_plex_instance(
            section_keys=[2, 4, 5],
            ondeck_by_section={2: [], 4: [], 5: []},
        )
        with patch.object(api, 'get_plex_instance', return_value=("main", plex)), \
             patch.object(api, '_process_episode_ondeck'), \
             patch.object(api, '_process_movie_ondeck'):
            api._fetch_user_on_deck_media(
                valid_sections=[2, 4, 5],
                days_to_monitor=30,
                number_episodes=3,
            )
        called_section_keys = [c.args[0] for c in plex.library.sectionByID.call_args_list]
        assert sorted(called_section_keys) == [2, 4, 5]

    def test_global_ondeck_not_called(self):
        """The filtered global endpoint must not be used."""
        api = _bare_api()
        plex = _make_plex_instance(section_keys=[2], ondeck_by_section={2: []})
        with patch.object(api, 'get_plex_instance', return_value=("main", plex)):
            api._fetch_user_on_deck_media(
                valid_sections=[2], days_to_monitor=30, number_episodes=3,
            )
        plex.library.onDeck.assert_not_called()

    def test_section_not_in_valid_sections_is_skipped(self):
        """A section present on the server but not in valid_sections is not queried."""
        api = _bare_api()
        plex = _make_plex_instance(
            section_keys=[2, 4, 5],
            ondeck_by_section={2: [], 4: [], 5: []},
        )
        with patch.object(api, 'get_plex_instance', return_value=("main", plex)), \
             patch.object(api, '_process_episode_ondeck'), \
             patch.object(api, '_process_movie_ondeck'):
            api._fetch_user_on_deck_media(
                valid_sections=[2, 4],  # section 5 not configured as cacheable
                days_to_monitor=30,
                number_episodes=3,
            )
        called_keys = [c.args[0] for c in plex.library.sectionByID.call_args_list]
        assert 5 not in called_keys
        assert sorted(called_keys) == [2, 4]

    def test_hidden_library_items_surfaced(self):
        """Items from a cacheable library not on the home screen are surfaced.

        This is the regression test for issue #151. Under the old code, a library
        with 'Exclude from home screen' was silently dropped by the global endpoint;
        the new per-section code returns its items.
        """
        api = _bare_api()
        hidden_video = _make_video(section_key=5)
        plex = _make_plex_instance(
            section_keys=[2, 5],
            ondeck_by_section={2: [], 5: [hidden_video]},
        )
        with patch.object(api, 'get_plex_instance', return_value=("main", plex)), \
             patch.object(api, '_process_episode_ondeck') as mock_ep, \
             patch.object(api, '_process_movie_ondeck') as mock_mv, \
             patch('core.plex_api.isinstance', side_effect=lambda o, t: o is hidden_video):
            api._fetch_user_on_deck_media(
                valid_sections=[2, 5], days_to_monitor=30, number_episodes=3,
            )
        total_processed = mock_ep.call_count + mock_mv.call_count
        assert total_processed >= 1, "hidden-library video was not processed"

    def test_days_to_monitor_still_filters(self):
        """Items older than days_to_monitor are dropped even on per-section path."""
        api = _bare_api()
        fresh = _make_video(section_key=2, last_viewed_days_ago=1)
        stale = _make_video(section_key=2, last_viewed_days_ago=60)
        plex = _make_plex_instance(
            section_keys=[2], ondeck_by_section={2: [fresh, stale]},
        )
        with patch.object(api, 'get_plex_instance', return_value=("main", plex)), \
             patch.object(api, '_process_episode_ondeck') as mock_ep, \
             patch.object(api, '_process_movie_ondeck') as mock_mv, \
             patch('core.plex_api.isinstance', side_effect=lambda o, t: True):
            api._fetch_user_on_deck_media(
                valid_sections=[2], days_to_monitor=30, number_episodes=3,
            )
        calls = mock_ep.call_args_list + mock_mv.call_args_list
        processed_videos = [c.args[0] for c in calls]
        assert fresh in processed_videos
        assert stale not in processed_videos

    def test_section_fetch_error_isolated(self):
        """If one section errors, other sections are still fetched."""
        api = _bare_api()

        def section_by_id(key):
            s = MagicMock()
            if key == 4:
                s.onDeck.side_effect = RuntimeError("boom")
            else:
                s.onDeck.return_value = []
            return s

        plex = MagicMock()
        plex.library.sections.return_value = [MagicMock(key=k) for k in [2, 4, 5]]
        for mock_sec, k in zip(plex.library.sections.return_value, [2, 4, 5]):
            mock_sec.key = k
        plex.library.sectionByID.side_effect = section_by_id

        with patch.object(api, 'get_plex_instance', return_value=("main", plex)):
            api._fetch_user_on_deck_media(
                valid_sections=[2, 4, 5], days_to_monitor=30, number_episodes=3,
            )

        called_keys = [c.args[0] for c in plex.library.sectionByID.call_args_list]
        assert sorted(called_keys) == [2, 4, 5]

    def test_partial_failure_marks_ondeck_incomplete_for_main(self):
        """Main-account section failure flips _ondeck_data_complete to False."""
        api = _bare_api()
        assert api._ondeck_data_complete is True

        def section_by_id(key):
            s = MagicMock()
            s.onDeck.side_effect = RuntimeError("boom")
            return s

        plex = MagicMock()
        plex.library.sections.return_value = [MagicMock(key=2)]
        plex.library.sections.return_value[0].key = 2
        plex.library.sectionByID.side_effect = section_by_id

        with patch.object(api, 'get_plex_instance', return_value=("main", plex)):
            api._fetch_user_on_deck_media(
                valid_sections=[2], days_to_monitor=30, number_episodes=3, user=None,
            )

        assert api._ondeck_data_complete is False

    def test_empty_valid_sections_falls_back_to_all_available(self):
        """Preserves existing 'no restriction' behavior when valid_sections is empty."""
        api = _bare_api()
        plex = _make_plex_instance(
            section_keys=[2, 4, 5],
            ondeck_by_section={2: [], 4: [], 5: []},
        )
        with patch.object(api, 'get_plex_instance', return_value=("main", plex)), \
             patch.object(api, '_process_episode_ondeck'), \
             patch.object(api, '_process_movie_ondeck'):
            api._fetch_user_on_deck_media(
                valid_sections=[], days_to_monitor=30, number_episodes=3,
            )
        called_keys = [c.args[0] for c in plex.library.sectionByID.call_args_list]
        assert sorted(called_keys) == [2, 4, 5]
