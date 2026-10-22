"""Jellyfin support for the caching run.

Implements the subset of PlexManager that PlexCacheApp calls, so a run can be
driven by Jellyfin instead of Plex:
  - OnDeck   -> Jellyfin "Continue Watching" (resume) + "Next Up"
  - Watchlist -> Jellyfin favorites (Jellyfin has no watchlist)
  - Sessions -> /Sessions now-playing items

Web UI features built on plexapi (pins, recently added, OAuth, user discovery)
remain Plex-only.
"""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, Generator, List, Optional, Tuple

import requests

from core.media_servers import jellyfin_username
from core.plex_api import OnDeckItem, PlexManager

FIELDS = "Path,MediaSources"


def _paths(item: dict) -> List[str]:
    """File paths for every version of an item (falls back to item Path)."""
    paths = [s["Path"] for s in item.get("MediaSources") or []
             if s.get("Path") and s.get("Protocol", "File") == "File"]
    return paths or ([item["Path"]] if item.get("Path") else [])


def _episode_info(item: dict) -> Optional[Dict]:
    season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
    if season is None or episode is None:
        return None
    return {"show": item.get("SeriesName"), "season": season, "episode": episode}


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    # Jellyfin sends 7 fractional digits; seconds precision is plenty here.
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class JellyfinManager:
    """Drop-in replacement for PlexManager during a caching run."""

    def __init__(self, url: str, api_key: str, main_user: str = "", timeout: int = 30):
        self.url = url.rstrip("/")
        self.main_user = main_user
        self.timeout = timeout
        self.plex = None  # Plex-only features (pins, session lookup) check this
        self._http = requests.Session()
        self._http.headers["Authorization"] = (
            'MediaBrowser Client="MediaCache", Device="MediaCache", '
            f'DeviceId="mediacache", Version="1", Token="{api_key}"'
        )
        self._users: Dict[str, str] = {}  # name -> id
        self._ondeck_complete = True
        self._watchlist_complete = True

    def _get(self, path: str, **params):
        resp = self._http.get(f"{self.url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def connect(self) -> None:
        try:
            version = self._get("/System/Info").get("Version")
            users = self._get("/Users")
        except requests.RequestException as e:
            logging.error(f"Cannot connect to Jellyfin server at {self.url}: {e}")
            raise ConnectionError(f"Cannot connect to Jellyfin server: {e}") from None
        logging.debug(f"Jellyfin server version: {version}")
        self._users = {u["Name"]: u["Id"] for u in users}
        if not self.main_user:
            self.main_user = next(
                (u["Name"] for u in users if (u.get("Policy") or {}).get("IsAdministrator")),
                users[0]["Name"] if users else "",
            )
        if self.main_user not in self._users:
            raise ConnectionError(f"Jellyfin user '{self.main_user}' not found")

    def get_libraries(self) -> List[dict]:
        """Libraries in the shape SettingsService.get_plex_libraries returns (for the setup wizard)."""
        types = {"movies": ("movie", "Movies"), "tvshows": ("show", "TV Shows")}
        libraries = []
        for folder in self._get("/Library/VirtualFolders"):
            kind, label = types.get(folder.get("CollectionType"), ("mixed", "Mixed"))
            libraries.append({"id": folder["ItemId"], "title": folder["Name"], "type": kind,
                              "type_label": label, "locations": folder.get("Locations") or []})
        return libraries

    # PlexManager API used by PlexCacheApp; Jellyfin needs no per-user tokens.
    def load_user_tokens(self, **kwargs) -> None:
        pass

    def get_newly_discovered_users(self) -> List[dict]:
        return []

    def is_ondeck_data_complete(self) -> bool:
        return self._ondeck_complete

    def is_watchlist_data_complete(self) -> bool:
        return self._watchlist_complete

    def _users_for(self, users_toggle: bool, skip: List[str]) -> List[str]:
        names = list(self._users) if users_toggle else [self.main_user]
        return [n for n in names if n == self.main_user or jellyfin_username(n) not in skip]

    def list_users(self) -> List[dict]:
        """All Jellyfin users; only the main user is flagged is_admin."""
        return [{"name": u["Name"], "title": jellyfin_username(u["Name"]), "id": u["Id"],
                 "is_admin": u["Name"] == self.main_user} for u in self._get("/Users")]

    def get_active_sessions(self) -> List[str]:
        """Returns file paths (not session objects) of everything now playing."""
        ids = [s["NowPlayingItem"]["Id"] for s in self._get("/Sessions", activeWithinSeconds=960)
               if s.get("NowPlayingItem")]
        if not ids:
            return []
        items = self._get("/Items", ids=",".join(ids), fields=FIELDS)["Items"]
        return [p for item in items for p in _paths(item)[:1]]

    # -------------------- OnDeck --------------------

    def get_on_deck_media(self, valid_sections: List[int], days_to_monitor: int,
                          number_episodes: int, users_toggle: bool, skip_ondeck: List[str],
                          per_user_days: Optional[Dict[str, int]] = None,
                          prefetch_minimum_minutes: int = 0) -> List[OnDeckItem]:
        items: List[OnDeckItem] = []
        for name in self._users_for(users_toggle, skip_ondeck):
            days = (per_user_days or {}).get(jellyfin_username(name), days_to_monitor)
            try:
                found = self._fetch_user_on_deck(name, days, number_episodes, prefetch_minimum_minutes)
            except requests.RequestException as e:
                logging.error(f"[USER:{name}] Failed to fetch Jellyfin OnDeck: {e}")
                if name == self.main_user:
                    self._ondeck_complete = False
                continue
            logging.debug(f"[USER:{name}] Found {len(found)} OnDeck items")
            items.extend(found)
        return items

    def _fetch_user_on_deck(self, name: str, days: int, number_episodes: int,
                            prefetch_minimum_minutes: int) -> List[OnDeckItem]:
        uid = self._users[name]
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        resume = [i for i in self._get("/UserItems/Resume", userId=uid, fields=FIELDS,
                                       mediaTypes="Video")["Items"]
                  if (_parse_date((i.get("UserData") or {}).get("LastPlayedDate")) or cutoff) >= cutoff]
        next_up = self._get("/Shows/NextUp", userId=uid, fields=FIELDS,
                            nextUpDateCutoff=cutoff.isoformat())["Items"]

        result: List[OnDeckItem] = []
        seen = set()
        for item in resume + next_up:
            if item["Id"] in seen:
                continue
            seen.add(item["Id"])
            info = _episode_info(item) if item.get("Type") == "Episode" else None
            result += [OnDeckItem(p, jellyfin_username(name), info, True, item["Id"]) for p in _paths(item)]
            if item.get("Type") == "Episode" and info and item.get("SeriesId"):
                for ep in self._next_episodes(uid, item, number_episodes, prefetch_minimum_minutes):
                    result += [OnDeckItem(p, jellyfin_username(name), _episode_info(ep), False, ep["Id"]) for p in _paths(ep)]
        return result

    def _next_episodes(self, uid: str, item: dict, number_episodes: int,
                       prefetch_minimum_minutes: int) -> List[dict]:
        episodes = self._get(f"/Shows/{item['SeriesId']}/Episodes", userId=uid,
                             fields=FIELDS, isMissing="false")["Items"]
        # Adapt to the attribute names PlexManager._get_next_episodes reads,
        # so count/runtime prefetch rules stay identical across servers.
        adapted = [SimpleNamespace(
            parentIndex=e.get("ParentIndexNumber"), index=e.get("IndexNumber"),
            duration=(e.get("RunTimeTicks") or 0) // 10_000,  # 100ns ticks -> ms
            title=e.get("Name"), grandparentTitle=e.get("SeriesName"), raw=e,
        ) for e in episodes]
        chosen = PlexManager._get_next_episodes(
            self, adapted, item["ParentIndexNumber"], item["IndexNumber"],
            number_episodes, prefetch_minimum_minutes=prefetch_minimum_minutes,
        )
        return [c.raw for c in chosen]

    # -------------------- Watchlist (favorites) --------------------

    def get_watchlist_media(self, valid_sections: List[int], watchlist_episodes: int,
                            users_toggle: bool, skip_watchlist: List[str], rss_url: Optional[str] = None,
                            home_users: Optional[List[str]] = None
                            ) -> Generator[Tuple[str, str, Optional[datetime], Optional[Dict], Optional[str], str], None, None]:
        if rss_url:
            return  # Plex RSS watchlists don't apply to Jellyfin
        for name in self._users_for(users_toggle, skip_watchlist):
            try:
                found = list(self._fetch_user_favorites(name, watchlist_episodes))
            except requests.RequestException as e:
                logging.error(f"[USER:{name}] Failed to fetch Jellyfin favorites: {e}")
                self._watchlist_complete = False
                continue
            yield from found

    def _fetch_user_favorites(self, name: str, watchlist_episodes: int):
        uid = self._users[name]
        favorites = self._get("/Items", userId=uid, isFavorite="true", recursive="true",
                              includeItemTypes="Movie,Series,Episode", fields=FIELDS)["Items"]
        for item in favorites:
            if item.get("Type") == "Movie":
                for p in _paths(item):
                    yield (p, jellyfin_username(name), None, None, item["Id"], "movie")
                continue
            if item.get("Type") == "Series":
                episodes = self._get(f"/Shows/{item['Id']}/Episodes", userId=uid,
                                     fields=FIELDS, isMissing="false")["Items"]
                episodes = [e for e in episodes
                            if not (e.get("UserData") or {}).get("Played")][:watchlist_episodes]
            else:
                episodes = [item]
            for ep in episodes:
                for p in _paths(ep):
                    yield (p, jellyfin_username(name), None, _episode_info(ep), ep["Id"], "episode")
