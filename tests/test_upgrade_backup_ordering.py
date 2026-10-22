"""An upgrade must never leave a cached file with no .plexcached backup.

When Sonarr/Radarr replaces a cached file, PlexCache swaps the array backup:
the old .plexcached is outdated, and a new one is copied from the new cache
file. Doing the delete first means any failure on the copy (ENOSPC, a
permission error, a disappearing source) ends with neither backup present, and
PlexcachedRestorer has nothing to restore for that file.

CacheService._handle_upgrade_plexcached() already created-then-deleted. These
cover the same contract on the CLI path in PlexCacheApp.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests']:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from conftest import create_test_file  # noqa: E402

PLEXCACHED = ".plexcached"


def _app(backup_upgraded=True, create_backups=True):
    from core.app import PlexCacheApp

    app = object.__new__(PlexCacheApp)
    app.config_manager = MagicMock()
    app.config_manager.cache.create_plexcached_backups = create_backups
    app.config_manager.cache.backup_upgraded_files = backup_upgraded
    return app


@pytest.fixture
def upgrade(tmp_path):
    """An upgraded file: old backup on the array, new file on cache."""
    array = tmp_path / "array" / "Movies"
    cache = tmp_path / "cache" / "Movies"
    array.mkdir(parents=True)
    cache.mkdir(parents=True)

    old_real = str(array / "Movie.1080p.mkv")
    new_real = str(array / "Movie.2160p.mkv")
    old_backup = old_real + PLEXCACHED
    new_cache = str(cache / "Movie.2160p.mkv")

    create_test_file(old_backup, size_bytes=2048)
    create_test_file(new_cache, size_bytes=4096)

    return {
        "old_real": old_real, "new_real": new_real,
        "old_backup": old_backup, "new_backup": new_real + PLEXCACHED,
        "new_cache": new_cache,
    }


def _run(app, upgrade):
    """Call the swap with path helpers pinned to the tmp tree."""
    with patch("core.app.get_array_direct_path", side_effect=lambda p: p), \
         patch("core.app.find_matching_plexcached", return_value=upgrade["old_backup"]), \
         patch("core.app.create_dir_with_ownership"), \
         patch("core.app.get_media_identity", return_value="movie"):
        app._handle_upgrade_plexcached(
            upgrade["old_real"], upgrade["new_real"], "1234", upgrade["new_cache"])


class TestUpgradeBackupOrdering:

    def test_swaps_the_backup_on_the_happy_path(self, upgrade):
        _run(_app(), upgrade)

        assert not os.path.exists(upgrade["old_backup"]), "outdated backup should go"
        assert os.path.isfile(upgrade["new_backup"]), "replacement should exist"
        assert os.path.getsize(upgrade["new_backup"]) == 4096

    def test_failed_copy_keeps_the_old_backup(self, upgrade):
        """The regression: a file must never end up with no backup at all."""
        with patch("core.app.shutil.copy2", side_effect=OSError(28, "No space left on device")):
            _run(_app(), upgrade)

        assert os.path.isfile(upgrade["old_backup"]), (
            "old backup was deleted even though its replacement could not be written, "
            "leaving this file unrecoverable if the cache drive fails"
        )
        assert not os.path.exists(upgrade["new_backup"])

    def test_size_mismatch_keeps_the_old_backup(self, upgrade):
        """A short copy is discarded, so it must not count as a replacement."""
        real_getsize = os.path.getsize

        def truncated(path):
            return 1 if path == upgrade["new_backup"] else real_getsize(path)

        with patch("core.app.os.path.getsize", side_effect=truncated):
            _run(_app(), upgrade)

        assert os.path.isfile(upgrade["old_backup"])
        assert not os.path.exists(upgrade["new_backup"]), "short copy should be removed"

    def test_backup_upgraded_files_off_still_clears_the_stale_backup(self, upgrade):
        """No replacement is wanted, so the outdated one is simply removed."""
        _run(_app(backup_upgraded=False), upgrade)

        assert not os.path.exists(upgrade["old_backup"])
        assert not os.path.exists(upgrade["new_backup"])

    def test_backups_disabled_touches_nothing(self, upgrade):
        _run(_app(create_backups=False), upgrade)

        assert os.path.isfile(upgrade["old_backup"])
        assert not os.path.exists(upgrade["new_backup"])
