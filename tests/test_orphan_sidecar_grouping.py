"""Sidecars copied without their video still get grouped under it.

Artwork and NFOs are frequently copied on their own, because the video is
already on cache and only its metadata changed. The sibling merge indexed
parents from the current run only, so those sidecars had nothing to fold into
and rendered as several unattributed rows — four "…-poster.jpg" lines with
nothing naming the film.

The parent is known regardless (sibling_map covers every video with siblings,
not just the ones this run touched), so the group header can name it. The
header carries no size: the video did not move, and a size there would say it
did.
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests', 'apscheduler',
                  'apscheduler.schedulers', 'apscheduler.schedulers.background',
                  'apscheduler.triggers', 'apscheduler.triggers.cron',
                  'apscheduler.triggers.interval']:
    sys.modules.setdefault(_mod_name, MagicMock())

MOVIE = "Half Baked (1998) - [BLURAY-1080P][EAC3 5.1][X264][8Bit]-J3RICO.mkv"
STEM = "Half Baked (1998) - [BLURAY-1080P][EAC3 5.1][X264][8Bit]-J3RICO"

_COMPATIBLE = {
    "Restored": ("Restored", "Moved"),
    "Moved": ("Restored", "Moved"),
    "Cached": ("Cached",),
}


def _runner(files):
    from web.services.operation_runner import OperationRunner
    r = object.__new__(OperationRunner)
    r._current_run_files = files
    return r


def _sidecar(name, size="1.00 MB", action="Cached"):
    return {"action": action, "filename": name, "size": size, "size_bytes": 1024}


def _parent_map(*sidecar_names, parent=MOVIE):
    return {s: parent for s in sidecar_names}


class TestSidecarsWithoutTheirVideo:

    def test_grouped_under_a_header_naming_the_film(self):
        files = [_sidecar(f"{STEM}{ext}") for ext in
                 ("-poster.jpg", "-fanart.jpg", "-clearlogo.png", ".nfo")]
        runner = _runner(list(files))

        runner._merge_run_files(_parent_map(*[f["filename"] for f in files]), _COMPATIBLE)

        assert len(runner._current_run_files) == 1, runner._current_run_files
        header = runner._current_run_files[0]
        assert header["filename"] == MOVIE
        assert len(header["associated_files"]) == 4
        assert header["sidecars_only"] is True

    def test_header_carries_no_size(self):
        """The video did not move; a size here would claim it did."""
        files = [_sidecar(f"{STEM}-poster.jpg"), _sidecar(f"{STEM}.nfo")]
        runner = _runner(list(files))

        runner._merge_run_files(_parent_map(*[f["filename"] for f in files]), _COMPATIBLE)

        header = runner._current_run_files[0]
        assert header["size"] == ""
        assert header["size_bytes"] == 0

    def test_a_lone_sidecar_is_left_alone(self):
        """Its own filename already names the film; a header adds a row, not information."""
        files = [_sidecar(f"{STEM}.nfo")]
        runner = _runner(list(files))

        runner._merge_run_files(_parent_map(f"{STEM}.nfo"), _COMPATIBLE)

        assert len(runner._current_run_files) == 1
        assert runner._current_run_files[0]["filename"] == f"{STEM}.nfo"
        assert "associated_files" not in runner._current_run_files[0]

    def test_real_parent_still_wins_over_a_synthetic_one(self):
        """When the video IS in the run, nothing changes."""
        video = {"action": "Cached", "filename": MOVIE, "size": "4.00 GB",
                 "size_bytes": 4 * 1024 ** 3}
        files = [video] + [_sidecar(f"{STEM}-poster.jpg"), _sidecar(f"{STEM}.nfo")]
        runner = _runner(list(files))

        runner._merge_run_files(
            _parent_map(f"{STEM}-poster.jpg", f"{STEM}.nfo"), _COMPATIBLE)

        assert len(runner._current_run_files) == 1
        header = runner._current_run_files[0]
        assert header["size_bytes"] == 4 * 1024 ** 3, "the real video row must survive"
        assert "sidecars_only" not in header
        assert len(header["associated_files"]) == 2

    def test_two_films_do_not_merge_into_one_group(self):
        other = "Saltburn (2023) - [WEBDL-1080P][EAC3 ATMOS 5.1][H264][8Bit]-RE.mkv"
        other_stem = other[:-4]
        files = [_sidecar(f"{STEM}-poster.jpg"), _sidecar(f"{STEM}.nfo"),
                 _sidecar(f"{other_stem}-poster.jpg"), _sidecar(f"{other_stem}.nfo")]
        mapping = {f"{STEM}-poster.jpg": MOVIE, f"{STEM}.nfo": MOVIE,
                   f"{other_stem}-poster.jpg": other, f"{other_stem}.nfo": other}
        runner = _runner(list(files))

        runner._merge_run_files(mapping, _COMPATIBLE)

        names = sorted(f["filename"] for f in runner._current_run_files)
        assert names == sorted([MOVIE, other]), names

    def test_unrelated_rows_are_untouched(self):
        unrelated = {"action": "Cached", "filename": "Troy (2004).mkv",
                     "size": "14.00 GB", "size_bytes": 14 * 1024 ** 3}
        files = [unrelated, _sidecar(f"{STEM}-poster.jpg"), _sidecar(f"{STEM}.nfo")]
        runner = _runner(list(files))

        runner._merge_run_files(
            _parent_map(f"{STEM}-poster.jpg", f"{STEM}.nfo"), _COMPATIBLE)

        assert unrelated in runner._current_run_files
        assert len(runner._current_run_files) == 2

    def test_actions_are_not_mixed_into_one_group(self):
        """A cached sidecar and a restored one describe different events."""
        files = [_sidecar(f"{STEM}-poster.jpg", action="Cached"),
                 _sidecar(f"{STEM}.nfo", action="Cached"),
                 _sidecar(f"{STEM}-fanart.jpg", action="Restored")]
        runner = _runner(list(files))

        runner._merge_run_files(
            _parent_map(f"{STEM}-poster.jpg", f"{STEM}.nfo", f"{STEM}-fanart.jpg"),
            _COMPATIBLE)

        actions = sorted(f["action"] for f in runner._current_run_files)
        assert actions == ["Cached", "Restored"], actions


class TestTheHeaderDoesNotClaimTheVideoMoved:
    """The header names a .mkv that was never copied — the badge must say so."""

    def test_run_file_header_is_flagged(self):
        files = [_sidecar(f"{STEM}-poster.jpg"), _sidecar(f"{STEM}.nfo")]
        runner = _runner(list(files))

        runner._merge_run_files(_parent_map(*[f["filename"] for f in files]), _COMPATIBLE)

        assert runner._current_run_files[0]["sidecars_only"] is True

    def test_activity_entries_round_trip_the_flag(self):
        """It is persisted, so a reload must not turn it back into a Cached row."""
        from core.activity import FileActivity

        entry = FileActivity(timestamp=datetime.now(), action="Cached",
                             filename=MOVIE, size_bytes=0, sidecars_only=True)

        assert entry.to_dict().get("sidecars_only") is True

    def test_normal_rows_carry_no_flag(self):
        from core.activity import FileActivity

        entry = FileActivity(timestamp=datetime.now(), action="Cached",
                             filename=MOVIE, size_bytes=1024)

        assert "sidecars_only" not in entry.to_dict()

    def test_banner_renders_a_neutral_tag(self):
        """Not the green CACHED tag, which would claim the video was copied."""
        import pathlib, re
        html = (pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" /
                "components" / "global_operation_banner.html").read_text(encoding="utf-8")

        assert "f.sidecars_only" in html
        # Only the truthy branch — the else branch legitimately styles real rows.
        branch = html[html.index("f.sidecars_only"):]
        branch = branch[:branch.index("{% else %}")]
        assert "EXTRAS" in branch
        assert "di-action-tag--cached" not in branch, (
            "the sidecars-only header must not use the cached styling"
        )

    def test_activity_feed_renders_a_neutral_badge(self):
        import pathlib
        html = (pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" /
                "components" / "recent_activity.html").read_text(encoding="utf-8")

        assert "entry.sidecars_only" in html
        assert "Extras only" in html
