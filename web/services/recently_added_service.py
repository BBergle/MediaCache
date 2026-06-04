"""Recently Added service (web layer).

Surfaces recently-added Plex media and where each item physically lives
(cache vs array), so users can see new content and Pin items they want held
on cache. This view does NOT cache anything itself — caching happens through
the existing Watchlist/OnDeck pipeline or an explicit Pin. See issue #174 and
FUTURE_ENHANCEMENTS.md #7.

Enrichment per item cross-references the existing trackers/exclude list to
derive a display state:

* ``pinned``               — rating_key is in the pin tracker (always cached)
* ``protected``            — currently OnDeck or Watchlist (transient)
* ``on_cache_not_pinned``  — physically on cache but not pinned (the actionable
                             case; the mover may move it on its next run)
* ``on_array``             — on the array, not cached
* ``unknown``              — location could not be resolved (unmapped path)
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from web.config import DATA_DIR, SETTINGS_FILE
from core.system_utils import get_array_direct_path, format_bytes, format_cache_age

logger = logging.getLogger(__name__)


@dataclass
class RecentlyAddedRow:
    """A recently-added item enriched with location + protection state."""
    rating_key: str
    title: str
    media_type: str               # "movie" | "episode"
    library_title: str
    file_path: str                # Plex path
    size: int
    size_display: str
    added_at: Optional[datetime]
    added_display: Optional[str]  # "2 hr ago" etc. (None if no timestamp)
    location: str                 # "cache" | "array" | "unknown"
    state: str                    # see module docstring
    is_pinned: bool = False
    is_ondeck: bool = False
    is_watchlist: bool = False
    is_cache_tracked: bool = False
    protected_by: List[str] = field(default_factory=list)  # ["Pinned"], ["OnDeck"], ...
    pin_type: str = "movie"       # scope passed to /api/pinned/toggle
    episode_info: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rating_key": self.rating_key,
            "title": self.title,
            "media_type": self.media_type,
            "library_title": self.library_title,
            "file_path": self.file_path,
            "size": self.size,
            "size_display": self.size_display,
            "added_display": self.added_display,
            "location": self.location,
            "state": self.state,
            "is_pinned": self.is_pinned,
            "is_ondeck": self.is_ondeck,
            "is_watchlist": self.is_watchlist,
            "is_cache_tracked": self.is_cache_tracked,
            "protected_by": self.protected_by,
            "pin_type": self.pin_type,
            "episode_info": self.episode_info,
        }


class RecentlyAddedService:
    """Business logic for the Recently Added view."""

    def __init__(self):
        self.settings_file = SETTINGS_FILE
        self.pinned_file = DATA_DIR / "pinned_media.json"
        self.ondeck_file = DATA_DIR / "ondeck_tracker.json"
        self.watchlist_file = DATA_DIR / "watchlist_tracker.json"
        self.timestamps_file = DATA_DIR / "timestamps.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recently_added(self, days: int = 7, max_items: int = 100) -> Dict[str, Any]:
        """Fetch + enrich recently-added media.

        Returns a dict ``{available, rows, summary, error}``. ``available`` is
        False (with ``error`` set) when Plex isn't configured/reachable; the
        caller renders an empty state in that case.
        """
        try:
            from core.config import ConfigManager
            config = ConfigManager(str(self.settings_file))
            config.load_config()
        except Exception as e:
            logger.warning(f"RecentlyAddedService: settings not loadable: {e}")
            return self._empty_result("PlexCache is not configured yet.")

        if not config.plex.plex_url or not config.plex.plex_token:
            return self._empty_result("Plex server is not configured.")

        plex_manager = self._connect_plex(config)
        if plex_manager is None:
            return self._empty_result("Could not connect to the Plex server.")

        valid_sections = config.plex.valid_sections or []
        try:
            items = plex_manager.get_recently_added_media(valid_sections, days, max_items)
        except Exception as e:
            logger.warning(f"RecentlyAddedService: fetch failed: {e}")
            return self._empty_result(f"Could not fetch recently added media: {e}")

        from core.file_operations import PathModifier
        path_modifier = PathModifier(config.paths.path_mappings)

        rows = self._enrich(
            items,
            path_modifier,
            pinned_keys=self._json_keys(self.pinned_file),
            ondeck_keys=self._json_keys(self.ondeck_file),
            watchlist_keys=self._json_keys(self.watchlist_file),
            timestamps_keys=self._json_keys(self.timestamps_file),
        )

        return {
            "available": True,
            "rows": rows,
            "summary": self._summary(rows),
            "error": None,
        }

    # ------------------------------------------------------------------
    # Enrichment (pure — unit-testable without Plex/filesystem)
    # ------------------------------------------------------------------

    def _enrich(self, items, path_modifier, pinned_keys, ondeck_keys,
                watchlist_keys, timestamps_keys) -> List[RecentlyAddedRow]:
        rows: List[RecentlyAddedRow] = []
        for item in items:
            real_path, _ = path_modifier.convert_plex_to_real(item.file_path)
            cache_path, _ = (
                path_modifier.convert_real_to_cache(real_path) if real_path else (None, None)
            )

            on_cache = bool(cache_path) and self._file_exists(cache_path)
            array_direct = get_array_direct_path(real_path) if real_path else None
            on_array = bool(array_direct) and self._file_exists(array_direct)
            location = "cache" if on_cache else ("array" if on_array else "unknown")

            is_pinned = item.rating_key in pinned_keys if item.rating_key else False
            is_ondeck = bool(real_path) and real_path in ondeck_keys
            is_watchlist = item.file_path in watchlist_keys
            is_cache_tracked = bool(cache_path) and cache_path in timestamps_keys

            protected_by: List[str] = []
            if is_pinned:
                protected_by.append("Pinned")
            if is_ondeck:
                protected_by.append("OnDeck")
            if is_watchlist:
                protected_by.append("Watchlist")

            if is_pinned:
                state = "pinned"
            elif is_ondeck or is_watchlist:
                state = "protected"
            elif location == "cache":
                state = "on_cache_not_pinned"
            elif location == "array":
                state = "on_array"
            else:
                state = "unknown"

            rows.append(RecentlyAddedRow(
                rating_key=item.rating_key or "",
                title=item.title,
                media_type=item.media_type,
                library_title=item.library_title,
                file_path=item.file_path,
                size=item.size,
                size_display=format_bytes(item.size) if item.size else "",
                added_at=item.added_at,
                added_display=format_cache_age(item.added_at),
                location=location,
                state=state,
                is_pinned=is_pinned,
                is_ondeck=is_ondeck,
                is_watchlist=is_watchlist,
                is_cache_tracked=is_cache_tracked,
                protected_by=protected_by,
                pin_type="episode" if item.media_type == "episode" else "movie",
                episode_info=item.episode_info,
            ))
        return rows

    @staticmethod
    def _summary(rows: List[RecentlyAddedRow]) -> Dict[str, Any]:
        on_cache = sum(1 for r in rows if r.location == "cache")
        on_cache_not_pinned = sum(1 for r in rows if r.state == "on_cache_not_pinned")
        on_array = sum(1 for r in rows if r.location == "array")
        total_size = sum(r.size for r in rows)
        return {
            "total": len(rows),
            "on_cache": on_cache,
            "on_cache_not_pinned": on_cache_not_pinned,
            "on_array": on_array,
            "total_size": total_size,
            "total_size_display": format_bytes(total_size) if total_size else "0 B",
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connect_plex(self, config):
        """Build + connect a PlexManager from loaded config. None on failure."""
        try:
            from core.plex_api import PlexManager
            plex_manager = PlexManager(
                config.plex.plex_url,
                config.plex.plex_token,
                plex_db_path=getattr(config.plex, "plex_db_path", "") or "",
            )
            plex_manager.connect()
            return plex_manager
        except Exception as e:
            logger.warning(f"RecentlyAddedService: Plex connection failed: {e}")
            return None

    @staticmethod
    def _file_exists(path: str) -> bool:
        """Wrapper around os.path.exists (patch point for tests)."""
        try:
            return os.path.exists(path)
        except OSError:
            return False

    def _json_keys(self, path: Path) -> set:
        """Return the top-level key set of a JSON object file (empty on error)."""
        if not path.exists():
            return set()
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.keys()) if isinstance(data, dict) else set()
        except (json.JSONDecodeError, IOError, OSError):
            return set()

    def _empty_result(self, error: str) -> Dict[str, Any]:
        return {
            "available": False,
            "rows": [],
            "summary": self._summary([]),
            "error": error,
        }


# Module-level singleton (mirrors the other web services).
_recently_added_service: Optional[RecentlyAddedService] = None


def get_recently_added_service() -> RecentlyAddedService:
    """Return the shared RecentlyAddedService instance."""
    global _recently_added_service
    if _recently_added_service is None:
        _recently_added_service = RecentlyAddedService()
    return _recently_added_service
