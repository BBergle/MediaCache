"""Per-user retention overrides accept the values their inputs offer.

The global Watchlist Retention is a float rendered with ``step="0.5"``, and
``core/config.py`` casts the per-user value with ``float()``. The save handler
parsed it with ``int()``, so every half-day the form invites raised ValueError
and the override was dropped, silently falling back to the global default.

Days to Monitor really is a whole number (``core/config.py`` casts it with
``int()``), so it stays an int here.
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests', 'apscheduler',
                  'apscheduler.schedulers', 'apscheduler.schedulers.background',
                  'apscheduler.triggers', 'apscheduler.triggers.cron',
                  'apscheduler.triggers.interval']:
    sys.modules.setdefault(_mod_name, MagicMock())


def _save(form: dict, caplog):
    """Run save_user_settings over one user and return that user's dict.

    The handler mutates the dicts it gets from get_user_settings() in place and
    hands the same list to save_user_settings(), so inspecting the dict we
    supplied shows exactly what would be persisted.
    """
    from starlette.datastructures import ImmutableMultiDict
    from web.routers import settings as settings_router

    user = {"title": "alice", "token": "t"}

    settings_service = MagicMock()
    settings_service.get_user_settings.return_value = {"users": [user]}
    settings_service.save_user_settings.return_value = True

    with patch.object(settings_router, "get_settings_service", return_value=settings_service), \
         patch.object(settings_router, "templates", MagicMock()), \
         caplog.at_level(logging.WARNING):
        settings_router.save_user_settings(MagicMock(), ImmutableMultiDict(form))

    assert settings_service.save_user_settings.called, "handler did not reach the save"
    return user, caplog.text


class TestPerUserRetentionParsing:

    def test_half_day_watchlist_retention_survives(self, caplog):
        """step="0.5" is offered by the form, so it has to be accepted."""
        user, _ = _save({"watchlist_days_alice": "0.5"}, caplog)

        assert user.get("watchlist_retention_days") == 0.5, (
            "a half-day override was dropped, silently reverting this user "
            "to the global default"
        )

    def test_whole_day_watchlist_retention_still_works(self, caplog):
        user, _ = _save({"watchlist_days_alice": "14"}, caplog)

        assert user.get("watchlist_retention_days") == 14

    def test_days_to_monitor_stays_a_whole_number(self, caplog):
        user, _ = _save({"ondeck_days_alice": "30"}, caplog)

        assert user.get("days_to_monitor") == 30
        assert isinstance(user.get("days_to_monitor"), int)

    def test_unparseable_value_is_reported(self, caplog):
        """Falling back to the global default must not be silent."""
        user, log_text = _save({"watchlist_days_alice": "soon"}, caplog)

        assert "watchlist_retention_days" not in user
        assert "Watchlist Retention" in log_text
        assert "alice" in log_text
