"""System tray integration for a managed Kirox service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from kirox.utils.config import Config, load_config

if TYPE_CHECKING:
    from kirox.service.daemon import KiroxService

logger = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False


def create_icon_image(color: str = "green") -> Any:
    if not HAS_PYSTRAY:
        return None
    image = Image.new("RGB", (64, 64), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([16, 16, 48, 48], fill="white")
    return image


class KiroTray:
    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        service: Optional[KiroxService] = None,
    ) -> None:
        self._config = config or Config()
        self._service = service
        self._status = "Running" if service is not None and service.is_running else "Stopped"
        self._icon: Any = None

    def _get_service(self) -> KiroxService:
        if self._service is None:
            from kirox.service.daemon import KiroxService

            self._service = KiroxService(self._config)
        return self._service

    def _start_service(self, *args: object) -> None:
        del args
        self._get_service().start()
        self._status = "Running"

    def _stop_service(self, *args: object) -> None:
        del args
        self._get_service().stop()
        self._status = "Stopped"

    def _shutdown(self, *args: object) -> None:
        del args
        try:
            if self._service is not None:
                self._service.stop()
        finally:
            if self._icon is not None:
                self._icon.stop()

    def start(self) -> bool:
        """Run the tray without replacing an injected service."""
        if not HAS_PYSTRAY:
            logger.error("pystray not installed. Run: pip install kirox[service]")
            return False

        service = self._get_service()
        self._status = "Running" if service.is_running else "Stopped"
        menu = pystray.Menu(
            pystray.MenuItem("Status: " + self._status, None, enabled=False),
            pystray.MenuItem("Start", self._start_service),
            pystray.MenuItem("Stop", self._stop_service),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._shutdown),
        )
        self._icon = pystray.Icon("kirox", create_icon_image(), "Kirox Service", menu)
        try:
            self._icon.run()
        finally:
            if service.is_running:
                service.stop()
        return True


def main() -> None:
    from kirox.utils.logging import setup_logging

    config = load_config()
    log_file = Path(config.log_file).expanduser() if config.log_file else None
    setup_logging(level=config.log_level, log_file=log_file)
    KiroTray(config).start()
