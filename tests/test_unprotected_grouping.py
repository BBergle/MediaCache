"""Tests for the audit's unprotected-files grouping.

This table pairs a video with its own sidecars for remediation, so it keys on
*directory* rather than show — but it uses the same ordered-bucket rule from
``core.media_grouping`` as every other grouped list, so these lock in the
ordering and primary/children semantics.
"""

import pytest

pytest.importorskip("web.services.maintenance_service")

from web.services.maintenance_service import MaintenanceService, UnprotectedFile


def _f(cache_path, size=100):
    import os
    return UnprotectedFile(
        cache_path=cache_path,
        filename=os.path.basename(cache_path),
        size=size,
        size_display=f"{size} B",
        has_plexcached_backup=False,
        backup_path=None,
        has_array_duplicate=False,
        array_path=None,
        recommended_action="add_to_exclude",
    )


def _group(files):
    return MaintenanceService._group_unprotected_by_directory(
        MaintenanceService.__new__(MaintenanceService), files
    )


class TestGroupUnprotectedByDirectory:
    def test_video_with_sidecars_becomes_one_group(self):
        result = _group([
            _f("/mnt/cache/Movies/Dune/Dune.mkv", 900),
            _f("/mnt/cache/Movies/Dune/Dune.en.srt", 50),
            _f("/mnt/cache/Movies/Dune/Dune.nfo", 50),
        ])
        assert len(result) == 1
        assert result[0]["primary"].filename == "Dune.mkv"
        assert [c.filename for c in result[0]["children"]] == ["Dune.en.srt", "Dune.nfo"]
        assert result[0]["folder"] == "Dune"
        assert result[0]["total_size_display"] == "1000 B"

    def test_lone_video_is_not_grouped(self):
        result = _group([_f("/mnt/cache/Movies/Dune/Dune.mkv")])
        assert len(result) == 1
        assert result[0]["children"] == []
        assert result[0]["folder"] is None

    def test_sidecars_without_video_group_under_folder(self):
        result = _group([
            _f("/mnt/cache/Movies/Dune/Dune.en.srt", 30),
            _f("/mnt/cache/Movies/Dune/Dune.nfo", 20),
        ])
        assert len(result) == 1
        assert result[0]["primary"].filename == "Dune.en.srt"
        assert [c.filename for c in result[0]["children"]] == ["Dune.nfo"]
        assert result[0]["folder"] == "Dune"

    def test_separate_directories_stay_separate(self):
        result = _group([
            _f("/mnt/cache/Movies/Dune/Dune.mkv"),
            _f("/mnt/cache/Movies/Dune/Dune.srt"),
            _f("/mnt/cache/TV/Sugar/S01E01.mkv"),
            _f("/mnt/cache/TV/Sugar/S01E01.srt"),
        ])
        assert len(result) == 2
        assert [g["folder"] for g in result] == ["Dune", "Sugar"]

    def test_directory_order_follows_first_seen_file(self):
        # Interleaved input must not reorder the directories — a group anchors
        # where its first file appeared.
        result = _group([
            _f("/mnt/cache/B/b.mkv"),
            _f("/mnt/cache/A/a.mkv"),
            _f("/mnt/cache/B/b.srt"),
            _f("/mnt/cache/A/a.srt"),
        ])
        assert [g["folder"] for g in result] == ["B", "A"]

    def test_two_videos_in_one_directory_keep_first_as_primary(self):
        result = _group([
            _f("/mnt/cache/TV/Sugar/S01E01.mkv"),
            _f("/mnt/cache/TV/Sugar/S01E02.mkv"),
        ])
        assert len(result) == 1
        assert result[0]["primary"].filename == "S01E01.mkv"
        assert [c.filename for c in result[0]["children"]] == ["S01E02.mkv"]

    def test_empty_input(self):
        assert _group([]) == []
