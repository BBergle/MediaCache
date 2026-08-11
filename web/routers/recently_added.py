"""Recently Added routes.

A visibility view (issue #174): surfaces recently-added Plex media and where
each item physically lives (cache vs array). It does NOT cache anything itself —
caching happens through the existing Watchlist/OnDeck pipeline or an explicit
Pin (reuses ``/api/pinned/toggle``).

* ``GET /recently-added``        — full page shell (lazy-loads the list partial)
* ``GET /recently-added/list``   — HTMX partial: stat cards + filter bar + table
* ``GET /recently-added/widget`` — compact dashboard card
"""

import logging

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

from web.config import templates
from web.services import get_recently_added_service, get_settings_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Allowed "Added Within" windows (days). Mirrors the filter dropdown.
ALLOWED_DAYS = (1, 7, 30)
DEFAULT_DAYS = 7
# Counts individual media files. TV episodes are collapsed into per-show groups
# for display, but each episode file still consumes one slot here — so keep the
# cap generous enough that recent movies aren't crowded out by a burst of
# episodes. Users with very large libraries can raise it in Settings (up to 500).
DEFAULT_MAX_ITEMS = 250
# Display rows on the compact dashboard card. Counted *after* grouping, so a
# six-episode show spends one slot rather than six.
WIDGET_ROWS = 5


def _resolve_days(requested: int) -> int:
    """Clamp an arbitrary days value to an allowed window."""
    return requested if requested in ALLOWED_DAYS else DEFAULT_DAYS


def _settings_defaults():
    """Read display defaults from settings, falling back to module defaults."""
    try:
        settings = get_settings_service().get_all()
    except Exception:
        settings = {}
    days = settings.get("recently_added_days", DEFAULT_DAYS)
    max_items = settings.get("recently_added_max_items", DEFAULT_MAX_ITEMS)
    try:
        days = _resolve_days(int(days))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    try:
        max_items = max(1, min(int(max_items), 500))
    except (TypeError, ValueError):
        max_items = DEFAULT_MAX_ITEMS
    return days, max_items


@router.get("/", response_class=HTMLResponse)
def recently_added_page(request: Request):
    """Full Recently Added page (the table itself lazy-loads via HTMX)."""
    default_days, _ = _settings_defaults()
    return templates.TemplateResponse(
        request,
        "recently_added/index.html",
        {
            "page_title": "Recently Added",
            "default_days": default_days,
            "allowed_days": ALLOWED_DAYS,
        },
    )


@router.get("/list", response_class=HTMLResponse)
def recently_added_list(
    request: Request,
    days: int = Query(None, description="Added-within window in days"),
):
    """HTMX partial: stat cards + filter bar + table for the chosen window."""
    default_days, max_items = _settings_defaults()
    days = _resolve_days(days) if days is not None else default_days

    service = get_recently_added_service()
    result = service.get_recently_added(days=days, max_items=max_items)

    # Distinct library titles (for the Library filter dropdown).
    libraries = sorted({r.library_title for r in result["rows"] if r.library_title})
    # Collapse multi-episode TV runs into expandable show groups.
    groups = service.group_rows_for_display(result["rows"])

    return templates.TemplateResponse(
        request,
        "recently_added/partials/list.html",
        {
            "available": result["available"],
            "error": result["error"],
            "rows": result["rows"],
            "groups": groups,
            "summary": result["summary"],
            "libraries": libraries,
            "days": days,
            "allowed_days": ALLOWED_DAYS,
            "max_items": max_items,
            # Libraries Plex refused to list. Their rows are missing from the
            # table, the counts and the filter above, so the page has to say so.
            "unreadable_libraries": result.get("unreadable_libraries") or [],
            # The fetch caps at max_items (newest first); if we hit it, older
            # media is hidden — surface that rather than silently truncating.
            "capped": result["summary"].get("total", 0) >= max_items,
        },
    )


@router.get("/widget", response_class=HTMLResponse)
def recently_added_widget(request: Request):
    """Compact dashboard card: counts + the few most recent titles."""
    default_days, max_items = _settings_defaults()
    service = get_recently_added_service()
    result = service.get_recently_added(days=default_days, max_items=max_items)

    # Same grouping as the full page — collapse first, then take the top rows,
    # so a multi-episode show doesn't crowd everything else off the card.
    groups = service.group_rows_for_display(result["rows"])

    return templates.TemplateResponse(
        request,
        "recently_added/partials/widget.html",
        {
            "available": result["available"],
            "error": result["error"],
            "summary": result["summary"],
            "groups": groups[:WIDGET_ROWS],
            "days": default_days,
            "unreadable_libraries": result.get("unreadable_libraries") or [],
        },
    )
