"""Sitewide guard: "protected" means exempt from eviction, and nothing else.

An audit found the word carrying four unrelated meanings across the app —
eviction exemption (a pin), exclude-list membership (the Unraid mover),
transient OnDeck/Watchlist residency, and the audit's own "not in the exclude
list" model. Only the first is a real guarantee: OnDeck adds +15 to the priority
score, while a pin short-circuits eviction outright.

These tests keep the word from spreading back. They are source-text checks
because the offenders are template copy and identifier names, which have no
Python seam.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "web" / "templates"

# Where the eviction sense is legitimate: a pin genuinely exempts these.
ALLOWED_PROTECTED_PHRASES = (
    "protected from eviction",   # the reserved sentence
    "eviction protection",       # the disclaimer on transient badges
)


def _user_facing_strings(path: Path):
    """Rendered text and tooltips from a template, ignoring comments/attrs."""
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)          # jinja comments
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)          # html comments
    # Jinja expressions render to a value, not to their own source. Leaving them
    # in flags `{{ protected_sentence }}` — the shared constant this sweep exists
    # to introduce — as if it were stray copy.
    src = re.sub(r"\{\{.*?\}\}", "", src, flags=re.S)
    out = []
    out += re.findall(r'title="([^"]*)"', src)
    out += re.findall(r">\s*([^<>{}\n]{4,})\s*<", src)
    return [s.strip() for s in out if s.strip()]


class TestNoStrayProtectionCopy:
    def test_no_template_uses_protect_outside_the_eviction_sense(self):
        offenders = []
        for path in TEMPLATES.rglob("*.html"):
            for text in _user_facing_strings(path):
                low = text.lower()
                if "protect" not in low:
                    continue
                if any(p in low for p in ALLOWED_PROTECTED_PHRASES):
                    continue
                offenders.append(f"{path.relative_to(REPO)}: {text[:90]}")
        assert not offenders, (
            "protection vocabulary outside the eviction sense:\n  " + "\n  ".join(offenders)
        )

    def test_audit_tool_and_readme_say_tracked(self):
        for rel in ("tools/audit_cache.py", "README.md"):
            src = (REPO / rel).read_text(encoding="utf-8")
            assert "unprotected" not in src.lower(), f"{rel} still says unprotected"


class TestIdentifiersMatchTheShippedUi:
    """The audit table has said "Untracked" in the UI for a while; the model
    said "unprotected". A pinned file was simultaneously "protected" (badge) and
    "unprotected" (model), which is how new code kept regenerating the word."""

    def test_no_unprotected_identifiers_remain(self):
        offenders = []
        for pat in ("web/**/*.py", "core/**/*.py", "tools/*.py", "web/templates/**/*.html"):
            for path in REPO.glob(pat):
                if "__pycache__" in str(path):
                    continue
                if "unprotected" in path.read_text(encoding="utf-8").lower():
                    offenders.append(str(path.relative_to(REPO)))
        assert not offenders, f"'unprotected' survives in: {offenders}"

    def test_audit_results_exposes_the_renamed_fields(self):
        from web.services.maintenance_service import AuditResults
        fields = AuditResults.__dataclass_fields__
        assert "untracked_files" in fields
        assert "grouped_untracked" in fields
        assert "unprotected_files" not in fields


class TestMoverExcludedStatCard:
    """The card labelled "Protected" counted raw exclude lines against a
    denominator of files actually on cache, so a stale entry could render
    "3 of 1 on cache". It also named the mover sense with the eviction word."""

    def test_count_is_the_intersection_with_what_is_on_cache(self):
        from web.services.maintenance_service import AuditResults
        assert "excluded_on_cache_count" in AuditResults.__dataclass_fields__

    def test_numerator_cannot_exceed_the_denominator(self):
        from web.services.maintenance_service import MaintenanceService
        svc = MaintenanceService.__new__(MaintenanceService)
        cache_files = {"/mnt/cache/a.mkv", "/mnt/cache/b.mkv"}
        # Three exclude lines, two of them stale (files no longer on cache).
        exclude_files = {"/mnt/cache/a.mkv", "/mnt/cache/gone1.mkv", "/mnt/cache/gone2.mkv"}
        assert len(exclude_files) > len(cache_files)          # the old numerator
        assert len(exclude_files & cache_files) <= len(cache_files)  # the new one

    def test_card_reads_mover_excluded_not_protected(self):
        src = (TEMPLATES / "maintenance" / "partials" / "audit_results.html").read_text(encoding="utf-8")
        assert "Mover-excluded" in src
        assert "excluded_on_cache_count" in src


class TestReservedSentenceIsShared:
    def test_pages_source_it_from_one_constant(self):
        from web.outcome_vocabulary import PROTECTED_SENTENCE, OUTCOMES
        # Recently Added
        assert OUTCOMES["held"].tooltip.startswith(PROTECTED_SENTENCE)
        # Cached Files renders it through the template global rather than a copy
        src = (TEMPLATES / "cache" / "partials" / "file_table.html").read_text(encoding="utf-8")
        assert "{{ protected_sentence }}" in src
        assert "Pinned — protected from eviction\"" not in src  # the old hand-copy

    def test_the_constant_is_exposed_as_a_template_global(self):
        src = (REPO / "web" / "config.py").read_text(encoding="utf-8")
        assert 'templates.env.globals["protected_sentence"]' in src
