"""System tray icon."""

from __future__ import annotations
import logging
from typing import Optional
from kirox.utils.config import Config

logger = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False


def create_icon_image(color: str = "green"):
    if not HAS_PYSTRAY: return None
    image = Image.new("RGB", (64, 64), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([16, 16, 48, 48], fill="white")
    return image


class KiroTray:
    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()
        self._status = "Stopped"
        self._icon = None

    def start(self):
        if not HAS_PYSTRAY:
            logger.error("pystray not installed. Run: pip install kirox[service]")
            return
        from kirox.service.daemon import KuroService
        self._service = KuroService(self._config)
        menu = pystray.Menu(
            pystray.MenuItem("Status: " + self._status, None, enabled=False),
            pystray.MenuItem("Start", lambda: self._service.start()),
            pystray.MenuItem("Stop", lambda: self._service.stop()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda: self._icon.stop() if self._icon else None),
        )
        self._icon = pystray.Icon("kirox", create_icon_image(), "Kirox Service", menu)
        self._icon.run()


def main():
    from kirox.utils.logging import setup_logging
    setup_logging()
    KiroTray().start()
