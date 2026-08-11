"""Outcome vocabulary for Recently Added.

The defect this replaces: the group-header badge chain lived in Jinja and ended
in `{% else %}` → "Protected", so a group of three episodes sitting on the array
with nothing pinned rendered a reassuring green "Protected". Groups render
collapsed by default, so that false badge was the default view on the one page
whose job is answering "is this held or not".

Three classes of test here:
  1. the classifier maps facts → exactly one outcome;
  2. the group reduction leads with its weakest member;
  3. source-text exhaustiveness — every enum member is handled explicitly in the
     template, and the template names no outcome the enum can't emit. These are
     what keep the neutral `{% else %}` guard unreachable in practice.
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
sys.modules.setdefault('apscheduler', MagicMock())
sys.modules.setdefault('apscheduler.schedulers', MagicMock())
sys.modules.setdefault('apscheduler.schedulers.background', MagicMock())
sys.modules.setdefault('apscheduler.triggers', MagicMock())
sys.modules.setdefault('apscheduler.triggers.cron', MagicMock())
sys.modules.setdefault('apscheduler.triggers.interval', MagicMock())
sys.modules.setdefault('plexapi', MagicMock())
sys.modules.setdefault('plexapi.server', MagicMock())

from web.outcome_vocabulary import OUTCOMES, outcome_tooltip
from web.services.recently_added_service import RecentlyAddedRow, RecentlyAddedService

REPO = Path(__file__).resolve().parent.parent
LIST_TEMPLATE = REPO / "web" / "templates" / "recently_added" / "partials" / "list.html"

_classify = RecentlyAddedService._classify_outcome


def _facts(**over):
    base = dict(
        pin_index_available=True, is_pinned=False, location="cache",
        is_mover_protected=True, watched_move=True,
        is_ondeck=False, is_watchlist=False,
    )
    base.update(over)
    return base


class TestClassifier:
    def test_pinned_and_on_cache(self):
        assert _classify(**_facts(is_pinned=True)) == "held"

    def test_pinned_but_not_yet_on_cache(self):
        # The state Option B could not represent without stating something false.
        assert _classify(**_facts(is_pinned=True, location="array")) == "arriving"

    def test_ondeck_on_cache(self):
        assert _classify(**_facts(is_ondeck=True)) == "returns_when_done"

    def test_watchlist_on_cache(self):
        assert _classify(**_facts(is_watchlist=True)) == "returns_when_done"

    def test_on_cache_no_longer_held(self):
        assert _classify(**_facts()) == "moves_back"

    def test_on_cache_but_not_in_exclude_list_is_the_movers_call(self):
        # Covers both a deliberately released file and a never-tracked fresh
        # download — the most common state on this page.
        assert _classify(**_facts(is_mover_protected=False)) == "mover_decides"

    def test_watched_move_off_holds_everything_on_cache(self):
        # A and B are both wrong on every cached row when this setting is off.
        assert _classify(**_facts(watched_move=False)) == "held_by_setting"
        assert _classify(**_facts(watched_move=False, is_ondeck=True)) == "held_by_setting"

    def test_on_array(self):
        assert _classify(**_facts(location="array")) == "stays_on_array"

    def test_unmapped_path(self):
        assert _classify(**_facts(location="unknown")) == "unmapped"

    def test_pin_state_unknown_outranks_everything(self):
        # A row whose pin state is unverified cannot honestly claim any lower
        # outcome — including "unmapped".
        assert _classify(**_facts(pin_index_available=False)) == "pin_unknown"
        assert _classify(**_facts(pin_index_available=False, location="unknown")) == "pin_unknown"
        assert _classify(**_facts(pin_index_available=False, is_pinned=True)) == "pin_unknown"

    def test_every_emitted_value_is_a_known_outcome(self):
        import itertools
        for combo in itertools.product([True, False], repeat=6):
            avail, pinned, mover, wm, od, wl = combo
            for loc in ("cache", "array", "unknown"):
                got = _classify(pin_index_available=avail, is_pinned=pinned, location=loc,
                                is_mover_protected=mover, watched_move=wm,
                                is_ondeck=od, is_watchlist=wl)
                assert got in OUTCOMES, got


def _row(outcome, ep=1):
    return RecentlyAddedRow(
        rating_key=str(ep), title=f"E{ep}", media_type="episode", library_title="TV",
        file_path=f"/data/{ep}.mkv", size=100, size_display="100 B",
        added_at=datetime.now(), added_display="1 hr ago",
        location="cache", state="on_cache_not_pinned", outcome=outcome,
        episode_info={"show": "Show", "season": 1, "episode": ep},
    )


class TestGroupReduction:
    def test_all_on_array_does_not_claim_protection(self):
        # The exact shape that used to render a green "Protected".
        g = RecentlyAddedService.group_rows_for_display(
            [_row("stays_on_array", i) for i in (1, 2, 3)]
        )[0]
        assert g["outcome_lead"] == "stays_on_array"
        assert "Protected" not in g["outcome_label"]
        assert g["outcome_label"] == "Not on cache"

    def test_all_unmapped_does_not_claim_protection(self):
        g = RecentlyAddedService.group_rows_for_display(
            [_row("unmapped", i) for i in (1, 2, 3)]
        )[0]
        assert g["outcome_lead"] == "unmapped"
        assert "Protected" not in g["outcome_label"]

    def test_weakest_member_leads(self):
        # 2 held + 1 leaving must not read like a healthy group.
        g = RecentlyAddedService.group_rows_for_display(
            [_row("held", 1), _row("held", 2), _row("moves_back", 3)]
        )[0]
        assert g["outcome_lead"] == "moves_back"
        assert g["outcome_label"] == "1 of 3 Moves to array"
        assert g["outcome_mixed"] is True

    def test_unanimous_group_has_a_bare_label(self):
        g = RecentlyAddedService.group_rows_for_display([_row("held", i) for i in (1, 2)])[0]
        assert g["outcome_label"] == "Stays on cache"
        assert g["outcome_mixed"] is False

    def test_stays_on_array_yields_to_a_real_outcome(self):
        # A file that never arrived on cache cannot leave it, so it sits outside
        # the ranking and only leads when unanimous.
        g = RecentlyAddedService.group_rows_for_display(
            [_row("stays_on_array", 1), _row("stays_on_array", 2), _row("moves_back", 3)]
        )[0]
        assert g["outcome_lead"] == "moves_back"

    def test_pin_unknown_leads_over_everything(self):
        g = RecentlyAddedService.group_rows_for_display(
            [_row("held", 1), _row("pin_unknown", 2)]
        )[0]
        assert g["outcome_lead"] == "pin_unknown"

    def test_counts_partition_the_group(self):
        rows = [_row("held", 1), _row("moves_back", 2), _row("moves_back", 3)]
        g = RecentlyAddedService.group_rows_for_display(rows)[0]
        assert sum(g["outcome_counts"].values()) == g["episode_count"] == 3

    def test_tooltip_lists_every_present_outcome(self):
        g = RecentlyAddedService.group_rows_for_display(
            [_row("held", 1), _row("moves_back", 2)]
        )[0]
        assert "1 Stays on cache" in g["outcome_tooltip"]
        assert "1 Moves to array" in g["outcome_tooltip"]


class TestTemplateExhaustiveness:
    """Source-text checks — the enum and the template must not drift apart."""

    @pytest.fixture(scope="class")
    def source(self):
        return LIST_TEMPLATE.read_text(encoding="utf-8")

    def test_every_outcome_is_handled_explicitly_in_the_row_badge(self, source):
        row_macro = source[source.index("{% macro ra_status_badge"):source.index("{% macro ra_row")]
        for key in OUTCOMES:
            assert f"row.outcome == '{key}'" in row_macro, f"row badge does not handle {key}"

    def test_every_outcome_is_handled_explicitly_in_the_group_header(self, source):
        for key in OUTCOMES:
            assert f"item.outcome_lead == '{key}'" in source, f"group header does not handle {key}"

    def test_template_names_no_outcome_the_enum_cannot_emit(self, source):
        named = set(re.findall(r"(?:row\.outcome|item\.outcome_lead) == '([a-z_]+)'", source))
        assert named <= set(OUTCOMES), f"template names unknown outcomes: {named - set(OUTCOMES)}"

    def test_no_bare_protected_badge_survives(self, source):
        # "Protected" may appear only inside a tooltip sentence, never as badge text.
        assert ">Protected<" not in source
        assert "> Protected<" not in source


class TestReservedProtectedSentence:
    """The owner's constraint: "Protected" means protected from EVICTION only."""

    SENTENCE = "Pinned — protected from eviction."

    def test_only_pin_backed_outcomes_use_the_word(self):
        for key, outcome in OUTCOMES.items():
            if "protected from eviction" in outcome.tooltip:
                assert key in ("held", "held_mover_gap"), (
                    f"{key} claims eviction protection but is not pin-backed"
                )

    def test_on_cache_pinned_states_carry_it_verbatim(self):
        for key in ("held", "held_mover_gap"):
            assert OUTCOMES[key].tooltip.startswith(self.SENTENCE)

    def test_arriving_makes_no_eviction_claim(self):
        # Nothing is on cache to evict, so the sentence would be vacuous.
        assert "protected from eviction" not in OUTCOMES["arriving"].tooltip

    def test_pinned_tooltips_also_name_the_move_back_guarantee(self):
        # A pin short-circuits the watched move-back too
        # (core/file_operations.py:3814), so "eviction" alone undersells it.
        for key in ("held", "held_mover_gap"):
            assert "move it back" in OUTCOMES[key].tooltip

    def test_only_held_claims_mover_protection(self):
        # Pinning writes no exclude line, so mover protection is not a pin's to
        # promise until a run has added one.
        assert "mover is told to skip it" in OUTCOMES["held"].tooltip
        assert "could still relocate it" in OUTCOMES["held_mover_gap"].tooltip

    def test_no_two_outcomes_share_a_label_unless_they_share_a_badge(self):
        # At group level only the label survives — the badge colour and icon
        # that distinguished two same-labelled states are gone.
        seen = {}
        for key, o in OUTCOMES.items():
            if o.label in seen:
                other = seen[o.label]
                assert o.badge == OUTCOMES[other].badge, (
                    f"{key} and {other} render the same label {o.label!r} with "
                    f"different badges — indistinguishable in a group header"
                )
            seen.setdefault(o.label, key)

    def test_transient_states_never_claim_protection(self):
        # OnDeck/Watchlist only add +15 to the priority score; they exempt nothing.
        for key in ("returns_when_done", "moves_back", "mover_decides",
                    "stays_on_array", "unmapped", "pin_unknown", "held_by_setting"):
            assert "protected" not in OUTCOMES[key].tooltip.lower()

    def test_every_outcome_has_a_tooltip(self):
        for key, outcome in OUTCOMES.items():
            assert outcome.tooltip.strip(), f"{key} has no tooltip"
            assert outcome_tooltip(key) == outcome.tooltip

    def test_unknown_key_yields_empty_tooltip(self):
        assert outcome_tooltip("nonexistent") == ""

    def test_tiers_are_unique_among_ranked_outcomes(self):
        ranked = [o.tier for o in OUTCOMES.values() if o.tier < 99]
        assert len(ranked) == len(set(ranked))
