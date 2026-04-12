"""CLI handlers for pinned media management (--list-pins, --pin, --unpin, --pin-by-title)."""

import logging
import sys
from typing import Optional

from core.config import ConfigManager
from core.pinned_media import PinnedMediaTracker


def _get_tracker(config_manager: ConfigManager) -> PinnedMediaTracker:
    tracker_file = config_manager.get_pinned_media_file()
    return PinnedMediaTracker(str(tracker_file))


def _connect_plex(config_manager: ConfigManager):
    """Connect to Plex server. Returns PlexServer instance or None."""
    plex_url = config_manager.plex.plex_url
    plex_token = config_manager.plex.plex_token
    if not plex_url or not plex_token:
        print("Error: Plex URL and token must be configured. Run --setup first.")
        return None
    try:
        from plexapi.server import PlexServer
        return PlexServer(plex_url, plex_token, timeout=10)
    except Exception as e:
        print(f"Error: Could not connect to Plex server: {e}")
        return None


def handle_list_pins(config_manager: ConfigManager) -> None:
    """Handle --list-pins: display all pinned media."""
    tracker = _get_tracker(config_manager)
    pins = tracker.list_pins()

    if not pins:
        print("No pinned media.")
        return

    print(f"Pinned media ({len(pins)} item{'s' if len(pins) != 1 else ''}):\n")

    for pin in pins:
        scope = pin.get("type", "unknown")
        title = pin.get("title", "Unknown")
        rk = pin.get("rating_key", "?")
        added_by = pin.get("added_by", "?")
        added_at = pin.get("added_at", "?")

        print(f"  [{scope}]  {title}")
        print(f"           rating_key={rk}  added_by={added_by}  added_at={added_at}")

    plex = _connect_plex(config_manager)
    if plex:
        preference = config_manager.plex.pinned_preferred_resolution
        from core.pinned_media import resolve_pins_to_paths
        resolved, orphaned = resolve_pins_to_paths(plex, tracker, preference)
        if resolved:
            print(f"\n  Resolved to {len(resolved)} file(s) (preference: {preference})")
        if orphaned:
            print(f"  {len(orphaned)} orphaned pin(s) were auto-removed (items no longer in Plex)")


def handle_pin(config_manager: ConfigManager, rating_key: str) -> None:
    """Handle --pin <rating_key>: pin a specific item by rating key."""
    tracker = _get_tracker(config_manager)

    if tracker.is_pinned(rating_key):
        print(f"Already pinned: rating_key={rating_key}")
        return

    plex = _connect_plex(config_manager)
    if not plex:
        return

    try:
        item = plex.fetchItem(int(rating_key))
    except Exception as e:
        print(f"Error: Could not fetch item {rating_key} from Plex: {e}")
        return

    pin_type = _derive_pin_type(item)
    title = getattr(item, "title", "Unknown")

    tracker.add_pin(rating_key, pin_type, title, added_by="cli")
    print(f"Pinned: [{pin_type}] {title} (rating_key={rating_key})")


def handle_unpin(config_manager: ConfigManager, rating_key: str) -> None:
    """Handle --unpin <rating_key>: unpin a specific item."""
    tracker = _get_tracker(config_manager)

    pin = tracker.get_pin(rating_key)
    if not pin:
        print(f"Not pinned: rating_key={rating_key}")
        return

    title = pin.get("title", "Unknown")
    tracker.remove_pin(rating_key)
    print(f"Unpinned: {title} (rating_key={rating_key})")


def handle_pin_by_title(config_manager: ConfigManager, query: str) -> None:
    """Handle --pin-by-title "title": search Plex and pin interactively."""
    plex = _connect_plex(config_manager)
    if not plex:
        return

    tracker = _get_tracker(config_manager)

    results = []
    for media_type in ("movie", "show"):
        try:
            hits = plex.search(query, mediatype=media_type, limit=10)
            for item in hits:
                rk = str(item.ratingKey)
                results.append({
                    "rating_key": rk,
                    "title": item.title,
                    "type": item.type,
                    "year": getattr(item, "year", ""),
                    "already_pinned": tracker.is_pinned(rk),
                })
        except Exception as e:
            logging.debug(f"Search for {media_type} failed: {e}")

    if not results:
        print(f'No results for "{query}".')
        return

    print(f'Search results for "{query}":\n')
    for i, r in enumerate(results, 1):
        pinned_marker = " [PINNED]" if r["already_pinned"] else ""
        year_str = f" ({r['year']})" if r["year"] else ""
        print(f"  {i}. [{r['type']}] {r['title']}{year_str}{pinned_marker}")

    print(f"\nEnter number to pin (1-{len(results)}), or 'q' to cancel: ", end="")
    try:
        choice = input().strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return

    if choice.lower() in ("q", "quit", ""):
        print("Cancelled.")
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(results):
            raise ValueError()
    except ValueError:
        print(f"Invalid choice: {choice}")
        return

    selected = results[idx]
    if selected["already_pinned"]:
        print(f"Already pinned: {selected['title']}")
        return

    pin_type = selected["type"] if selected["type"] in ("movie", "show") else "movie"
    tracker.add_pin(selected["rating_key"], pin_type, selected["title"], added_by="cli")
    print(f"Pinned: [{pin_type}] {selected['title']} (rating_key={selected['rating_key']})")


def _derive_pin_type(item) -> str:
    """Derive pin_type from a plexapi item."""
    item_type = getattr(item, "type", "")
    if item_type in ("movie", "show", "season", "episode"):
        return item_type
    return "movie"


def extract_flag_value(flag: str) -> Optional[str]:
    """Extract the value following a flag in sys.argv. Returns None if flag not found or no value."""
    try:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    except ValueError:
        pass
    return None
