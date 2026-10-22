"""The Recently Added display settings must survive a Cache-tab save.

`recently_added_days` / `recently_added_max_items` were originally wired into
`save_cache_settings()` only. The Cache tab renders them from
`get_cache_settings()`, so the read side omitting them made the form show a
default and write that default back over the user's stored value on the next
save of any unrelated Cache field.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('fcntl', MagicMock())
sys.modules.setdefault('apscheduler', MagicMock())
sys.modules.setdefault('apscheduler.schedulers', MagicMock())
sys.modules.setdefault('apscheduler.schedulers.background', MagicMock())
sys.modules.setdefault('apscheduler.triggers', MagicMock())
sys.modules.setdefault('apscheduler.triggers.cron', MagicMock())
sys.modules.setdefault('apscheduler.triggers.interval', MagicMock())
sys.modules.setdefault('plexapi', MagicMock())
sys.modules.setdefault('plexapi.server', MagicMock())


@pytest.fixture
def service(tmp_path):
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    with patch("web.services.settings_service.SETTINGS_FILE", settings_file), \
         patch("web.services.settings_service.DATA_DIR", tmp_path):
        from web.services.settings_service import SettingsService
        svc = SettingsService()
        yield svc


def _store(service, data):
    with open(service.settings_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    service._cached_settings = None


class TestRecentlyAddedSettingsRoundTrip:
    def test_get_cache_settings_reads_stored_values(self, service):
        _store(service, {"recently_added_days": 30, "recently_added_max_items": 400})
        cache = service.get_cache_settings()
        assert cache["recently_added_days"] == 30
        assert cache["recently_added_max_items"] == 400

    def test_defaults_match_the_router(self, service):
        _store(service, {})
        cache = service.get_cache_settings()
        from web.routers.recently_added import DEFAULT_DAYS, DEFAULT_MAX_ITEMS
        assert cache["recently_added_days"] == DEFAULT_DAYS
        assert cache["recently_added_max_items"] == DEFAULT_MAX_ITEMS

    def test_saving_an_unrelated_cache_field_preserves_them(self, service):
        _store(service, {"recently_added_days": 30, "recently_added_max_items": 400})

        # Simulate the Cache tab round-trip: the form is populated from
        # get_cache_settings(), the user edits one unrelated field, and the
        # whole form posts back.
        form = service.get_cache_settings()
        form["cache_limit"] = "500GB"
        service.save_cache_settings(form)

        service._cached_settings = None
        reloaded = service.get_cache_settings()
        assert reloaded["cache_limit"] == "500GB"
        assert reloaded["recently_added_days"] == 30
        assert reloaded["recently_added_max_items"] == 400
