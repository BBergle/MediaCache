"""Phase 5 template test: the ``pin_button`` macro must render the exact
form shape that ``POST /api/pinned/toggle`` returns via
``pinned_toggle_response.html``. If the two drift, HTMX's self-swap
(``hx-target=this`` + ``hx-swap=outerHTML``) will visually "stick" the
first toggle but subsequent toggles break because the inner form is no
longer recognizable to the route / response pair.

See PINNED_MEDIA_PLAN.md → Phase 4 discovery #6 and Phase 5 kickoff step 5.
"""

from pathlib import Path

import pytest

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "web" / "templates"


def _render_macro(is_pinned: bool) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
        autoescape=select_autoescape(["html"]),
    )
    source = (
        '{% from "macros/pin_button.html" import pin_button %}'
        '{{ pin_button(rating_key, pin_type, title, is_pinned) }}'
    )
    return env.from_string(source).render(
        rating_key="12345",
        pin_type="episode",
        title="Show - S01E05",
        is_pinned=is_pinned,
    )


def _render_toggle_response(is_pinned: bool) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("settings/partials/pinned_toggle_response.html")
    return tmpl.render(
        error=None,
        rating_key="12345",
        pin_type="episode",
        title="Show - S01E05",
        is_pinned=is_pinned,
    )


class TestPinButtonMacroShape:
    def test_unpinned_state_renders_pin_button(self):
        html = _render_macro(is_pinned=False)
        assert 'hx-post="/api/pinned/toggle"' in html
        assert 'hx-target="this"' in html
        assert 'hx-swap="outerHTML"' in html
        assert 'name="rating_key" value="12345"' in html
        assert 'name="pin_type" value="episode"' in html
        assert 'name="title" value="Show - S01E05"' in html
        assert 'data-lucide="pin"' in html
        assert 'btn-primary' in html
        assert 'Pin this item' in html  # button title, unambiguous

    def test_pinned_state_renders_unpin_button(self):
        html = _render_macro(is_pinned=True)
        assert 'data-lucide="pin-off"' in html
        assert 'btn-secondary' in html
        assert 'Unpin this item' in html
        assert 'data-is-pinned="true"' in html

    def test_macro_matches_toggle_response_attributes(self):
        """The macro and the toggle response must share the same form
        attributes so HTMX's outerHTML self-swap is seamless."""
        macro_html = _render_macro(is_pinned=False)
        response_html = _render_toggle_response(is_pinned=True)

        # Both render a <form> with identical hx attributes
        for attr in (
            'hx-post="/api/pinned/toggle"',
            'hx-target="this"',
            'hx-swap="outerHTML"',
            'class="pinned-toggle-form"',
        ):
            assert attr in macro_html, f"macro missing {attr}"
            assert attr in response_html, f"toggle response missing {attr}"


class TestFileTableRowMarkup:
    """file_table.html never renders the pin toggle form — pinning is a
    Settings-only action. Rows show the Evict button in Actions and a
    Pinned badge in the Source column whenever ``file.is_pinned`` is
    truthy."""

    def _render_file_table(self, files, eviction_enabled=True):
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_ROOT)),
            autoescape=select_autoescape(["html"]),
        )
        tmpl = env.get_template("cache/partials/file_table.html")
        return tmpl.render(
            files=files,
            eviction_enabled=eviction_enabled,
            source_filter="all",
            search="",
            totals={
                "total_files": len(files),
                "total_size_display": "0 B",
                "ondeck_count": 0,
                "watchlist_count": 0,
                "pinned_count": 0,
                "other_count": 0,
            },
        )

    def _make_file(self, **overrides):
        base = {
            "path": "/mnt/cache/movies/Movie.mkv",
            "filename": "Movie.mkv",
            "size_display": "1.00 GB",
            "priority_score": 50,
            "is_ondeck": False,
            "is_watchlist": False,
            "source": "unknown",
            "users": [],
            "cache_age_hours": 1.5,
            "subtitle_count": 0,
            "sidecar_count": 0,
            "associated_files": None,
            "is_pinned": False,
        }
        base.update(overrides)
        return type("F", (), base)()

    def test_row_never_renders_pin_toggle_form(self):
        """Pinning lives on Settings → Pinned Media. The cached-files row
        must not render the toggle form — otherwise the Actions column
        grows a button that only works for rows with tracker metadata."""
        f = self._make_file()
        html = self._render_file_table([f])
        assert 'hx-post="/api/pinned/toggle"' not in html
        # Evict button is always present
        assert 'showEvictConfirm' in html

    def test_pinned_row_shows_pinned_badge(self):
        f = self._make_file(is_pinned=True, priority_score=100)
        html = self._render_file_table([f])
        assert 'badge-pinned' in html
        assert '>\n                Pinned\n' in html
