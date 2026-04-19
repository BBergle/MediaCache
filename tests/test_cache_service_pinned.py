"""Phase 3 tests: CacheService is_pinned surfacing + eviction/simulate guards.

Exercises:
- get_all_cached_files populates ``CachedFile.is_pinned`` from the pinned
  path set (and forces ``priority_score`` to 100 for pinned rows).
- simulate_eviction skips any file whose ``is_pinned`` is True.
- evict_file refuses pinned paths with "File is pinned — unpin first".
"""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# conftest.py handles fcntl mock and sys.path setup


MOCK_SETTINGS = {
    "path_mappings": [
        {
            "name": "Movies",
            "plex_path": "/plex/movies",
            "real_path": "/mnt/user/media/Movies",
            "cache_path": "/mnt/cache/media/Movies",
            "cacheable": True,
            "enabled": True,
        },
    ],
    "cache_eviction_mode": "smart",
    "cache_drive_size": "",
    "cache_limit": "10GB",
}


def _make_service(tmp_path, pinned_paths=None, pinned_map=None):
    """Build a CacheService with its pinned-resolution hooks replaced.

    ``pinned_paths`` is a convenience for tests that only care about the
    set membership (is_pinned true/false). ``pinned_map`` is the richer
    form — a dict of ``cache_path → (rating_key, pin_type)`` — used by
    tests that need the row's rating_key / pin_type to be populated.
    """
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps(MOCK_SETTINGS), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    exclude_file = tmp_path / "plexcache_cached_files.txt"
    timestamps_file = data_dir / "timestamps.json"
    ondeck_file = data_dir / "ondeck_tracker.json"
    watchlist_file = data_dir / "watchlist_tracker.json"

    with patch("web.services.cache_service.SETTINGS_FILE", settings_file), \
         patch("web.services.cache_service.CONFIG_DIR", tmp_path), \
         patch("web.services.cache_service.DATA_DIR", data_dir):
        from web.services.cache_service import CacheService
        svc = CacheService()

    svc.settings_file = settings_file
    svc.exclude_file = exclude_file
    svc.timestamps_file = timestamps_file
    svc.ondeck_file = ondeck_file
    svc.watchlist_file = watchlist_file

    # Prefer the explicit map when given; otherwise synthesize an empty-
    # metadata map from the set so both helpers agree on membership.
    if pinned_map is not None:
        fixed_map = dict(pinned_map)
    else:
        fixed_map = {p: (None, None) for p in (pinned_paths or ())}
    svc._get_pinned_cache_path_map = lambda: fixed_map
    svc._get_pinned_cache_paths = lambda: set(fixed_map.keys())
    return svc


def _create_video(path, size=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    return path


def _write_exclude(svc, paths):
    with open(svc.exclude_file, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(p + "\n")


def _write_timestamps(svc, entries):
    with open(svc.timestamps_file, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


# ---------------------------------------------------------------------------
# get_all_cached_files — is_pinned surfacing
# ---------------------------------------------------------------------------


class TestCachedFileIsPinned:
    def test_pinned_file_marked_and_scored_100(self, tmp_path):
        cache_dir = tmp_path / "cache" / "media" / "Movies"
        pinned_path = str(cache_dir / "Pinned.mkv")
        other_path = str(cache_dir / "Other.mkv")
        _create_video(pinned_path)
        _create_video(other_path)

        svc = _make_service(tmp_path, pinned_paths={pinned_path})
        _write_exclude(svc, [pinned_path, other_path])
        _write_timestamps(svc, {
            pinned_path: {"cached_at": "2026-04-01T12:00:00", "source": "pinned"},
            other_path: {"cached_at": "2026-04-01T12:00:00", "source": "unknown"},
        })

        # get_cached_files_list reads exclude file; override settings to point cache_dir there
        svc.exclude_file = Path(str(svc.exclude_file))

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}

        assert by_path[pinned_path].is_pinned is True
        assert by_path[pinned_path].priority_score == 100
        assert by_path[other_path].is_pinned is False

    def test_no_pins_all_unpinned(self, tmp_path):
        cache_dir = tmp_path / "cache" / "media" / "Movies"
        path = str(cache_dir / "Movie.mkv")
        _create_video(path)

        svc = _make_service(tmp_path, pinned_paths=set())
        _write_exclude(svc, [path])
        _write_timestamps(svc, {
            path: {"cached_at": "2026-04-01T12:00:00", "source": "unknown"},
        })

        files = svc.get_all_cached_files()
        assert all(f.is_pinned is False for f in files)


# ---------------------------------------------------------------------------
# simulate_eviction — pinned skipped
# ---------------------------------------------------------------------------


class TestSimulateEvictionSkipsPinned:
    def test_pinned_file_never_in_would_evict(self, tmp_path):
        cache_dir = tmp_path / "cache" / "media" / "Movies"
        pinned_path = str(cache_dir / "Pinned.mkv")
        other_path = str(cache_dir / "Other.mkv")
        _create_video(pinned_path, size=5000)
        _create_video(other_path, size=5000)

        svc = _make_service(tmp_path, pinned_paths={pinned_path})
        _write_exclude(svc, [pinned_path, other_path])
        _write_timestamps(svc, {
            pinned_path: {"cached_at": "2026-01-01T00:00:00", "source": "pinned"},
            other_path: {"cached_at": "2026-01-01T00:00:00", "source": "unknown"},
        })

        # Force simulate_eviction to try to free a large amount — even so,
        # pinned file must be skipped.
        with patch.object(svc, "_get_cache_dir", return_value=str(cache_dir)), \
             patch("web.services.cache_service.get_disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(used=10_000, total=10_000)
            result = svc.simulate_eviction(threshold_percent=10)

        assert all(f["path"] != pinned_path for f in result["would_evict"])


# ---------------------------------------------------------------------------
# evict_file — refuse pinned
# ---------------------------------------------------------------------------


class TestEvictFileRefusesPinned:
    def test_pinned_path_refused(self, tmp_path):
        cache_dir = tmp_path / "cache" / "media" / "Movies"
        path = str(cache_dir / "Movie.mkv")
        _create_video(path)

        svc = _make_service(tmp_path, pinned_paths={path})
        _write_exclude(svc, [path])
        _write_timestamps(svc, {path: "2026-04-01T12:00:00"})

        result = svc.evict_file(path)
        assert result["success"] is False
        assert "pinned" in result["message"].lower()
        # File must still be on disk
        assert os.path.exists(path)

    def test_evict_files_accumulates_pinned_errors(self, tmp_path):
        cache_dir = tmp_path / "cache" / "media" / "Movies"
        p1 = str(cache_dir / "A.mkv")
        p2 = str(cache_dir / "B.mkv")
        _create_video(p1)
        _create_video(p2)

        svc = _make_service(tmp_path, pinned_paths={p1})
        _write_exclude(svc, [p1, p2])
        _write_timestamps(svc, {p1: "2026-04-01T12:00:00", p2: "2026-04-01T12:00:00"})

        result = svc.evict_files([p1, p2])
        # Both may fail (p2 fails because no array copy confirmed), but the
        # contract we test is specific: the p1 error mentions "pinned".
        errors_joined = " ".join(result["errors"])
        assert "pinned" in errors_joined.lower()


# ---------------------------------------------------------------------------
# Phase 5: CachedFile.rating_key + pin_type population for pin-in-row button
# ---------------------------------------------------------------------------


def _write_ondeck(svc, entries):
    with open(svc.ondeck_file, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _write_watchlist(svc, entries):
    with open(svc.watchlist_file, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


class TestCachedFileRatingKeyPopulation:
    """Phase 5: ``CachedFile.rating_key`` + ``pin_type`` drive the
    pin/unpin button in the Cached Files row. They must be populated
    from OnDeck/Watchlist tracker entries (for un-pinned files) and
    from the pinned path map (for pinned files)."""

    def test_ondeck_episode_propagates_rating_key_and_episode_pin_type(self, tmp_path):
        cache_dir = tmp_path / "cache" / "tv" / "Show"
        episode_path = str(cache_dir / "Show - S01E05.mkv")
        _create_video(episode_path)

        svc = _make_service(tmp_path)
        _write_exclude(svc, [episode_path])
        _write_timestamps(svc, {
            episode_path: {"cached_at": "2026-04-01T12:00:00", "source": "ondeck"},
        })
        _write_ondeck(svc, {
            "/data/tv/Show/Show - S01E05.mkv": {
                "users": ["Brandon"],
                "rating_key": "12345",
                "episode_info": {"show": "Show", "season": 1, "episode": 5},
            },
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[episode_path]

        assert row.is_pinned is False
        assert row.rating_key == "12345"
        assert row.pin_type == "episode"

    def test_ondeck_movie_without_episode_info_gets_movie_pin_type(self, tmp_path):
        cache_dir = tmp_path / "cache" / "movies"
        movie_path = str(cache_dir / "Movie.mkv")
        _create_video(movie_path)

        svc = _make_service(tmp_path)
        _write_exclude(svc, [movie_path])
        _write_timestamps(svc, {
            movie_path: {"cached_at": "2026-04-01T12:00:00", "source": "ondeck"},
        })
        _write_ondeck(svc, {
            "/data/movies/Movie.mkv": {
                "users": ["Brandon"],
                "rating_key": "99999",
            },
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[movie_path]

        assert row.rating_key == "99999"
        assert row.pin_type == "movie"

    def test_watchlist_entry_populates_rating_key(self, tmp_path):
        cache_dir = tmp_path / "cache" / "movies"
        movie_path = str(cache_dir / "Watched.mkv")
        _create_video(movie_path)

        svc = _make_service(tmp_path)
        _write_exclude(svc, [movie_path])
        _write_timestamps(svc, {
            movie_path: {"cached_at": "2026-04-01T12:00:00", "source": "watchlist"},
        })
        _write_watchlist(svc, {
            "/data/movies/Watched.mkv": {
                "users": ["Brandon"],
                "rating_key": "77777",
                "media_type": "movie",
            },
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[movie_path]

        assert row.rating_key == "77777"
        assert row.pin_type == "movie"
        assert row.is_watchlist is True

    def test_watchlist_episode_gets_episode_pin_type(self, tmp_path):
        """Watchlisted TV episode derives pin_type='episode' from tracker media_type.

        Without the stored media_type, the cache_service fallback would treat
        every watchlist-only row as a movie (episode_info only lives on OnDeck
        entries). Storing media_type at watchlist-gather time fixes the
        cosmetic bug where pinned watchlisted episodes rendered as movies.
        """
        cache_dir = tmp_path / "cache" / "tv"
        episode_path = str(cache_dir / "Show.S01E03.mkv")
        _create_video(episode_path)

        svc = _make_service(tmp_path)
        _write_exclude(svc, [episode_path])
        _write_timestamps(svc, {
            episode_path: {"cached_at": "2026-04-01T12:00:00", "source": "watchlist"},
        })
        _write_watchlist(svc, {
            "/data/tv/Show.S01E03.mkv": {
                "users": ["Brandon"],
                "rating_key": "88888",
                "media_type": "episode",
            },
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[episode_path]

        assert row.rating_key == "88888"
        assert row.pin_type == "episode"
        assert row.is_watchlist is True

    def test_legacy_watchlist_entry_falls_back_to_movie(self, tmp_path):
        """Legacy watchlist entries (pre-media_type) keep the old 'movie' fallback.

        Entries written before #12 landed have no ``media_type`` field. The
        cache_service must not crash and must preserve the historical
        behavior until the next Plex fetch repopulates the tracker.
        """
        cache_dir = tmp_path / "cache" / "movies"
        legacy_path = str(cache_dir / "Legacy.mkv")
        _create_video(legacy_path)

        svc = _make_service(tmp_path)
        _write_exclude(svc, [legacy_path])
        _write_timestamps(svc, {
            legacy_path: {"cached_at": "2026-04-01T12:00:00", "source": "watchlist"},
        })
        _write_watchlist(svc, {
            "/data/movies/Legacy.mkv": {
                "users": ["Brandon"],
                "rating_key": "99999",
                # no media_type — pre-migration entry
            },
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[legacy_path]

        assert row.rating_key == "99999"
        assert row.pin_type == "movie"
        assert row.is_watchlist is True

    def test_untracked_file_has_none_rating_key(self, tmp_path):
        cache_dir = tmp_path / "cache" / "other"
        orphan_path = str(cache_dir / "Orphan.mkv")
        _create_video(orphan_path)

        svc = _make_service(tmp_path)
        _write_exclude(svc, [orphan_path])
        _write_timestamps(svc, {
            orphan_path: {"cached_at": "2026-04-01T12:00:00", "source": "unknown"},
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[orphan_path]

        # No OnDeck/Watchlist entry, no pin map → row can't offer a pin button
        assert row.rating_key is None
        assert row.pin_type is None

    def test_pinned_map_overrides_tracker_rating_key(self, tmp_path):
        """When a file is pinned, the pinned map is authoritative for the
        unpin button — use its rating_key/pin_type, not the tracker's."""
        cache_dir = tmp_path / "cache" / "tv" / "Show"
        episode_path = str(cache_dir / "Show - S01E05.mkv")
        _create_video(episode_path)

        svc = _make_service(
            tmp_path,
            pinned_map={episode_path: ("55555", "episode")},
        )
        _write_exclude(svc, [episode_path])
        _write_timestamps(svc, {
            episode_path: {"cached_at": "2026-04-01T12:00:00", "source": "ondeck"},
        })
        # Tracker says rating_key=12345 but pinned map says 55555 — the
        # pinned map wins so the unpin button targets the right item.
        _write_ondeck(svc, {
            "/data/tv/Show/Show - S01E05.mkv": {
                "users": ["Brandon"],
                "rating_key": "12345",
                "episode_info": {"show": "Show", "season": 1, "episode": 5},
            },
        })

        files = svc.get_all_cached_files()
        by_path = {f.path: f for f in files}
        row = by_path[episode_path]

        assert row.is_pinned is True
        assert row.rating_key == "55555"
        assert row.pin_type == "episode"

    def test_pinned_file_with_only_set_form_has_none_metadata(self, tmp_path):
        """Back-compat: callers using the set-only ``pinned_paths`` shortcut
        still get is_pinned=True, with rating_key/pin_type=None (no pin
        button rendered — the row shows only the pinned badge)."""
        cache_dir = tmp_path / "cache" / "movies"
        movie_path = str(cache_dir / "Pinned.mkv")
        _create_video(movie_path)

        svc = _make_service(tmp_path, pinned_paths={movie_path})
        _write_exclude(svc, [movie_path])
        _write_timestamps(svc, {
            movie_path: {"cached_at": "2026-04-01T12:00:00", "source": "pinned"},
        })

        files = svc.get_all_cached_files()
        row = {f.path: f for f in files}[movie_path]

        assert row.is_pinned is True
        assert row.rating_key is None
        assert row.pin_type is None
