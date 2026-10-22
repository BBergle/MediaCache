"""A cache copy is never visible under its real name until it is complete.

Unraid's shfs answers /mnt/user/<share>/... with the POOL copy whenever the
same share-relative path exists on both the pool and the array. Media copied
straight to its final name on the pool therefore publishes itself to Plex the
instant the copy opens the file — at zero bytes — and stays partial for the
whole transfer.

Measured on Unraid 7.3.1 / Plex 1.42 with a deliberately shadowed probe
(StudioNirin/PlexCache-D#207), reproduced twice:

    state on pool     what Plex recorded
    ---------------   ------------------------------------------
    82202531 (whole)  size 82202531, item healthy
    5242880 (part)    size 5242880   <- ingested the truncated size
    0 (just opened)   item GONE from the library entirely
    82202531 restored still gone; only a scan brought it back, as a
                      NEW ratingKey

The 0-byte state is the damaging one, and `open(dest, 'wb')` created it for
every single file PlexCache cached. Writing to a dot-prefixed partial name and
publishing with an atomic same-directory rename removes both windows.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
             'plexapi.library', 'requests', 'apscheduler',
             'apscheduler.schedulers', 'apscheduler.schedulers.background',
             'apscheduler.triggers', 'apscheduler.triggers.cron',
             'apscheduler.triggers.interval']:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.file_operations import (
    FileMover, PARTIAL_EXTENSION, get_partial_cache_path,
)


def _mover(tmp_path, **kw):
    exclude_file = os.path.join(str(tmp_path), "exclude.txt")
    open(exclude_file, "w").close()

    file_utils = MagicMock()
    file_utils.is_docker = False
    file_utils.is_linux = True
    file_utils.create_directory_with_permissions = MagicMock()

    return FileMover(
        real_source="/mnt/user/media",
        cache_dir=os.path.join(str(tmp_path), "cache"),
        is_unraid=False,
        file_utils=file_utils,
        debug=False,
        mover_cache_exclude_file=exclude_file,
        create_plexcached_backups=kw.get("create_backups", True),
    )


def _source(tmp_path, size=4096):
    array_dir = tmp_path / "array"
    array_dir.mkdir(exist_ok=True)
    src = array_dir / "Show - S01E01.mkv"
    src.write_bytes(b"\xab" * size)
    return str(src)


class TestThePartialName:

    def test_is_dot_prefixed_so_plex_ignores_it(self):
        """It surfaces through the user share, where Plex would otherwise scan it."""
        p = get_partial_cache_path("/mnt/cache/TV/Show - S01E01.mkv")
        assert os.path.basename(p).startswith(".")
        assert p.endswith(PARTIAL_EXTENSION)

    def test_stays_in_the_same_directory(self):
        """Cross-filesystem renames are not atomic, which is the whole point."""
        final = "/mnt/cache/TV/Season 01/Show - S01E01.mkv"
        assert os.path.dirname(get_partial_cache_path(final)) == os.path.dirname(final)

    def test_does_not_collide_between_files(self):
        a = get_partial_cache_path("/mnt/cache/TV/A.mkv")
        b = get_partial_cache_path("/mnt/cache/TV/B.mkv")
        assert a != b


class TestTheRealNameIsNeverIncomplete:
    """The regression this whole change exists to prevent."""

    def test_final_path_does_not_exist_while_copying(self, tmp_path):
        src = _source(tmp_path)
        cache_dir = tmp_path / "cache" / "TV"
        cache_dir.mkdir(parents=True)
        final = str(cache_dir / "Show - S01E01.mkv")
        mover = _mover(tmp_path)

        observed = []

        def fake_copy(s, d, **kw):
            # Mirror the real copy loop: open(dest,'wb') creates the file at
            # zero bytes, then chunks are written. The observation has to
            # happen AFTER creation — that empty moment is what deleted the
            # item from Plex, and checking before it would pass either way.
            with open(d, "wb") as f:
                f.flush()
                observed.append({
                    "final_exists": os.path.exists(final),
                    "dest_is_partial": d == get_partial_cache_path(final),
                })
                f.write(open(s, "rb").read())

        mover.file_utils.copy_file_with_permissions = fake_copy

        with patch("core.file_operations.get_console_lock"), \
             patch("tqdm.tqdm.write"), \
             patch("core.logging_config.mark_file_activity"):
            rc = mover._move_to_cache(src, str(cache_dir), final)

        assert rc == 0
        assert observed[0]["dest_is_partial"], "copy must target the partial name"
        assert not observed[0]["final_exists"], (
            "the real name existed during the copy — Plex can read it there"
        )
        assert os.path.getsize(final) == 4096

    def test_partial_is_gone_after_a_successful_publish(self, tmp_path):
        src = _source(tmp_path)
        cache_dir = tmp_path / "cache" / "TV"
        cache_dir.mkdir(parents=True)
        final = str(cache_dir / "Show - S01E01.mkv")
        mover = _mover(tmp_path)
        mover.file_utils.copy_file_with_permissions = \
            lambda s, d, **kw: open(d, "wb").write(open(s, "rb").read())

        with patch("core.file_operations.get_console_lock"), \
             patch("tqdm.tqdm.write"), \
             patch("core.logging_config.mark_file_activity"):
            mover._move_to_cache(src, str(cache_dir), final)

        assert not os.path.exists(get_partial_cache_path(final))
        assert os.path.isfile(final)


class TestFailureLeavesNothingBehind:

    def test_a_short_copy_is_rejected_before_publishing(self, tmp_path):
        """A truncated copy must never reach the real name."""
        src = _source(tmp_path, size=8192)
        cache_dir = tmp_path / "cache" / "TV"
        cache_dir.mkdir(parents=True)
        final = str(cache_dir / "Show - S01E01.mkv")
        mover = _mover(tmp_path)
        # Write only half the bytes, as a truncated transfer would.
        mover.file_utils.copy_file_with_permissions = \
            lambda s, d, **kw: open(d, "wb").write(open(s, "rb").read()[:4096])

        with patch("core.file_operations.get_console_lock"), \
             patch("tqdm.tqdm.write"), \
             patch("core.logging_config.mark_file_activity"):
            rc = mover._move_to_cache(src, str(cache_dir), final)

        assert rc == 1, "a size mismatch must fail the move"
        assert not os.path.exists(final), "the short copy reached the real name"
        assert not os.path.exists(get_partial_cache_path(final))

    def test_partial_removed_when_the_copy_raises(self, tmp_path):
        src = _source(tmp_path)
        cache_dir = tmp_path / "cache" / "TV"
        cache_dir.mkdir(parents=True)
        final = str(cache_dir / "Show - S01E01.mkv")
        mover = _mover(tmp_path)

        def exploding_copy(s, d, **kw):
            open(d, "wb").write(b"partial")
            raise RuntimeError("disk fell over")

        mover.file_utils.copy_file_with_permissions = exploding_copy

        with patch("core.file_operations.get_console_lock"), \
             patch("tqdm.tqdm.write"), \
             patch("core.logging_config.mark_file_activity"):
            rc = mover._move_to_cache(src, str(cache_dir), final)

        assert rc == 1
        assert not os.path.exists(get_partial_cache_path(final))
        assert not os.path.exists(final)

    def test_a_stale_partial_does_not_block_a_retry(self, tmp_path):
        src = _source(tmp_path)
        cache_dir = tmp_path / "cache" / "TV"
        cache_dir.mkdir(parents=True)
        final = str(cache_dir / "Show - S01E01.mkv")
        open(get_partial_cache_path(final), "wb").write(b"junk from last time")

        mover = _mover(tmp_path)
        mover.file_utils.copy_file_with_permissions = \
            lambda s, d, **kw: open(d, "wb").write(open(s, "rb").read())

        with patch("core.file_operations.get_console_lock"), \
             patch("tqdm.tqdm.write"), \
             patch("core.logging_config.mark_file_activity"):
            rc = mover._move_to_cache(src, str(cache_dir), final)

        assert rc == 0
        assert os.path.getsize(final) == 4096


class TestTheStaleSweep:
    """A crash or power cut skips _cleanup_failed_cache_copy entirely."""

    def _partial(self, d, name, age_seconds):
        p = d / name
        p.write_bytes(b"x" * 128)
        past = os.path.getmtime(p) - age_seconds
        os.utime(p, (past, past))
        return p

    def test_removes_old_partials(self, tmp_path):
        d = tmp_path / "cache"; d.mkdir()
        old = self._partial(d, ".Show - S01E01.mkv" + PARTIAL_EXTENSION, 7200)
        mover = _mover(tmp_path)

        assert mover.cleanup_stale_partials([str(d)]) == 1
        assert not old.exists()

    def test_leaves_recent_partials_alone(self, tmp_path):
        """Something else may be mid-copy; do not pull it out from under it."""
        d = tmp_path / "cache"; d.mkdir()
        fresh = self._partial(d, ".Show - S01E02.mkv" + PARTIAL_EXTENSION, 60)
        mover = _mover(tmp_path)

        assert mover.cleanup_stale_partials([str(d)]) == 0
        assert fresh.exists()

    def test_never_touches_real_media(self, tmp_path):
        d = tmp_path / "cache"; d.mkdir()
        keep = [d / "Show - S01E01.mkv", d / "Show - S01E01.mkv.plexcached",
                d / ".hidden.mkv", d / "poster.jpg"]
        for k in keep:
            k.write_bytes(b"real")
            past = os.path.getmtime(k) - 99999
            os.utime(k, (past, past))
        mover = _mover(tmp_path)

        assert mover.cleanup_stale_partials([str(d)]) == 0
        assert all(k.exists() for k in keep)

    def test_missing_directories_are_not_an_error(self, tmp_path):
        mover = _mover(tmp_path)
        assert mover.cleanup_stale_partials(
            [str(tmp_path / "nope"), "", None]) == 0
