"""Phase 7 coverage: pins must survive export → import round-trips.

Before this fix, SettingsService.export_settings() only serialized
plexcache_settings.json. The pinned_media tracker lives in a separate
JSON under data/pinned_media.json and was silently omitted, so any
user who exported → wiped → imported lost every pin.
"""

import json
from unittest.mock import patch

import pytest


def _build_service(tmp_path):
    """Construct a SettingsService pointed at a temp settings file + data dir."""
    settings_file = tmp_path / "plexcache_settings.json"
    settings_file.write_text(json.dumps({
        "PLEX_URL": "http://plex.local:32400",
        "PLEX_TOKEN": "t0ken",
        "cache_limit": "10GB",
    }), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    patches = [
        patch("web.config.SETTINGS_FILE", settings_file, create=True),
        patch("web.config.DATA_DIR", data_dir, create=True),
        patch("web.dependencies.DATA_DIR", data_dir),
    ]
    for p in patches:
        p.start()

    from web.services.settings_service import SettingsService
    svc = SettingsService()
    # The attribute is `settings_file` (no underscore). Explicitly point it
    # at our temp file in case a cached singleton from an earlier test
    # captured a stale path before the patches above took effect.
    svc.settings_file = settings_file

    return svc, settings_file, data_dir, patches


def _teardown(patches):
    for p in patches:
        p.stop()


def _write_pinned_tracker(data_dir, entries):
    path = data_dir / "pinned_media.json"
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path


class TestExportIncludesPins:
    def test_export_embeds_pinned_media_key(self, tmp_path):
        svc, _, data_dir, patches = _build_service(tmp_path)
        try:
            _write_pinned_tracker(data_dir, {
                "100": {"rating_key": "100", "type": "movie",
                         "title": "Matrix", "added_at": "2026-04-17T00:00:00",
                         "added_by": "web"},
                "200": {"rating_key": "200", "type": "show",
                         "title": "The Office", "added_at": "2026-04-17T00:01:00",
                         "added_by": "cli"},
            })
            export = svc.export_settings(include_sensitive=True)
            assert "pinned_media" in export
            assert set(export["pinned_media"].keys()) == {"100", "200"}
            assert export["pinned_media"]["100"]["title"] == "Matrix"
        finally:
            _teardown(patches)

    def test_export_without_sensitive_still_includes_pins(self, tmp_path):
        # Pin data (rating_keys + titles + timestamps) is not sensitive —
        # it must survive the redacted export so shared configs still work.
        svc, _, data_dir, patches = _build_service(tmp_path)
        try:
            _write_pinned_tracker(data_dir, {
                "100": {"rating_key": "100", "type": "movie",
                         "title": "Matrix", "added_at": "2026-04-17T00:00:00",
                         "added_by": "web"},
            })
            export = svc.export_settings(include_sensitive=False)
            assert "pinned_media" in export
            assert export["pinned_media"]["100"]["title"] == "Matrix"
        finally:
            _teardown(patches)

    def test_export_omits_pinned_media_when_tracker_absent(self, tmp_path):
        svc, _, _, patches = _build_service(tmp_path)
        try:
            # No tracker file on disk
            export = svc.export_settings()
            assert export.get("pinned_media", {}) == {}
        finally:
            _teardown(patches)


class TestImportRestoresPins:
    def test_import_replace_mode_overwrites_tracker(self, tmp_path):
        svc, _, data_dir, patches = _build_service(tmp_path)
        try:
            # Pre-existing pin that should be wiped in replace mode
            _write_pinned_tracker(data_dir, {
                "999": {"rating_key": "999", "type": "movie",
                         "title": "Old Pin", "added_at": "2026-01-01T00:00:00",
                         "added_by": "web"},
            })
            payload = {
                "PLEX_URL": "http://plex.local:32400",
                "pinned_media": {
                    "100": {"rating_key": "100", "type": "movie",
                             "title": "Matrix", "added_at": "2026-04-17T00:00:00",
                             "added_by": "web"},
                },
            }
            result = svc.import_settings(payload, merge=False)
            assert result["success"] is True

            tracker_path = data_dir / "pinned_media.json"
            saved = json.loads(tracker_path.read_text(encoding="utf-8"))
            # Replace mode: old pin is gone, only imported pin remains
            assert set(saved.keys()) == {"100"}
            assert saved["100"]["title"] == "Matrix"
        finally:
            _teardown(patches)

    def test_import_merge_mode_unions_pins(self, tmp_path):
        svc, _, data_dir, patches = _build_service(tmp_path)
        try:
            _write_pinned_tracker(data_dir, {
                "999": {"rating_key": "999", "type": "movie",
                         "title": "Existing", "added_at": "2026-01-01T00:00:00",
                         "added_by": "web"},
            })
            payload = {
                "pinned_media": {
                    "100": {"rating_key": "100", "type": "movie",
                             "title": "Imported", "added_at": "2026-04-17T00:00:00",
                             "added_by": "web"},
                },
            }
            result = svc.import_settings(payload, merge=True)
            assert result["success"] is True

            saved = json.loads((data_dir / "pinned_media.json").read_text(encoding="utf-8"))
            # Merge mode: both old and new pins present
            assert set(saved.keys()) == {"100", "999"}
        finally:
            _teardown(patches)

    def test_import_strips_pinned_media_from_main_settings_file(self, tmp_path):
        # The "pinned_media" key must NOT end up inside plexcache_settings.json
        # — it's a tracker, not a setting.
        svc, settings_file, data_dir, patches = _build_service(tmp_path)
        try:
            payload = {
                "PLEX_URL": "http://fresh.local:32400",
                "pinned_media": {
                    "100": {"rating_key": "100", "type": "movie",
                             "title": "X", "added_at": "2026-04-17T00:00:00",
                             "added_by": "web"},
                },
            }
            svc.import_settings(payload, merge=False)
            saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
            assert "pinned_media" not in saved_settings
        finally:
            _teardown(patches)

    def test_import_without_pinned_key_leaves_tracker_untouched(self, tmp_path):
        svc, _, data_dir, patches = _build_service(tmp_path)
        try:
            pre_existing = {
                "999": {"rating_key": "999", "type": "movie",
                         "title": "Keep Me", "added_at": "2026-01-01T00:00:00",
                         "added_by": "web"},
            }
            _write_pinned_tracker(data_dir, pre_existing)

            svc.import_settings({"PLEX_URL": "http://x"}, merge=False)
            saved = json.loads((data_dir / "pinned_media.json").read_text(encoding="utf-8"))
            assert saved == pre_existing
        finally:
            _teardown(patches)


class TestValidateImportRecognisesPinnedKey:
    def test_pinned_media_key_is_not_flagged_as_unknown(self, tmp_path):
        svc, _, _, patches = _build_service(tmp_path)
        try:
            payload = {
                "PLEX_URL": "http://x",
                "PLEX_TOKEN": "t",
                "pinned_media": {"100": {"rating_key": "100", "type": "movie"}},
            }
            result = svc.validate_import_settings(payload)
            # Should not contain pinned_media in the unknown-keys warning
            unknown_warning = next(
                (w for w in result["warnings"] if "Unknown settings" in w), "",
            )
            assert "pinned_media" not in unknown_warning
        finally:
            _teardown(patches)
