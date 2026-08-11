"""Tests for the shared grouping primitives in ``core/media_grouping.py``.

These cover the rule itself. The two callers that build on it — the activity
feed (``core.activity.group_episodes_by_show``) and the Recently Added views
(``RecentlyAddedService.group_rows_for_display``) — are covered in their own
modules; what matters here is that both inherit the same ordering and
singleton semantics.
"""

from core.media_grouping import (
    format_season_range,
    group_ordered,
    parse_show_episode,
    show_name_from_filename,
    stable_group_id,
)


class TestParseShowEpisode:
    def test_parses_show_season_episode(self):
        assert parse_show_episode("Sugar (2024) - S02E03 - Ruthless.mkv") == (
            "Sugar (2024)", 2, 3
        )

    def test_case_insensitive(self):
        assert parse_show_episode("The Bear - s01e04.mkv")[0] == "The Bear"

    def test_multi_digit_season_and_episode(self):
        assert parse_show_episode("Show - S10E120.mkv") == ("Show", 10, 120)

    def test_movie_is_not_an_episode(self):
        assert parse_show_episode("Dune (2021) 2160p.mkv") is None

    def test_empty_and_none_safe(self):
        assert parse_show_episode("") is None
        assert parse_show_episode(None) is None

    def test_show_name_helper(self):
        assert show_name_from_filename("Sugar (2024) - S01E01.mkv") == "Sugar (2024)"
        assert show_name_from_filename("Dune.mkv") is None


class TestGroupOrdered:
    def test_groups_by_key_preserving_first_seen_order(self):
        items = [
            {"n": "a", "k": "X"},
            {"n": "b", "k": "Y"},
            {"n": "c", "k": "X"},
        ]
        result = group_ordered(items, lambda i: i["k"])
        assert [key for key, _ in result] == ["X", "Y"]
        assert [i["n"] for i in result[0][1]] == ["a", "c"]
        assert [i["n"] for i in result[1][1]] == ["b"]

    def test_none_key_items_never_merge(self):
        items = [{"n": "a"}, {"n": "b"}, {"n": "c"}]
        result = group_ordered(items, lambda i: None)
        assert len(result) == 3
        assert all(key is None and len(members) == 1 for key, members in result)
        assert [m[0]["n"] for _, m in result] == ["a", "b", "c"]

    def test_group_anchors_at_first_member_position(self):
        # A group must render where its FIRST member appeared, not where its
        # last one did — otherwise re-renders reshuffle the list.
        items = [
            {"n": "movie1", "k": None},
            {"n": "ep1", "k": "Show"},
            {"n": "movie2", "k": None},
            {"n": "ep2", "k": "Show"},
        ]
        result = group_ordered(items, lambda i: i["k"])
        assert [key for key, _ in result] == [None, "Show", None]
        assert [i["n"] for i in result[1][1]] == ["ep1", "ep2"]

    def test_singletons_are_returned_as_one_member_buckets(self):
        items = [{"k": "Show"}]
        result = group_ordered(items, lambda i: i["k"])
        assert len(result) == 1
        key, members = result[0]
        assert key == "Show"
        assert len(members) == 1

    def test_empty_input(self):
        assert group_ordered([], lambda i: None) == []

    def test_tuple_keys_supported(self):
        items = [
            {"n": "a", "k": ("Cached", "Show")},
            {"n": "b", "k": ("Restored", "Show")},
            {"n": "c", "k": ("Cached", "Show")},
        ]
        result = group_ordered(items, lambda i: i["k"])
        assert len(result) == 2
        assert [i["n"] for i in result[0][1]] == ["a", "c"]


class TestStableGroupId:
    def test_same_key_same_id(self):
        # Stability is the whole point: the banner re-renders every 2s and
        # restores expand state by id.
        assert stable_group_id("Cached", "Entourage") == stable_group_id("Cached", "Entourage")

    def test_action_is_part_of_the_identity(self):
        # One run can cache some episodes of a show and restore others. Those
        # are two rows and must toggle independently.
        assert stable_group_id("Cached", "Entourage") != stable_group_id("Moved", "Entourage")

    def test_different_shows_differ(self):
        assert stable_group_id("Cached", "Entourage") != stable_group_id("Cached", "Sugar (2024)")

    def test_id_is_attribute_safe(self):
        # A title carrying a quote must not leak into the data-* attribute.
        gid = stable_group_id("Cached", 'Some "Quoted" Show \'s')
        assert gid.isalnum()
        assert '"' not in gid and "'" not in gid and "<" not in gid

    def test_parts_are_delimited_not_concatenated(self):
        # ("ab", "c") and ("a", "bc") are different groups, not the same one.
        assert stable_group_id("ab", "c") != stable_group_id("a", "bc")

    def test_none_parts_are_handled(self):
        # A missing part is treated as empty rather than raising. No caller
        # distinguishes None from "" in the same key position, so folding them
        # together is safe.
        assert stable_group_id("Cached", None) == stable_group_id("Cached", "")
        assert stable_group_id(None).startswith("g")

    def test_non_string_parts_are_accepted(self):
        # Recently Added keys include a season number in some call sites.
        assert stable_group_id("ra", "TV", 2) == stable_group_id("ra", "TV", 2)
        assert stable_group_id("ra", "TV", 2) != stable_group_id("ra", "TV", 3)


class TestFormatSeasonRange:
    def test_single_season(self):
        assert format_season_range([2]) == "Season 2"

    def test_span(self):
        assert format_season_range([1, 2]) == "Seasons 1–2"

    def test_unsorted_and_duplicated_input(self):
        assert format_season_range([3, 1, 3, 2]) == "Seasons 1–3"

    def test_no_seasons(self):
        assert format_season_range([]) == ""
        assert format_season_range([None]) == ""
