from core.media_servers import (enabled_media_servers, media_server_value, jellyfin_username,
                                server_of_user, server_label, plex_rating_key)

def test_enabled_media_servers():
    assert enabled_media_servers("plex") == ["plex"]
    assert enabled_media_servers("jellyfin") == ["jellyfin"]
    assert enabled_media_servers("both") == ["plex", "jellyfin"]
    assert enabled_media_servers("") == ["plex"] and enabled_media_servers(None) == ["plex"]

def test_media_server_value():
    assert media_server_value(True, True) == "both"
    assert media_server_value(True, False) == "plex"
    assert media_server_value(False, True) == "jellyfin"
    assert media_server_value(False, False) is None

def test_usernames_are_per_server():
    assert jellyfin_username("Ben") == "Ben (Jellyfin)"
    assert server_of_user("Ben (Jellyfin)") == "jellyfin"
    assert server_of_user("Ben") == "plex"
    assert server_label("jellyfin") == "Jellyfin"

def test_only_plex_rating_keys_survive():
    assert plex_rating_key("Ben", "123") == "123"
    assert plex_rating_key("Ben (Jellyfin)", "a1b2c3") is None
