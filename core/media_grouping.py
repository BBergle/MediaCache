"""Shared media-grouping primitives.

One definition, for the whole app, of "these items belong to the same show"
and "how does an ordered sequence collapse into groups". Every surface that
lists media — the Recent Activity feed, the completion banner, the Recently
Added page and its dashboard widget — builds on this module, so a burst of
episodes reads the same way wherever it appears.

Two layers, deliberately separate:

* ``parse_show_episode()`` / ``show_name_from_filename()`` — derive show
  identity from a Sonarr/Plex-style *filename*. Used where the only thing on
  hand is a path (the activity feed records filenames, not Plex metadata).
* ``group_ordered()`` — the ordering/bucketing rule itself, independent of
  where the key comes from. Callers that *do* have Plex metadata (Recently
  Added) key on that instead of a filename and still group identically.

No web framework imports — usable from ``core/`` (CLI path) and ``web/`` alike.
"""

import hashlib
import re
from typing import Any, Callable, Hashable, Iterable, List, Optional, Tuple

# Matches "<show> - S##E##" — the Sonarr/Plex TV naming convention.
# Non-TV files (movies, specials without episode numbering) don't match and
# are left ungrouped by the callers below.
SHOW_EPISODE_PATTERN = re.compile(r'^(.+?) - S(\d+)E(\d+)', re.IGNORECASE)


def parse_show_episode(filename: str) -> Optional[Tuple[str, int, int]]:
    """``(show, season, episode)`` parsed from a filename, or None if it isn't
    a recognizably-numbered TV episode."""
    match = SHOW_EPISODE_PATTERN.match(filename or "")
    if not match:
        return None
    return match.group(1).strip(), int(match.group(2)), int(match.group(3))


def show_name_from_filename(filename: str) -> Optional[str]:
    """Show name from a Sonarr/Plex-style filename, or None if it isn't one."""
    parsed = parse_show_episode(filename)
    return parsed[0] if parsed else None


def group_ordered(
    items: Iterable[Any],
    key_fn: Callable[[Any], Optional[Hashable]],
) -> List[Tuple[Optional[Hashable], List[Any]]]:
    """Bucket ``items`` by ``key_fn``, preserving first-seen order.

    ``key_fn`` returns a hashable group key, or ``None`` for an item that never
    groups (a movie, an unparseable filename). Each ungroupable item becomes
    its own single-member bucket keyed ``None``, so callers can emit everything
    in one pass and still render those in place.

    A group anchors at the position of its *first* member, so re-rendering the
    same list never reshuffles rows.

    Returns a list of ``(key, members)`` pairs. Buckets holding a single member
    are returned as-is — every current caller renders those as a plain row,
    since grouping one item offers no compression.
    """
    buckets: dict = {}
    order: List[Tuple[Optional[Hashable], Any]] = []

    for idx, item in enumerate(items):
        key = key_fn(item)
        # Ungroupable items get a positional bucket key so two of them never
        # collide, while the key reported back to the caller stays None.
        bucket_key = key if key is not None else ("__single__", idx)
        if bucket_key not in buckets:
            buckets[bucket_key] = []
            order.append((key, bucket_key))
        buckets[bucket_key].append(item)

    return [(key, buckets[bucket_key]) for key, bucket_key in order]


def stable_group_id(*parts: Any) -> str:
    """Deterministic DOM id for a group, derived from its grouping key.

    Two properties the surfaces depend on:

    * **Stable across re-renders.** Derived from the key, not from list
      position, so a poll that re-renders the same list produces the same ids
      and an expanded group stays expanded (the completion banner re-renders
      every 2s and restores expand state by id).
    * **Safe in an HTML attribute.** Carries no user content, so interpolating
      it into ``data-*`` can't be broken by a title containing a quote — which
      a raw show name can.

    Uniqueness holds *within one grouping call*. A page that renders several
    grouped lists must namespace it (Recent Activity prefixes the run id), or
    the same show in two runs would collide.
    """
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return "g" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def format_season_range(seasons: List[int]) -> str:
    """Human label for the seasons covered by a group.

    ``[1] -> "Season 1"``, ``[1, 2] -> "Seasons 1–2"``, ``[] -> ""``.
    """
    known = sorted({s for s in seasons if s is not None})
    if not known:
        return ""
    if len(known) == 1:
        return f"Season {known[0]}"
    return f"Seasons {known[0]}–{known[-1]}"


def format_season_episode(season: Any, episode: Any) -> str:
    """``"S01E03"`` for a numbered episode, ``""`` when either number is unknown.

    Both numbers or neither: a half-known episode renders as nothing rather
    than a fabricated ``S00E00``, which would be indistinguishable from a real
    Specials episode 0. Callers fall back to the episode title.

    The ``isinstance(..., int)`` guard is load-bearing, not defensive.
    ``plexapi.utils.cast(int, value)`` returns ``float('nan')`` — not ``None``
    — for anything it cannot parse, including the empty string. A ``None``
    check therefore passes NaN straight into the format, where it raises
    ``ValueError: cannot convert float NaN to integer``. ``bool`` is excluded
    separately because ``isinstance(True, int)`` is True, and ``S01ETrue`` is
    not a thing. This matches Jinja's own ``is integer`` test.

    Args:
        season: Season number, or any unparsed value from a Plex listing.
        episode: Episode number, same.

    Returns:
        The ``SxxEyy`` code, or ``""`` if either number is unusable.
    """
    if isinstance(season, bool) or isinstance(episode, bool):
        return ""
    if isinstance(season, int) and isinstance(episode, int):
        return f"S{season:02d}E{episode:02d}"
    return ""
