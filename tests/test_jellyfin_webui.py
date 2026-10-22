"""Route test: choosing Jellyfin in Settings > Plex and in setup wizard step 2.

Mounts the settings and setup routers on a minimal app backed by a real
SettingsService over a temp settings file, with Jellyfin's HTTP calls mocked.
"""

import json
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
        "media_server": "jellyfin", "plex_url": "http://plex:32400", "plex_token": "t",
        "jellyfin_url": " http://jf:8096 ", "jellyfin_api_key": "key", "jellyfin_user": ""})
    assert r.status_code == 200
    raw = svc._load_raw()
    assert (raw["media_server"], raw["jellyfin_url"], raw["PLEX_URL"]) == \
           ("jellyfin", "http://jf:8096", "http://plex:32400")

    page = test_client.get("/settings/plex").text
    assert '<option value="jellyfin" selected>' in page and 'value="http://jf:8096"' in page
    assert "Connected to Jellyfin as admin" in test_client.post("/settings/plex/test").text
    assert "</i> Media Server </a>" in " ".join(page.split())  # tab is no longer "Plex Server"

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
