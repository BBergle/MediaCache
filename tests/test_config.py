"""Validation of the media_server setting ("plex", "jellyfin" or "both")."""

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
sys.modules.setdefault('plexapi', MagicMock())
sys.modules.setdefault('plexapi.server', MagicMock())

from core.config import ConfigManager

PLEX = {"PLEX_URL": "http://plex", "PLEX_TOKEN": "tok"}
JELLYFIN = {"jellyfin_url": "http://jf", "jellyfin_api_key": "key"}


def _validate(tmp_path, **settings):
    cfg = ConfigManager(str(tmp_path / "s.json"))
    cfg.settings_data = {"cache_dir": "/cache", "path_mappings": [{"name": "m"}], **settings}
    cfg._validate_values()


def test_both_requires_every_server_field(tmp_path):
    with pytest.raises(ValueError) as exc:
        _validate(tmp_path, media_server="both")
    for field in ("jellyfin_url", "jellyfin_api_key", "PLEX_URL", "PLEX_TOKEN"):
        assert f"'{field}' cannot be empty" in str(exc.value)


def test_both_accepts_complete_settings(tmp_path):
    _validate(tmp_path, media_server="both", **PLEX, **JELLYFIN)


def test_single_server_only_requires_its_own_fields(tmp_path):
    _validate(tmp_path, media_server="plex", **PLEX)
    _validate(tmp_path, media_server="jellyfin", **JELLYFIN)


def test_unknown_media_server_rejected(tmp_path):
    with pytest.raises(ValueError, match="'media_server' must be 'plex', 'jellyfin' or 'both'"):
        _validate(tmp_path, media_server="emby", **PLEX)
