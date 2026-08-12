"""Tests for FileUtils.get_total_size_of_files() and its missing-path warning.

Sizing an array-destination move hands this helper /mnt/user0/ paths whose
originals were renamed to .plexcached when PlexCache cached them. Every path
legitimately misses, so warning about them fires on every healthy restore and
teaches users to ignore warnings.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

for _mod_name in [
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex', 'requests',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from conftest import create_test_file  # noqa: E402


def _file_utils():
    from core.system_utils import FileUtils
    return object.__new__(FileUtils)


class TestTotalSizeOfFiles:

    def test_sums_existing_files(self, tmp_path):
        a = create_test_file(str(tmp_path / "a.mkv"), size_bytes=1024)
        b = create_test_file(str(tmp_path / "b.mkv"), size_bytes=1024)

        size, unit = _file_utils().get_total_size_of_files([a, b])

        assert (size, unit) == (2.0, "KB")

    def test_missing_files_are_excluded_from_the_total(self, tmp_path):
        a = create_test_file(str(tmp_path / "a.mkv"), size_bytes=1024)
        missing = str(tmp_path / "gone.mkv")

        size, unit = _file_utils().get_total_size_of_files([a, missing])

        assert (size, unit) == (1.0, "KB")


class TestMissingPathWarning:

    def test_warns_by_default(self, tmp_path, caplog):
        missing = str(tmp_path / "gone.mkv")

        with caplog.at_level("DEBUG"):
            _file_utils().get_total_size_of_files([missing])

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "not found on disk" in warnings[0].message

    def test_silent_when_absence_is_expected(self, tmp_path, caplog):
        """The array-destination case: absence is normal, not a problem."""
        missing = str(tmp_path / "gone.mkv")

        with caplog.at_level("DEBUG"):
            _file_utils().get_total_size_of_files([missing], warn_missing=False)

        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    def test_still_records_which_paths_missed_at_debug(self, tmp_path, caplog):
        """Suppressing the warning must not lose the diagnostic detail."""
        missing = str(tmp_path / "gone.mkv")

        with caplog.at_level("DEBUG"):
            _file_utils().get_total_size_of_files([missing], warn_missing=False)

        debug_text = "\n".join(r.message for r in caplog.records if r.levelname == "DEBUG")
        assert "gone.mkv" in debug_text

    def test_no_warning_when_nothing_is_missing(self, tmp_path, caplog):
        a = create_test_file(str(tmp_path / "a.mkv"), size_bytes=10)

        with caplog.at_level("DEBUG"):
            _file_utils().get_total_size_of_files([a])

        assert [r for r in caplog.records if r.levelname == "WARNING"] == []


class TestCallerPassesTheRightFlag:
    """_check_free_space_and_move_files() sizes array moves without warning.

    Drives the real method with a genuinely missing path, which is what an
    array-destination move always looks like once the originals are .plexcached.
    """

    def _app(self, tmp_path, missing_path):
        from core.app import PlexCacheApp
        from core.system_utils import FileUtils

        app = object.__new__(PlexCacheApp)
        app.config_manager = MagicMock()
        app.dry_run = True
        app.all_active_media = []
        app.media_to_cache = []
        app.files_to_skip = []
        app.restored_count = 0
        app.restored_bytes = 0
        app.moved_to_array_count = 0
        app.moved_to_array_bytes = 0
        app.file_utils = object.__new__(FileUtils)
        app.file_filter = MagicMock()
        app.file_filter.filter_files.return_value = [missing_path]
        app.logging_manager = MagicMock()
        app.file_mover = MagicMock()
        return app

    def test_array_destination_does_not_warn(self, tmp_path, caplog):
        missing = str(tmp_path / "Movie.mkv")  # renamed to .plexcached on the array
        app = self._app(tmp_path, missing)

        with caplog.at_level("DEBUG"):
            app._check_free_space_and_move_files([missing], 'array', '', str(tmp_path))

        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    def test_cache_destination_still_warns(self, tmp_path, caplog):
        """A cache-bound file that isn't on the array is a real problem."""
        missing = str(tmp_path / "Movie.mkv")
        app = self._app(tmp_path, missing)
        app._apply_cache_limit = lambda files, cache_dir: files

        with caplog.at_level("DEBUG"):
            app._check_free_space_and_move_files([missing], 'cache', '', str(tmp_path))

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("not found on disk" in r.message for r in warnings)
