"""Tests for scheduler."""

from unittest.mock import Mock
from kirox.service.scheduler import TokenScheduler
from kirox.utils.config import Config


def test_scheduler_start_stop():
    s = TokenScheduler(Mock(), Config(refresh_interval=1))
    s.start(); assert s.is_running
    s.stop(); assert not s.is_running


def test_scheduler_refresh():
    c = Mock(); c.list_models.return_value = []
    cb = Mock()
    s = TokenScheduler(c, Config(refresh_interval=1, auto_refresh=True), on_token_refresh=cb)
    s._check_and_refresh()
    cb.assert_called_once()
