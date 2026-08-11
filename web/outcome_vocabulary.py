"""Outcome vocabulary for the Recently Added view.

Names each row by *what happens to the file next* rather than by a state noun,
because that is the question the page exists to answer ("will this still be on
fast storage tomorrow?"). A state noun can assert protection the code does not
actually provide; an outcome cannot.

Lives outside ``web/services/`` deliberately: ``web.config`` imports it as a
template global, and routing it through the services package would trigger a
circular import via ``cache_service`` -> ``web.config``. Same reason
``web/settings_search_index.py`` sits here.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Outcome:
    """One row state, named by what happens to the file next.

    ``tier`` orders states weakest-first for the group-header reduction: a show
    group must never look safer than its worst episode.
    """
    key: str
    label: str
    badge: str        # CSS class from plex-theme.css
    icon: str         # lucide icon name ("" for none)
    tier: int
    tooltip: str


# Reserved sentence. The word "Protected" is deliberately confined to states
# where a pin genuinely exempts the file from eviction — it is not used for
# OnDeck/Watchlist, which only add +15 to the priority score
# (core/file_operations.py) rather than exempting anything.
_PROTECTED_SENTENCE = "Pinned — protected from eviction."

# Ordered weakest-first. `stays_on_array` is deliberately outside the ranking
# (see _group_outcome): a file that never arrived on cache cannot leave it.
OUTCOMES: Dict[str, Outcome] = {o.key: o for o in (
    Outcome(
        key="pin_unknown", label="Pin state unknown", badge="badge-muted",
        icon="", tier=0,
        tooltip="Couldn't check pins — Plex didn't answer. Refresh to retry.",
    ),
    Outcome(
        key="unmapped", label="Unmapped", badge="badge-muted",
        icon="help-circle", tier=1,
        tooltip=("No path mapping covers this library, so PlexCache can't tell "
                 "where the file lives — check Settings → Libraries."),
    ),
    Outcome(
        key="mover_decides", label="Mover's call", badge="badge-released",
        icon="send", tier=2,
        tooltip=("PlexCache isn't holding this back from the Unraid mover. The "
                 "mover relocates it on its own schedule, not ours."),
    ),
    Outcome(
        key="moves_back", label="Moves to array", badge="badge-muted",
        icon="arrow-down", tier=3,
        tooltip=("No longer on OnDeck or a Watchlist, so PlexCache moves it "
                 "back to the array."),
    ),
    Outcome(
        key="returns_when_done", label="Returns when watched", badge="badge-info",
        icon="clock", tier=4,
        tooltip=("Held on cache while it's on OnDeck or a Watchlist. PlexCache "
                 "moves it back once it drops off, after the retention hold. An "
                 "active playback session or a hard link delays that further."),
    ),
    Outcome(
        key="held_by_setting", label="Stays on cache", badge="badge-info",
        icon="sliders-horizontal", tier=5,
        tooltip=("Move Watched to Array is off in Settings → Cache, so PlexCache "
                 "never moves anything back. This stays on the cache pool."),
    ),
    Outcome(
        key="arriving", label="Pinned, not on cache yet", badge="badge-pinned",
        icon="arrow-down-to-line", tier=6,
        tooltip=(_PROTECTED_SENTENCE + " It isn't on the cache pool yet — the "
                 "next PlexCache run copies it across."),
    ),
    Outcome(
        key="held", label="Stays on cache", badge="badge-pinned",
        icon="pin", tier=7,
        tooltip=(_PROTECTED_SENTENCE + " PlexCache also keeps it out of the "
                 "Unraid mover's reach and never moves it back, until you unpin."),
    ),
    Outcome(
        key="stays_on_array", label="Not on cache", badge="badge-muted",
        icon="server", tier=99,
        tooltip=("On the spinning array. PlexCache pulls it to cache only if it "
                 "lands on OnDeck or a Watchlist, or if you pin it."),
    ),
)}


def outcome_tooltip(key: str) -> str:
    """Tooltip for an outcome key (empty string when unrecognised)."""
    entry = OUTCOMES.get(key)
    return entry.tooltip if entry else ""
