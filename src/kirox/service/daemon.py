"""Background service daemon."""

from __future__ import annotations
import logging
import signal
import sys
import threading
from typing import Optional
from kirox.service.server import create_app
from kirox.service.scheduler import TokenScheduler
from kirox.core.client import AssistantClient
from kirox.utils.config import Config, load_config

logger = logging.getLogger(__name__)


class KuroService:
    def __init__(self, config: Optional[Config] = None):
        self._config = config or load_config()
        self._client: Optional[AssistantClient] = None
        self._scheduler: Optional[TokenScheduler] = None
        self._running = False

    def start(self):
        logger.info("Starting Kirox service...")
        self._running = True
        if self._config.token:
            from kirox.core.auth import AuthManager
            auth = AuthManager(token=self._config.token, profile_arn=self._config.profile_arn)
            self._client = AssistantClient(auth=auth, region=self._config.region)
        else:
            self._client = AssistantClient.from_cli_db(self._config.db_path, self._config.region)
        self._scheduler = TokenScheduler(self._client, self._config, on_token_refresh=self._on_token_refresh, on_error=self._on_error)
        self._scheduler.start()
        app = create_app(self._config)
        threading.Thread(target=lambda: app.run(host=self._config.server_host, port=self._config.server_port, debug=False, use_reloader=False), daemon=True).start()
        logger.info(f"Service started on {self._config.server_host}:{self._config.server_port}")

    def stop(self):
        logger.info("Stopping service...")
        self._running = False
        if self._scheduler: self._scheduler.stop()
        logger.info("Service stopped")

    def _on_token_refresh(self):
        logger.debug("Token refreshed")

    def _on_error(self, error: Exception):
        logger.error(f"Service error: {error}")

    def run(self):
        def handler(sig, frame):
            self.stop()
            sys.exit(0)
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        self.start()
        while self._running:
            try: signal.pause()
            except AttributeError:
                import time; time.sleep(1)


def main():
    from kirox.utils.logging import setup_logging
    setup_logging()
    KuroService().run()
