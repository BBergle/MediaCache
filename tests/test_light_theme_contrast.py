"""
Light theme text contrast.

Plex orange (#e5a00d) and the bright semantic colours are tuned for the dark
theme; as text on white they measure around 2:1. Text uses the *-text tokens,
which equal the accents in dark mode and resolve to deeper shades in light
mode. These tests compute WCAG contrast straight from plex-theme.css and keep
templates from putting the raw accent back on text.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
THEME_CSS = (WEB / "static" / "css" / "plex-theme.css").read_text(encoding="utf-8")
CUSTOM_CSS = (WEB / "static" / "css" / "custom.css").read_text(encoding="utf-8")


def _block(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", THEME_CSS)
    assert match, selector
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1)))


ROOT = _block(":root")
LIGHT = _block('[data-theme="light"]')


def _rgb(value):
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    nums = [float(n) for n in re.findall(r"[\d.]+", value)]
    return tuple(nums[:3]), (nums[3] if len(nums) > 3 else 1.0)


def _over(fg, bg):
    (r, g, b), a = _rgb(fg) if isinstance(fg, str) else fg
    (br, bgc, bb), _ = _rgb(bg) if isinstance(bg, str) else bg
    return (r * a + br * (1 - a), g * a + bgc * (1 - a), b * a + bb * (1 - a)), 1.0


def _luminance(rgb):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg, bg):
    background = _over(bg, "#ffffff")
    text = _over(fg, background)
    hi, lo = sorted((_luminance(text[0]), _luminance(background[0])), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


WHITE = "#ffffff"
SUCCESS_TINT = "rgba(76, 175, 80, 0.15)"   # .badge-success background
INFO_TINT = "rgba(33, 150, 243, 0.15)"     # .badge-info background
NAV_TINT = "rgba(229, 160, 13, 0.12)"      # light active nav background


@pytest.mark.parametrize("token, background, minimum", [
    ("--plex-orange-text", WHITE, 4.5),
    ("--plex-orange-text-lg", WHITE, 3.0),
    ("--plex-success-text", SUCCESS_TINT, 4.5),
    ("--plex-info-text", INFO_TINT, 4.5),
    ("--plex-text-muted", WHITE, 4.5),
    ("--plex-text", NAV_TINT, 4.5),
])
def test_light_text_tokens_meet_wcag(token, background, minimum):
    assert contrast(LIGHT[token], background) >= minimum, (token, LIGHT[token])


def test_light_muted_text_readable_on_page_background():
    assert contrast(LIGHT["--plex-text-muted"], LIGHT["--plex-bg-darker"]) >= 4.5


def test_raw_orange_fails_as_light_text():
    """Documents why the token exists: the accent itself is not readable on white."""
    assert contrast(ROOT["--plex-orange"], WHITE) < 3.0


@pytest.mark.parametrize("token, accent", [
    ("--plex-orange-text", "var(--plex-orange)"),
    ("--plex-orange-text-lg", "var(--plex-orange)"),
    ("--plex-success-text", "var(--plex-success)"),
    ("--plex-info-text", "var(--plex-info)"),
])
def test_dark_theme_text_tokens_are_the_accents(token, accent):
    """Dark mode must look exactly as before."""
    assert ROOT[token].strip() == accent


def test_templates_do_not_use_raw_orange_on_text():
    """Inline orange is fine on icons, and on wrappers holding only an icon; text uses var(--plex-orange-text)."""
    tag = re.compile(r'<(\w+)\b[^<>]*?style="[^"]*?(?<![-\w])color: *var\(--plex-orange(?:, *#e5a00d)?\)[^>]*>', re.S)
    icon_only = re.compile(r'\s*<i\b[^>]*></i>\s*</\w+>')
    offenders = []
    for template in (WEB / "templates").rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        for match in tag.finditer(source):
            if match.group(1).lower() in ("i", "svg") or icon_only.match(source, match.end()):
                continue
            line = source[:match.start()].count("\n") + 1
            offenders.append(f"{template.relative_to(WEB)}:{line} <{match.group(1)}>")
    assert not offenders, "Use var(--plex-orange-text) for text: " + ", ".join(offenders)


ICON_RULES_WITH_ORANGE = {
    ".card-header i", "th.sortable .sort-icon i", ".theme-toggle:hover",
    ".sidebar-nav li a.active", ".ss-result-icon",
    '[data-theme="light"] .sidebar-nav li a.active svg',
}


def test_stylesheets_only_use_raw_orange_text_on_icons():
    offenders = []
    for css in (THEME_CSS, CUSTOM_CSS):
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            selector = re.sub(r"/\*.*?\*/", "", selector, flags=re.S).strip()
            if re.search(r"(^|[;\s])color:\s*var\(--plex-orange(, *#e5a00d)?\)", body):
                if selector not in ICON_RULES_WITH_ORANGE:
                    offenders.append(selector)
    assert not offenders, offenders
