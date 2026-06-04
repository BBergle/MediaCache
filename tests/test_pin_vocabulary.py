"""Vocabulary-clarity guards for the Pin vs "Keep on Cache" distinction.

PlexCache has two related-but-different protect-on-cache concepts:

* **Pin** (``core/pinned_media.py``, keyed by rating_key) — deliberate
  curation: the item is *always* kept on cache, re-applied every run, and
  never evicted. Surfaced in Settings → Cache and the Cached Files badge.
* **Keep on Cache** (``MaintenanceService.protect_with_backup``, keyed by
  path) — a *one-time* triage action on the Maintenance → Untracked Files
  card, paired with "Move to Array". It backs the file up + excludes it once,
  but does NOT create a pin.

These tests assert the UI copy keeps that distinction legible so the two
mechanisms don't silently blur into "same name, different behavior". The
Untracked Files partial is large and depends on a fully-populated ``results``
object, so this guards the template source text directly rather than rendering.
"""

from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "web" / "templates"
AUDIT_RESULTS = TEMPLATE_ROOT / "maintenance" / "partials" / "audit_results.html"


def _audit_source() -> str:
    return AUDIT_RESULTS.read_text(encoding="utf-8")


class TestKeepOnCacheClarity:
    def test_keep_on_cache_button_tooltip_says_one_time_not_pin(self):
        src = _audit_source()
        assert "This is a one-time action, not a pin" in src
        # Points users at the real pin surface for permanent caching.
        assert "pin it in Settings → Cache" in src

    def test_confirm_modal_distinguishes_keep_from_pin(self):
        src = _audit_source()
        assert "one-time keep" in src
        assert "won't be re-cached automatically on future runs" in src

    def test_confirm_modal_step_clarifies_not_a_pin(self):
        src = _audit_source()
        assert "one-time — not a pin" in src

    def test_keep_on_cache_name_is_retained(self):
        """The 'Keep on Cache' verb stays distinct from 'Pin' on purpose —
        it's the one-time triage action, paired with 'Move to Array'."""
        src = _audit_source()
        assert "Keep on Cache" in src
        assert "Move to Array" in src
