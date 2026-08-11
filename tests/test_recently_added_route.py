"""Route tests for the Recently Added view (issue #174).

Mounts only the recently_added router on a minimal FastAPI app and patches the
service + settings accessors, so these exercise the real Jinja2 templates
without a live Plex server.

Test isolation: earlier tests sometimes replace ``web.config`` (and friends) in
``sys.modules`` with a MagicMock. Force-reload affected modules so route tests
run against the real templates instance.
"""

import re
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _force_real_modules():
    mocked_names = [
        "web.config",
        "web.routers",
        "web.routers.recently_added",
        "web.services",
        "web.services.recently_added_service",
        "web",
    ]
    for name in mocked_names:
        mod = sys.modules.get(name)
        if isinstance(mod, MagicMock):
            del sys.modules[name]
    import web  # noqa: F401
    import web.config  # noqa: F401
    import web.routers.recently_added  # noqa: F401
    import web.services.recently_added_service  # noqa: F401


_force_real_modules()

from web.services.recently_added_service import RecentlyAddedRow, RecentlyAddedService


def _row(rating_key="1", title="Dune", media_type="movie", library_title="Movies",
         size=28_000_000_000, size_display="28.00 GB", location="cache",
         state="on_cache_not_pinned", is_pinned=False, protected_by=None,
         pin_type="movie", episode_info=None, associated_files=None, filename=None,
         outcome=None, added_display="2 hr ago", pin_scope="", pin_holder_key="",
         pin_holder_title=""):
    # Rows are normally built by _enrich, which derives `outcome`. These are
    # constructed directly, so map the legacy `state` onto a sensible outcome
    # unless the test names one explicitly.
    if outcome is None:
        outcome = {
            "pinned": "held",
            "protected": "returns_when_done",
            "on_cache_not_pinned": "moves_back",
            "on_array": "stays_on_array",
        }.get(state, "unmapped")
    return RecentlyAddedRow(
        rating_key=rating_key,
        title=title,
        media_type=media_type,
        library_title=library_title,
        file_path=f"/data/movies/{title}.mkv",
        size=size,
        size_display=size_display,
        added_at=datetime.now(),
        added_display=added_display,
        location=location,
        state=state,
        outcome=outcome,
        pin_scope=pin_scope,
        pin_holder_key=pin_holder_key,
        pin_holder_title=pin_holder_title,
        is_pinned=is_pinned,
        is_ondeck="OnDeck" in (protected_by or []),
        is_watchlist="Watchlist" in (protected_by or []),
        is_cache_tracked=False,
        protected_by=protected_by or [],
        pin_type=pin_type,
        episode_info=episode_info,
        associated_files=associated_files or [],
        filename=filename if filename is not None else f"{title}.mkv",
    )


def _episode(rating_key, show, season, episode, title="Ep", **kw):
    return _row(rating_key=rating_key, title=title, media_type="episode",
                library_title="TV Shows", pin_type="episode",
                episode_info={"show": show, "season": season, "episode": episode}, **kw)


def _summary(rows):
    on_cache = sum(1 for r in rows if r.location == "cache")
    return {
        "total": len(rows),
        "on_cache": on_cache,
        "on_cache_not_pinned": sum(1 for r in rows if r.state == "on_cache_not_pinned"),
        "on_array": sum(1 for r in rows if r.location == "array"),
        "total_size": sum(r.size for r in rows),
        "total_size_display": "28.00 GB",
    }


def _result(rows, available=True, error=None):
    return {
        "available": available,
        "rows": rows,
        "summary": _summary(rows),
        "error": error,
    }


@pytest.fixture
def client():
    from web.routers import recently_added as ra_router

    app = FastAPI()
    app.include_router(ra_router.router, prefix="/recently-added")
    return TestClient(app)


def _patch(service_result, settings=None):
    """Patch the router's service + settings accessors."""
    fake_service = MagicMock()
    fake_service.get_recently_added.return_value = service_result
    # Use the real grouping transform so the template renders real rows/groups.
    fake_service.group_rows_for_display.side_effect = RecentlyAddedService.group_rows_for_display
    fake_settings = MagicMock()
    fake_settings.get_all.return_value = settings or {}
    return (
        patch("web.routers.recently_added.get_recently_added_service", return_value=fake_service),
        patch("web.routers.recently_added.get_settings_service", return_value=fake_settings),
        fake_service,
    )


class TestPage:
    def test_page_lazy_loads_the_list(self, client):
        p_svc, p_set, _ = _patch(_result([]), settings={})
        with p_svc, p_set:
            r = client.get("/recently-added/")
        assert r.status_code == 200
        assert "Recently Added" in r.text
        # The window is no longer pinned into the URL: #ra-content includes
        # #ra-days-input instead, so a pin/unpin refetch re-sends whatever the
        # user currently has selected rather than the page-load default. On the
        # initial load that input isn't in the DOM yet, so no days param is sent
        # and the router falls back to the persisted setting.
        assert 'hx-get="/recently-added/list"' in r.text
        assert 'hx-include="#ra-days-input"' in r.text

    def test_page_refetches_the_list_on_pin_change(self, client):
        """Pin/unpin must not leave the row's outcome badge asserting the old state."""
        p_svc, p_set, _ = _patch(_result([]), settings={})
        with p_svc, p_set:
            r = client.get("/recently-added/")
        assert "pinned-updated from:body" in r.text

    def test_page_preserves_the_users_view_across_a_swap(self, client):
        """A refetch that reset the filters would trade one wrong state for another."""
        p_svc, p_set, _ = _patch(_result([]), settings={})
        with p_svc, p_set:
            r = client.get("/recently-added/")
        assert "function restore()" in r.text
        assert "_state.expanded" in r.text

    def test_page_recovers_when_the_selected_library_leaves_the_window(self, client):
        """Restoring a library that is no longer in the options leaves the
        select at selectedIndex -1 / value "", which matches no row — every
        row hides behind "No items match" under a blank dropdown.

        Source-text assertion: there is no JS runtime in this suite, so this
        pins the guard's presence, not its behaviour. It would also pass on a
        commented-out guard. The behavioural half it depends on — that the
        option set really does vary with the rows — is pinned by
        TestList::test_library_options_follow_the_returned_rows below.
        """
        p_svc, p_set, _ = _patch(_result([]), settings={})
        with p_svc, p_set:
            r = client.get("/recently-added/")
        assert "lib.selectedIndex === -1" in r.text
        assert "_state.lib = 'all'" in r.text


class TestDegradedEpisodeNumbering:
    """An episode Plex could not number is still an episode."""

    def _render(self, client, path, row):
        p_svc, p_set, _ = _patch(_result([row]), settings={})
        with p_svc, p_set:
            return client.get(path)

    def test_episode_without_numbering_keeps_its_show_name(self, client):
        r = self._render(client, "/recently-added/list", _row(
            rating_key="1", title="Holiday Special", media_type="episode",
            pin_type="episode",
            episode_info={"show": "The Show", "season": None, "episode": None}))

        assert "The Show" in r.text
        assert ">Movie<" not in r.text

    def test_episode_with_no_episode_info_is_not_labelled_movie(self, client):
        """The terminal arm: an episode row carrying no episode_info at all
        fell into the movie branch and rendered a "Movie" caption directly
        under the tv icon the same row had already chosen."""
        r = self._render(client, "/recently-added/list", _row(
            rating_key="1", title="Orphan", media_type="episode",
            pin_type="episode", episode_info=None))

        # Match the sub-label element itself — a bare "Movie" also occurs in
        # the "Movies" library name elsewhere on the page.
        assert ">Movie</div>" not in r.text
        assert ">Episode</div>" in r.text

    def test_missing_numbering_does_not_fabricate_s00e00(self, client):
        """S00E00 is a real Specials episode 0, so inventing it for an unknown
        episode makes the two indistinguishable."""
        r = self._render(client, "/recently-added/list", _row(
            rating_key="1", title="Holiday Special", media_type="episode",
            pin_type="episode",
            episode_info={"show": "The Show", "season": None, "episode": None}))

        assert "S00E00" not in r.text
        assert "Holiday Special" in r.text

    def test_genuine_specials_episode_still_renders(self, client):
        r = self._render(client, "/recently-added/list", _row(
            rating_key="1", title="Xmas", media_type="episode", pin_type="episode",
            episode_info={"show": "The Show", "season": 0, "episode": 7}))

        assert "S00E07" in r.text

    def test_malformed_index_does_not_raise(self, client):
        """plexapi casts an unparseable index to float('nan'), and
        '%02d'|format(nan) raises ValueError — a 500 on the page, not a bad
        label. The isinstance guard is what prevents it."""
        nan = float("nan")
        r = self._render(client, "/recently-added/list", _row(
            rating_key="1", title="Weird Ep", media_type="episode",
            pin_type="episode",
            episode_info={"show": "The Show", "season": nan, "episode": nan}))

        assert r.status_code == 200
        assert "The Show" in r.text

    def test_widget_survives_the_same_row(self, client):
        nan = float("nan")
        r = self._render(client, "/recently-added/widget", _row(
            rating_key="1", title="Weird Ep", media_type="episode",
            pin_type="episode",
            episode_info={"show": "The Show", "season": nan, "episode": nan}))

        assert r.status_code == 200
        assert "S00E00" not in r.text

    def test_missing_show_name_does_not_leak_none_into_the_search_index(self, client):
        """`None|lower` renders the literal "none", which would make every such
        row match a search for "none"."""
        r = self._render(client, "/recently-added/list", _row(
            rating_key="1", title="Orphan Ep", media_type="episode",
            pin_type="episode",
            episode_info={"show": None, "season": 1, "episode": 2}))

        assert 'data-title="orphan ep none' not in r.text


class TestUnreadableLibraryStrip:
    """The page must not report an unread library as an empty one."""

    def _get(self, client, path, rows, unreadable):
        result = _result(rows)
        result["unreadable_libraries"] = unreadable
        p_svc, p_set, _ = _patch(result, settings={})
        with p_svc, p_set:
            return client.get(path)

    def test_strip_names_the_libraries(self, client):
        r = self._get(client, "/recently-added/list",
                      [_row(rating_key="1", title="Dune")], ["Documentaries"])
        assert "Couldn&#39;t read 1 library" in r.text or "Couldn't read 1 library" in r.text
        assert "Documentaries" in r.text

    def test_empty_state_does_not_claim_nothing_was_added(self, client):
        """With a library unread, "Nothing added in the last 7 days" would be a
        statement about media the app never looked at."""
        r = self._get(client, "/recently-added/list", [], ["Movies"])
        assert "Nothing added in the last" not in r.text
        assert "No results from the libraries that answered." in r.text

    def test_empty_state_is_unchanged_on_a_clean_sweep(self, client):
        r = self._get(client, "/recently-added/list", [], [])
        assert "Nothing added in the last" in r.text

    def test_no_strip_when_every_library_answered(self, client):
        r = self._get(client, "/recently-added/list",
                      [_row(rating_key="1", title="Dune")], [])
        assert "counts are incomplete" not in r.text
        assert "read 1 librar" not in r.text

    def test_widget_says_its_counts_are_incomplete(self, client):
        r = self._get(client, "/recently-added/widget",
                      [_row(rating_key="1", title="Dune")], ["Documentaries"])
        assert "counts are incomplete" in r.text
        assert "Documentaries" in r.text


class TestList:
    def test_library_options_follow_the_returned_rows(self, client):
        """The Library filter is built per response from the rows it returned,
        so a narrower window can drop a library the user had selected. This is
        the server-side fact the selectedIndex -1 guard in restore() exists to
        absorb; without it the guard would look like dead code."""
        import re

        def options(rows):
            p_svc, p_set, _ = _patch(_result(rows), settings={})
            with p_svc, p_set:
                r = client.get("/recently-added/list")
            block = re.search(r'<select[^>]*id="ra-lib-filter".*?</select>', r.text, re.S)
            assert block, "Library filter select not rendered"
            return set(re.findall(r'value="([^"]*)"', block.group(0)))

        wide = options([
            _row(rating_key="1", title="Dune", library_title="Movies"),
            _row(rating_key="2", title="Nova", library_title="Documentaries"),
        ])
        narrow = options([_row(rating_key="1", title="Dune", library_title="Movies")])

        assert "Documentaries" in wide
        assert "Documentaries" not in narrow, (
            "Library options no longer vary with the row set — re-check whether "
            "restore()'s selectedIndex guard is still needed."
        )

    def test_renders_states_and_pin_button(self, client):
        rows = [
            _row(rating_key="1", title="Dune", state="on_cache_not_pinned", location="cache"),
            _row(rating_key="2", title="Civil War", state="on_array", location="array",
                 size_display="62.00 GB"),
            _row(rating_key="3", title="Wild Robot", state="pinned", location="cache", is_pinned=True),
            _row(rating_key="4", title="Furiosa", state="protected", location="cache",
                 protected_by=["OnDeck"]),
        ]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list", headers={"HX-Request": "true"})

        assert r.status_code == 200
        # Outcome badges — named by what happens next, not by a state noun.
        assert "Moves to array" in r.text          # on cache, no longer held
        assert "Not on cache" in r.text            # on array
        assert "Stays on cache" in r.text          # pinned
        assert "Returns when watched" in r.text and "OnDeck" in r.text
        # "Protected" survives only as the reserved tooltip sentence on pinned
        # states — never as a badge for transient OnDeck/Watchlist membership.
        assert "Pinned — protected from eviction." in r.text
        # Pin button (macro) wires to the existing toggle endpoint
        assert "/api/pinned/toggle" in r.text
        # Array row shows byte-cost note
        assert "copies ~62.00 GB to cache" in r.text

    def test_clamps_invalid_days_to_default(self, client):
        p_svc, p_set, fake_service = _patch(_result([]))
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=999")
        assert r.status_code == 200
        # 999 is not an allowed window → clamps to 7
        fake_service.get_recently_added.assert_called_once()
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 7

    def test_respects_allowed_days(self, client):
        p_svc, p_set, fake_service = _patch(_result([]))
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=30")
        assert r.status_code == 200
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 30
        assert "selected" in r.text and "Last 30 days" in r.text

    def test_no_days_uses_settings_default(self, client):
        p_svc, p_set, fake_service = _patch(_result([]), settings={"recently_added_days": 1})
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 1

    def test_unavailable_state(self, client):
        p_svc, p_set, _ = _patch(_result([], available=False, error="Plex not configured."))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        assert "unavailable" in r.text.lower()
        assert "Plex not configured." in r.text

    def test_error_state_days_input_is_named_so_refresh_keeps_the_window(self, client):
        """Refresh after a Plex outage must re-request the user's window.

        The error branch keeps a hidden days input so `hx-include` has something
        to send, but it had no `name` — and htmx's shouldInclude skips any
        element with an empty name, so Refresh silently fell back to the
        default window instead of the 30-day view the user was on.
        """
        p_svc, p_set, fake_service = _patch(
            _result([], available=False, error="Plex not configured."),
            settings={"recently_added_days": 7},
        )
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=30")
            # The input carries a name, in either attribute order.
            assert (re.search(r'<input[^>]*id="ra-days-input"[^>]*name="days"', r.text)
                    or re.search(r'<input[^>]*name="days"[^>]*id="ra-days-input"', r.text)), \
                "error-state days input has no name; hx-include will send nothing"
            assert 'value="30"' in r.text

            # Round trip: what Refresh would send actually reaches the service.
            fake_service.get_recently_added.reset_mock()
            client.get("/recently-added/list?days=30")
        assert fake_service.get_recently_added.call_args.kwargs["days"] == 30

    def test_empty_window_message(self, client):
        p_svc, p_set, _ = _patch(_result([]))
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=7")
        assert r.status_code == 200
        assert "Nothing added in the last 7 days" in r.text

    def test_truncation_note_when_capped(self, client):
        # rows count reaches max_items → footer surfaces the "showing newest N" note.
        rows = [_row(rating_key=str(i), title=f"M{i}") for i in range(3)]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_max_items": 3})
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=7")
        assert r.status_code == 200
        assert "most recently added files" in r.text
        assert "Max Items" in r.text

    def test_no_truncation_note_under_cap(self, client):
        rows = [_row(rating_key=str(i), title=f"M{i}") for i in range(3)]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_max_items": 250})
        with p_svc, p_set:
            r = client.get("/recently-added/list?days=7")
        assert r.status_code == 200
        assert "most recently added files" not in r.text

    def test_episode_row_shows_show_and_code(self, client):
        rows = [_row(rating_key="9", title="Future Days", media_type="episode",
                     library_title="TV Shows", state="on_cache_not_pinned", location="cache",
                     pin_type="episode",
                     episode_info={"show": "The Last of Us", "season": 2, "episode": 1})]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert "The Last of Us" in r.text
        assert "S02E01" in r.text


class TestGrouping:
    def test_multi_episode_show_renders_expandable_group(self, client):
        rows = [
            _episode("1", "The Last of Us", 2, 1, title="Future Days"),
            _episode("2", "The Last of Us", 2, 2, title="Through the Valley"),
            _episode("3", "The Last of Us", 2, 3, title="The Path"),
        ]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        # One group header for the show + child rows
        assert 'class="ra-group-header"' in r.text
        assert "3 new episodes" in r.text
        # Header id and child rows agree (id format is opaque — minted by
        # core.media_grouping, not positional).
        gid = re.search(r'data-group-id="([^"]+)"', r.text).group(1)
        assert f'data-group="{gid}"' in r.text
        assert r.text.count('ra-group-child') >= 3
        # Episode codes appear in child rows
        assert "S02E01" in r.text and "S02E03" in r.text
        # Per-episode pin actions present
        assert "/api/pinned/toggle" in r.text

    def test_single_episode_not_grouped(self, client):
        rows = [_episode("9", "Severance", 1, 1, title="Solo")]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert 'class="ra-group-header"' not in r.text
        assert "Severance" in r.text

    def test_group_header_spanning_seasons_shows_range(self, client):
        rows = [
            _episode("1", "Sugar (2024)", 2, 3),
            _episode("2", "Sugar (2024)", 1, 5),
            _episode("3", "Sugar (2024)", 1, 7),
        ]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert 'class="ra-group-header"' in r.text
        assert "Seasons 1–2" in r.text
        assert "3 new episodes" in r.text


class TestExpandDetail:
    def test_row_is_expandable_with_filename_and_size(self, client):
        rows = [_row(rating_key="1", title="Dune",
                     filename="Dune.2021.2160p.mkv", size_display="28.00 GB")]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        # Title cell is click-to-expand with a chevron
        assert "RecentlyAdded.toggleDetail(this)" in r.text
        assert "ra-detail-chevron" in r.text
        # Detail sub-row carries the primary filename + size
        assert "ra-detail-row" in r.text
        assert "Dune.2021.2160p.mkv" in r.text
        assert "28.00 GB" in r.text

    def test_associated_files_listed_in_detail(self, client):
        rows = [_row(rating_key="1", title="Dune", filename="Dune.mkv",
                     associated_files=[{"filename": "Dune.en.srt", "size": "84 KB"}])]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert r.status_code == 200
        # +N hint badge + the associated file appears in a detail sub-row
        assert "+1" in r.text
        assert "Dune.en.srt" in r.text
        assert "ra-detail-row" in r.text


class TestWidget:
    def test_widget_renders_counts(self, client):
        rows = [
            _row(rating_key="1", title="Dune", location="cache", state="on_cache_not_pinned"),
            _row(rating_key="2", title="Civil War", location="array", state="on_array"),
        ]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert "on cache" in r.text
        # Third stat is the neutral "on array" count, not a "not pinned" nag.
        assert "on array" in r.text
        assert "not pinned" not in r.text
        assert "Dune" in r.text

    def test_widget_rows_expand_to_filename_and_size(self, client):
        rows = [_row(rating_key="1", title="Dune",
                     filename="Dune.2021.2160p.mkv", size_display="28.00 GB")]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        # Self-contained expand (the page-level RecentlyAdded JS isn't on the dashboard)
        assert "raWidgetToggle(this)" in r.text
        assert "ra-w-detail" in r.text
        assert "Dune.2021.2160p.mkv" in r.text
        assert "28.00 GB" in r.text

    def test_widget_groups_multi_episode_show(self, client):
        # The dashboard card must group like the full page — six episodes of one
        # show are one line, not six.
        rows = [_episode(str(i), "Sugar (2024)", 1, i) for i in range(1, 7)]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert r.text.count('class="ra-w-row"') == 1
        assert "6 eps" in r.text
        assert "Season 1" in r.text

    def test_widget_grouping_frees_slots_for_other_titles(self, client):
        # Before grouping, a burst of episodes filled all five widget slots and
        # pushed newer movies off the card entirely.
        rows = [_episode(str(i), "Sugar (2024)", 1, i) for i in range(1, 7)]
        rows += [_row(rating_key="m1", title="Dune"), _row(rating_key="m2", title="Civil War")]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert r.text.count('class="ra-w-row"') == 3
        assert "Dune" in r.text and "Civil War" in r.text

    def test_widget_group_expands_to_episode_files(self, client):
        rows = [
            _episode("1", "Sugar (2024)", 1, 1, filename="Sugar (2024) - S01E01.mkv"),
            _episode("2", "Sugar (2024)", 1, 2, filename="Sugar (2024) - S01E02.mkv"),
        ]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert "raWidgetToggle(this)" in r.text
        assert "Sugar (2024) - S01E01.mkv" in r.text
        assert "Sugar (2024) - S01E02.mkv" in r.text

    def test_widget_caps_display_rows(self, client):
        rows = [_row(rating_key=str(i), title=f"Movie {i}") for i in range(10)]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.text.count('class="ra-w-row"') == 5

    def test_widget_unavailable(self, client):
        p_svc, p_set, _ = _patch(_result([], available=False, error="No Plex."))
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert "No Plex." in r.text


class TestMissingAddedAt:
    """`added_display` is Optional. The full page guarded it with `or '—'`; the
    widget did not, so a row with no Plex `addedAt` rendered the literal string
    "None" in the Added column."""

    def test_widget_row_renders_a_dash_not_none(self, client):
        rows = [_row(rating_key="1", title="Dune", added_display=None)]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert ">None<" not in r.text

    def test_widget_group_renders_a_dash_not_none(self, client):
        rows = [_episode(str(i), "Sugar (2024)", 1, i, added_display=None) for i in (1, 2)]
        p_svc, p_set, _ = _patch(_result(rows), settings={"recently_added_days": 7})
        with p_svc, p_set:
            r = client.get("/recently-added/widget")
        assert r.status_code == 200
        assert ">None<" not in r.text

    def test_full_page_still_guards_it(self, client):
        rows = [_row(rating_key="1", title="Dune", added_display=None)]
        p_svc, p_set, _ = _patch(_result(rows))
        with p_svc, p_set:
            r = client.get("/recently-added/list")
        assert ">None<" not in r.text
