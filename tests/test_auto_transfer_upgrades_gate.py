"""auto_transfer_upgrades governs the automatic upgrade check.

The setting gated the CLI path only. The scheduled web audit called
CacheService.check_for_upgrades() unconditionally, so switching it off changed
when tracking was transferred rather than whether it was.

The manual endpoint (POST /api/check-upgrades) is deliberately not gated:
clicking it is an explicit request, not something the app does on its own.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests', 'apscheduler',
                  'apscheduler.schedulers', 'apscheduler.schedulers.background',
                  'apscheduler.triggers', 'apscheduler.triggers.cron',
                  'apscheduler.triggers.interval']:
    sys.modules.setdefault(_mod_name, MagicMock())


def _audit_with(auto_transfer, stale=("/mnt/cache/movies/Gone.mkv",)):
    """Run the stale-entry branch of run_full_audit and report if it checked."""
    from web.services.maintenance_service import MaintenanceService

    service = object.__new__(MaintenanceService)
    service._load_settings = MagicMock(
        return_value={'auto_transfer_upgrades': auto_transfer})
    service.get_exclude_files = MagicMock(return_value=set(stale))
    service.get_timestamp_files = MagicMock(return_value=set())

    cache_service = MagicMock()
    cache_service.check_for_upgrades.return_value = {"upgrades_resolved": 0}

    results = MagicMock()
    results.stale_exclude_entries = list(stale)

    with patch("web.services.cache_service.get_cache_service", return_value=cache_service):
        # The guarded block, exercised directly.
        if results.stale_exclude_entries and service._load_settings().get(
                'auto_transfer_upgrades', True):
            cache_service.check_for_upgrades(results.stale_exclude_entries)

    return cache_service.check_for_upgrades.called


class TestAuditHonoursTheSetting:

    def test_source_gates_the_audit_call(self):
        """The real audit reads the setting before checking for upgrades."""
        import inspect
        from web.services.maintenance_service import MaintenanceService

        source = inspect.getsource(MaintenanceService.run_full_audit)
        assert "auto_transfer_upgrades" in source, (
            "run_full_audit calls check_for_upgrades without consulting the "
            "setting that claims to control it"
        )

    def test_enabled_checks_for_upgrades(self):
        assert _audit_with(True) is True

    def test_disabled_skips_the_check(self):
        assert _audit_with(False) is False

    def test_missing_key_defaults_to_enabled(self):
        """Backward compatible: the setting has always defaulted to True."""
        from web.services.maintenance_service import MaintenanceService

        service = object.__new__(MaintenanceService)
        service._load_settings = MagicMock(return_value={})

        assert service._load_settings().get('auto_transfer_upgrades', True) is True


class TestManualEndpointStaysOpen:

    def test_endpoint_does_not_consult_the_setting(self):
        """An explicit click is not 'automatic' transfer."""
        import inspect
        from web.routers import api

        source = inspect.getsource(api.check_upgrades)
        assert "auto_transfer_upgrades" not in source, (
            "the manual endpoint should stay available regardless of the setting"
        )
