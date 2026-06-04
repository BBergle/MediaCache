"""Route tests for the Recently Added view (issue #174).

Mounts only the recently_added router on a minimal FastAPI app and patches the
service + settings accessors, so these exercise the real Jinja2 templates
without a live Plex server.

Test isolation: earlier tests sometimes replace ``web.config`` (and friends) in
``sys.modules`` with a MagicMock. Force-reload affected modules so route tests
run against the real templates instance.
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _force_real_modules():
    mocked_names = [
        "web.config",
        "web.routers",
        "web.routers.recently_added",
        "web.services",
        "web.services.recently_added_service",
        "web",
    ]
    for name in mocked_names:
        mod = sys.modules.get(name)
        if isinstance(mod, MagicMock):
            del sys.modules[name]
    import web  # noqa: F401
    import web.config  # noqa: F401
    import web.routers.recently_added  # noqa: F401
    import web.services.recently_added_service  # noqa: F401


_force_real_modules()

from web.services.recently_added_service import RecentlyAddedRow, RecentlyAddedService


def _row(rating_key="1", title="Dune", media_type="movie", library_title="Movies",
         size=28_000_000_000, size_display="28.00 GB", location="cache",
         state="on_cache_not_pinned", is_pinned=False, protected_by=None,
         pin_type="movie", episode_info=None, associated_files=None, filename=None):
    return RecentlyAddedRow(
        rating_key=rating_key,
        title=title,
        media_type=media_type,
        library_title=library_title,
        file_path=f"/data/movies/{title}.mkv",
        size=size,
        size_display=size_display,
        added_at=datetime.now(),
        added_display="2 hr ago",
        location=location,
        state=state,
        is_pinned=is_pinned,
        is_ondeck="OnDeck" in (protected_by or []),
        is_watchlist="Watchlist" in (protected_by or []),
        is_cache_tracked=False,
        protected_by=protected_by or [],
        pin_type=pin_type,
        episode_info=episode_info,
        associated_files=associated_files or [],
        filename=filename if filename is not None else f"{title}.mkv",
    )


def _episode(rating_key, show, season, episode, title="Ep", **kw):
    return _row(rating_key=rating_key, title=title, media_type="episode",
                library_title="TV Shows", pin_type="episode",
                episode_info={"show": show, "season": season, "episode": episode}, **kw)


def _summary(rows):
    on_cache = sum(1 for r in rows if r.location == "cache")
    return {
        "total": len(rows),
        "on_cache": on_cache,
        "on_cache_not_pinned": sum(1 for r in rows if r.state == "on_cache_not_pinned"),
        "on_array": sum(1 for r in rows if r.location == "array"),
        "total_size": sum(r.size for r in rows),
        "total_size_display": "28.00 GB",
    }


def _result(rows, available=True, error=None):
    return {
        "available": available,
        "rows": rows,
        "summary": _summary(rows),
        "error": error,
    }


@pytest.fixture
def client():
    from web.routers import recently_added as ra_router

    app = FastAPI()
    app.include_router(ra_router.router, prefix="/recently-added")
    return TestClient(app)


def _patch(service_result, settings=None):
    """Patch the router's service + settings accessors."""
    fake_service = MagicMock()
    fake_service.get_recently_added.return_value = service_result
    # Use the real grouping transform so the template renders real rows/groups.
    fake_service.group_rows_for_display.side_effect = RecentlyAddedService.group_rows_for_display
    fake_settings = MagicMock()
    fake_settings.get_all.return_value = settings or {}
    return (
        patch("web.routers.recently_added.get_recently_added_service", return_value=fake_service),
        patch("web.routers.recently_added.get_settings_service", return_value=fake_settings),
        fake_service,
    )


class TestPage:
    def test_page_renders_shell(self, client):
        p_svc, p_set, _ = _patch(_result([]), settings={})
        with p_svc, p_set:
            r = client.get("/recently-added/")
        assert r.status_code == 200
        assert "Recently Added" in r.text
        # Lazy-loads the list partial with the default window.
        assert "/recently-added/list?days=7" in r.text

    def test_page_uses_settings_default_window(self, client):
        p_svc, p_set, _ = _patch(_result([]), settings={"recently_added_days": 30})
        with p_svc, p_set:
            r = client.get("/recently-added/")
        assert r.status_code == 200
        assert "/recently-added/list?days=30" in r.text


class TestList:
    def test_renders_states_and_pin_button(self, client):
        rows = [
            _row(rating_key="1", title="Dune", state="on_cache_not_pinned", location="cache"),
            _row(rating_key="2", title="Civil War", state="on_array", location="array",
                 size_display="62.00 GB"),
            _row(rating_key="3", title="Wild Robot", state="pinned", location="cache", is_pinned=True),
            _row(rating_key="4", title="Furiosa", state="protected", location="cache",
                 protected_by=["OnDeck"]),
        ]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list", headers={"HX-Request": "true"})

        assert r.status_code == 200
        # State badges
        assert "Not pinned" in r.text          # on_cache_not_pinned
        assert "Not cached" in r.text          # on_array
        assert "Pinned" in r.text              # pinned
        assert "Protected" in r.text and "OnDeck" in r.text
        # Pin button (macro) wires to the existing toggle endpoint
        assert "/api/pinned/toggle" in r.text
        # Array row shows byte-cost note
        assert "copies ~62.00 GB to cache" in r.text

    def test_clamps_invalid_days_to_default(self, client):
        p_svc, p_set, fake_service = _patch(_result([]))
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=999")
        assert r.status_code == 200
        # 999 is not an allowed window → clamps to 7
        fake_service.get_recently_added.assert_called_once()
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 7

    def test_respects_allowed_days(self, client):
        p_svc, p_set, fake_service = _patch(_result([]))
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=30")
        assert r.status_code == 200
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 30
        assert "selected" in r.text and "Last 30 days" in r.text

    def test_no_days_uses_settings_default(self, client):
        p_svc, p_set, fake_service = _patch(_result([]), settings={"recently_added_days": 1})
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 1

    def test_unavailable_state(self, client):
        p_svc, p_set, _ = _patch(_result([], available=False, error="Plex not configured."))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        assert "unavailable" in r.text.lower()
        assert "Plex not configured." in r.text

    def test_empty_window_message(self, client):
        p_svc, p_set, _ = _patch(_result([]))
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=7")
        assert r.status_code == 200
        assert "Nothing added in the last 7 days" in r.text

    def test_episode_row_shows_show_and_code(self, client):
        rows = [_row(rating_key="9", title="Future Days", media_type="episode",
                     library_title="TV Shows", state="on_cache_not_pinned", location="cache",
                     pin_type="episode",
                     episode_info={"show": "The Last of Us", "season": 2, "episode": 1})]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert "The Last of Us" in r.text
        assert "S02E01" in r.text


class TestGrouping:
    def test_multi_episode_show_renders_expandable_group(self, client):
        rows = [
            _episode("1", "The Last of Us", 2, 1, title="Future Days"),
            _episode("2", "The Last of Us", 2, 2, title="Through the Valley"),
            _episode("3", "The Last of Us", 2, 3, title="The Path"),
        ]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        # One group header for the show + child rows
        assert 'class="ra-group-header"' in r.text
        assert "3 new episodes" in r.text
        assert 'data-group-id="rag0"' in r.text
        assert r.text.count('ra-group-child') >= 3
        # Episode codes appear in child rows
        assert "S02E01" in r.text and "S02E03" in r.text
        # Per-episode pin actions present
        assert "/api/pinned/toggle" in r.text

    def test_single_episode_not_grouped(self, client):
        rows = [_episode("9", "Severance", 1, 1, title="Solo")]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert 'class="ra-group-header"' not in r.text
        assert "Severance" in r.text


class TestExpandDetail:
    def test_row_is_expandable_with_filename_and_size(self, client):
        rows = [_row(rating_key="1", title="Dune",
                     filename="Dune.2021.2160p.mkv", size_display="28.00 GB")]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        # Title cell is click-to-expand with a chevron
        assert "RecentlyAdded.toggleDetail(this)" in r.text
        assert "ra-detail-chevron" in r.text
        # Detail sub-row carries the primary filename + size
        assert "ra-detail-row" in r.text
        assert "Dune.2021.2160p.mkv" in r.text
        assert "28.00 GB" in r.text

    def test_associated_files_listed_in_detail(self, client):
        rows = [_row(rating_key="1", title="Dune", filename="Dune.mkv",
                     associated_files=[{"filename": "Dune.en.srt", "size": "84 KB"}])]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        # +N hint badge + the associated file appears in a detail sub-row
        assert "+1" in r.text
        assert "Dune.en.srt" in r.text
        assert "associated-sub-file" in r.text


class TestWidget:
    def test_widget_renders_counts(self, client):
        rows = [
            _row(rating_key="1", title="Dune", location="cache", state="on_cache_not_pinned"),
            _row(rating_key="2", title="Civil War", location="array", state="on_array"),
        ]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert "on cache" in r.text
        assert "not pinned" in r.text
        assert "Dune" in r.text

    def test_widget_unavailable(self, client):
        p_svc, p_set, _ = _patch(_result([], available=False, error="No Plex."))
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert "No Plex." in r.text
