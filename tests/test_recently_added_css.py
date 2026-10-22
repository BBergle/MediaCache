"""Contrast gate for the Recently Added group-child rows.

Asserting that a design token is *used* is not enough: several tokens satisfy
that and still leave the row without visible hover feedback. So this resolves
the declaration the same way a browser does — token lookup per theme, alpha
compositing over the card surface — and checks the resting/hover delta is
actually perceptible in both themes.

Pure text + arithmetic, no browser. Follows the CSS-text precedent in
tests/test_released_ui_state.py.
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME_CSS = os.path.join(ROOT, "web/static/css/plex-theme.css")
CUSTOM_CSS = os.path.join(ROOT, "web/static/css/custom.css")

# Below this the row reads as static to a sighted user at a glance. HEAD's
# hardcoded rgba scored 1.03 in light theme, which is why this file exists.
MIN_HOVER_CONTRAST = 1.15


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _tokens(css, block_selector):
    """Pull the --plex-* hex values out of one selector block."""
    start = css.index(block_selector)
    body = css[start:css.index("}", start)]
    return {
        name: value
        for name, value in re.findall(r"(--plex-[\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", body)
    }


def _rgb(value):
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _composite(fg, alpha, bg):
    return tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))


def _relative_luminance(rgb):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _resolve(declaration, tokens, backdrop):
    """Resolve a background value to RGB, compositing alpha over the backdrop."""
    declaration = declaration.strip().rstrip(";").strip()
    var_match = re.fullmatch(r"var\((--plex-[\w-]+)\)", declaration)
    if var_match:
        return _rgb(tokens[var_match.group(1)])
    rgba_match = re.fullmatch(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", declaration)
    if rgba_match:
        r, g, b, a = rgba_match.groups()
        return _composite((int(r), int(g), int(b)), float(a) if a else 1.0, backdrop)
    if declaration.startswith("#"):
        return _rgb(declaration)
    pytest.fail(f"Cannot resolve background {declaration!r} — extend this helper.")


def _group_child_background():
    css = _read(CUSTOM_CSS)
    match = re.search(r"\.ra-group-child\s*\{([^}]*)\}", css)
    assert match, ".ra-group-child rule not found in custom.css"
    decl = re.search(r"background:\s*([^;]+);", match.group(1))
    assert decl, ".ra-group-child has no background declaration"
    return decl.group(1)


@pytest.mark.parametrize("theme,selector", [
    ("dark", ":root {"),
    ("light", '[data-theme="light"] {'),
])
def test_group_child_rows_have_visible_hover_feedback(theme, selector):
    """The rows are clickable (RecentlyAdded.toggleDetail on the title cell),
    so the hover state has to be distinguishable from rest."""
    theme_css = _read(THEME_CSS)
    tokens = _tokens(theme_css, selector)
    if theme == "light":
        # Light overrides only what it changes; the rest inherits from :root.
        base = _tokens(theme_css, ":root {")
        tokens = {**base, **tokens}

    card = _rgb(tokens["--plex-bg-card"])
    resting = _resolve(_group_child_background(), tokens, card)
    # tbody tr:hover (plex-theme.css) paints an opaque token over the row.
    hover = _rgb(tokens["--plex-bg-hover"])

    ratio = _contrast(resting, hover)
    assert ratio >= MIN_HOVER_CONTRAST, (
        f"{theme} theme: .ra-group-child rests at {resting} and hovers to {hover}, "
        f"contrast {ratio:.3f} < {MIN_HOVER_CONTRAST}. The row would look static."
    )


def test_group_child_uses_a_theme_token():
    """A hardcoded rgba cannot follow the theme, which is how the light-theme
    hover died in the first place (CLAUDE.md design-token rule)."""
    assert "var(--plex-" in _group_child_background()


def test_no_light_theme_companion_outranks_the_hover_rule():
    """A `[data-theme="light"] .ra-group-child` override scores (0,2,0) and
    beats `tbody tr:hover` at (0,1,2), which removes hover feedback entirely
    rather than fixing it. The one-line token swap avoids the trap by keeping
    the selector at (0,1,0)."""
    css = _read(CUSTOM_CSS)
    offenders = re.findall(r'\[data-theme="[^"]+"\]\s+\.ra-group-child\s*\{[^}]*background',
                           css)
    assert not offenders, (
        "A theme-scoped .ra-group-child background outranks tbody tr:hover. "
        "Change the token on the base rule instead."
    )
