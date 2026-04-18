"""Phase 3 route tests for web/routers/pinned.py.

Exercises each endpoint end-to-end using a minimal FastAPI app that mounts
only the pinned router. The PinnedService singleton is monkeypatched with
a fake that bypasses Plex / the tracker entirely — route tests only verify
that wiring, parameter validation, partials, and status codes are correct.

Test isolation: some earlier tests (``test_activity_feed``,
``test_auth_service``, ``test_eviction_safety``) replace ``web.config`` in
``sys.modules`` with a ``MagicMock``. That poisons template rendering here
because ``templates.TemplateResponse`` becomes a MagicMock return value
instead of a real ``TemplateResponse``. We force-reload the affected modules
so route tests always run against the real ``Jinja2Templates`` instance.
"""

import json
import os
import sys
import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# conftest.py handles fcntl/apscheduler/plexapi mocks and sys.path setup


def _force_real_web_config():
    """Drop mocked web.config / web.routers.pinned and re-import for real."""
    mocked_names = [
        "web.config",
        "web.routers.pinned",
        "web.routers",
        "web",
    ]
    for name in mocked_names:
        mod = sys.modules.get(name)
        if isinstance(mod, MagicMock):
            del sys.modules[name]
    # Re-import the real modules
    import web  # noqa: F401
    import web.config  # noqa: F401
    import web.routers.pinned  # noqa: F401


_force_real_web_config()


class _FakePinnedService:
    def __init__(self):
        self._pins = {}  # rating_key -> dict
        self.search_results = []
        self.children = []
        self.toggle_error = None

    def search(self, q, limit=25):
        results = []
        for r in self.search_results:
            if not q or q.lower() in r.get("title", "").lower():
                results.append({
                    **r,
                    "already_pinned": r["rating_key"] in self._pins,
                })
        return results[:limit]

    def expand(self, rating_key, level):
        if level not in ("show", "season"):
            raise ValueError(f"Unknown level: {level}")
        return [
            {
                **c,
                "already_pinned": c["rating_key"] in self._pins,
            }
            for c in self.children
        ]

    def toggle_pin(self, rating_key, pin_type, title):
        if self.toggle_error:
            return {"is_pinned": False, "error": self.toggle_error, "budget": {}}
        if rating_key in self._pins:
            del self._pins[rating_key]
            return {"is_pinned": False, "error": None, "budget": {}}
        self._pins[rating_key] = {"type": pin_type, "title": title}
        return {"is_pinned": True, "error": None, "budget": {}}

    def list_pins_with_metadata(self):
        return [
            {
                "rating_key": rk,
                "type": p["type"],
                "title": p["title"],
                "added_at": "",
                "added_by": "web",
                "resolved_file_count": 1,
                "size_bytes": 0,
                "size_display": "0 B",
                "budget_percent": 0,
            }
            for rk, p in self._pins.items()
        ]

    def list_pins_grouped(self):
        # Route-test stub: one group per pin, matches shape used by the template.
        groups = []
        for rk, p in self._pins.items():
            groups.append({
                "group_rating_key": rk,
                "group_title": p["title"],
                "group_type": "show" if p["type"] in ("show", "season", "episode") else "movie",
                "pin_count": 1,
                "group_bytes": 0,
                "group_size_display": "0 B",
                "pins": [{
                    "rating_key": rk,
                    "type": p["type"],
                    "title": p["title"],
                    "scope_text": p["title"],
                    "scope_icon": "film",
                    "size_bytes": 0,
                    "size_display": "0 B",
                    "budget_percent": 0,
                    "sort_key": (0, 0, 0),
                }],
            })
        return groups

    def unpin_many(self, rating_keys):
        removed = 0
        for rk in rating_keys:
            if rk in self._pins:
                del self._pins[rk]
                removed += 1
        return {
            "removed": removed,
            "evict_paths": [],
            "budget": self.budget_check(),
        }

    def budget_check(self, additional_rating_key=None, additional_pin_type=None):
        return {
            "total_pinned_bytes": 0,
            "budget_bytes": 0,
            "effective_budget_bytes": 0,
            "headroom_bytes": 0,
            "additional_bytes": 0,
            "over_budget": False,
            "would_exceed": False,
        }


@pytest.fixture
def fake_service():
    return _FakePinnedService()


@pytest.fixture
def client(fake_service):
    """Build a minimal FastAPI app mounting only the pinned router."""
    from web.routers import pinned as pinned_router
    app = FastAPI()
    app.include_router(pinned_router.router, prefix="/api/pinned")

    with patch("web.routers.pinned.get_pinned_service", return_value=fake_service):
        yield TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/pinned/search
# ---------------------------------------------------------------------------


class TestSearchRoute:
    def test_empty_query_returns_empty_partial(self, client, fake_service):
        r = client.get("/api/pinned/search?q=")
        assert r.status_code == 200
        assert "Type to search" in r.text or "No results" in r.text or "pinned-search-results" in r.text

    def test_returns_result_rows(self, client, fake_service):
        fake_service.search_results = [
            {"rating_key": "1", "title": "Matrix", "type": "movie", "year": 1999, "library": "Movies"},
            {"rating_key": "2", "title": "Breaking Bad", "type": "show", "year": 2008, "library": "TV"},
        ]
        r = client.get("/api/pinned/search?q=m")
        assert r.status_code == 200
        # Both rows should appear (query "m" matches both titles case-insensitively)
        assert "Matrix" in r.text
        assert 'data-rating-key="1"' in r.text

    def test_limit_capped(self, client, fake_service):
        # Limit param is accepted but hard-capped to 50 via the route Query(...)
        r = client.get("/api/pinned/search?q=x&limit=25")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/pinned/expand
# ---------------------------------------------------------------------------


class TestExpandRoute:
    def test_show_level_returns_children(self, client, fake_service):
        fake_service.children = [
            {"rating_key": "20", "title": "Season 1", "type": "season", "episode_count": 10,
             "parent_rating_key": "10"},
        ]
        r = client.get("/api/pinned/expand?rating_key=10&level=show")
        assert r.status_code == 200
        assert "Season 1" in r.text
        assert 'data-rating-key="20"' in r.text

    def test_season_level_returns_episodes(self, client, fake_service):
        fake_service.children = [
            {"rating_key": "100", "title": "Pilot", "type": "episode", "index": 1,
             "season_number": 1, "parent_rating_key": "20"},
        ]
        r = client.get("/api/pinned/expand?rating_key=20&level=season")
        assert r.status_code == 200
        assert "Pilot" in r.text

    def test_invalid_level_returns_422(self, client):
        r = client.get("/api/pinned/expand?rating_key=10&level=movie")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/pinned/toggle
# ---------------------------------------------------------------------------


class TestToggleRoute:
    def test_toggle_adds_pin_returns_200(self, client, fake_service):
        r = client.post("/api/pinned/toggle", data={
            "rating_key": "1",
            "pin_type": "movie",
            "title": "Matrix",
        })
        assert r.status_code == 200
        assert "Unpin" in r.text  # The pin is now on, button says Unpin
        assert "1" in fake_service._pins

    def test_toggle_idempotent_double_click(self, client, fake_service):
        r1 = client.post("/api/pinned/toggle", data={
            "rating_key": "1", "pin_type": "movie", "title": "Matrix",
        })
        r2 = client.post("/api/pinned/toggle", data={
            "rating_key": "1", "pin_type": "movie", "title": "Matrix",
        })
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert "1" not in fake_service._pins  # Second click removed it

    def test_budget_overrun_returns_400(self, client, fake_service):
        fake_service.toggle_error = "Pinning this item would exceed the cache budget"
        r = client.post("/api/pinned/toggle", data={
            "rating_key": "1", "pin_type": "movie", "title": "Matrix",
        })
        assert r.status_code == 400
        assert "Cannot pin" in r.text or "budget" in r.text.lower()


# ---------------------------------------------------------------------------
# GET /api/pinned/list
# ---------------------------------------------------------------------------


class TestListRoute:
    def test_empty_list_renders_placeholder(self, client):
        r = client.get("/api/pinned/list")
        assert r.status_code == 200
        assert "No pinned media" in r.text

    def test_populated_list_shows_chips(self, client, fake_service):
        fake_service._pins = {
            "1": {"type": "movie", "title": "Matrix"},
            "2": {"type": "show", "title": "Breaking Bad"},
        }
        r = client.get("/api/pinned/list")
        assert r.status_code == 200
        assert "Matrix" in r.text
        assert "Breaking Bad" in r.text


# ---------------------------------------------------------------------------
# POST /api/pinned/unpin-group
# ---------------------------------------------------------------------------


class TestUnpinGroupRoute:
    """Phase 7 bulk-unpin: one POST carrying N rating_keys → single diff,
    one background eviction, one HX-Trigger event."""

    def test_unpin_group_removes_all_keys(self, client, fake_service):
        fake_service._pins = {
            "1": {"type": "episode", "title": "S01E01"},
            "2": {"type": "episode", "title": "S01E02"},
            "3": {"type": "episode", "title": "S01E03"},
        }
        # httpx encodes dict-with-list as repeated form fields
        r = client.post(
            "/api/pinned/unpin-group",
            data={"rating_keys": ["1", "2", "3"]},
        )
        assert r.status_code == 200
        # Response is an inline info alert summarizing the removal count
        assert "Unpinned 3" in r.text
        # Fake service mirrors the unpin_many behaviour — all three gone
        assert fake_service._pins == {}

    def test_unpin_group_emits_hx_trigger(self, client, fake_service):
        fake_service._pins = {
            "1": {"type": "episode", "title": "S01E01"},
            "2": {"type": "episode", "title": "S01E02"},
        }
        r = client.post(
            "/api/pinned/unpin-group",
            data={"rating_keys": ["1", "2"]},
        )
        # The HX-Trigger header is how the chip list knows to re-fetch.
        # Event name is JSON-encoded, so just assert substring.
        trigger = r.headers.get("HX-Trigger", "")
        assert "pinned-updated" in trigger

    def test_unpin_group_empty_payload_noops(self, client, fake_service):
        # No rating_keys → route still succeeds, nothing removed
        r = client.post("/api/pinned/unpin-group", data={})
        assert r.status_code == 200
        assert "Unpinned 0" in r.text
