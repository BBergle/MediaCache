"""JellyfinManager maps Jellyfin API responses onto the PlexManager run interface."""

from datetime import datetime, timedelta, timezone

from core.jellyfin_api import JellyfinManager


def _ep(id_, season, ep, played=False, path=None):
    return {"Id": id_, "Type": "Episode", "SeriesId": "show", "SeriesName": "Show",
            "Name": f"S{season}E{ep}", "ParentIndexNumber": season, "IndexNumber": ep,
            "RunTimeTicks": 45 * 60 * 10_000_000, "UserData": {"Played": played},
            "MediaSources": [{"Path": path or f"/tv/Show/S{season}E{ep}.mkv", "Protocol": "File"}]}


EPISODES = [_ep("e1", 1, 1, played=True), _ep("e2", 1, 2), _ep("e3", 1, 3), _ep("e4", 2, 1)]
MOVIE = {"Id": "m1", "Type": "Movie", "Path": "/movies/M.mkv",
         "UserData": {"LastPlayedDate": "2099-01-01T00:00:00.0000000Z"}}
OLD_MOVIE = {"Id": "m2", "Type": "Movie", "Path": "/movies/Old.mkv",
             "UserData": {"LastPlayedDate": "2000-01-01T00:00:00.0000000Z"}}

ROUTES = {
    "/System/Info": {"Version": "10.10.0"},
    "/Users": [{"Name": "admin", "Id": "u1", "Policy": {"IsAdministrator": True}},
               {"Name": "kid", "Id": "u2", "Policy": {}}],
    "/UserItems/Resume": {"Items": [MOVIE, OLD_MOVIE]},
    "/Shows/NextUp": {"Items": [EPISODES[1]]},
    "/Shows/show/Episodes": {"Items": EPISODES},
    "/Sessions": [{"NowPlayingItem": {"Id": "m1"}}, {}],
}


def _manager():
    m = JellyfinManager("http://jf/", "key")
    calls = []

    def fake_get(path, **params):
        calls.append((path, params))
        if path == "/Items" and params.get("isFavorite"):
            return {"Items": [{"Id": "show", "Type": "Series"}, MOVIE]}
        if path == "/Items":
            return {"Items": [MOVIE]}
        return ROUTES[path]

    m._get = fake_get
    m.connect()
    return m, calls


def test_jellyfin_manager():
    m, calls = _manager()
    assert m.main_user == "admin"  # first administrator

    # OnDeck: recent resume movie + next-up episode + 2 prefetched; old movie filtered out
    items = m.get_on_deck_media([], 30, 2, users_toggle=False, skip_ondeck=[])
    assert items[0].username == "admin (Jellyfin)"
    got = [(i.file_path, i.is_current_ondeck) for i in items]
    assert got == [("/movies/M.mkv", True), ("/tv/Show/S1E2.mkv", True),
                   ("/tv/Show/S1E3.mkv", False), ("/tv/Show/S2E1.mkv", False)]
    assert items[1].episode_info == {"show": "Show", "season": 1, "episode": 2}

    # users_toggle fetches every user except skipped ones (main user never skipped)
    m.get_on_deck_media([], 30, 0, users_toggle=True, skip_ondeck=["kid (Jellyfin)", "admin (Jellyfin)"])
    resume_users = [p["userId"] for path, p in calls if path == "/UserItems/Resume"]
    assert resume_users == ["u1", "u1"]

    # Watchlist = favorites: first N unplayed episodes of a favorite series + movies
    wl = list(m.get_watchlist_media([], 2, users_toggle=False, skip_watchlist=[]))
    assert all(w[1] == "admin (Jellyfin)" for w in wl)
    assert [(w[0], w[5]) for w in wl] == [("/tv/Show/S1E2.mkv", "episode"),
                                          ("/tv/Show/S1E3.mkv", "episode"),
                                          ("/movies/M.mkv", "movie")]
    assert list(m.get_watchlist_media([], 2, False, [], rss_url="http://rss")) == []

    assert m.get_active_sessions() == ["/movies/M.mkv"]
    assert m.is_ondeck_data_complete() and m.is_watchlist_data_complete()


def test_per_user_days_use_qualified_names():
    m, calls = _manager()
    m.get_on_deck_media([], 30, 0, users_toggle=False, skip_ondeck=[],
                        per_user_days={"admin (Jellyfin)": 1})
    cutoff = [p["nextUpDateCutoff"] for path, p in calls if path == "/Shows/NextUp"][-1]
    assert cutoff > (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()


def test_list_users():
    m, _ = _manager()
    assert m.list_users() == [
        {"name": "admin", "title": "admin (Jellyfin)", "id": "u1", "is_admin": True},
        {"name": "kid", "title": "kid (Jellyfin)", "id": "u2", "is_admin": False},
    ]


def test_jellyfin_libraries():
    m = JellyfinManager("http://jf", "key")
    m._get = lambda path, **params: [
        {"Name": "Movies", "ItemId": "abc", "CollectionType": "movies", "Locations": ["/media/movies"]},
        {"Name": "Home", "ItemId": "def", "CollectionType": "homevideos"},
    ]
    assert m.get_libraries() == [
        {"id": "abc", "title": "Movies", "type": "movie", "type_label": "Movies", "locations": ["/media/movies"]},
        {"id": "def", "title": "Home", "type": "mixed", "type_label": "Mixed", "locations": []},
    ]
