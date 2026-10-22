"""Tests for Plex watchlist RSS fetching and its fallback path.

Plex serves watchlist RSS from `rss.plex.tv/<uuid>`. In July 2026 that host
began returning 401 without a token and 404 with one, while the same feed
remained reachable via `discover.provider.plex.tv/rss/<uuid>`. Plex's own UI
still hands out `rss.plex.tv` URLs, so the configured URL stays primary and the
mirror is only tried when Plex refuses.

Covered here:
  - URL derivation (only legacy Plex feed URLs are ever rewritten)
  - token scoping (never sent to a non-Plex host)
  - primary-first ordering, and no fallback when primary works
  - auth/not-found responses are not retried
  - transient errors still retry
  - cache fallback when nothing responds, plus the staleness warning
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.modules['fcntl'] = MagicMock()
for _mod in [
    'apscheduler', 'apscheduler.schedulers',
    'apscheduler.schedulers.background', 'apscheduler.triggers',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
    'plexapi.library', 'plexapi.exceptions',
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core.plex_api import (
    PlexManager,
    RSS_MAX_RETRIES,
    RSS_CACHE_STALE_HOURS,
    _derive_rss_fallback_url,
    _should_send_plex_token,
)


FEED_ID = "e9744518-296d-4bce-9856-2a8550631974"
PRIMARY_URL = f"https://rss.plex.tv/{FEED_ID}"
FALLBACK_URL = f"https://discover.provider.plex.tv/rss/{FEED_ID}"

RSS_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Plex Watchlist</title>
<item>
  <title>The Menu (2022)</title><category>movie</category>
  <pubDate>Mon, 27 Jul 2026 12:00:00 GMT</pubDate>
  <author>4016660</author><guid>imdb://tt9764362</guid>
</item>
<item>
  <title>Slow Horses (2022)</title><category>show</category>
  <pubDate>Mon, 27 Jul 2026 13:00:00 GMT</pubDate>
  <author>4016660</author><guid>tvdb://397785</guid>
</item>
</channel></rss>"""


def _resp(status=200, text="", location=None):
    """Build a mock requests.Response."""
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.headers = {"Location": location} if location else {}
    r.is_redirect = location is not None
    r.is_permanent_redirect = False
    if status >= 400:
        err = requests.HTTPError(f"{status} Error")
        err.response = MagicMock(status_code=status)
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


def _api(tmp_path=None, token="TOKEN123"):
    api = PlexManager.__new__(PlexManager)
    api.plex_token = token
    api._rss_cache_file = str(tmp_path / "rss_cache.json") if tmp_path else None
    api._token_lock = threading.Lock()
    return api


class TestDeriveFallbackUrl:
    def test_legacy_url_maps_to_discover(self):
        assert _derive_rss_fallback_url(PRIMARY_URL) == FALLBACK_URL

    def test_trailing_slash_tolerated(self):
        assert _derive_rss_fallback_url(PRIMARY_URL + "/") == FALLBACK_URL

    def test_already_migrated_url_not_rewritten(self):
        assert _derive_rss_fallback_url(FALLBACK_URL) is None

    def test_third_party_url_not_rewritten(self):
        assert _derive_rss_fallback_url("https://evil.example.com/rss/" + FEED_ID) is None

    def test_bare_host_has_no_feed_id(self):
        assert _derive_rss_fallback_url("https://rss.plex.tv/") is None

    def test_multi_segment_path_rejected(self):
        assert _derive_rss_fallback_url("https://rss.plex.tv/a/b") is None


class TestTokenScoping:
    @pytest.mark.parametrize("url", [
        PRIMARY_URL,
        FALLBACK_URL,
        "https://plex.tv/api/v2/user",
        "https://abc.plex.direct:32400/library",
    ])
    def test_plex_hosts_get_token(self, url):
        assert _should_send_plex_token(url) is True

    @pytest.mark.parametrize("url", [
        "https://evil.example.com/rss/x",
        "https://notplex.tv/feed",
        "https://plex.tv.evil.com/feed",
        "https://plex-rss-feeds.s3.us-east-1.amazonaws.com/e/x.xml",
    ])
    def test_other_hosts_do_not(self, url):
        assert _should_send_plex_token(url) is False


class TestFetchOrdering:
    def test_primary_success_skips_fallback(self, tmp_path):
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get", return_value=_resp(200, RSS_BODY)) as g:
            items = api._fetch_rss_titles(PRIMARY_URL)
        assert len(items) == 2
        assert g.call_count == 1
        assert g.call_args_list[0][0][0] == PRIMARY_URL

    def test_primary_404_falls_back(self, tmp_path):
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get",
                   side_effect=[_resp(404), _resp(200, RSS_BODY)]) as g:
            items = api._fetch_rss_titles(PRIMARY_URL)
        assert len(items) == 2
        assert [c[0][0] for c in g.call_args_list] == [PRIMARY_URL, FALLBACK_URL]

    def test_primary_401_falls_back(self, tmp_path):
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get",
                   side_effect=[_resp(401), _resp(200, RSS_BODY)]) as g:
            items = api._fetch_rss_titles(PRIMARY_URL)
        assert len(items) == 2
        assert g.call_count == 2

    def test_auth_failures_are_not_retried(self, tmp_path):
        """401/404 are deterministic - one attempt each, not RSS_MAX_RETRIES."""
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get", return_value=_resp(404)) as g:
            with patch("core.plex_api.time.sleep") as slept:
                api._fetch_rss_titles(PRIMARY_URL)
        assert g.call_count == 2  # one primary, one fallback
        slept.assert_not_called()

    def test_non_plex_url_has_no_fallback(self, tmp_path):
        api = _api(tmp_path)
        custom = "https://example.com/my-feed.xml"
        with patch("core.plex_api.requests.get", return_value=_resp(404)) as g:
            items = api._fetch_rss_titles(custom)
        assert items == []
        assert g.call_count == 1  # nothing to fall back to

    def test_transient_error_still_retries(self, tmp_path):
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get",
                   side_effect=[requests.ConnectionError("boom"), _resp(200, RSS_BODY)]) as g:
            with patch("core.plex_api.time.sleep"):
                items = api._fetch_rss_titles(PRIMARY_URL)
        assert len(items) == 2
        assert g.call_count == 2
        assert all(c[0][0] == PRIMARY_URL for c in g.call_args_list)

    def test_persistent_network_error_exhausts_then_falls_back(self, tmp_path):
        api = _api(tmp_path)
        calls = [requests.ConnectionError("boom")] * RSS_MAX_RETRIES + [_resp(200, RSS_BODY)]
        with patch("core.plex_api.requests.get", side_effect=calls) as g:
            with patch("core.plex_api.time.sleep"):
                items = api._fetch_rss_titles(PRIMARY_URL)
        assert len(items) == 2
        assert g.call_args_list[-1][0][0] == FALLBACK_URL


class TestRedirectHandling:
    def test_token_sent_to_plex_but_not_to_storage_host(self, tmp_path):
        api = _api(tmp_path)
        s3 = "https://plex-rss-feeds.s3.us-east-1.amazonaws.com/e/x.xml?sig=abc"
        with patch("core.plex_api.requests.get",
                   side_effect=[_resp(302, location=s3), _resp(200, RSS_BODY)]) as g:
            items = api._fetch_rss_titles(FALLBACK_URL)

        assert len(items) == 2
        plex_call, s3_call = g.call_args_list
        assert plex_call[1]["headers"].get("X-Plex-Token") == "TOKEN123"
        assert s3_call[0][0] == s3
        # The presigned URL carries its own auth - our token must not ride along.
        assert "headers" not in s3_call[1] or not s3_call[1].get("headers")

    def test_no_token_configured_sends_no_header(self, tmp_path):
        api = _api(tmp_path, token="")
        with patch("core.plex_api.requests.get", return_value=_resp(200, RSS_BODY)) as g:
            api._fetch_rss_titles(PRIMARY_URL)
        assert g.call_args_list[0][1]["headers"] == {}


class TestCacheFallback:
    def _seed_cache(self, path, age_hours):
        ts = (datetime.now() - timedelta(hours=age_hours)).isoformat()
        path.write_text(json.dumps({
            "timestamp": ts,
            "url": PRIMARY_URL,
            "items": [["Cached Movie", "movie", None, "123", "imdb://tt1"]],
        }), encoding="utf-8")

    def test_both_urls_fail_uses_cache(self, tmp_path):
        api = _api(tmp_path)
        self._seed_cache(tmp_path / "rss_cache.json", age_hours=2)
        with patch("core.plex_api.requests.get", return_value=_resp(404)):
            items = api._fetch_rss_titles(PRIMARY_URL)
        assert len(items) == 1
        assert items[0][0] == "Cached Movie"

    def test_fresh_cache_does_not_warn(self, tmp_path, caplog):
        api = _api(tmp_path)
        self._seed_cache(tmp_path / "rss_cache.json", age_hours=2)
        with patch("core.plex_api.requests.get", return_value=_resp(404)):
            with caplog.at_level(logging.WARNING):
                api._fetch_rss_titles(PRIMARY_URL)
        assert not [r for r in caplog.records if "hours old" in r.message]

    def test_stale_cache_warns(self, tmp_path, caplog):
        api = _api(tmp_path)
        self._seed_cache(tmp_path / "rss_cache.json", age_hours=RSS_CACHE_STALE_HOURS + 5)
        with patch("core.plex_api.requests.get", return_value=_resp(404)):
            with caplog.at_level(logging.WARNING):
                items = api._fetch_rss_titles(PRIMARY_URL)
        # Stale data is still returned - losing every remote item would be worse.
        assert len(items) == 1
        assert any("hours old" in r.message for r in caplog.records)

    def test_no_cache_returns_empty(self, tmp_path):
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get", return_value=_resp(404)):
            assert api._fetch_rss_titles(PRIMARY_URL) == []

    def test_cache_keyed_to_configured_url_after_fallback(self, tmp_path):
        """Cache records the user's URL, not the mirror, so it stays correct
        if Plex restores the primary host."""
        api = _api(tmp_path)
        with patch("core.plex_api.requests.get",
                   side_effect=[_resp(404), _resp(200, RSS_BODY)]):
            api._fetch_rss_titles(PRIMARY_URL)
        saved = json.loads((tmp_path / "rss_cache.json").read_text(encoding="utf-8"))
        assert saved["url"] == PRIMARY_URL
        assert len(saved["items"]) == 2
