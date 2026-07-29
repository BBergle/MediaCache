"""Tests for Unraid share alignment validation.

Unraid merges a share's pool tier and array tier under one /mnt/user/<share>/
path. Caching only works when the cache destination is the *same* share as the
media: a file moves between tiers and the path Plex reads never changes. Point
the cache at a different share and the cached copy lands where Plex isn't
looking, leaving only the .plexcached stub (issues #189, #196).

These tests pin the real configurations from both reports so the checks can't
silently regress into missing them.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules['fcntl'] = MagicMock()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.system_utils import check_cache_share_alignment, extract_unraid_share


class TestExtractUnraidShare:
    @pytest.mark.parametrize("path,expected", [
        ("/mnt/user/Movies/Action/", "Movies"),
        ("/mnt/user0/data/media/movies/", "data"),
        ("/mnt/disk3/data/media/", "data"),
        ("/mnt/disk12/Plex_Media/Videos/", "Plex_Media"),
        ("/mnt/cache_downloads/Movies/", "Movies"),
        ("/mnt/ssd_cache/plex/Cache/movies/", "plex"),
        ("/mnt/user/Movies", "Movies"),          # no trailing slash
        ("  /mnt/user/Movies/  ", "Movies"),      # surrounding whitespace
        ("/mnt/user//Movies//Action/", "Movies"),  # duplicate separators
    ])
    def test_resolves_share(self, path, expected):
        assert extract_unraid_share(path) == expected

    @pytest.mark.parametrize("path", [
        "/mnt/cache/",        # bare mount, no share segment
        "/mnt/user/",
        "/mnt/user",
        "/mnt/",
        "/movies/",           # container-relative fragment
        "/srv/media/movies/",  # not an Unraid layout
        "",
        None,
    ])
    def test_returns_none_when_undeterminable(self, path):
        assert extract_unraid_share(path) is None


class TestReportedConfigurations:
    """The exact mappings from #189 and #196, and configs known to be correct."""

    def test_issue_196_current_config_is_flagged(self):
        """Media in `data`, host cache path a bare container fragment."""
        w = check_cache_share_alignment(
            "/mnt/user/data/media/movies/", "/mnt/cache/movies/", "/movies/"
        )
        assert w is not None
        assert w["kind"] == "cache_not_under_mnt"
        assert w["real_share"] == "data"

    def test_issue_189_current_config_is_flagged(self):
        """Media in `Plex_Media`, cache pointed at the separate `plexdb` share."""
        w = check_cache_share_alignment(
            "/mnt/user/Plex_Media/Videos/TV Shows/",
            "/mnt/cache/Videos/TV Shows/",
            "/mnt/user/plexdb/Media Cache/Videos/TV Shows/",
        )
        assert w is not None
        assert w["kind"] == "share_mismatch"
        assert w["real_share"] == "Plex_Media"
        assert w["cache_share"] == "plexdb"

    def test_issue_189_after_fix_is_clean(self):
        assert check_cache_share_alignment(
            "/mnt/user/Plex_Media/Videos/TV Shows/",
            "/mnt/cache/Plex_Media/Videos/TV Shows/",
            "/mnt/plexdatabase/Plex_Media/Videos/TV Shows/",
        ) is None

    def test_issue_196_after_fix_is_clean(self):
        assert check_cache_share_alignment(
            "/mnt/user/data/media/movies/",
            "/mnt/cache/data/media/movies/",
            "/mnt/ssd_cache/data/media/movies/",
        ) is None

    def test_known_good_config_is_clean(self):
        """A working two-tier setup: array-direct real path, pool host cache."""
        assert check_cache_share_alignment(
            "/mnt/user0/TV Shows/", "/mnt/cache/TV Shows/", "/mnt/cache_downloads/TV Shows/"
        ) is None


class TestMismatchDetection:
    def test_different_shares_without_host_cache(self):
        w = check_cache_share_alignment("/mnt/user/Movies/", "/mnt/cache/OtherShare/")
        assert w["kind"] == "share_mismatch"
        assert (w["real_share"], w["cache_share"]) == ("Movies", "OtherShare")

    def test_same_share_across_tiers_is_clean(self):
        assert check_cache_share_alignment("/mnt/user/Movies/", "/mnt/cache/Movies/") is None

    def test_host_cache_path_wins_over_container_path(self):
        """Docker remaps the container prefix, so the host path is authoritative."""
        # Container path alone would look mismatched (Movies vs media)...
        assert check_cache_share_alignment("/mnt/user/media/Movies/", "/mnt/cache/Movies/") is not None
        # ...but the real host path agrees, so no warning.
        assert check_cache_share_alignment(
            "/mnt/user/media/Movies/", "/mnt/cache/Movies/", "/mnt/pool/media/Movies/"
        ) is None

    def test_message_names_both_shares(self):
        w = check_cache_share_alignment("/mnt/user/Movies/", "/mnt/cache/Downloads/")
        assert "Movies" in w["message"] and "Downloads" in w["message"]


class TestStaysQuiet:
    """The check is advisory. It must not fire on layouts it can't reason about."""

    @pytest.mark.parametrize("real,cache,host", [
        ("/srv/media/movies/", "/fast/movies/", None),   # non-Unraid layout
        ("/mnt/user/Movies/", "", None),                  # no cache configured
        ("", "/mnt/cache/Movies/", None),                 # no real path
        ("/mnt/user/Movies/", "/mnt/cache/", None),       # bare cache mount
    ])
    def test_no_warning(self, real, cache, host):
        assert check_cache_share_alignment(real, cache, host) is None

    def test_non_mnt_real_path_skips_even_with_odd_cache(self):
        """Anchored on the media path — if that isn't Unraid-shaped, stay silent."""
        assert check_cache_share_alignment("/srv/media/", "/movies/", "/movies/") is None
