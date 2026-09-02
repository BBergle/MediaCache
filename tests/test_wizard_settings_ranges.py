"""The setup wizard must be able to represent values the engine treats as valid.

Two retention settings use 0 as a documented "disabled" sentinel:

* ``cache_retention_hours`` 0 means no move-back delay
* ``watchlist_retention_days`` 0 means never expire (``core/config.py:160``)

If the wizard's input declares ``min="1"``, a user who deliberately set 0 cannot
be shown their own value, and re-running setup silently raises it. The same trap
exists in the template expression: ``{{ x or 12 }}`` renders 12 for a stored 0,
because 0 is falsy in Jinja, so these fields must use the ``default()`` filter,
which only fires on undefined.
"""

import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
WIZARD = WEB / "templates" / "setup" / "step5.html"
CACHE_SETTINGS = WEB / "templates" / "settings" / "cache.html"

# Settings whose 0 is meaningful rather than "unset".
ZERO_IS_VALID = ["cache_retention_hours", "watchlist_retention_days"]


def _input_tag(template: pathlib.Path, name: str) -> str:
    match = re.search(
        r'<input[^>]*?name="%s"[^>]*?>' % re.escape(name),
        template.read_text(encoding="utf-8"),
        re.S,
    )
    assert match, f"no input named {name} in {template.name}"
    return match.group(0)


@pytest.mark.parametrize("setting", ZERO_IS_VALID)
@pytest.mark.parametrize("template", [WIZARD, CACHE_SETTINGS], ids=["wizard", "settings"])
def test_zero_is_reachable(template, setting):
    """The input accepts 0, so a disabled setting can round-trip."""
    tag = _input_tag(template, setting)
    minimum = re.search(r'min="([^"]*)"', tag)

    assert minimum is not None, f"{setting} in {template.name} declares no min"
    assert float(minimum.group(1)) == 0, (
        f"{setting} in {template.name} has min={minimum.group(1)}, "
        f"so a stored 0 cannot be displayed"
    )


@pytest.mark.parametrize("setting", ZERO_IS_VALID)
@pytest.mark.parametrize("template", [WIZARD, CACHE_SETTINGS], ids=["wizard", "settings"])
def test_value_expression_does_not_swallow_zero(template, setting):
    """`{{ x or N }}` renders N for a stored 0. Use `default(N)` instead."""
    tag = _input_tag(template, setting)
    value = re.search(r'value="\{\{(.*?)\}\}"', tag, re.S)

    assert value is not None, f"{setting} in {template.name} has no value expression"
    assert not re.search(r"\bor\s+[\d.]+", value.group(1)), (
        f"{setting} in {template.name} uses `or` as its fallback, which "
        f"replaces a stored 0. Use the default() filter."
    )


def test_cache_retention_range_agrees_across_templates():
    """Both surfaces offer the same ceiling, so neither truncates the other."""
    def bounds(template):
        tag = _input_tag(template, "cache_retention_hours")
        return (re.search(r'min="([^"]*)"', tag).group(1),
                re.search(r'max="([^"]*)"', tag).group(1))

    assert bounds(WIZARD) == bounds(CACHE_SETTINGS)
