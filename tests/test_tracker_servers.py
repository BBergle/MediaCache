"""Trackers with two media servers: a down server's users survive; watchlist keyed by real path."""
import json
from datetime import datetime

from core.config import PathMapping
from core.file_operations import MultiPathModifier, OnDeckTracker, WatchlistTracker


def test_missing_server_users_survive_a_run(tmp_path):
    t = OnDeckTracker(str(tmp_path / "ondeck.json"))
    t.prepare_for_run(); t.update_entry("/m/a.mkv", "Ben", is_current_ondeck=True)
    t.update_entry("/m/a.mkv", "Ben (Jellyfin)", is_current_ondeck=True)
    t.update_entry("/m/b.mkv", "Kim (Jellyfin)"); t.cleanup_unseen()
    first = t.get_entry("/m/a.mkv")["user_first_seen"]["Ben (Jellyfin)"]

    t.prepare_for_run(keep_user=lambda u: u.endswith(" (Jellyfin)"))  # Jellyfin down
    t.update_entry("/m/a.mkv", "Ben"); t.cleanup_unseen()
    a, b = t.get_entry("/m/a.mkv"), t.get_entry("/m/b.mkv")
    assert set(a["users"]) == {"Ben", "Ben (Jellyfin)"} and "Ben (Jellyfin)" in a["ondeck_users"]
    assert a["user_first_seen"]["Ben (Jellyfin)"] == first
    assert b is not None and b["users"] == ["Kim (Jellyfin)"]


def test_without_keep_user_unseen_entries_still_go(tmp_path):
    t = OnDeckTracker(str(tmp_path / "ondeck.json"))
    t.prepare_for_run()
    t.update_entry("/m/a.mkv", "Ben"); t.update_entry("/m/b.mkv", "Kim (Jellyfin)")
    t.prepare_for_run(); t.update_entry("/m/a.mkv", "Ben"); t.cleanup_unseen()
    assert t.get_entry("/m/a.mkv") is not None
    assert "/m/b.mkv" not in t._data


def test_same_episode_from_two_servers_is_one_entry(tmp_path):
    mods = MultiPathModifier([
        PathMapping(name="plex", plex_path="/data/tv/", real_path="/mnt/user/tv/", cache_path="/mnt/cache/tv/"),
        PathMapping(name="jf", plex_path="/media/tv/", real_path="/mnt/user/tv/", cache_path="/mnt/cache/tv/"),
    ])
    t = OnDeckTracker(str(tmp_path / "ondeck.json"))
    t.prepare_for_run()
    for raw, user in (("/data/tv/S/e1.mkv", "Ben"), ("/media/tv/S/e1.mkv", "Ben (Jellyfin)")):
        t.update_entry(mods.convert_plex_to_real(raw)[0], user)
    assert list(t._data) == ["/mnt/user/tv/S/e1.mkv"]
    assert set(t._data["/mnt/user/tv/S/e1.mkv"]["users"]) == {"Ben", "Ben (Jellyfin)"}


def test_watchlist_rekey_merges_raw_and_real_paths(tmp_path):
    f = tmp_path / "wl.json"
    t = WatchlistTracker(str(f))
    t.update_entry("/data/tv/x.mkv", "Ben", datetime(2026, 1, 1))
    t.update_entry("/mnt/user/tv/x.mkv", "Kim (Jellyfin)", datetime(2026, 2, 1))
    conv = lambda p: p.replace("/data/tv", "/mnt/user/tv")
    assert t.rekey_paths(conv) == 1
    assert list(t._data) == ["/mnt/user/tv/x.mkv"]
    e = t._data["/mnt/user/tv/x.mkv"]
    assert set(e["users"]) == {"Ben", "Kim (Jellyfin)"}
    assert e["watchlisted_at"] == datetime(2026, 2, 1).isoformat()
    assert list(json.loads(f.read_text())) == ["/mnt/user/tv/x.mkv"]
    assert t.rekey_paths(conv) == 0
