"""Route test: choosing Jellyfin in Settings > Plex and in setup wizard step 2.

Mounts the settings and setup routers on a minimal app backed by a real
SettingsService over a temp settings file, with Jellyfin's HTTP calls mocked.
"""

import json
import re
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests
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

JELLYFIN_ROUTES = {
    "/System/Info": {"Version": "10.10.0"},
    "/Users": [{"Name": "admin", "Id": "u1", "Policy": {"IsAdministrator": True}}],
    "/Library/VirtualFolders": [{"Name": "Movies", "ItemId": "abc",
                                 "CollectionType": "movies", "Locations": ["/media/movies"]}],
}


@pytest.fixture
def client(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps({"PLEX_URL": "http://plex:32400", "PLEX_TOKEN": "t"}))
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.routers import settings as settings_router, setup as setup_router
        from web.services.settings_service import SettingsService
        svc = SettingsService()
        svc._cached_settings = None
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        app.include_router(setup_router.router)
        with patch("web.routers.settings.get_settings_service", return_value=svc), \
             patch("web.routers.setup.get_settings_service", return_value=svc), \
             patch("core.jellyfin_api.JellyfinManager._get",
                   lambda self, path, **params: JELLYFIN_ROUTES[path]):
            setup_router.clear_setup_state()
            yield TestClient(app), svc, setup_router
            setup_router.clear_setup_state()


def test_settings_tab_saves_and_tests_jellyfin(client):
    test_client, svc, _ = client
    r = test_client.put("/settings/plex", data={
        "jellyfin_enabled": "on", "plex_url": "http://plex:32400", "plex_token": "t",
        "jellyfin_url": " http://jf:8096 ", "jellyfin_api_key": "key", "jellyfin_user": ""})
    assert r.status_code == 200
    raw = svc._load_raw()
    assert (raw["media_server"], raw["jellyfin_url"], raw["PLEX_URL"]) == \
           ("jellyfin", "http://jf:8096", "http://plex:32400")

    page = test_client.get("/settings/plex").text
    assert re.search(r'name="jellyfin_enabled"[^>]*checked', page) and 'value="http://jf:8096"' in page
    assert not re.search(r'name="plex_enabled"[^>]*checked', page)
    assert "Connected to Jellyfin as admin" in test_client.post("/settings/plex/test").text
    assert "</i> Media Servers </a>" in " ".join(page.split())  # tab is no longer "Plex Server"

    # Dashboard "connected" badges follow the configured server, not Plex
    assert svc.check_plex_connection() is True
    with patch("core.jellyfin_api.JellyfinManager._get", side_effect=requests.ConnectionError("down")):
        assert svc.check_plex_connection() is False


def test_wizard_step2_jellyfin_feeds_step3_libraries(client):
    test_client, _, setup_router = client
    r = test_client.post("/setup/step2", data={
        "media_server": "jellyfin", "jellyfin_url": "http://jf:8096", "jellyfin_api_key": "key"},
        follow_redirects=False)
    assert r.headers["location"] == "/setup?step=3"
    assert setup_router.get_setup_state()["media_server"] == "jellyfin"

    assert "Movies" in test_client.get("/setup?step=3").text
    test_client.post("/setup/step3", data={"libraries": "abc", "library_cacheable_abc": "on"},
                     follow_redirects=False)
    state = setup_router.get_setup_state()
    assert state["valid_sections"] == ["abc"]
    assert state["path_mappings"][0]["plex_path"] == "/media/movies/"

    # Step 4 drops the Plex-only user list and RSS watchlist prompt
    step4 = test_client.get("/setup?step=4").text
    assert "Every Jellyfin user" in step4 and "rss.plex.tv" not in step4

    # Bad credentials re-render step 2 with the error, Jellyfin still selected
    with patch("core.jellyfin_api.JellyfinManager._get", side_effect=requests.ConnectionError("down")):
        r = test_client.post("/setup/step2", data={"media_server": "jellyfin", "jellyfin_url": "http://jf"})
    assert "Cannot connect to Jellyfin" in r.text and '<option value="jellyfin" selected>' in r.text


BOTH_FORM = {"plex_enabled": "on", "jellyfin_enabled": "on", "plex_url": "http://plex:32400",
             "plex_token": "t", "jellyfin_url": "http://jf:8096", "jellyfin_api_key": "key",
             "jellyfin_user": ""}


def test_both_servers_save_and_status(client):
    test_client, svc, _ = client
    test_client.put("/settings/plex", data=BOTH_FORM)
    assert svc._load_raw()["media_server"] == "both"
    page = test_client.get("/settings/plex").text
    assert re.search(r'name="plex_enabled"[^>]*checked', page)
    assert re.search(r'name="jellyfin_enabled"[^>]*checked', page)

    with patch("requests.get", return_value=MagicMock(status_code=200)):
        assert svc.check_media_server_connections() == {"plex": True, "jellyfin": True}
    with patch("requests.get", side_effect=requests.ConnectionError("down")):
        assert svc.check_media_server_connections() == {"plex": False, "jellyfin": True}
        assert svc.check_plex_connection() is False

    from web.config import _media_server_name
    with patch("web.services.settings_service.get_settings_service", return_value=svc):
        assert _media_server_name() == "Plex and Jellyfin"


def test_save_rejects_no_servers(client):
    test_client, svc, _ = client
    r = test_client.put("/settings/plex", data={"plex_url": "http://other:32400"})
    assert "Turn on at least one media server." in r.text
    raw = svc._load_raw()
    assert "media_server" not in raw and raw["PLEX_URL"] == "http://plex:32400"


def test_test_connection_reports_each_server(client):
    test_client, svc, _ = client
    test_client.put("/settings/plex", data=BOTH_FORM)
    with patch("plexapi.server.PlexServer", side_effect=Exception("Connection refused")):
        text = test_client.post("/settings/plex/test").text
    assert "Plex: " in text and "Jellyfin: Connected to Jellyfin as admin" in text and "alert-error" in text


def test_setup_complete_needs_every_enabled_server(client):
    _, svc, setup_router = client
    raw = svc._load_raw()
    raw.update(media_server="both")
    svc._save_raw(raw)
    assert setup_router.is_setup_complete() is False
    raw.update(jellyfin_url="http://jf", jellyfin_api_key="k")
    svc._save_raw(raw)
    assert setup_router.is_setup_complete() is True


def test_tab_test_checks_only_its_server_with_typed_values(client):
    test_client, svc, _ = client
    test_client.put("/settings/plex", data=BOTH_FORM)
    with patch("plexapi.server.PlexServer") as plex:
        text = test_client.post("/settings/plex/test", data={
            **BOTH_FORM, "server": "plex", "plex_url": "http://typed:32400"}).text
    assert plex.call_args.args[0] == "http://typed:32400"
    assert "alert-success" in text and "Jellyfin" not in text
