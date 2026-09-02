"""Web-layer defaults must agree with the engine's.

``core.config.CacheConfig`` is what actually runs. When the web layer repeats a
default as a literal, the two can drift, and the drift is invisible: the UI
shows one number, the run uses another. Two concrete cases this guards:

* ``cache_limit`` defaulted to ``"250GB"`` in the web getter against core's
  ``""``. Saving any unrelated field on the Cache tab wrote that cap to disk,
  and since ``cache_limit`` gates both eviction entry points
  (``core/app.py`` ``_filter_low_priority_files`` and ``_run_smart_eviction``),
  it also armed eviction for someone who never asked for it.
* ``cache_eviction_threshold_percent`` defaulted to 95 across seven web sites
  against core's 90, so the cache page drew the eviction line in the wrong
  place.

Settings deliberately absent here are ones the web layer has no business
defaulting at all.
"""

import sys
from unittest.mock import MagicMock

import pytest

for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from core.config import CacheConfig  # noqa: E402

SHARED = [
    "cache_limit",
    "cache_eviction_mode",
    "cache_eviction_threshold_percent",
    "eviction_min_priority",
    "cache_retention_hours",
    "watchlist_retention_days",
    "ondeck_retention_days",
]


@pytest.mark.parametrize("setting", SHARED)
def test_web_getter_matches_core(setting, tmp_path, monkeypatch):
    """get_cache_settings() on an empty settings file returns core's default."""
    from web.services.settings_service import SettingsService

    service = object.__new__(SettingsService)
    monkeypatch.setattr(SettingsService, "_load_raw", lambda self: {})

    values = SettingsService.get_cache_settings(service)
    if setting not in values:
        pytest.skip(f"{setting} is not surfaced by get_cache_settings()")

    assert values[setting] == getattr(CacheConfig, setting), (
        f"web default for {setting} is {values[setting]!r}, "
        f"core default is {getattr(CacheConfig, setting)!r}"
    )


@pytest.mark.parametrize("setting", SHARED)
def test_pydantic_model_matches_core(setting):
    """The declared model agrees too, so reading it does not mislead."""
    from web.models.settings import CacheSettingsModel

    fields = CacheSettingsModel.model_fields
    if setting not in fields:
        pytest.skip(f"{setting} is not on CacheSettingsModel")

    assert fields[setting].default == getattr(CacheConfig, setting), (
        f"CacheSettingsModel.{setting} defaults to {fields[setting].default!r}, "
        f"core defaults to {getattr(CacheConfig, setting)!r}"
    )


def test_no_hardcoded_cache_limit_literal():
    """The 250GB fallback is gone from every web source."""
    import pathlib
    import re
    web = pathlib.Path(__file__).resolve().parent.parent / "web"

    # Only a *default* counts: an assignment, a .get() fallback keyed by name,
    # or a Jinja default(). "250GB" as an example inside a placeholder or a
    # validation message is legitimate and must not trip this.
    as_default = re.compile(
        r"""=\s*["']250GB["']"""                             # x = "250GB"
        r"""|\.get\(\s*["'][^"']+["']\s*,\s*["']250GB["']"""  # .get("k", "250GB")
        r"""|default\(\s*["']250GB["']\s*\)"""                # | default('250GB')
    )

    offenders = [
        f"{p.relative_to(web)}:{i}"
        for p in list(web.rglob("*.py")) + list(web.rglob("*.html"))
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if as_default.search(line)
    ]

    assert not offenders, f"hardcoded 250GB default still present: {offenders}"
