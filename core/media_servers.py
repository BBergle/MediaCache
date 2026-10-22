"""Helpers for running Plex and Jellyfin side by side."""

JELLYFIN_SUFFIX = " (Jellyfin)"


def enabled_media_servers(media_server):
    if media_server == "both":
        return ["plex", "jellyfin"]
    return ["jellyfin"] if media_server == "jellyfin" else ["plex"]


def media_server_value(plex, jellyfin):
    if plex and jellyfin:
        return "both"
    return "plex" if plex else "jellyfin" if jellyfin else None


def jellyfin_username(name):
    return f"{name}{JELLYFIN_SUFFIX}"


def server_of_user(username):
    return "jellyfin" if username.endswith(JELLYFIN_SUFFIX) else "plex"


def server_label(server):
    return "Jellyfin" if server == "jellyfin" else "Plex"


def plex_rating_key(username, rating_key):
    return rating_key if server_of_user(username) == "plex" else None
