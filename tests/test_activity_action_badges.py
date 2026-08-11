"""Recent Activity action badges and filter pills.

Two defects these lock down:

* `Moved` and `Released` had no arm in `action_badge`, so both fell through to
  the muted default and were styled like an unrecognised action. `Moved` is the
  main restore-by-copy path and `Released` is a deliberate, user-visible state.
* The "Moved" pill sent ``data-filter="Moved to Array"`` — the label maintenance
  writes — so it matched no entry from an actual run, and those rows landed
  under "Other" instead. The hardcoded ``KNOWN_ACTIONS`` also named `Protected`,
  which has no pill, so those rows matched neither their own filter nor "Other"
  and were reachable only under "All".

Source-text tests: this is template + inline JS with no Python seam, and the
contract that matters is "every action a writer emits is handled".
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ACTIVITY = REPO / "web" / "templates" / "components" / "recent_activity.html"
DASHBOARD = REPO / "web" / "templates" / "dashboard.html"

# Every action string written to the activity feed, with its writer.
#   FileMover._move_to_cache / _move_to_array  -> Cached / Restored / Moved
#   PlexCacheApp._release_files                -> Released
#   MaintenanceRunner.ACTION_ACTIVITY_LABELS   -> the rest
#   web/routers/pinned.py                      -> Cached
ALL_ACTIONS = [
    "Cached", "Restored", "Moved", "Released",
    "Protected", "Moved to Array", "Fixed", "Restored Backup", "Deleted Backup",
]


@pytest.fixture(scope="module")
def activity_src():
    return ACTIVITY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard_src():
    return DASHBOARD.read_text(encoding="utf-8")


class TestWriterInventory:
    def test_action_labels_match_the_maintenance_runner(self):
        """If a maintenance action label is added, this list must grow with it."""
        src = (REPO / "web" / "services" / "maintenance_runner.py").read_text(encoding="utf-8")
        block = src[src.index("ACTION_ACTIVITY_LABELS = {"):]
        block = block[:block.index("}")]
        for label in re.findall(r':\s*"([^"]+)"', block):
            assert label in ALL_ACTIONS, f"maintenance emits {label!r}, not in ALL_ACTIONS"

    def test_release_action_is_still_spelled_the_same(self):
        src = (REPO / "core" / "app.py").read_text(encoding="utf-8")
        assert '_record_file_activity("Released"' in src


class TestBadgeCoverage:
    def test_every_action_has_a_tooltip(self, activity_src):
        tips = activity_src[activity_src.index("ACTION_TOOLTIPS = {"):]
        tips = tips[:tips.index("} %}")]
        for action in ALL_ACTIONS:
            assert f"'{action}':" in tips, f"no tooltip for {action}"

    def test_moved_and_released_are_not_left_to_the_default_arm(self, activity_src):
        macro = activity_src[activity_src.index("{% macro action_badge"):
                             activity_src.index("{% macro source_icon")]
        assert "'Moved'" in macro
        assert "action == 'Released'" in macro

    def test_released_uses_its_own_badge_not_muted(self, activity_src):
        macro = activity_src[activity_src.index("{% macro action_badge"):
                             activity_src.index("{% macro source_icon")]
        released_arm = macro[macro.index("action == 'Released'"):]
        released_arm = released_arm[:released_arm.index("{%- elif")]
        assert "badge-released" in released_arm

    def test_tooltips_are_non_empty(self, activity_src):
        tips = activity_src[activity_src.index("ACTION_TOOLTIPS = {"):]
        tips = tips[:tips.index("} %}")]
        for action, text in re.findall(r"'([^']+)':\s*'([^']*)'", tips):
            assert len(text) > 20, f"{action} tooltip is too short to be useful"


class TestProtectedRelabel:
    """"Protected" is the mover sense, and the same action's own button says
    "Keep on Cache" (tests/test_pin_vocabulary.py guards that wording). Re-label
    at render only — the action string is persisted in recent_activity.json."""

    def test_protected_is_relabelled_for_display(self, activity_src):
        assert "'Protected': 'Kept on Cache'" in activity_src

    def test_relabel_happens_in_the_template_not_the_writer(self):
        # Renaming at the writer would need a migration and would rewrite what
        # already happened; old entries must keep rendering consistently.
        src = (REPO / "web" / "services" / "maintenance_runner.py").read_text(encoding="utf-8")
        assert '"protect-with-backup": "Protected"' in src

    def test_protected_tooltip_disclaims_being_a_pin(self, activity_src):
        tips = activity_src[activity_src.index("ACTION_TOOLTIPS = {"):]
        assert "not a pin" in tips[:tips.index("} %}")].lower()


class TestFilterPills:
    def test_moved_pill_matches_both_restore_labels(self, dashboard_src):
        assert 'data-filter="Moved|Moved to Array"' in dashboard_src

    def test_filter_matching_splits_on_pipe(self, dashboard_src):
        assert "filter.split('|').indexOf(action) !== -1" in dashboard_src

    def test_known_actions_is_derived_from_the_pills(self, dashboard_src):
        # A hardcoded list is what let the two drift.
        assert "KNOWN_ACTIONS:" not in dashboard_src
        assert "knownActions: function()" in dashboard_src

    def test_filter_bar_is_scoped_by_id(self, dashboard_src):
        # Recently Added reuses .activity-filter-bar; an unscoped query would
        # pick up its location pills.
        assert 'id="activity-filter-bar"' in dashboard_src
        assert "#activity-filter-bar .activity-filter-pill" in dashboard_src

    def test_every_pill_action_is_a_real_action(self, dashboard_src):
        bar = dashboard_src[dashboard_src.index('id="activity-filter-bar"'):]
        bar = bar[:bar.index("</div>")]
        for value in re.findall(r'data-filter="([^"]+)"', bar):
            if value in ("all", "other"):
                continue
            for action in value.split("|"):
                assert action in ALL_ACTIONS, f"pill filters on unknown action {action!r}"

    def test_actions_without_a_pill_fall_through_to_other(self, dashboard_src):
        """Anything not named by a pill must be reachable under "Other"."""
        bar = dashboard_src[dashboard_src.index('id="activity-filter-bar"'):]
        bar = bar[:bar.index("</div>")]
        covered = set()
        for value in re.findall(r'data-filter="([^"]+)"', bar):
            if value not in ("all", "other"):
                covered.update(value.split("|"))
        uncovered = [a for a in ALL_ACTIONS if a not in covered]
        # These have no pill of their own, so "Other" is their only filter —
        # which the derived knownActions() now guarantees.
        assert "Released" in uncovered
        assert "Protected" in uncovered
