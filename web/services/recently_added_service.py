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
from core.media_grouping import format_season_range, group_ordered, stable_group_id

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
    associated_files: List[Dict[str, str]] = field(default_factory=list)  # [{filename, size}]
    filename: str = ""            # basename of file_path (shown in the expand detail)

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
            "associated_files": self.associated_files,
            "filename": self.filename,
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

        try:
            from core.file_operations import MultiPathModifier
            path_modifier = MultiPathModifier(config.paths.path_mappings)

            rows = self._enrich(
                items,
                path_modifier,
                pinned_keys=self._json_keys(self.pinned_file),
                ondeck_keys=self._json_keys(self.ondeck_file),
                watchlist_keys=self._json_keys(self.watchlist_file),
                timestamps_keys=self._json_keys(self.timestamps_file),
            )
        except Exception as e:
            logger.warning(f"RecentlyAddedService: enrichment failed: {e}")
            return self._empty_result(f"Could not process recently added media: {e}")

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
                associated_files=self._scan_associated_files(cache_path) if on_cache else [],
                filename=os.path.basename(item.file_path) if item.file_path else "",
            ))
        return rows

    @staticmethod
    def group_rows_for_display(rows: List[RecentlyAddedRow]) -> List[Dict[str, Any]]:
        """Collapse multi-episode TV runs into expandable show groups.

        Uses the app-wide bucketing rule from ``core.media_grouping`` — the same
        one behind Recent Activity and the completion banner — so a burst of
        episodes collapses identically everywhere. Only the *key* differs: here
        it comes from Plex metadata (library + show) rather than a filename.

        A show groups across seasons. A mid-season rollover would otherwise
        split one show into two adjacent rows, which is exactly the fragmenting
        the grouping exists to remove; ``season_display`` names the span.

        Returns an ordered list of display items, each either:

        * ``{"kind": "row", "row": RecentlyAddedRow}`` — a movie or a standalone
          episode (a show with only one episode in the window), or
        * ``{"kind": "show", "group_id", "show", "season", "seasons",
          "season_display", "library_title", "episodes": [...], "episode_count",
          "total_size", "total_size_display", "added_display", "locations": [...],
          "not_pinned_count", "pinned_count"}``.

        Order follows first-seen position (rows arrive newest-first), so a show
        group anchors where its first episode appeared.
        """
        def key_fn(row: RecentlyAddedRow):
            info = row.episode_info or {}
            if row.media_type == "episode" and info.get("show"):
                return ("show", row.library_title, info.get("show"))
            return None

        display: List[Dict[str, Any]] = []
        for key, members in group_ordered(rows, key_fn):
            if key is None or len(members) == 1:
                display.extend({"kind": "row", "row": r} for r in members)
                continue

            eps = sorted(members, key=lambda r: (
                (r.episode_info or {}).get("season") or 0,
                (r.episode_info or {}).get("episode") or 0,
            ))
            seasons = sorted({
                s for s in ((r.episode_info or {}).get("season") for r in eps)
                if s is not None
            })
            total_size = sum(r.size for r in eps)
            locations: List[str] = []
            for r in eps:
                if r.location not in locations:
                    locations.append(r.location)
            display.append({
                "kind": "show",
                # Same minting as the activity/banner groups — derived from the
                # key, not from list position, and free of user content that
                # could break the data-* attribute it lands in.
                "group_id": stable_group_id("ra", key[1], key[2]),
                "show": key[2],
                # Kept for callers that want a plain season number; None once a
                # group spans more than one season. Prefer season_display.
                "season": seasons[0] if len(seasons) == 1 else None,
                "seasons": seasons,
                "season_display": format_season_range(seasons),
                "library_title": key[1],
                "episodes": eps,
                "episode_count": len(eps),
                "total_size": total_size,
                "total_size_display": format_bytes(total_size) if total_size else "",
                # Rows arrive newest-first, so the first member is the newest
                # episode — the group's "added" age.
                "added_display": members[0].added_display,
                "locations": locations,
                "not_pinned_count": sum(1 for r in eps if r.state == "on_cache_not_pinned"),
                "pinned_count": sum(1 for r in eps if r.is_pinned),
            })
        return display

    def _scan_associated_files(self, video_cache_path: Optional[str]) -> List[Dict[str, str]]:
        """Find subtitle/sidecar files sharing a cache-resident video's basename.

        Scans the video's directory for siblings whose stem matches the video's
        stem (exact, or with a ``.``/``-`` suffix — catches ``Movie.en.srt``,
        ``Movie.nfo``, ``Movie-poster.jpg``). Cache-only: array/unknown items
        are never probed. Returns ``[{filename, size}]`` sorted by name.
        """
        if not video_cache_path:
            return []
        directory = os.path.dirname(video_cache_path)
        video_name = os.path.basename(video_cache_path)
        stem = os.path.splitext(video_name)[0]
        if not directory or not stem:
            return []
        found: List[Dict[str, str]] = []
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if entry.name == video_name:
                        continue
                    try:
                        if not entry.is_file():
                            continue
                    except OSError:
                        continue
                    entry_stem = os.path.splitext(entry.name)[0]
                    if (entry_stem == stem
                            or entry_stem.startswith(stem + ".")
                            or entry_stem.startswith(stem + "-")):
                        try:
                            size = entry.stat().st_size
                        except OSError:
                            size = 0
                        found.append({
                            "filename": entry.name,
                            "size": format_bytes(size) if size else "",
                        })
        except OSError:
            return []
        return sorted(found, key=lambda f: f["filename"])

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
