"""Helpers for running Plex and Jellyfin side by side."""

import logging

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


class CombinedMediaManager:
    """Runs several media-server managers behind the single-manager interface."""

    def __init__(self, managers):
        self.managers = dict(managers)
        self.failed = set()

    def connect(self):
        errors = []
        for name, manager in self.managers.items():
            try:
                manager.connect()
            except ConnectionError as e:
                logging.warning(f"[MEDIA SERVER] {server_label(name)} unavailable: {e}")
                self.failed.add(name)
                errors.append(f"{server_label(name)}: {e}")
        if len(self.failed) == len(self.managers):
            raise ConnectionError(f"No media server could be reached ({'; '.join(errors)})")

    def _connected(self):
        return [(n, m) for n, m in self.managers.items() if n not in self.failed]

    @property
    def plex(self):
        if "plex" in self.managers and "plex" not in self.failed:
            return self.managers["plex"].plex
        return None

    def _plex_manager(self):
        return None if "plex" in self.failed else self.managers.get("plex")

    def load_user_tokens(self, **kwargs):
        manager = self._plex_manager()
        return manager.load_user_tokens(**kwargs) if manager else None

    def get_newly_discovered_users(self):
        manager = self._plex_manager()
        return manager.get_newly_discovered_users() if manager else []

    def get_active_sessions(self):
        return [s for _, m in self._connected() for s in m.get_active_sessions()]

    def get_on_deck_media(self, *args, **kwargs):
        return [i for _, m in self._connected() for i in m.get_on_deck_media(*args, **kwargs)]

    def get_watchlist_media(self, *args, rss_url=None, home_users=None, **kwargs):
        for name, m in self._connected():
            if name == "plex":
                yield from m.get_watchlist_media(*args, rss_url=rss_url, home_users=home_users, **kwargs)
            elif not rss_url:  # the remote-RSS pass is Plex-only
                yield from m.get_watchlist_media(*args, **kwargs)

    def missing_servers(self):
        return set(self.failed) | {n for n, m in self._connected() if not m.is_ondeck_data_complete()}

    def is_ondeck_data_complete(self):
        return not self.missing_servers()

    def is_watchlist_data_complete(self):
        return not self.failed and all(m.is_watchlist_data_complete() for _, m in self._connected())
