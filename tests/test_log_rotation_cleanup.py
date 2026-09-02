"""Rotation backups are cleaned up with the log file they belong to.

Each run writes its own timestamped log, so RotatingFileHandler only rolls over
when a single run exceeds maxBytes (20 MB). The backup it leaves is named
``plexcache_log_<stamp>.log.1``, which the ``plexcache_log_*.log`` cleanup glob
cannot match, so nothing removed them.
"""

import sys
from unittest.mock import MagicMock

import pytest

if 'fcntl' not in sys.modules:
    sys.modules['fcntl'] = MagicMock()


def _manager(logs_folder, max_log_files=3):
    from core.logging_config import LoggingManager

    manager = object.__new__(LoggingManager)
    manager.logs_folder = logs_folder
    manager.max_log_files = max_log_files
    manager.log_file_pattern = "plexcache_log_*.log"
    return manager


def _make(folder, name, mtime):
    import os
    path = folder / name
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


class TestRotationBackupCleanup:

    def test_backup_goes_when_its_log_is_cleaned(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        # Oldest run overflowed 20MB and left two backups.
        old = _make(logs, "plexcache_log_20260101_000000.log", 1_000)
        old_b1 = _make(logs, "plexcache_log_20260101_000000.log.1", 1_000)
        old_b2 = _make(logs, "plexcache_log_20260101_000000.log.2", 1_000)
        for i, stamp in enumerate(["20260102", "20260103", "20260104"], start=1):
            _make(logs, f"plexcache_log_{stamp}_000000.log", 2_000 + i)

        _manager(logs, max_log_files=3)._clean_old_log_files()

        assert not old.exists(), "oldest log should be cleaned"
        assert not old_b1.exists(), "its rotation backup should go with it"
        assert not old_b2.exists()

    def test_backup_stays_while_its_log_stays(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        current = _make(logs, "plexcache_log_20260104_000000.log", 5_000)
        backup = _make(logs, "plexcache_log_20260104_000000.log.1", 5_000)

        _manager(logs, max_log_files=3)._clean_old_log_files()

        assert current.exists()
        assert backup.exists(), "a live run's overflow must be kept"

    def test_leaves_unrelated_files_alone(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        _make(logs, "plexcache_log_20260104_000000.log", 5_000)
        # Not a numeric rollover suffix.
        notes = _make(logs, "plexcache_log_20260101_000000.log.bak", 1_000)
        latest = _make(logs, "plexcache_log_latest.log", 5_001)

        _manager(logs, max_log_files=3)._clean_old_log_files()

        assert notes.exists(), ".bak is not a rollover suffix"
        assert latest.exists()

    def test_count_limit_still_applies(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        for i in range(6):
            _make(logs, f"plexcache_log_2026010{i}_000000.log", 1_000 + i)

        _manager(logs, max_log_files=3)._clean_old_log_files()

        remaining = sorted(p.name for p in logs.glob("plexcache_log_*.log"))
        assert len(remaining) == 3, remaining


class TestLatestPointerIsNotCounted:

    def test_symlink_does_not_consume_a_retention_slot(self, tmp_path):
        """"Keep 3" should keep 3 runs, not 2 runs plus the pointer."""
        logs = tmp_path / "logs"
        logs.mkdir()
        for i in range(3):
            _make(logs, f"plexcache_log_2026010{i}_000000.log", 1_000 + i)
        _make(logs, "plexcache_log_latest.log", 9_999)

        _manager(logs, max_log_files=3)._clean_old_log_files()

        runs = sorted(p.name for p in logs.glob("plexcache_log_*.log")
                      if p.name != "plexcache_log_latest.log")
        assert len(runs) == 3, runs
        assert (logs / "plexcache_log_latest.log").exists()
