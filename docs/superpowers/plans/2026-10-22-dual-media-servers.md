# Plex + Jellyfin at the Same Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let MediaCache use Plex and Jellyfin together, caching what anyone on either server needs and treating the same file as one item.

**Architecture:**
- `media_server` gains a third value, `"both"`.
- A `CombinedMediaManager` wraps one `PlexManager` and/or one `JellyfinManager` behind the interface `core/app.py` already calls (`self.plex_manager`). It merges results and reports which servers are missing.
- Jellyfin usernames are qualified as `"<name> (Jellyfin)"` at the source, so every existing username-keyed structure keeps the two servers' users apart with no other changes.
- While a server is missing, the run skips move-back and eviction, and carries that server's users over in the OnDeck tracker.

**Tech Stack:** Python 3.11, FastAPI + Jinja2/HTMX, plexapi, requests, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-dual-media-servers-design.md`

## Global Constraints

- `media_server` values: `"plex"`, `"jellyfin"`, `"both"`. A missing key means `"plex"`. Existing `"plex"` and `"jellyfin"` files must keep working unchanged (spec goal 8).
- Plex usernames are never altered anywhere (existing trackers, skip lists and per-user settings must keep matching).
- Jellyfin usernames, as stored and compared everywhere: `f"{name} (Jellyfin)"`, e.g. `"Ben (Jellyfin)"`.
- Files are matched on real path after path mappings, and nothing smarter.
- Only Plex rating keys reach tracker `rating_key` fields (upgrade detection and pins assume Plex ints).
- No new dependencies. Tests are pytest, in the repo's existing style (`tests/test_*.py`, plain asserts, `unittest.mock`).
- Branch: `dual-servers` (based on BBergle/MediaCache `main`). Locally, 14 tests already fail under Python 3.15 (`test_eviction_safety`, `test_recently_added_service`); CI on 3.11 is green. "Suite passes" means no failures beyond those 14.
- Commits end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01GFZpqAkhovSdkC8SxPz9an`, using `git -c user.email=bennybergle@gmail.com commit`.

## Review Focus

1. **Turning Jellyfin off after using both:** the Users page should list only the enabled servers' users. Jellyfin users stay saved in `users[]`, so turning Jellyfin back on restores their settings. Test: Task 8 `test_users_list_hides_disabled_server_users`.
2. **Jellyfin titles in form field names:** a title like `"Ben (Jellyfin)"`, with a space and brackets, must round-trip through the Users form (`include_ondeck_Ben (Jellyfin)`). Test: Task 8 `test_save_users_round_trips_jellyfin_titles`.
3. **Jellyfin fetch fails mid-run:** if Jellyfin connects but its OnDeck fetch for the main user fails, it counts as missing, the same as being unreachable. Test: Task 3 `test_incomplete_server_counts_as_missing`.
4. **Both servers switched off in Settings:** the save must be refused with a message and nothing written. Test: Task 7 `test_save_rejects_no_servers`.
5. **Jellyfin users reaching Plex token loading:** Plex token loading and the home-user list must only ever see Plex users, or PlexManager tries "Home User fallback" for `"Ben (Jellyfin)"`. Test: Task 6 `test_connect_passes_only_plex_users_to_plex`.

---

### Task 1: Media-server helpers and the `"both"` setting

**Files:**
- Create: `core/media_servers.py`
- Modify: `core/config.py` (PlexConfig comment ~105; `_validate_*` block ~900-909)
- Test: `tests/test_media_servers.py`, `tests/test_config.py` (add to existing file; create it if absent)

**Interfaces:**
- Produces (in `core/media_servers.py`):
  - `JELLYFIN_SUFFIX = " (Jellyfin)"`
  - `enabled_media_servers(media_server: str) -> list[str]`: `"plex"`→`["plex"]`, `"jellyfin"`→`["jellyfin"]`, `"both"`→`["plex", "jellyfin"]`, anything else (including `""`/`None`)→`["plex"]`.
  - `media_server_value(plex: bool, jellyfin: bool) -> str | None`: the inverse; `None` when both are false.
  - `jellyfin_username(name: str) -> str`
  - `server_of_user(username: str) -> str`: `"jellyfin"` if it ends with `JELLYFIN_SUFFIX`, else `"plex"`.
  - `server_label(server: str) -> str`: `"Plex"` / `"Jellyfin"`.
  - `plex_rating_key(username: str, rating_key) -> str | None`: `rating_key` for Plex users, `None` for Jellyfin users.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_media_servers.py
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
```

Add to `tests/test_config.py` a validation test using the same ConfigManager setup that file already uses. With `media_server: "both"`, validation must report `jellyfin_url`, `jellyfin_api_key`, `PLEX_URL` and `PLEX_TOKEN` when they're empty, and accept them when set. `media_server: "emby"` must give the error `'media_server' must be 'plex', 'jellyfin' or 'both'`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_media_servers.py tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError: core.media_servers`, and the config error message mismatch).

- [ ] **Step 3: Implement `core/media_servers.py` and update config validation**

In `core/config.py` validation, replace the single-server check:
- loop over `enabled_media_servers(media_server)`, requiring `['PLEX_URL', 'PLEX_TOKEN']` for plex and `['jellyfin_url', 'jellyfin_api_key']` for jellyfin;
- accept `"both"` as a value, with the error text exactly as above.

Update the `media_server` field comment to `# "plex", "jellyfin" or "both"`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_media_servers.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit** (`Add media-server helpers and the "both" setting`)

---

### Task 2: Jellyfin usernames carry their server; Jellyfin user listing

**Files:**
- Modify: `core/jellyfin_api.py`
- Test: `tests/test_jellyfin_api.py`

**Interfaces:**
- Consumes: `jellyfin_username`, `JELLYFIN_SUFFIX` (Task 1).
- Produces:
  - `OnDeckItem.username` and watchlist tuple index 1 are `jellyfin_username(name)` for every Jellyfin item.
  - `JellyfinManager.list_users() -> list[dict]`: each dict is `{"name": str, "title": jellyfin_username(name), "id": str, "is_admin": bool}`, from `/Users`. Requires `connect()` to have run (it uses `self.main_user`); `is_admin` is `True` only for `self.main_user`.
  - `get_on_deck_media` / `get_watchlist_media` take `skip_ondeck` / `skip_watchlist` and `per_user_days` keyed by **qualified** names (the same lists Plex receives).
  - The main user is never skipped, compared by raw name as today.

- [ ] **Step 1: Update the existing test expectations and add new ones**

In `test_jellyfin_manager`:
- assert `items[0].username == "admin (Jellyfin)"`;
- the users_toggle skip call becomes `skip_ondeck=["kid (Jellyfin)", "admin (Jellyfin)"]`, still expecting `resume_users == ["u1", "u1"]` (admin is never skipped);
- assert every watchlist tuple's `w[1] == "admin (Jellyfin)"`.

Add:

```python
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
```

(Add `from datetime import datetime, timedelta, timezone` to the test imports.)

- [ ] **Step 2: Run `python -m pytest tests/test_jellyfin_api.py -q` and confirm it fails**
- [ ] **Step 3: Implement.** Qualify names where `OnDeckItem`s and watchlist tuples are built. `_users_for` returns raw names, filtering on `jellyfin_username(n) not in skip`. `per_user_days` lookups use the qualified name. Log lines can keep using the qualified name.
- [ ] **Step 4: Run `python -m pytest tests/test_jellyfin_api.py tests/test_jellyfin_webui.py -q` and confirm it passes**
- [ ] **Step 5: Commit** (`Jellyfin usernames carry their server`)

---

### Task 3: `CombinedMediaManager`

**Files:**
- Modify: `core/media_servers.py`
- Test: `tests/test_media_servers.py`

**Interfaces:**
- Consumes: any objects with the PlexManager/JellyfinManager run interface.
- Produces: `class CombinedMediaManager` with:
  - `__init__(self, managers: dict[str, object])`: keys `"plex"` / `"jellyfin"`, in the order given.
  - `connect() -> None`: connects each one. `ConnectionError` from one is logged as `[MEDIA SERVER] <Label> unavailable: <err>` and recorded. Re-raises `ConnectionError("No media server could be reached")` only if all fail.
  - `plex` (property): the Plex manager's `.plex` if Plex is connected, else `None`.
  - `load_user_tokens(**kwargs)` and `get_newly_discovered_users() -> list`: forwarded to Plex only, if connected (`[]` otherwise).
  - `get_active_sessions() -> list`: the concatenation over connected managers.
  - `get_on_deck_media(*args, **kwargs) -> list`: the concatenation over connected managers, Plex first.
  - `get_watchlist_media(*args, rss_url=None, home_users=None, **kwargs)`: a generator yielding Plex's items (with `rss_url`, `home_users`) then Jellyfin's (with `rss_url=None`, no `home_users`).
  - `missing_servers() -> set[str]`: servers that failed to connect, plus connected ones whose `is_ondeck_data_complete()` is `False`.
  - `is_ondeck_data_complete() -> bool`: `not missing_servers()`.
  - `is_watchlist_data_complete() -> bool`: no connect failures, and every connected manager's `is_watchlist_data_complete()`.

- [ ] **Step 1: Write failing tests** using a small `FakeManager` class in the test file. It has the run methods, a `fail_connect` flag, and canned `ondeck` / `watchlist` / `sessions` lists and completeness flags.

```python
def test_merges_both_servers_in_order(): ...
    # plex ondeck [A], jellyfin ondeck [B] -> get_on_deck_media(...) == [A, B]
    # watchlist: plex receives rss_url="http://rss", jellyfin receives rss_url=None
def test_one_server_down_is_missing_but_run_continues():
    # jellyfin fail_connect -> connect() doesn't raise; missing_servers() == {"jellyfin"}
    # get_on_deck_media returns plex items only; is_ondeck_data_complete() is False
    # is_watchlist_data_complete() is False; plex property returns the plex fake's .plex
def test_all_servers_down_raises(): ...  # pytest.raises(ConnectionError, match="No media server")
def test_incomplete_server_counts_as_missing():
    # both connect, jellyfin.ondeck_complete = False -> missing_servers() == {"jellyfin"}
def test_plex_only_features_need_plex():
    # managers={"jellyfin": fake} -> .plex is None, get_newly_discovered_users() == []
```

- [ ] **Step 2: Run `python -m pytest tests/test_media_servers.py -q` and confirm it fails**
- [ ] **Step 3: Implement `CombinedMediaManager` in `core/media_servers.py`**
- [ ] **Step 4: Run it again and confirm it passes**
- [ ] **Step 5: Commit** (`Add CombinedMediaManager to run Plex and Jellyfin together`)

---

### Task 4: Trackers: keep a missing server's users; key the watchlist by real path

**Files:**
- Modify: `core/file_operations.py` (`OnDeckTracker.prepare_for_run` ~1694, `WatchlistTracker`)
- Test: `tests/test_tracker_servers.py`

**Interfaces:**
- Produces:
  - `OnDeckTracker.prepare_for_run(self, keep_user: Callable[[str], bool] | None = None) -> None`:
    - Users for which `keep_user(u)` is true stay in `users`, `ondeck_users` and `user_first_seen`.
    - Any entry still holding a kept user is added to `_seen_this_run`, so `cleanup_unseen()` keeps it.
    - With `keep_user=None`, behaviour is exactly as today.
  - `WatchlistTracker.rekey_paths(self, convert: Callable[[str], str]) -> int`:
    - Re-keys every entry to `convert(key)` and returns how many keys changed.
    - On collision: union `users`, keep the most recent `watchlisted_at` and `last_seen`, and keep the target's other fields.
    - Saves only if anything changed.

- [ ] **Step 1: Write failing tests**

```python
def test_missing_server_users_survive_a_run(tmp_path):
    t = OnDeckTracker(str(tmp_path / "ondeck.json"))
    t.prepare_for_run(); t.update_entry("/m/a.mkv", "Ben", is_current_ondeck=True)
    t.update_entry("/m/a.mkv", "Ben (Jellyfin)", is_current_ondeck=True)
    t.update_entry("/m/b.mkv", "Kim (Jellyfin)"); t.cleanup_unseen()
    first = t.get_entry("/m/a.mkv")["user_first_seen"]["Ben (Jellyfin)"]

    t.prepare_for_run(keep_user=lambda u: u.endswith(" (Jellyfin)"))  # Jellyfin down
    t.update_entry("/m/a.mkv", "Ben"); t.cleanup_unseen()
    a, b = t.get_entry("/m/a.mkv"), t.get_entry("/m/b.mkv")
    assert set(a["users"]) == {"Ben", "Ben (Jellyfin)"} and "Ben (Jellyfin)" in a["ondeck_users"]
    assert a["user_first_seen"]["Ben (Jellyfin)"] == first
    assert b is not None and b["users"] == ["Kim (Jellyfin)"]

def test_without_keep_user_unseen_entries_still_go(tmp_path): ...
    # prepare_for_run() with no args: an entry not re-seen is removed, as today

def test_same_episode_from_two_servers_is_one_entry(tmp_path):
    # MultiPathModifier with mappings Plex /data/tv -> /mnt/user/tv and Jellyfin /media/tv -> /mnt/user/tv;
    # convert "/data/tv/S/e1.mkv" and "/media/tv/S/e1.mkv", update_entry both (users "Ben", "Ben (Jellyfin)")
    # -> exactly one entry "/mnt/user/tv/S/e1.mkv" with both users

def test_watchlist_rekey_merges_raw_and_real_paths(tmp_path):
    # entries "/data/tv/x.mkv" (users ["Ben"], watchlisted_at 2026-01-01) and
    # "/mnt/user/tv/x.mkv" (users ["Kim (Jellyfin)"], watchlisted_at 2026-02-01);
    # rekey_paths(lambda p: p.replace("/data/tv", "/mnt/user/tv")) == 1
    # -> one key "/mnt/user/tv/x.mkv", users {"Ben", "Kim (Jellyfin)"}, watchlisted_at 2026-02-01;
    # a second rekey_paths call returns 0
```

- [ ] **Step 2: Run `python -m pytest tests/test_tracker_servers.py -q` and confirm it fails**
- [ ] **Step 3: Implement both methods.** Keep the existing lock usage. `rekey_paths` must keep the tracker's in-memory index structures consistent, the way `remove_entry` / `update_entry` do.
- [ ] **Step 4: Run `python -m pytest tests/test_tracker_servers.py tests/test_file_operations*.py -q` and confirm it passes**
- [ ] **Step 5: Commit** (`Trackers: carry a missing server's users; re-key watchlist by real path`)

---

### Task 5: Path mappings shared by two servers

**Files:**
- Modify: `core/file_operations.py` (`MultiPathModifier.__init__` ~2776, plus two new module functions next to it)
- Modify: `web/routers/settings.py` (Libraries tab GET, ~563-600) and `web/templates/settings/libraries.html` (alert at the top of the content)
- Test: `tests/test_mapping_conflicts.py`

**Interfaces:**
- Produces:
  - `merge_shared_real_paths(mappings: list[PathMapping]) -> list[PathMapping]`:
    - Groups by `real_path.rstrip("/")`.
    - In a group, if any mapping is enabled, all are enabled. If any is cacheable, all are cacheable, and any missing `cache_path` / `host_cache_path` is filled from the first cacheable mapping in the group.
    - Returns new objects (`dataclasses.replace`), never mutating its input.
  - `MultiPathModifier.__init__` applies it before sorting.
  - `find_mapping_conflicts(mappings: list[dict]) -> list[str]` takes raw settings dicts and returns one warning string per problem:
    - same real folder with disagreeing `cacheable` or `enabled`: `"'<name1>' and '<name2>' point at <real_path> but disagree on caching. It will be cached."` for cacheable, and `"... disagree on enabled. It will be used."` for enabled;
    - same `plex_path` with different `real_path`: `"'<name1>' and '<name2>' use the same server path <plex_path> for different folders. Only one can apply."`
  - The Libraries tab passes `mapping_conflicts=find_mapping_conflicts(raw_mappings)` and renders each in an `alert alert-warning` list when non-empty.

- [ ] **Step 1: Write failing tests**

```python
def test_cacheable_if_any_mapping_to_folder_is():
    plex = PathMapping(name="Plex TV", plex_path="/data/tv/", real_path="/mnt/user/tv/",
                       cache_path="/mnt/cache/tv/", cacheable=True)
    jf = PathMapping(name="Jellyfin TV", plex_path="/media/tv/", real_path="/mnt/user/tv/",
                     cache_path=None, cacheable=False)
    merged = {m.name: m for m in merge_shared_real_paths([plex, jf])}
    assert merged["Jellyfin TV"].cacheable and merged["Jellyfin TV"].cache_path == "/mnt/cache/tv/"
    assert jf.cacheable is False  # input untouched
    mod = MultiPathModifier([plex, jf])
    assert mod.convert_plex_to_real("/media/tv/S/e1.mkv")[0] == "/mnt/user/tv/S/e1.mkv"

def test_conflict_messages(): ...
    # dicts mirroring the two mappings above -> one message containing "disagree on caching"
    # two dicts with plex_path "/data/" and different real_path -> one "same server path" message
    # identical, agreeing mappings -> []
```

- [ ] **Step 2: Run `python -m pytest tests/test_mapping_conflicts.py -q` and confirm it fails**
- [ ] **Step 3: Implement both functions, the `__init__` call, and the Libraries tab alert**
- [ ] **Step 4: Run `python -m pytest tests/test_mapping_conflicts.py tests/test_library_paths_delete.py -q` and confirm it passes**
- [ ] **Step 5: Commit** (`Path mappings: merge flags for a shared folder, warn on conflicts`)

---

### Task 6: Run integration in `core/app.py`

**Files:**
- Modify: `core/app.py`:
  - `_init_plex_manager` ~561
  - `_connect_to_plex` ~1006
  - `_process_media` ~1150-1236 and the move-back block ~1352-1368
  - `_process_watchlist` ~1525-1600
  - `_move_files` eviction call ~2396
- Test: `tests/test_app_dual_servers.py` (build apps with `PlexCacheApp.__new__(PlexCacheApp)` as `tests/test_empty_folder_backfill.py` does, setting only the attributes each method touches, with MagicMocks)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `_init_plex_manager` sets `self.plex_manager = CombinedMediaManager({...})` built from `enabled_media_servers(plex_config.media_server)`, with each manager constructed exactly as today.
  - `PlexCacheApp._can_remove_from_cache(self, action: str) -> bool`:
    - returns `False` when `not self.plex_manager.is_ondeck_data_complete()`, or watchlist caching is on and `not is_watchlist_data_complete()`;
    - in that case logs `Skipping <action> - media server data incomplete (<labels of missing servers or "watchlist">)` plus the existing `Files will remain on cache until next successful run` line;
    - otherwise returns `True`.
  - The move-back block uses `self._can_remove_from_cache("array restore")`.
  - `_move_files` only calls `_run_smart_eviction()` when `self._can_remove_from_cache("eviction")`.
  - `_connect_to_plex` passes `settings_users=[u for u in users if server_of_user(u.get("title", "")) == "plex"]`.
  - `_process_watchlist`'s `home_users` filters the same way.
  - `_process_media`:
    - moves `self.ondeck_tracker.prepare_for_run(...)` to just after `get_on_deck_media(...)` returns (nothing between the old and new position touches the tracker), passing `keep_user=lambda u: server_of_user(u) in missing` where `missing = self.plex_manager.missing_servers()`;
    - `update_entry(..., rating_key=plex_rating_key(item.username, item.rating_key))`.
  - `_process_watchlist`:
    - calls `self.watchlist_tracker.rekey_paths(lambda p: self.file_path_modifier.convert_plex_to_real(p)[0])` once before the fetch loop;
    - converts each `file_path` to its real path before `update_entry` and `is_expired`;
    - passes `rating_key=plex_rating_key(username, rating_key)` (in both the API and RSS loops).

- [ ] **Step 1: Write failing tests**

```python
def test_init_builds_both_managers(): ...
    # config_manager.plex.media_server = "both" -> plex_manager is CombinedMediaManager
    # with keys ["plex", "jellyfin"] (patch PlexManager and core.jellyfin_api.JellyfinManager)
def test_plex_only_install_unchanged(): ...
    # media_server = "plex" -> CombinedMediaManager with keys ["plex"] only
def test_connect_passes_only_plex_users_to_plex():
    # plex.users = [{"title": "Ben"}, {"title": "Ben (Jellyfin)", "server": "jellyfin"}]
    # -> plex_manager.load_user_tokens called with settings_users == [{"title": "Ben"}]
def test_no_eviction_or_move_back_while_a_server_is_missing():
    # plex_manager.is_ondeck_data_complete.return_value = False; missing_servers() == {"jellyfin"}
    # -> _can_remove_from_cache("eviction") is False and the log mentions "Jellyfin"
def test_removal_allowed_when_all_data_complete(): ...
```

- [ ] **Step 2: Run `python -m pytest tests/test_app_dual_servers.py -q` and confirm it fails**
- [ ] **Step 3: Implement the changes listed in Interfaces**
- [ ] **Step 4: Run `python -m pytest tests/test_app_dual_servers.py tests/test_jellyfin_api.py -q`, then the whole suite, and confirm they pass**
- [ ] **Step 5: Commit** (`Run Plex and Jellyfin together; hold removals while one is missing`)

---

### Task 7: Settings, dashboard and status text for two servers

**Files:**
- Modify:
  - `web/services/settings_service.py`: `get_plex_settings` / `save_plex_settings` (~176-215) and `check_plex_connection` (~915)
  - `web/routers/settings.py`: plex tab save/test (~130-230)
  - `web/templates/settings/plex.html`
  - `web/services/web_cache.py` (~281-310)
  - `web/templates/partials/dashboard_stats.html` (~142-152)
  - `web/config.py`: `_media_server_name`
  - `web/services/recently_added_service.py` (~149)
  - `web/routers/setup.py`: `is_setup_complete` (~53)
  - `README.md` (Jellyfin section)
- Test: `tests/test_jellyfin_webui.py` (extend)

**Interfaces:**
- Consumes: `enabled_media_servers`, `media_server_value`, `server_label` (Task 1); `JellyfinManager.connect` / `main_user`.
- Produces:
  - Settings form fields `plex_enabled` / `jellyfin_enabled` (checkbox `"on"`). Save computes `media_server_value(...)`. If that's `None`, it returns an error alert `Turn on at least one media server.` and saves nothing.
  - `SettingsService.check_media_server_connections() -> dict[str, bool]`, keyed by enabled server.
  - `check_plex_connection()` keeps its name and becomes `all(check_media_server_connections().values())`.
  - `web_cache` stats gain `"server_status": [{"name": "Plex", "connected": bool}, ...]` in enabled order. `"plex_connected"` stays as the all-connected bool.
  - `dashboard_stats.html` renders one badge per `server_status` entry: `"<name> Connected"` / `"<name> Not Connected"`, with the existing classes and icons.
  - `media_server_name()` returns the labels of enabled servers joined with `" and "` (e.g. `"Plex and Jellyfin"`).
  - POST `/settings/plex/test` tests each enabled server. Plex keeps its existing hint messages, and Jellyfin keeps `Connected to Jellyfin as <user>`. The alert is success only if all pass, with messages joined by `" · "`, each prefixed `"Plex: "` / `"Jellyfin: "` when both are on.
  - Recently Added returns `"Recently Added needs Plex, so it isn't available with Jellyfin."` only when `"plex" not in enabled_media_servers(...)`.
  - `is_setup_complete()` is true when every enabled server has its required fields.
  - `plex.html`:
    - two `toggle-switch` checkboxes, "Plex" and "Jellyfin (experimental)", replace the `<select>`;
    - each one shows or hides its own field group (`data-server="plex"` / `"jellyfin"`);
    - the existing JS's `required` handling is applied per checkbox;
    - the hint text keeps the Plex-only feature list.

- [ ] **Step 1: Extend tests** in `tests/test_jellyfin_webui.py`:

```python
def test_both_servers_save_and_status(client):
    # PUT /settings/plex with plex_enabled=on, jellyfin_enabled=on + both servers' fields
    # -> raw["media_server"] == "both"; GET /settings/plex shows both checkboxes checked
    # patch Plex reachability (requests.get in check_media_server_connections) to fail:
    # check_media_server_connections() == {"plex": False, "jellyfin": True}
def test_save_rejects_no_servers(client):
    # PUT without either checkbox -> "Turn on at least one media server." and media_server unchanged
def test_test_connection_reports_each_server(client): ...  # both on, Plex fails -> alert has "Plex: " and "Jellyfin: Connected"
```

Also update the existing `test_settings_tab_saves_and_tests_jellyfin` form to send `jellyfin_enabled=on` instead of `media_server=jellyfin`.

- [ ] **Step 2: Run `python -m pytest tests/test_jellyfin_webui.py -q` and confirm it fails**
- [ ] **Step 3: Implement the changes listed in Interfaces.** In the README Jellyfin section, document `"media_server": "both"` and the per-server path mappings.
- [ ] **Step 4: Run `python -m pytest tests/test_jellyfin_webui.py tests/test_recently_added_route.py -q` and the full suite, and confirm they pass**
- [ ] **Step 5: Commit** (`Settings and dashboard handle Plex and Jellyfin together`)

---

### Task 8: Users page lists both servers' users

**Files:**
- Modify:
  - `web/services/settings_service.py`: `sync_users_from_plex` ~1203; new `sync_users_from_jellyfin`; new `sync_users`
  - `web/routers/settings.py`: `/users`, `/users/list`, `/users/sync` (~243-327)
  - `web/templates/settings/partials/users_table.html` (token column ~70-90)
- Test: `tests/test_users_two_servers.py`

**Interfaces:**
- Consumes: `JellyfinManager.list_users()` (Task 2), `enabled_media_servers`, `server_of_user` (Task 1).
- Produces:
  - Jellyfin `users[]` entries look like `{"title": "<name> (Jellyfin)", "server": "jellyfin", "id": str, "is_local": True, "is_admin": bool, "skip_ondeck": bool, "skip_watchlist": bool}`. Existing per-user `days_to_monitor` / `watchlist_retention_days` are preserved across a sync, the same way the Plex sync does it.
  - `SettingsService.sync_users_from_jellyfin() -> dict`: same result shape as `sync_users_from_plex`. It replaces only `server == "jellyfin"` entries and leaves every other entry untouched.
  - `sync_users_from_plex` keeps every `server == "jellyfin"` entry. Both syncs rebuild `skip_ondeck` / `skip_watchlist` from the whole merged `users[]`.
  - `SettingsService.sync_users() -> dict` runs each enabled server's sync and returns:
    - `success`: any succeeded;
    - `users`: the final `users[]`;
    - `added_count` / `removed_count`: summed;
    - `error`: the failures joined with `"; "`, or `None`.
  - `/users/sync` calls `sync_users()`.
  - `/users/list` passes only users whose server is enabled.
  - `/users` sets `has_plex_config` to true if any enabled server has its required fields (the template variable keeps its name).
  - `users_table.html` treats Jellyfin users like the admin in the token column: no token panel and no token icon.

- [ ] **Step 1: Write failing tests** (with the same router/service fixture pattern as `tests/test_jellyfin_webui.py`, and JellyfinManager `_get` patched):

```python
def test_jellyfin_sync_adds_labelled_users_and_keeps_plex(): ...
    # raw users = [{"title": "Ben", "is_admin": True, ...}], media_server "both"
    # sync_users_from_jellyfin() -> titles {"Ben", "admin (Jellyfin)", "kid (Jellyfin)"}; "Ben" entry unchanged
def test_plex_sync_keeps_jellyfin_users(): ...
    # patch plexapi PlexServer/account; existing "kid (Jellyfin)" with skip_ondeck True survives,
    # and "kid (Jellyfin)" is still in raw["skip_ondeck"]
def test_same_name_users_stay_separate(): ...
    # Plex "Ben" skip_ondeck False, Jellyfin "Ben (Jellyfin)" skip_ondeck True -> skip_ondeck == ["Ben (Jellyfin)"]
def test_save_users_round_trips_jellyfin_titles(client):
    # PUT /settings/users with include_ondeck_Ben=on, include_watchlist_Ben=on and
    # "include_watchlist_Ben (Jellyfin)": "on" (no include_ondeck for it) -> Jellyfin Ben skip_ondeck True,
    # skip_watchlist False; Plex Ben unchanged
def test_users_list_hides_disabled_server_users(client):
    # media_server "plex" with a Jellyfin entry in users[] -> GET /settings/users/list doesn't contain "(Jellyfin)";
    # the entry is still in raw users
```

- [ ] **Step 2: Run `python -m pytest tests/test_users_two_servers.py -q` and confirm it fails**
- [ ] **Step 3: Implement the changes listed in Interfaces**
- [ ] **Step 4: Run `python -m pytest tests/test_users_two_servers.py tests/test_jellyfin_webui.py -q` and the full suite, and confirm they pass**
- [ ] **Step 5: Commit** (`Users page lists Plex and Jellyfin users`)

---

### Task 9: Check it in the real app

**Files:** none (verification only; fixes found here go back to the owning task's files with their own commit)

- [ ] **Step 1:** Run the full suite. Expected: no failures beyond the 14 known ones.
- [ ] **Step 2:** Start a throwaway copy of the app (`rsync` to the scratchpad, without `plexcache_settings.json`), plus the fake Jellyfin stub (`scratchpad/stub/fake_jellyfin.py`, key `good`). Write a settings file with:
  - `media_server: "both"`;
  - `PLEX_URL: "http://127.0.0.1:9"` (unreachable) and any `PLEX_TOKEN`;
  - the Jellyfin URL/key;
  - `firststart: false`.
- [ ] **Step 3:** In headless Chrome (Playwright, `channel="chrome"`), screenshot and check:
  - the dashboard shows "Plex Not Connected" and "Jellyfin Connected";
  - Settings → Media Server shows both switches on;
  - turning both off and saving shows the error;
  - Users → Sync lists "admin (Jellyfin)" and "kid (Jellyfin)", and reports the Plex failure without losing them;
  - no page errors.
- [ ] **Step 4:** Push the branch to `mine` and open a PR against `main` on BBergle/MediaCache.
