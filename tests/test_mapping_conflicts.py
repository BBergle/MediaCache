"""Path mappings shared by two servers: flag merging and conflict warnings."""
from core.config import PathMapping
from core.file_operations import (
    MultiPathModifier, find_mapping_conflicts, merge_shared_real_paths,
)


def _pair():
    plex = PathMapping(name="Plex TV", plex_path="/data/tv/", real_path="/mnt/user/tv/",
                       cache_path="/mnt/cache/tv/", cacheable=True)
    jf = PathMapping(name="Jellyfin TV", plex_path="/media/tv/", real_path="/mnt/user/tv/",
                     cache_path=None, cacheable=False)
    return plex, jf


def test_cacheable_if_any_mapping_to_folder_is():
    plex, jf = _pair()
    merged = {m.name: m for m in merge_shared_real_paths([plex, jf])}
    assert merged["Jellyfin TV"].cacheable and merged["Jellyfin TV"].cache_path == "/mnt/cache/tv/"
    assert jf.cacheable is False  # input untouched
    mod = MultiPathModifier([plex, jf])
    assert mod.convert_plex_to_real("/media/tv/S/e1.mkv")[0] == "/mnt/user/tv/S/e1.mkv"


def test_enabled_stays_per_mapping():
    # Each server's library toggle works on its own: a disabled Jellyfin mapping
    # stays disabled even when Plex's mapping to the same folder is enabled.
    plex, jf = _pair()
    jf.enabled = False
    merged = {m.name: m for m in merge_shared_real_paths([plex, jf])}
    assert not merged["Jellyfin TV"].enabled and merged["Plex TV"].enabled
    mod = MultiPathModifier([plex, jf])
    assert mod.convert_plex_to_real("/media/tv/S/e1.mkv")[1] is None


def _dicts():
    return [
        {"name": "Plex TV", "plex_path": "/data/tv/", "real_path": "/mnt/user/tv/",
         "cacheable": True, "enabled": True},
        {"name": "Jellyfin TV", "plex_path": "/media/tv/", "real_path": "/mnt/user/tv/",
         "cacheable": False, "enabled": True},
    ]


def test_conflict_messages():
    assert find_mapping_conflicts(_dicts()) == [
        "'Plex TV' and 'Jellyfin TV' point at /mnt/user/tv/ but disagree on caching. It will be cached."]

    same_path = [
        {"name": "A", "plex_path": "/data/", "real_path": "/mnt/a/", "cacheable": True, "enabled": True},
        {"name": "B", "plex_path": "/data/", "real_path": "/mnt/b/", "cacheable": True, "enabled": True},
    ]
    assert find_mapping_conflicts(same_path) == [
        "'A' and 'B' use the same server path /data/ for different folders. Only one can apply."]


def test_agreeing_mappings_have_no_conflicts():
    d = _dicts()
    d[1]["cacheable"] = True
    assert find_mapping_conflicts(d) == []
    d[1]["enabled"] = False  # per-server toggles may differ
    assert find_mapping_conflicts(d) == []
