"""
Sidebar icons keep their size when the sidebar is collapsed.

Lucide replaces every <i data-lucide> with an <svg> when the page loads, so
rules that only target `i` never reach the rendered icon. The sidebar's size
rules target the svg as well, with flex-shrink: 0, and collapsed links drop
their horizontal padding so a 24px icon fits the 60px rail.
"""

import re
from pathlib import Path

THEME_CSS = (Path(__file__).resolve().parents[1] / "web" / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")


def _rule(selector_fragment):
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", THEME_CSS):
        selectors = [s.strip() for s in re.sub(r"/\*.*?\*/", "", selector, flags=re.S).split(",")]
        if selector_fragment in selectors:
            return dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", body))
    raise AssertionError(f"no rule for {selector_fragment}")


def test_icon_rules_reach_the_rendered_svg():
    for selector in (".sidebar-nav li a svg", ".sidebar-logout-btn svg", ".sidebar-collapse-btn svg"):
        rule = _rule(selector)
        assert rule.get("flex-shrink") == "0", selector
        assert rule.get("width") == "24px" and rule.get("height") == "24px", selector


def test_collapsed_items_leave_room_for_the_icon():
    for selector in (".sidebar.collapsed .sidebar-nav li a",
                     ".sidebar.collapsed .sidebar-logout-btn",
                     ".sidebar.collapsed .sidebar-collapse-btn"):
        padding = _rule(selector)["padding"].split()
        assert len(padding) == 2 and padding[1] == "0", (selector, padding)


def test_collapse_icon_flips_when_collapsed():
    assert "rotate(180deg)" in _rule(".sidebar.collapsed .sidebar-collapse-btn svg")["transform"]


def test_collapsed_items_keep_expanded_height():
    """Collapsing only removes the labels: same vertical padding, no thicker border."""
    expanded = _rule(".sidebar-nav li a")["padding"].split()[0]
    collapsed = _rule(".sidebar.collapsed .sidebar-nav li a")
    assert collapsed["padding"].split()[0] == expanded
    assert "border-bottom" not in collapsed and "border-bottom-width" not in collapsed
    active = _rule(".sidebar.collapsed .sidebar-nav li a.active")
    assert "inset" in active.get("box-shadow", "")
    assert "border-bottom-color" not in active


def test_collapsed_brand_keeps_its_height():
    """The brand text is hidden in place, not removed, so the nav does not move up."""
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", THEME_CSS):
        selectors = [s.strip() for s in re.sub(r"/\*.*?\*/", "", selector, flags=re.S).split(",")]
        if ".sidebar.collapsed .sidebar-brand-text" in selectors and "@media" not in selector:
            assert "display: none" not in body.replace(":none", ": none")
    assert _rule(".sidebar.collapsed .sidebar-brand-text").get("visibility") == "hidden"
