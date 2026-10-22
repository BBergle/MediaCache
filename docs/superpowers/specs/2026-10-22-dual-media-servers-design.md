# Plex and Jellyfin connected at the same time

## Intent

The owner runs Plex and Jellyfin side by side on one Unraid box. Both point at the same
media files on the array, but each container may mount them at a different path. Different
people use different servers. MediaCache should cache what anyone is about to watch on
either server, and when both servers want the same file (say the same TV episode is up next
on both) it must treat it as one file: cache it once, and only move it back to the array
when nobody on either server needs it any more.

Today `media_server` picks exactly one server (`"plex"` or `"jellyfin"`), and `core/app.py`
builds either a `PlexManager` or a `JellyfinManager` as `self.plex_manager`.

## Goals

1. Plex, Jellyfin, or both can be enabled. Every run fetches OnDeck and Watchlist from every
   enabled server and merges them.
2. Files are matched on the **real path on the array**, after path mappings. Each server's
   container paths get their own path mapping(s). A file wanted by both servers is cached
   once and counts once against cache limits and quotas.
3. A cached file stays on cache while **anyone on any enabled server** still needs it, whether
   it's OnDeck, on a Watchlist/Favorites list, or playing right now. Being watched on one
   server never moves a file back while a user on the other still needs it.
4. Retention rules that say "every user who has it OnDeck" count users across both servers.
5. Users are **per server**. Plex "Ben" and Jellyfin "Ben" are two different people, with their
   own skip and retention settings. They are never merged.
   - *(Owner's answer)* **Settings → Users lists both servers' users together**, each labelled
     with its server, and each with the same per-user controls Plex users have today (skip
     OnDeck, skip Watchlist, per-user retention). That needs a Jellyfin user sync alongside
     `sync_users_from_plex`, and `users[]` entries that record their server. The skip lists
     must be rebuilt from both servers' users, so syncing or saving one server's users never
     wipes the other's.
   - *(Claude's call)* "Monitor other users" (`users_toggle`) stays one shared setting. The
     main user is per server: the Plex account owner, and `jellyfin_user` (blank = first
     administrator).
6. If any enabled server can't be reached, or returns incomplete data, that run must not
   lose anything the missing server wanted. The run still caches what the reachable servers
   asked for, but:
   - nothing is moved back: the existing `is_ondeck_data_complete` /
     `is_watchlist_data_complete` gate (app.py ~1357) is complete only if every enabled
     server's data is complete;
   - eviction (smart and FIFO, `_run_smart_eviction` app.py ~2396) doesn't run either, so new
     items from the reachable server can't push out the missing server's files;
   - the missing server's users and retention clocks in the trackers carry over from its last
     good run, instead of being cleared by `prepare_for_run` / `cleanup_unseen`. Its entries
     keep their users, `ondeck_users` and `user_first_seen`.
7. Settings → Media Server lets each server be switched on or off independently, each with
   its own connection fields and its own Test Connection. The dashboard shows a connection
   status for each enabled server.
8. Existing installs keep working with no changes: an install with only Plex settings stays
   Plex-only, and `media_server: "jellyfin"` stays Jellyfin-only.

## Non-goals

- Syncing watch state between the servers.
- The setup wizard configuring both servers. The wizard still sets up one server; the second
  is enabled afterwards in Settings.
- Jellyfin versions of Plex-only features: pinned media, Recently Added, upgrade detection,
  the duplicate finder, RSS watchlists and Plex OAuth. These keep working from Plex when
  Plex is enabled.
- Auto-generating Jellyfin path mappings in Settings. They're added with the existing manual
  "Add Path Mapping" form on the Libraries tab. The wizard already generates them when
  Jellyfin is the wizard's server.

## Anti-goals (a failure even if it "works")

- A file moved back to the array while someone on the other server still has it OnDeck or
  on their list.
- The same file cached twice, or counted twice toward the cache limit or quota.
- Users from the two servers merged, so one person's skip list or retention applies to
  someone else.
- A server outage (container stopped, unreachable or erroring) making files look unneeded,
  so they get moved back or evicted, or losing that server's retention clocks.
- Existing Plex-only installs changing behaviour, or losing tracking history, after the
  upgrade.

## Constraints and facts from the code

- Path mappings (`MultiPathModifier`, core/file_operations.py ~2760) are server-agnostic
  prefix matches, longest prefix first. One mapping per container prefix already works for
  two servers.
- `OnDeckTracker` (`ondeck_tracker.json`) is keyed by real path. It stores `users[]`,
  `ondeck_users[]`, `user_first_seen{}` and a single `rating_key`. `prepare_for_run` clears
  users each run, and `cleanup_unseen` drops entries not seen this run. So **all servers must
  be fetched within the same run, before cleanup**.
- `WatchlistTracker` (`watchlist_tracker.json`) is keyed by the **server-reported path before
  conversion** (app.py ~1548/1590). With two servers the same file would get two entries, so
  it must be keyed by real path. Existing entries need migrating, or matching on load: an
  existing raw Plex path converts to the same real path.
- Move-back (`FileFilter.get_files_to_move_back_to_array`, file_operations.py ~3787) decides
  "still needed" from the merged OnDeck ∪ Watchlist sets by show/season/episode or movie
  name, so it works on merged data once the merge happens upstream.
- Usernames are bare strings in: both trackers, `per_user_ondeck_days`,
  `per_user_watchlist_days`, `skip_ondeck`, `skip_watchlist`, `users[]` settings entries, and
  the web UI's merged user lists (web/services/cache_service.py, operation_runner.py).
- `rating_key` from Plex is an int and from Jellyfin a GUID string. Upgrade detection and
  `check_for_upgrades` assume Plex keys (the latter does `int(rating_key)`).
- Active sessions: Plex returns session objects, while `JellyfinManager.get_active_sessions`
  returns file paths (app.py `_get_media_path_from_session` handles both).

## Decided

- **Plex usernames stay exactly as they are**, so existing tracker data, per-user settings and
  skip lists keep matching. Jellyfin users are made distinct from Plex users wherever
  usernames are keyed or compared, and the UI labels them with their server (e.g.
  "Ben (Jellyfin)"). A Jellyfin-only install upgrading from the current release may have its
  Jellyfin users re-keyed; it's brand new, so a one-time reset of Jellyfin-user tracking is
  acceptable.
- Matching "the same file" is by real path after mappings. Nothing smarter.
- One server failing to connect at the start of a run doesn't abort the run. The run continues
  with the servers that connected, and move-backs are skipped (goal 6). If no enabled server
  connects, the run fails as it does today.
- *(Builder-check answer, Claude's call)* **Switching a server off in Settings** is different
  from an outage: it means its needs are dropped. Files only that server wanted move back on
  the next run as usual, and its users drop out of the trackers.
- *(Builder-check answer, Claude's call)* **Mappings that disagree:** when two path mappings
  (e.g. a Plex one and a Jellyfin one) resolve to the same real folder, the folder counts as
  cacheable if any of them says cacheable, and as enabled if any is enabled, whichever
  mapping wins the prefix match. Settings → Libraries shows a warning when such mappings
  disagree. Two servers using the **same container prefix for different real folders** isn't
  supported; it gets a warning only.
- Plex-only features check that Plex is enabled and connected, as they do today via
  `plex_manager.plex is None`.

## Left to the builder

- **Data model:** how enabled servers are represented in settings (e.g. per-server enabled
  flags vs a list), as long as goal 8 holds and old `media_server` values are migrated or
  read compatibly.
- **Manager structure:** whether to merge results in a combined manager that wraps a list of
  `PlexManager` / `JellyfinManager` (keeping `app.py`'s single `self.plex_manager` call sites),
  or to loop over managers in `app.py`.
- **Jellyfin username form:** how Jellyfin usernames are made distinct internally (a prefix,
  a suffix or a separate field), as long as Plex names are untouched and display labels show
  the server.
- **rating_key:** how it's stored when a file comes from both servers (per-server keys, or
  Plex preferred), as long as upgrade detection and pins only ever see Plex keys.
- **Layout:** how Settings → Media Server and the dashboard status are laid out, within the
  current Graphite + Mint look.
- **Tests:** follow the repo's pytest style. At minimum:
  - the same episode OnDeck on both servers through different container paths yields one
    cached file and one tracker entry, with users from both servers;
  - watched on one server but still OnDeck on the other is not moved back;
  - one server unreachable means no move-backs;
  - a Plex-only settings file behaves as before;
  - same-named users on both servers stay separate.
