"""Every Jinja template under web/templates/ must parse.

A syntax error in a template is invisible until someone loads the page that
uses it — the CI py_compile step covers web/routers/ and web/services/, but
nothing reaches the templates. Rendering each one is not practical (they need
request context, globals and populated models), but parsing is: it catches the
whole class of nesting and tag errors, which is what actually breaks here.

The concrete case this was written for: a merge resolved a conflict correctly
but also pulled in an adjacent, non-conflicting `{% endif %}`, leaving
cache/partials/file_table.html with one more endif than if. Balanced by eye,
unbalanced to Jinja.
"""

import pathlib

import pytest
from jinja2 import Environment, FileSystemLoader
from jinja2.exceptions import TemplateSyntaxError

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"


def _template_paths():
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


def _template_ids():
    return [p.relative_to(TEMPLATE_ROOT).as_posix() for p in _template_paths()]


@pytest.mark.parametrize("template_path", _template_paths(), ids=_template_ids())
def test_template_parses(template_path):
    """The template is syntactically valid Jinja."""
    relative = template_path.relative_to(TEMPLATE_ROOT).as_posix()
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_ROOT)))

    try:
        env.parse(template_path.read_text(encoding="utf-8"), filename=relative)
    except TemplateSyntaxError as exc:
        pytest.fail(f"{relative}:{exc.lineno} — {exc.message}")


def test_template_root_is_populated():
    """Guard the guard: an empty glob would make every check above vacuous."""
    assert len(_template_paths()) > 50
