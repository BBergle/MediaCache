"""Phase 7 coverage for MaintenanceService.cache_pinned() early-return branches.

The full copy path (array → cache rename, exclude-list update, timestamp
write) is exercised indirectly via the broader maintenance integration
tests. Here we lock the three short-circuit paths so regressions don't
silently hang on empty inputs.
"""

from unittest.mock import patch

import pytest


def _make_service(tmp_path, pinned_paths=None):
    """Build a MaintenanceService with its pinned-cache-path helper stubbed."""
    from web.services.maintenance_service import MaintenanceService

    svc = MaintenanceService.__new__(MaintenanceService)
    svc._get_pinned_cache_paths = lambda: set(pinned_paths or [])
    svc._cache_to_array_path = lambda cache_path: cache_path.replace("/mnt/cache/", "/mnt/user/")
    return svc


class TestCachePinnedEarlyReturns:
    def test_no_pinned_returns_success_with_zero_affected(self, tmp_path):
        svc = _make_service(tmp_path, pinned_paths=set())
        result = svc.cache_pinned(dry_run=False)
        assert result.success is True
        assert result.affected_count == 0
        assert "No pinned media" in result.message

    def test_all_pinned_already_on_cache_short_circuits(self, tmp_path):
        # Create a real cache file so os.path.exists returns True
        cache_file = tmp_path / "already_cached.mkv"
        cache_file.write_bytes(b"x" * 1024)
        svc = _make_service(tmp_path, pinned_paths={str(cache_file)})

        result = svc.cache_pinned(dry_run=False)
        assert result.success is True
        assert result.affected_count == 0
        assert "already on cache" in result.message

    def test_dry_run_reports_missing_count(self, tmp_path):
        # Missing cache file → dry_run reports the would-cache count without
        # touching disk.
        missing_path = str(tmp_path / "not_yet.mkv")
        svc = _make_service(tmp_path, pinned_paths={missing_path})

        result = svc.cache_pinned(dry_run=True)
        assert result.success is True
        assert result.affected_count == 1
        assert "Would cache 1" in result.message


class TestCachePinnedRoute:
    """Route-level coverage: /maintenance/cache-pinned pre-resolves the
    missing count so the banner can show 'Caching N file(s)…' instead of 0."""

    def test_route_returns_info_alert_when_nothing_missing(self, tmp_path):
        """When every pinned path is already on cache, the route must return
        an inline info alert — NOT kick off a no-op background action."""
        # Build a service with one pinned path that exists on disk
        cache_file = tmp_path / "cached.mkv"
        cache_file.write_bytes(b"x")
        fake_svc = _make_service(tmp_path, pinned_paths={str(cache_file)})

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.routers import maintenance as maint_router

        app = FastAPI()
        app.include_router(maint_router.router, prefix="/maintenance")

        with patch("web.routers.maintenance.get_maintenance_service", return_value=fake_svc):
            with TestClient(app) as client:
                r = client.post("/maintenance/cache-pinned")
                assert r.status_code == 200
                assert "All pinned media is already on cache" in r.text
                # Verify it auto-dismisses (shared app.js handler needs the class)
                assert "alert-auto-dismiss" in r.text
