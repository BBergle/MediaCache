"""Users page: Plex and Jellyfin users side by side."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.modules.setdefault('fcntl', MagicMock())
for _mod in [
    'apscheduler', 'apscheduler.schedulers', 'apscheduler.schedulers.background',
    'apscheduler.triggers', 'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server',
]:
    sys.modules.setdefault(_mod, MagicMock())

for _name in ["web.config", "web.routers", "web.routers.settings", "web.routers.setup",
              "web.services", "web.services.settings_service", "web"]:
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

JF_USERS = [{"Name": "admin", "Id": "u1", "Policy": {"IsAdministrator": True}},
            {"Name": "kid", "Id": "u2", "Policy": {"IsAdministrator": False}}]
BEN = {"title": "Ben", "is_admin": True, "is_local": True, "token": "t",
       "skip_ondeck": False, "skip_watchlist": False}
JF_BEN = {"title": "Ben (Jellyfin)", "server": "jellyfin", "id": "j1", "is_local": True,
          "is_admin": False, "skip_ondeck": True, "skip_watchlist": False}


def make(tmp_path, extra):
    f = tmp_path / "plexcache_settings.json"
    f.write_text(json.dumps({"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t",
                             "jellyfin_url": "http://jf:8096", "jellyfin_api_key": "k",
                             "jellyfin_user": "admin", **extra}))
    return f


@pytest.fixture
def env(tmp_path):
    def build(extra):
        f = make(tmp_path, extra)
        p1 = patch("web.services.settings_service.SETTINGS_FILE", f)
        p2 = patch("web.services.settings_service.DATA_DIR", tmp_path)
        p1.start(); p2.start()
        from web.routers import settings as settings_router
        from web.services.settings_service import SettingsService
        svc = SettingsService()
        svc._cached_settings = None
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        p3 = patch("web.routers.settings.get_settings_service", return_value=svc)
        p4 = patch("core.jellyfin_api.JellyfinManager._get",
                   lambda self, path, **params: JF_USERS if path == "/Users" else {})
        p3.start(); p4.start()
        env.stop = [p1, p2, p3, p4]
        return TestClient(app), svc
    yield build
    for p in getattr(env, "stop", []):
        p.stop()


def test_jellyfin_sync_adds_labelled_users_and_keeps_plex(env):
    _, svc = env({"media_server": "both", "users": [dict(BEN)]})
    r = svc.sync_users_from_jellyfin()
    assert r["success"] and r["added_count"] == 3 - 1
    users = svc._load_raw()["users"]
    assert {u["title"] for u in users} == {"Ben", "admin (Jellyfin)", "kid (Jellyfin)"}
    assert next(u for u in users if u["title"] == "Ben") == BEN
    kid = next(u for u in users if u["title"] == "kid (Jellyfin)")
    assert kid["server"] == "jellyfin" and kid["id"] == "u2" and not kid["is_admin"]


def test_plex_sync_keeps_jellyfin_users(env):
    _, svc = env({"media_server": "both", "users": [dict(BEN), {**JF_BEN, "title": "kid (Jellyfin)"}],
                  "skip_ondeck": ["kid (Jellyfin)"]})
    account = MagicMock(title="Ben", username="Ben", id=1, uuid="x")
    account.users.return_value = []
    with patch("plexapi.server.PlexServer") as ps:
        ps.return_value.myPlexAccount.return_value = account
        r = svc.sync_users_from_plex()
    assert r["success"] and r["removed_count"] == 0
    raw = svc._load_raw()
    assert {u["title"] for u in raw["users"]} == {"Ben", "kid (Jellyfin)"}
    assert raw["skip_ondeck"] == ["kid (Jellyfin)"]


def test_same_name_users_stay_separate(env):
    _, svc = env({"media_server": "both", "users": [dict(BEN), dict(JF_BEN)]})
    JF_USERS.append({"Name": "Ben", "Id": "j1", "Policy": {}})
    try:
        svc.sync_users_from_jellyfin()
    finally:
        JF_USERS.pop()
    raw = svc._load_raw()
    assert raw["skip_ondeck"] == ["Ben (Jellyfin)"]
    assert next(u for u in raw["users"] if u["title"] == "Ben")["skip_ondeck"] is False


def test_sync_users_combines_results(env):
    _, svc = env({"media_server": "both", "users": []})
    with patch("plexapi.server.PlexServer", side_effect=Exception("boom")):
        r = svc.sync_users()
    assert r["success"] and "boom" in r["error"] and r["added_count"] == 2


def test_save_users_round_trips_jellyfin_titles(env):
    client, svc = env({"media_server": "both", "users": [dict(BEN), dict(JF_BEN)]})
    r = client.put("/settings/users", data={
        "include_ondeck_Ben": "on", "include_watchlist_Ben": "on",
        "include_watchlist_Ben (Jellyfin)": "on"})
    assert r.status_code == 200
    users = {u["title"]: u for u in svc._load_raw()["users"]}
    assert users["Ben (Jellyfin)"]["skip_ondeck"] is True
    assert users["Ben (Jellyfin)"]["skip_watchlist"] is False
    assert users["Ben"]["skip_ondeck"] is False


def test_users_list_hides_disabled_server_users(env):
    client, svc = env({"media_server": "plex", "users": [dict(BEN), dict(JF_BEN)]})
    page = client.get("/settings/users/list").text
    assert "(Jellyfin)" not in page and "Ben" in page
    client.put("/settings/users", data={"include_ondeck_Ben": "on"})
    users = {u["title"]: u for u in svc._load_raw()["users"]}
    assert users["Ben (Jellyfin)"]["skip_ondeck"] is True  # untouched while hidden


def test_jellyfin_users_have_no_token_panel(env):
    client, _ = env({"media_server": "both", "users": [dict(BEN), dict(JF_BEN)]})
    page = client.get("/settings/users/list").text
    assert "user_token_Ben (Jellyfin)" not in page


def test_sync_hides_disabled_server_users(env):
    client, svc = env({"media_server": "plex", "users": [dict(BEN), dict(JF_BEN)]})
    account = MagicMock(title="Ben", username="Ben", id=1, uuid="x")
    account.users.return_value = []
    with patch("plexapi.server.PlexServer") as ps:
        ps.return_value.myPlexAccount.return_value = account
        page = client.post("/settings/users/sync").text
    assert "Synced 1 users" in page and "(Jellyfin)" not in page


def test_sync_shows_error_of_failed_server(env):
    client, _ = env({"media_server": "both", "users": []})
    with patch("plexapi.server.PlexServer", side_effect=Exception("plex down")):
        page = client.post("/settings/users/sync").text
    assert "kid (Jellyfin)" in page and "plex down" in page
