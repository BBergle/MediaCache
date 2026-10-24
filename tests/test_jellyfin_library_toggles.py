"""Route test: Jellyfin libraries get their own toggles on Settings → Libraries.

Turning a Jellyfin library on creates its path mapping without typing paths:
the first few entries Jellyfin lists in the library folder are looked up under the
existing (Plex) mappings' real folders and their siblings, and the folder holding
them supplies the real + cache paths.
"""

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

from tests.test_library_paths_delete import _force_real_modules  # noqa: E402

_force_real_modules()

JF_TV = {"id": "a1b2c3", "title": "Shows", "type": "show", "type_label": "TV Shows",
         "locations": ["/data/tvshows"]}
JF_DEMOS = {"id": "d4e5f6", "title": "Demos", "type": "mixed", "type_label": "Mixed",
            "locations": ["/data/demos"]}
JF_RIPS = {"id": "g7h8i9", "title": "Rips", "type": "movie", "type_label": "Movies",
           "locations": ["/data/my-rips"]}


@pytest.fixture
def client(tmp_path):
    real_tv = tmp_path / "user/data/media/tv"
    (real_tv / "Ted Lasso (2020)/Season 2").mkdir(parents=True)
    (tmp_path / "user/data/media/My-Rips/Possession Remux").mkdir(parents=True)
    settings = {
        "PLEX_URL": "http://localhost:32400", "PLEX_TOKEN": "abc",
        "media_server": "both", "jellyfin_url": "jf:8096", "jellyfin_api_key": "k",
        "valid_sections": [26],
        "path_mappings": [
            {"name": "TV Shows", "plex_path": "/data/media/tv/", "real_path": f"{real_tv}/",
             "cache_path": "/mnt/cache/data/media/tv/", "host_cache_path": "/mnt/cache/data/media/tv/",
             "cacheable": True, "enabled": True, "section_id": 26},
        ],
        "cache_dir": "/mnt/cache",
    }
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    folder_names = {"/data/tvshows": ["Ted Lasso (2020)"], "/data/demos": ["demo.mkv"],
                    "/data/my-rips": ["Possession Remux"]}
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        from web.routers import settings as settings_router
        svc = SettingsService()
        svc._cached_settings = None
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        with patch.object(svc, "get_plex_libraries", return_value=[]), \
             patch.object(svc, "get_jellyfin_libraries", return_value=[JF_TV, JF_DEMOS, JF_RIPS]), \
             patch.object(svc, "_jellyfin_folder_names", side_effect=folder_names.get), \
             patch("web.routers.settings.get_settings_service", return_value=svc):
            yield TestClient(app), svc, real_tv


def _jf_mappings(svc, lib_id):
    return [m for m in svc._load_raw()["path_mappings"] if m.get("jellyfin_library_id") == lib_id]


def test_toggle_on_reuses_plex_folder_holding_the_same_file(client):
    test_client, svc, real_tv = client
    r = test_client.post("/settings/libraries/jellyfin/a1b2c3/toggle")
    assert r.status_code == 200

    [m] = _jf_mappings(svc, "a1b2c3")
    assert m["plex_path"] == "/data/tvshows/"
    assert m["real_path"] == f"{real_tv}/"
    assert m["cache_path"] == "/mnt/cache/data/media/tv/"
    assert m["enabled"] is True and m["cacheable"] is True
    assert "section_id" not in m
    assert svc._load_raw()["valid_sections"] == [26]  # Plex sections untouched
    assert "Jellyfin Path" in r.text


def test_toggle_on_finds_sibling_of_a_plex_folder(client):
    test_client, svc, real_tv = client
    test_client.post("/settings/libraries/jellyfin/g7h8i9/toggle")
    [m] = _jf_mappings(svc, "g7h8i9")
    assert m["real_path"] == f"{real_tv.parent}/My-Rips/"
    assert m["cache_path"] == "/mnt/cache/data/media/My-Rips/"


def test_toggle_on_without_match_falls_back_and_warns(client):
    test_client, svc, _ = client
    r = test_client.post("/settings/libraries/jellyfin/d4e5f6/toggle")

    [m] = _jf_mappings(svc, "d4e5f6")
    assert m["real_path"] == "/mnt/user/demos/"  # /data/ -> /mnt/user/ guess
    assert m["auto_fill_recognized"] is False
    assert "likely need manual correction" in r.text


def test_toggle_off_disables_and_on_again_reenables(client):
    test_client, svc, _ = client
    test_client.post("/settings/libraries/jellyfin/a1b2c3/toggle")
    test_client.post("/settings/libraries/jellyfin/a1b2c3/toggle")
    assert [m["enabled"] for m in _jf_mappings(svc, "a1b2c3")] == [False]
    test_client.post("/settings/libraries/jellyfin/a1b2c3/toggle")
    assert [m["enabled"] for m in _jf_mappings(svc, "a1b2c3")] == [True]


def test_toggle_on_adopts_unlinked_mapping_for_same_path(client):
    test_client, svc, _ = client
    raw = svc._load_raw()
    raw["path_mappings"].append({"name": "Demos", "plex_path": "/data/demos/", "real_path": "/x/",
                                 "cache_path": None, "cacheable": False, "enabled": False})
    svc._save_raw(raw)

    test_client.post("/settings/libraries/jellyfin/d4e5f6/toggle")
    [m] = _jf_mappings(svc, "d4e5f6")
    assert m["real_path"] == "/x/" and m["enabled"] is True
    assert len(svc._load_raw()["path_mappings"]) == 2


def test_libraries_page_lists_jellyfin_cards_not_as_custom_mappings(client):
    test_client, svc, _ = client
    test_client.post("/settings/libraries/jellyfin/a1b2c3/toggle")
    r = test_client.get("/settings/libraries")
    assert "Jellyfin Libraries" in r.text
    assert "/settings/libraries/jellyfin/d4e5f6/toggle" in r.text
    assert 'id="orphan-mappings"' not in r.text  # linked Jellyfin mapping isn't "custom"


def test_edit_paths_keeps_jellyfin_link(client):
    test_client, svc, _ = client
    test_client.post("/settings/libraries/jellyfin/a1b2c3/toggle")
    r = test_client.put("/settings/libraries/jellyfin/a1b2c3/paths", data={
        "name_0": "Shows", "plex_path_0": "/data/tvshows/", "real_path_0": "/mnt/user/tv/",
        "cache_path_0": "/mnt/cache/tv/", "cacheable_0": "on"})
    assert r.status_code == 200
    [m] = _jf_mappings(svc, "a1b2c3")
    assert m["real_path"] == "/mnt/user/tv/"


def test_core_config_save_keeps_jellyfin_library_id(tmp_path):
    from core.config import ConfigManager
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"cache_dir": "/mnt/cache", "path_mappings": [
        {"name": "Shows", "plex_path": "/data/tvshows/", "real_path": "/mnt/user/tv/",
         "jellyfin_library_id": "a1b2c3"}]}))
    cm = ConfigManager(str(path))
    cm.settings_data = json.loads(path.read_text())
    cm._load_path_config()
    cm._save_updated_config()
    assert json.loads(path.read_text())["path_mappings"][0]["jellyfin_library_id"] == "a1b2c3"

