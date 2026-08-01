"""Tray lifecycle tests with no real GUI."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

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
