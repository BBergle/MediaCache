"""Settings copy states the mechanism, not a plausible-sounding summary.

Each check below corresponds to a place where the wording described something
the code does not do:

* OnDeck retention said "auto-expire", which reads as deletion. It stops
  holding the item; a later run moves it back to the array.
* Hard-linked files were described by their inode topology. Detection is
  automatic, so the only choice is what to do with them.
* Nothing said that retention clocks advance only while PlexCache runs, which
  bounds how fine any of these values can usefully be.
* A per-user Days to Monitor override also sets that user's OnDeck retention
  threshold. One field, two clocks, two epochs, and no surface said so.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE_HTML = REPO / "web" / "templates" / "settings" / "cache.html"
USERS_TABLE = REPO / "web" / "templates" / "settings" / "partials" / "users_table.html"
SEARCH_INDEX = REPO / "web" / "settings_search_index.py"


def _hint_for(setting_id: str) -> str:
    """The form-hint text belonging to one setting's form-group."""
    html = CACHE_HTML.read_text(encoding="utf-8")
    start = html.index(f'data-setting-id="{setting_id}"')
    # Up to the next form-group, so a neighbour's hint cannot satisfy the test.
    nxt = html.find("data-setting-id=", start + 1)
    block = html[start:nxt if nxt != -1 else len(html)]
    hints = re.findall(r'<div class="form-hint">(.*?)</div>', block, re.S)
    return " ".join(" ".join(h.split()) for h in hints)


class TestOnDeckRetentionNamesItsMechanism:

    def test_says_it_does_not_delete(self):
        hint = _hint_for("ondeck_retention_days")
        assert "not deleted" in hint, hint

    def test_names_the_epoch(self):
        hint = _hint_for("ondeck_retention_days")
        assert "PlexCache first saw it" in hint, hint

    def test_states_the_across_all_users_rule(self):
        """AND across users, not OR: the last user to finish decides."""
        hint = _hint_for("ondeck_retention_days")
        assert "every" in hint.lower(), hint

    def test_no_longer_claims_auto_expire(self):
        assert "Auto-expire OnDeck" not in CACHE_HTML.read_text(encoding="utf-8")


class TestWatchlistRetentionNamesItsEpoch:

    def test_counts_from_the_plex_date(self):
        hint = _hint_for("watchlist_retention_days")
        assert "added in Plex" in hint, hint
        assert "not from when" in hint, hint


class TestHardlinkedFilesDescribesTheOutcome:

    def test_hint_is_about_the_choice_not_the_inode(self):
        hint = _hint_for("hardlinked_files")
        assert "torrent client" in hint, hint
        assert "automatically" in hint, hint
        assert "hardlinks" not in hint.lower(), f"still explaining inodes: {hint}"

    def test_move_option_no_longer_claims_to_break_links(self):
        """'move' caches the file; the seed survives via the remaining link."""
        html = CACHE_HTML.read_text(encoding="utf-8")
        assert "break hardlinks" not in html, (
            "the 'move' option said it breaks hardlinks, which is the opposite "
            "of what _move_to_cache does for those files"
        )


class TestRetentionClocksAreQualified:

    def test_section_says_clocks_need_runs(self):
        html = CACHE_HTML.read_text(encoding="utf-8")
        retention = html[html.index(">\n                Retention"):]
        assert "only advance while PlexCache is running" in " ".join(retention.split())


class TestPerUserCouplingIsDocumented:

    def test_global_hint_mentions_the_per_user_coupling(self):
        hint = _hint_for("days_to_monitor")
        assert "retention" in hint.lower(), hint

    def test_per_user_tooltip_mentions_both_clocks(self):
        html = USERS_TABLE.read_text(encoding="utf-8")
        tag = re.search(r'<input[^>]*?name="ondeck_days_[^>]*?>', html, re.S).group(0)
        assert "retention threshold" in tag, tag

    def test_per_user_watchlist_input_accepts_half_days(self):
        """The handler parses float; the input has to offer it."""
        html = USERS_TABLE.read_text(encoding="utf-8")
        tag = re.search(r'<input[^>]*?name="watchlist_days_[^>]*?>', html, re.S).group(0)
        assert 'step="0.5"' in tag, (
            f"input rejects the half-days the backend accepts: {tag}"
        )


class TestSearchIndexMatchesTheTemplates:

    @pytest.mark.parametrize("setting_id,phrase", [
        ("ondeck_retention_days", "does not delete"),
        ("hardlinked_files", "torrent client"),
        ("days_to_monitor", "retention threshold"),
    ])
    def test_index_hint_reflects_the_new_copy(self, setting_id, phrase):
        source = SEARCH_INDEX.read_text(encoding="utf-8")
        start = source.index(f'"setting_id": "{setting_id}"')
        entry = source[start:source.index("}", start)]
        assert phrase in entry, f"{setting_id} index hint is stale: {entry}"


class TestBackupsAreNoLongerAFirstRunQuestion:

    def test_wizard_does_not_prompt(self):
        """40 lines of interrogation for a setting almost nobody should change."""
        source = (REPO / "core" / "setup.py").read_text(encoding="utf-8")
        assert "Create .plexcached backups?" not in source

    def test_wizard_still_defaults_it_on(self):
        source = (REPO / "core" / "setup.py").read_text(encoding="utf-8")
        assert "settings_data['create_plexcached_backups'] = True" in source

    def test_web_toggle_now_carries_the_consequence(self):
        """Removing the prompt leaves this as the only surface that warns."""
        hint = _hint_for("create_plexcached_backups")
        assert "cannot be" in hint and "recovered" in hint, hint
        assert "deleted, not renamed" in hint, hint

    def test_web_toggle_does_not_repeat_the_obsolete_hardlink_advice(self):
        hint = _hint_for("create_plexcached_backups")
        assert "do not need it off" in hint, hint
