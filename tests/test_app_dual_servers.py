"""PlexCacheApp running Plex and Jellyfin together.

Removals from cache (array restore, eviction, watchlist cleanup) wait while a
server's data is missing, and each server only gets its own users.
"""

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app import PlexCacheApp
from core.config import PathMapping
from core.media_servers import CombinedMediaManager


def _app(missing=(), watchlist_complete=True, watchlist_toggle=False):
    app = PlexCacheApp.__new__(PlexCacheApp)
    app.dry_run = False
    cm = MagicMock()
    cm.cache.watchlist_toggle = watchlist_toggle
    app.config_manager = cm
    pm = MagicMock()
    pm.missing_servers.return_value = set(missing)
    pm.is_ondeck_data_complete.return_value = not missing
    pm.is_watchlist_data_complete.return_value = watchlist_complete
    app.plex_manager = pm
    return app


def _init_app(media_server):
    app = PlexCacheApp.__new__(PlexCacheApp)
    cm = MagicMock()
    cm.plex.media_server = media_server
    cm.plex.jellyfin_url = "http://jf:8096"
    cm.plex.jellyfin_api_key = "key"
    cm.plex.jellyfin_user = "Ben"
    app.config_manager = cm
    return app


# --- managers ---

def test_init_builds_both_managers():
    app = _init_app("both")
    with patch("core.app.PlexManager") as plex_cls, \
            patch("core.jellyfin_api.JellyfinManager") as jf_cls:
        app._init_plex_manager()
    assert isinstance(app.plex_manager, CombinedMediaManager)
    assert list(app.plex_manager.managers) == ["plex", "jellyfin"]
    assert app.plex_manager.managers["plex"] is plex_cls.return_value
    jf_cls.assert_called_once_with("http://jf:8096", "key", "Ben")


def test_plex_only_install_unchanged():
    app = _init_app("plex")
    with patch("core.app.PlexManager") as plex_cls, \
            patch("core.jellyfin_api.JellyfinManager") as jf_cls:
        app._init_plex_manager()
    assert list(app.plex_manager.managers) == ["plex"]
    cm = app.config_manager
    plex_cls.assert_called_once_with(
        plex_url=cm.plex.plex_url,
        plex_token=cm.plex.plex_token,
        retry_limit=cm.performance.retry_limit,
        delay=cm.performance.delay,
        token_cache_file=str(cm.get_user_tokens_file()),
        rss_cache_file=str(cm.get_rss_cache_file()),
        plex_db_path=cm.plex.plex_db_path,
        watchlist_enabled=cm.cache.watchlist_toggle,
    )
    jf_cls.assert_not_called()


def test_jellyfin_only_install_unchanged():
    app = _init_app("jellyfin")
    with patch("core.app.PlexManager") as plex_cls, \
            patch("core.jellyfin_api.JellyfinManager"):
        app._init_plex_manager()
    assert list(app.plex_manager.managers) == ["jellyfin"]
    plex_cls.assert_not_called()


def test_connect_passes_only_plex_users_to_plex():
    app = _app()
    app.config_manager.plex.users_toggle = True
    app.config_manager.plex.skip_ondeck = []
    app.config_manager.plex.skip_watchlist = []
    app.config_manager.plex.users = [{"title": "Ben"}, {"title": "Ben (Jellyfin)", "server": "jellyfin"}]
    app.plex_manager.get_newly_discovered_users.return_value = []
    app._connect_to_plex()
    kwargs = app.plex_manager.load_user_tokens.call_args.kwargs
    assert kwargs["settings_users"] == [{"title": "Ben"}]


# --- removal guard ---

def test_no_eviction_or_move_back_while_a_server_is_missing(caplog):
    app = _app(missing={"jellyfin"})
    with caplog.at_level(logging.WARNING):
        assert app._can_remove_from_cache("eviction") is False
    assert "Skipping eviction - media server data incomplete (Jellyfin)" in caplog.text
    assert "Files will remain on cache until next successful run" in caplog.text


def test_watchlist_incomplete_blocks_only_with_watchlist_on(caplog):
    app = _app(watchlist_complete=False, watchlist_toggle=True)
    with caplog.at_level(logging.WARNING):
        assert app._can_remove_from_cache("array restore") is False
    assert "Skipping array restore - media server data incomplete (watchlist)" in caplog.text

    app.config_manager.cache.watchlist_toggle = False
    assert app._can_remove_from_cache("array restore") is True


def test_removal_allowed_when_all_data_complete(caplog):
    app = _app(watchlist_toggle=True)
    with caplog.at_level(logging.WARNING):
        assert app._can_remove_from_cache("eviction") is True
    assert "Skipping" not in caplog.text


def _move_app(**kw):
    app = _app(**kw)
    app.config_manager.cache.watched_move = False
    app.media_to_cache = ["/real/a.mkv"]
    app.source_map = {}
    app.sibling_map = {}
    app.timestamp_tracker = None
    app.evicted_count = 0
    app.evicted_bytes = 0
    app._run_smart_eviction = MagicMock(return_value=(0, 0))
    app._filter_low_priority_files = MagicMock(side_effect=lambda files, _: files)
    app._safe_move_files = MagicMock()
    return app


def test_move_files_skips_eviction_while_a_server_is_missing():
    app = _move_app(missing={"plex"})
    app._move_files()
    app._run_smart_eviction.assert_not_called()
    app._safe_move_files.assert_called_once_with(["/real/a.mkv"], "cache")


def test_move_files_evicts_when_data_complete():
    app = _move_app()
    app._move_files()
    app._run_smart_eviction.assert_called_once()


def _finish_app(**kw):
    app = _app(**kw)
    app.start_time = 0
    app.file_mover = None
    app.file_filter = None
    app.logging_manager = MagicMock()
    app.timestamp_tracker = None
    app.watchlist_tracker = MagicMock()
    app._log_results_summary = MagicMock()
    app._record_activity = False
    app._run_id = None
    return app


def test_watchlist_cleanup_waits_for_missing_server():
    app = _finish_app(missing={"jellyfin"})
    app._finish()
    app.watchlist_tracker.cleanup_stale_entries.assert_not_called()
    app.watchlist_tracker.cleanup_missing_files.assert_called_once()


def test_watchlist_cleanup_runs_when_data_complete():
    app = _finish_app()
    app._finish()
    app.watchlist_tracker.cleanup_stale_entries.assert_called_once()


# --- OnDeck ---

def _media_app(items, missing=()):
    app = _app(missing=missing)
    cache = app.config_manager.cache
    cache.ondeck_retention_days = 0
    cache.auto_transfer_upgrades = False
    cache.cache_associated_files = "none"
    cache.watched_move = True
    app.plex_manager.get_on_deck_media.return_value = items
    app.ondeck_tracker = MagicMock()
    app.ondeck_tracker._rating_key_index = {}
    app.file_path_modifier = MagicMock()
    app.file_path_modifier.modify_file_paths.side_effect = lambda paths: [p.replace("/data", "/real") for p in paths]
    app.media_info_map = {}
    app.source_map = {}
    app.sibling_map = {}
    app.files_to_skip = []
    app.priority_manager = None
    app.file_filter = MagicMock()
    app._process_pinned_media = MagicMock()
    app._file_needs_caching = MagicMock(return_value=True)
    app._check_files_to_move_back_to_array = MagicMock()
    return app


def _item(path, user, rk):
    return SimpleNamespace(file_path=path, username=user, episode_info=None,
                           is_current_ondeck=True, rating_key=rk)


def test_ondeck_keeps_missing_servers_users_and_only_plex_rating_keys():
    items = [_item("/data/a.mkv", "Ben", "42"), _item("/data/b.mkv", "Ben (Jellyfin)", "jf-id")]
    app = _media_app(items, missing={"jellyfin"})
    order = MagicMock()
    order.attach_mock(app.plex_manager.get_on_deck_media, "fetch")
    order.attach_mock(app.ondeck_tracker.prepare_for_run, "prepare")
    app._process_media()

    assert [c[0] for c in order.mock_calls[:2]] == ["fetch", "prepare"]
    keep = app.ondeck_tracker.prepare_for_run.call_args.kwargs["keep_user"]
    assert keep("Ann (Jellyfin)") and not keep("Ann")

    keys = {c.args[0]: c.kwargs["rating_key"] for c in app.ondeck_tracker.update_entry.call_args_list}
    assert keys == {"/real/a.mkv": "42", "/real/b.mkv": None}
    app._check_files_to_move_back_to_array.assert_not_called()


def test_ondeck_move_back_runs_when_data_complete():
    app = _media_app([_item("/data/a.mkv", "Ben", "42")])
    app._process_media()
    keep = app.ondeck_tracker.prepare_for_run.call_args.kwargs["keep_user"]
    assert not keep("Ann (Jellyfin)")
    app._check_files_to_move_back_to_array.assert_called_once()


# --- watchlist ---

def _watchlist_app(items):
    app = _app()
    cache = app.config_manager.cache
    cache.watchlist_retention_days = 0
    cache.remote_watchlist_toggle = False
    cache.cache_associated_files = "none"
    app.config_manager.plex.per_user_watchlist_days = {}
    app.config_manager.plex.users = [
        {"title": "Ben", "is_local": True},
        {"title": "Ben (Jellyfin)", "is_local": True, "server": "jellyfin"},
        {"title": "Remote"},
    ]
    app.plex_manager.get_watchlist_media.return_value = iter(items)
    app.watchlist_tracker = MagicMock()
    app.watchlist_tracker.is_expired.return_value = False
    app.file_path_modifier = MagicMock()
    convert = lambda p: (p.replace("/data", "/real"), None)
    app.file_path_modifier.convert_plex_to_real.side_effect = convert
    app.file_path_modifier.modify_file_paths.side_effect = lambda paths: [convert(p)[0] for p in paths]
    app.media_info_map = {}
    app.sibling_map = {}
    app.files_to_skip = []
    return app


def test_watchlist_uses_real_paths_plex_users_and_plex_rating_keys():
    items = [
        ("/data/a.mkv", "Ben", None, None, "42", "movie"),
        ("/data/b.mkv", "Ben (Jellyfin)", None, None, "jf-id", "movie"),
    ]
    app = _watchlist_app(items)
    result = app._process_watchlist()

    assert app.plex_manager.get_watchlist_media.call_args.kwargs["home_users"] == ["Ben"]

    rekey = app.watchlist_tracker.rekey_paths
    rekey.assert_called_once()
    assert rekey.call_args.args[0]("/data/x.mkv") == "/real/x.mkv"

    calls = app.watchlist_tracker.update_entry.call_args_list
    assert [(c.args[0], c.kwargs["rating_key"]) for c in calls] == [
        ("/real/a.mkv", "42"), ("/real/b.mkv", None)]
    assert {"/real/a.mkv", "/real/b.mkv"} <= result


def test_watchlist_rss_items_get_real_paths_and_plex_rating_keys():
    app = _watchlist_app([])
    app.config_manager.cache.remote_watchlist_toggle = True
    app.config_manager.cache.remote_watchlist_rss_url = "http://rss"
    app.config_manager.cache.watchlist_retention_days = 5
    rss = [("/data/r.mkv", "Remote", None, None, "7", "movie")]
    app.plex_manager.get_watchlist_media.side_effect = [iter([]), iter(rss)]
    app._process_watchlist()

    call = app.watchlist_tracker.update_entry.call_args
    assert call.args[0] == "/real/r.mkv" and call.kwargs["rating_key"] == "7"
    assert app.watchlist_tracker.is_expired.call_args.args[0] == "/real/r.mkv"


# --- path mappings ---

def test_path_mappings_keep_user_flags_while_conversion_shares_them():
    plex = PathMapping(name="Plex", plex_path="/data/tv", real_path="/mnt/user/tv",
                       cache_path="/mnt/cache/tv", cacheable=True, enabled=True)
    jf = PathMapping(name="Jellyfin", plex_path="/media/tv", real_path="/mnt/user/tv",
                     cache_path=None, cacheable=False, enabled=False)
    app = PlexCacheApp.__new__(PlexCacheApp)
    cm = MagicMock()
    cm.has_legacy_path_arrays.return_value = False
    cm.paths.path_mappings = [plex, jf]
    app.config_manager = cm
    app._init_path_modifier()

    # Settings keep the user's own flags (they are saved back to the settings file)
    assert cm.paths.path_mappings[0] is plex and cm.paths.path_mappings[1] is jf
    assert not jf.enabled and not jf.cacheable and jf.cache_path is None

    # Path conversion shares the folder's flags
    real, _ = app.file_path_modifier.convert_plex_to_real("/media/tv/Show/e1.mkv")
    assert real == "/mnt/user/tv/Show/e1.mkv"
    cache, _ = app.file_path_modifier.convert_real_to_cache(real)
    assert cache == "/mnt/cache/tv/Show/e1.mkv"
