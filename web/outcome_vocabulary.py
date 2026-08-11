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


# Reserved sentence. The word "Protected" is confined to states where a pin
# genuinely exempts the file from eviction — never for OnDeck/Watchlist, which
# only add +15 to the priority score (core/file_operations.py) rather than
# exempting anything.
#
# Two things this sentence must NOT be trimmed back to:
#   * "only from eviction" would be too narrow. A pin also short-circuits the
#     watched move-back to array (core/file_operations.py:3814), and therefore
#     also blocks release-to-mover, so the move-back clause belongs in every
#     copy of this text.
#   * eviction exemption is NOT the same as mover exemption. Pinning writes no
#     exclude-file line (PinnedService.toggle_pin never touches it), so a file
#     pinned while already on cache is exempt from PlexCache eviction while the
#     Unraid mover can still relocate it, until a run adds the exclude entry.
#     That gap is its own outcome (`held_mover_gap`) rather than a footnote.
PROTECTED_SENTENCE = "Pinned — protected from eviction."

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
                 "where the file lives. Check Settings → Libraries."),
    ),
    Outcome(
        key="mover_decides", label="Mover's call", badge="badge-released",
        icon="send", tier=2,
        tooltip=("PlexCache isn't holding this back. The Unraid mover relocates "
                 "it on its own schedule."),
    ),
    Outcome(
        key="moves_back", label="Moves to array", badge="badge-muted",
        icon="arrow-down", tier=3,
        tooltip=("No longer on OnDeck or a Watchlist, so PlexCache moves it back "
                 "to the array."),
    ),
    Outcome(
        key="returns_when_done", label="Returns when watched", badge="badge-info",
        icon="clock", tier=4,
        # The last sentence is load-bearing, not padding: OnDeck/Watchlist only
        # add +15 to the priority score, so this is a move-back deferral and not
        # the eviction exemption the label could be read as.
        tooltip=("Held while it's on OnDeck or a Watchlist. PlexCache moves it "
                 "back once it drops off. This defers the move — it doesn't "
                 "prevent eviction."),
    ),
    # Deliberately NOT labelled "Stays on cache": watched_move only governs the
    # move-back to array. Eviction is a separate path with no watched_move
    # awareness, so this file can still be evicted, and a "stays" promise would
    # be false. It also collided with `held`'s label at group level, where the
    # badge colour that distinguished them is gone.
    Outcome(
        key="held_by_setting", label="Not moved back", badge="badge-info",
        icon="sliders-horizontal", tier=5,
        tooltip=("Move Watched to Array is off, so PlexCache never moves "
                 "anything back. It can still be evicted if the pool runs short."),
    ),
    Outcome(
        key="arriving", label="Pinned, not on cache yet", badge="badge-pinned",
        icon="arrow-down-to-line", tier=6,
        # No eviction claim: there is nothing on cache to evict, so the reserved
        # sentence would be vacuous here. The copy promise is hedged because
        # _apply_cache_limit has no pin awareness and can skip a pinned file
        # when the pool is full.
        tooltip=("Pinned, but not on cache yet. The next run copies it across. "
                 "Cache Pinned Now copies it immediately, ignoring cache limits."),
    ),
    Outcome(
        key="held_mover_gap", label="Stays on cache", badge="badge-pinned",
        icon="pin", tier=7,
        tooltip=(PROTECTED_SENTENCE + " PlexCache won't move it back to the "
                 "array either. Not in the Unraid mover's exclude list yet, so "
                 "the mover could still relocate it — the next run fixes that."),
    ),
    Outcome(
        key="held", label="Stays on cache", badge="badge-pinned",
        icon="pin", tier=8,
        tooltip=(PROTECTED_SENTENCE + " PlexCache won't move it back to the "
                 "array, and the Unraid mover is told to skip it."),
    ),
    Outcome(
        key="stays_on_array", label="Not on cache", badge="badge-muted",
        icon="server", tier=99,
        tooltip=("On the array. PlexCache caches it only if it lands on OnDeck "
                 "or a Watchlist, or you pin it."),
    ),
)}


def outcome_tooltip(key: str) -> str:
    """Tooltip for an outcome key (empty string when unrecognised)."""
    entry = OUTCOMES.get(key)
    return entry.tooltip if entry else ""
