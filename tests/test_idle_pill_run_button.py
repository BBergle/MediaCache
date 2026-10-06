"""
The operation pill is the single place to start a run.

The Dashboard header used to carry its own Verbose / Run Now / Dry Run
controls, duplicating the pill with a separate Verbose setting and a disabled
state evaluated only at page load. Both idle pills now carry a labelled Run
button, and every clickable pill is keyboard focusable.
"""

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

# Load templates straight from disk rather than through web.config: several test
# modules replace web.config in sys.modules with a MagicMock at import time, so
# importing it here would depend on collection order.
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "web" / "templates"
BANNER = "components/global_operation_banner.html"


def _render(**ctx):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    # Filters the app registers on the shared instance; not exercised by the idle pills.
    for name in ("truncate_filename", "format_bytes", "format_time", "format_datetime"):
        env.filters[name] = lambda value, *args, **kwargs: value
    ctx.setdefault("status", {"state": "idle"})
    return env.get_template(BANNER).render(**ctx)


@pytest.mark.parametrize("scheduler_status, pill_id", [
    ({"next_run_relative": "32m"}, "idle-countdown-pill"),
    (None, "idle-ready-pill"),
], ids=["countdown", "ready"])
def test_idle_pill_has_run_button(scheduler_status, pill_id):
    html = _render(scheduler_status=scheduler_status)
    pill = html[html.index(f'id="{pill_id}"'):]
    compact = pill[:pill.index('class="di-pill__expanded"')]

    assert 'class="di-btn di-btn--primary di-btn--run"' in compact
    assert "diTriggerRun(false)" in compact
    assert ">Run" in compact
    # Hovering the button must not start the pill's expand timer
    assert "clearTimeout(_diHoverTimer)" in compact


def test_run_button_stops_click_reaching_the_pill():
    html = _render(scheduler_status={"next_run_relative": "32m"})
    button = re.search(r'<button[^>]*di-btn--run[^>]*>', html, re.S).group(0)
    assert "event.stopPropagation(); diTriggerRun(false);" in button


def test_expanded_idle_pill_keeps_verbose_and_dry_run():
    html = _render(scheduler_status={"next_run_relative": "32m"})
    expanded = html[html.index('class="di-pill__expanded"'):]
    assert 'id="di-verbose-cb"' in expanded
    assert "diTriggerRun(true)" in expanded


def test_clickable_pills_are_focusable():
    source = (TEMPLATES_DIR / BANNER).read_text(encoding="utf-8")
    clickable = re.findall(r'<div class="di-pill[^>]*onclick="diPillClick\(\)"[^>]*>', source, re.S)
    assert clickable
    assert all('tabindex="0"' in tag for tag in clickable)


def test_dashboard_header_has_no_run_controls():
    source = (TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    header = source[source.index('<header class="page-header">'):source.index("</header>")]
    for marker in ('id="run-form"', 'id="verbose-toggle"', 'id="dry-run-input"', "Run Now", "Dry Run"):
        assert marker not in header, marker
    assert "plexcache_dashboard_verbose" not in source
