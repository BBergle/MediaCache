from core.media_servers import (enabled_media_servers, media_server_value, jellyfin_username,
                                server_of_user, server_label, plex_rating_key,
                                CombinedMediaManager)
import pytest

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


class FakeManager:
    def __init__(self, ondeck=(), watchlist=(), sessions=(), fail_connect=False):
        self.plex = object()
        self.ondeck, self.watchlist, self.sessions = list(ondeck), list(watchlist), list(sessions)
        self.fail_connect = fail_connect
        self.ondeck_complete = self.watchlist_complete = True
        self.rss_seen = self.home_users_seen = "unset"

    def connect(self):
        if self.fail_connect:
            raise ConnectionError(self.fail_connect)

    def get_active_sessions(self):
        return self.sessions

    def get_on_deck_media(self, *args, **kwargs):
        return self.ondeck

    def get_watchlist_media(self, *args, rss_url=None, home_users="unset", **kwargs):
        self.rss_seen, self.home_users_seen = rss_url, home_users
        yield from self.watchlist

    def is_ondeck_data_complete(self):
        return self.ondeck_complete

    def is_watchlist_data_complete(self):
        return self.watchlist_complete

    def get_newly_discovered_users(self):
        return ["new"]

def _combined(**fakes):
    c = CombinedMediaManager(fakes)
    c.connect()
    return c

def test_merges_both_servers_in_order():
    p, j = FakeManager(ondeck=["A"], watchlist=["w1"], sessions=["s1"]), FakeManager(ondeck=["B"], watchlist=["w2"], sessions=["s2"])
    c = _combined(plex=p, jellyfin=j)
    assert c.get_on_deck_media([1], 7, 5, True, []) == ["A", "B"]
    assert c.get_active_sessions() == ["s1", "s2"]
    assert list(c.get_watchlist_media([1], 1, True, [], home_users=["x"])) == ["w1", "w2"]
    assert (p.home_users_seen, j.home_users_seen) == (["x"], "unset")
    assert c.missing_servers() == set() and c.is_ondeck_data_complete() and c.is_watchlist_data_complete()

def test_one_server_down_is_missing_but_run_continues():
    p, j = FakeManager(ondeck=["A"]), FakeManager(ondeck=["B"], fail_connect="down")
    c = _combined(plex=p, jellyfin=j)
    assert c.missing_servers() == {"jellyfin"}
    assert c.get_on_deck_media() == ["A"]
    assert not c.is_ondeck_data_complete() and not c.is_watchlist_data_complete()
    assert c.plex is p.plex

def test_all_servers_down_raises_with_each_reason():
    c = CombinedMediaManager({"plex": FakeManager(fail_connect="bad token"),
                              "jellyfin": FakeManager(fail_connect="timed out")})
    with pytest.raises(ConnectionError) as e:
        c.connect()
    assert str(e.value) == "No media server could be reached (Plex: bad token; Jellyfin: timed out)"

def test_rss_pass_asks_plex_only():
    p, j = FakeManager(watchlist=["w1"]), FakeManager(watchlist=["w2"])
    c = _combined(plex=p, jellyfin=j)
    assert list(c.get_watchlist_media([1], 1, True, [], rss_url="http://rss")) == ["w1"]
    assert p.rss_seen == "http://rss" and j.rss_seen == "unset"

def test_incomplete_watchlist_on_a_connected_server_is_incomplete():
    j = FakeManager()
    j.watchlist_complete = False
    c = _combined(plex=FakeManager(), jellyfin=j)
    assert not c.is_watchlist_data_complete()

def test_incomplete_server_counts_as_missing():
    j = FakeManager()
    j.ondeck_complete = False
    c = _combined(plex=FakeManager(), jellyfin=j)
    assert c.missing_servers() == {"jellyfin"} and not c.is_ondeck_data_complete()

def test_plex_only_features_need_plex():
    c = _combined(jellyfin=FakeManager())
    assert c.plex is None and c.get_newly_discovered_users() == []
