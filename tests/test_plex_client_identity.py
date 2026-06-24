"""Tests for the stable Plex device identity (issue #190).

PlexCache must present one persistent ``X-Plex-Client-Identifier`` so Plex sees a
single known "PlexCache-D" device instead of re-flagging it as new on every run or
container recreation. These cover ``apply_plex_client_identity`` (header mutation)
and ``ensure_plex_client_identity`` (load/create/persist + apply).
"""

import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

from core.plex_api import (
    apply_plex_client_identity,
    ensure_plex_client_identity,
    PLEXCACHE_CLIENT_ID_KEY,
    PLEXCACHE_PRODUCT_NAME,
)

# Module-global rebound by the autouse fixture to the real plexapi module.
plexapi = None


@pytest.fixture(autouse=True)
def restore_plexapi_globals():
    """Guarantee the real plexapi module, then restore everything we touched.

    Other test modules replace ``sys.modules['plexapi']`` with a MagicMock and
    don't restore it, and our functions do a lazy ``import plexapi`` — so without
    this we'd mutate a mock. Force the real package in, snapshot the header
    globals, and on teardown restore both the globals and the original
    sys.modules entries (so the mock other modules rely on is put back).
    """
    global plexapi
    saved_modules = {
        k: v for k, v in list(sys.modules.items())
        if k == "plexapi" or k.startswith("plexapi.")
    }

    if isinstance(sys.modules.get("plexapi"), MagicMock):
        for k in list(saved_modules):
            del sys.modules[k]
        try:
            plexapi = importlib.import_module("plexapi")
        except ImportError:
            sys.modules.update(saved_modules)
            pytest.skip("plexapi not installed")
    else:
        try:
            plexapi = importlib.import_module("plexapi")
        except ImportError:
            pytest.skip("plexapi not installed")

    saved_globals = {
        "X_PLEX_IDENTIFIER": plexapi.X_PLEX_IDENTIFIER,
        "X_PLEX_PRODUCT": plexapi.X_PLEX_PRODUCT,
        "X_PLEX_DEVICE_NAME": plexapi.X_PLEX_DEVICE_NAME,
        "X_PLEX_VERSION": plexapi.X_PLEX_VERSION,
        "BASE_HEADERS": dict(plexapi.BASE_HEADERS),
    }
    try:
        yield
    finally:
        plexapi.X_PLEX_IDENTIFIER = saved_globals["X_PLEX_IDENTIFIER"]
        plexapi.X_PLEX_PRODUCT = saved_globals["X_PLEX_PRODUCT"]
        plexapi.X_PLEX_DEVICE_NAME = saved_globals["X_PLEX_DEVICE_NAME"]
        plexapi.X_PLEX_VERSION = saved_globals["X_PLEX_VERSION"]
        plexapi.BASE_HEADERS.clear()
        plexapi.BASE_HEADERS.update(saved_globals["BASE_HEADERS"])
        # Put back whatever sys.modules['plexapi'] was (possibly a mock).
        for k in list(sys.modules):
            if k == "plexapi" or k.startswith("plexapi."):
                del sys.modules[k]
        sys.modules.update(saved_modules)


def test_apply_sets_stable_identity_on_base_headers(restore_plexapi_globals):
    apply_plex_client_identity("fixed-client-id-123")

    assert plexapi.X_PLEX_IDENTIFIER == "fixed-client-id-123"
    assert plexapi.BASE_HEADERS["X-Plex-Client-Identifier"] == "fixed-client-id-123"
    assert plexapi.BASE_HEADERS["X-Plex-Product"] == PLEXCACHE_PRODUCT_NAME
    assert plexapi.BASE_HEADERS["X-Plex-Device-Name"] == PLEXCACHE_PRODUCT_NAME


def test_apply_mutates_base_headers_in_place(restore_plexapi_globals):
    # Modules bind BASE_HEADERS via `from plexapi import BASE_HEADERS`, so the
    # change must land on the same dict object, not a replacement.
    same_ref = plexapi.BASE_HEADERS
    apply_plex_client_identity("ref-check-id")
    assert plexapi.BASE_HEADERS is same_ref
    assert same_ref["X-Plex-Client-Identifier"] == "ref-check-id"


def test_ensure_returns_none_when_file_missing(tmp_path, restore_plexapi_globals):
    missing = tmp_path / "nope.json"
    assert ensure_plex_client_identity(str(missing)) is None


def test_ensure_reuses_existing_client_id(tmp_path, restore_plexapi_globals):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"PLEX_TOKEN": "tok", PLEXCACHE_CLIENT_ID_KEY: "existing-id"}),
        encoding="utf-8",
    )

    result = ensure_plex_client_identity(str(settings))

    assert result == "existing-id"
    assert plexapi.BASE_HEADERS["X-Plex-Client-Identifier"] == "existing-id"
    # Existing value reused, not regenerated.
    on_disk = json.loads(settings.read_text(encoding="utf-8"))
    assert on_disk[PLEXCACHE_CLIENT_ID_KEY] == "existing-id"


def test_ensure_creates_and_persists_when_missing(tmp_path, restore_plexapi_globals):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"PLEX_TOKEN": "tok", "PLEX_URL": "http://x"}),
        encoding="utf-8",
    )

    result = ensure_plex_client_identity(str(settings))

    assert result  # a new id was minted
    on_disk = json.loads(settings.read_text(encoding="utf-8"))
    # New id persisted, and existing keys preserved.
    assert on_disk[PLEXCACHE_CLIENT_ID_KEY] == result
    assert on_disk["PLEX_TOKEN"] == "tok"
    assert on_disk["PLEX_URL"] == "http://x"
    assert plexapi.BASE_HEADERS["X-Plex-Client-Identifier"] == result


def test_ensure_does_not_clobber_unparseable_file(tmp_path, restore_plexapi_globals):
    settings = tmp_path / "settings.json"
    settings.write_text("{ this is not valid json", encoding="utf-8")

    result = ensure_plex_client_identity(str(settings))

    assert result is None
    # File left untouched rather than overwritten with a stub.
    assert settings.read_text(encoding="utf-8") == "{ this is not valid json"
