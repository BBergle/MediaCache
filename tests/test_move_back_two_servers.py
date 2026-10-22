"""Move-back with Plex and Jellyfin: a file still wanted on either server stays on cache.

The servers can name the same show differently ("Doctor Who (2005)" on Plex,
"Doctor Who" on Jellyfin), so "still needed" must match on real path first.
"""

import os

from core.config import PathMapping
from core.file_operations import (
    CacheTimestampTracker,
    FileFilter,
    MultiPathModifier,
    OnDeckTracker,
    WatchlistTracker,
)


def _setup(tmp_path):
    cache, real = tmp_path / "cache" / "tv", tmp_path / "user" / "tv"
    season = cache / "Doctor Who" / "Season 01"
    season.mkdir(parents=True)
    ep4, ep5 = season / "Doctor Who - S01E04.mkv", season / "Doctor Who - S01E05.mkv"
    for f in (ep4, ep5):
        f.write_bytes(b"\0")
    excl = tmp_path / "exclude.txt"
    excl.write_text(f"{ep4}\n{ep5}\n")
    mods = MultiPathModifier([
        PathMapping(name="plex", plex_path="/data/tv", real_path=str(real), cache_path=str(cache)),
        PathMapping(name="jf", plex_path="/media/tv", real_path=str(real), cache_path=str(cache)),
    ])
    ts = CacheTimestampTracker(str(tmp_path / "ts.json"))
    # Both cached earlier from Plex OnDeck, so they carry Plex's show title.
    for f, n in ((ep4, 4), (ep5, 5)):
        ts.record_cache_time(str(f), source="ondeck", media_type="episode",
                             episode_info={"show": "Doctor Who (2005)", "season": 1, "episode": n})
    od = OnDeckTracker(str(tmp_path / "od.json"))
    od.prepare_for_run()
    ff = FileFilter(real_source=str(real), cache_dir=str(cache), is_unraid=False,
                    mover_cache_exclude_file=str(excl), timestamp_tracker=ts,
                    cache_retention_hours=0, ondeck_tracker=od,
                    watchlist_tracker=WatchlistTracker(str(tmp_path / "wl.json")),
                    path_modifier=mods)
    real_of = lambda f: str(f).replace(str(cache), str(real), 1)
    return ff, od, mods, real_of(ep4), real_of(ep5)


def test_watched_on_plex_but_ondeck_on_jellyfin_is_not_moved_back(tmp_path):
    ff, od, mods, ep4, ep5 = _setup(tmp_path)
    # Ben watched it on Plex (gone from Plex OnDeck); Ben (Jellyfin) has it OnDeck.
    jf_real, _ = mods.convert_plex_to_real("/media/tv/Doctor Who/Season 01/Doctor Who - S01E05.mkv")
    assert jf_real == ep5
    od.update_entry(jf_real, "Ben (Jellyfin)", is_current_ondeck=True,
                    episode_info={"show": "Doctor Who", "season": 1, "episode": 5})
    back, _, _ = ff.get_files_to_move_back_to_array({jf_real}, set(), set())
    assert ep5 not in back
    assert ep4 in back  # nobody needs E04 any more: still moved back


def test_watched_on_plex_but_jellyfin_favorite_under_other_show_name_stays(tmp_path):
    ff, _, _, ep4, ep5 = _setup(tmp_path)
    # Off every Plex list now; only a Jellyfin favorite (watchlist) still wants E05,
    # and Jellyfin calls the show "Doctor Who", not "Doctor Who (2005)".
    ff.set_media_info_map({ep5: {"media_type": "episode",
                                 "episode_info": {"show": "Doctor Who", "season": 1, "episode": 5}}})
    back, _, _ = ff.get_files_to_move_back_to_array(set(), {ep5}, set())
    assert ep5 not in back
    assert ep4 in back
