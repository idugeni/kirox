"""Tray lifecycle tests with no real GUI."""

from __future__ import annotations

import builtins
import importlib.util
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

import kirox.service.tray as tray_module
from kirox.service.daemon import KiroxService
from kirox.service.tray import KiroTray
from kirox.utils.config import Config


class FakeService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.is_running = True

    def start(self) -> None:
        self.events.append("service.start")
        self.is_running = True

    def stop(self) -> None:
        self.events.append("service.stop")
        self.is_running = False


class FakeIcon:
    def __init__(self, *args, events: list[str], **kwargs) -> None:
        del args, kwargs
        self.events = events

    def run(self) -> None:
        self.events.append("icon.run")

    def stop(self) -> None:
        self.events.append("icon.stop")


def test_tray_keeps_injected_service_and_stops_it_after_run(monkeypatch):
    events: list[str] = []
    service = FakeService(events)

    class Icon(FakeIcon):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, events=events, **kwargs)

    fake_pystray = SimpleNamespace(
        Menu=lambda *items: items,
        MenuItem=lambda *args, **kwargs: (args, kwargs),
        Icon=Icon,
    )
    fake_pystray.Menu.SEPARATOR = object()
    monkeypatch.setattr(tray_module, "HAS_PYSTRAY", True)
    monkeypatch.setattr(tray_module, "pystray", fake_pystray, raising=False)
    monkeypatch.setattr(tray_module, "create_icon_image", lambda: None)

    tray = KiroTray(Config(), service=cast(KiroxService, service))
    assert tray.start() is True

    assert tray._service is service
    assert events == ["icon.run", "service.stop"]


def test_tray_shutdown_orders_service_before_icon():
    events: list[str] = []
    service = FakeService(events)
    tray = KiroTray(Config(), service=cast(KiroxService, service))
    tray._icon = FakeIcon(events=events)

    tray._shutdown()

    assert events == ["service.stop", "icon.stop"]


class HeadlessDisplayError(Exception):
    """Stand-in for a backend error such as Xlib.error.DisplayNameError."""


def _load_tray_without_pystray(monkeypatch, error: Exception):
    """Execute a private copy of the tray module while `import pystray` fails."""
    real_import = builtins.__import__

    def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pystray":
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    spec = importlib.util.spec_from_file_location("kirox_tray_isolated", tray_module.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "error", [ImportError("no module"), HeadlessDisplayError('Bad display ""')]
)
def test_tray_import_degrades_when_pystray_cannot_load(monkeypatch, error: Exception) -> None:
    module = _load_tray_without_pystray(monkeypatch, error)

    assert module.HAS_PYSTRAY is False
    assert module.create_icon_image() is None
    assert module.KiroTray(Config()).start() is False
    assert "kirox_tray_isolated" not in sys.modules


def test_tray_start_returns_false_and_reports_display_hint(monkeypatch, caplog) -> None:
    monkeypatch.setattr(tray_module, "HAS_PYSTRAY", False)

    with caplog.at_level("ERROR"):
        assert KiroTray(Config()).start() is False

    assert tray_module.TRAY_UNAVAILABLE_MESSAGE in caplog.text
    assert "--no-tray" in tray_module.TRAY_UNAVAILABLE_MESSAGE
