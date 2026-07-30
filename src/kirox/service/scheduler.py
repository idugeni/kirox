"""Token scheduler."""

from __future__ import annotations
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional
from kirox.core.client import AssistantClient
from kirox.utils.config import Config

logger = logging.getLogger(__name__)


class TokenScheduler:
    def __init__(self, client: AssistantClient, config: Config, on_token_refresh: Optional[Callable[[], None]] = None, on_error: Optional[Callable[[Exception], None]] = None):
        self._client = client
        self._config = config
        self._on_token_refresh = on_token_refresh
        self._on_error = on_error
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_refresh: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Token scheduler started")

    def stop(self):
        self._running = False
        if self._thread: self._thread.join(timeout=5)
        logger.info("Token scheduler stopped")

    def _run(self):
        while self._running:
            try:
                self._check_and_refresh()
                time.sleep(self._config.refresh_interval)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                if self._on_error: self._on_error(e)
                time.sleep(60)

    def _check_and_refresh(self):
        if not self._config.auto_refresh: return
        try:
            self._client.list_models()
            self._last_refresh = datetime.now()
            if self._on_token_refresh: self._on_token_refresh()
        except Exception as e:
            if "expired" in str(e).lower() or "invalid" in str(e).lower():
                self._refresh_token()
            else:
                raise

    def _refresh_token(self):
        try:
            from kirox.core.auth import AuthManager
            auth = AuthManager.from_cli_db(self._config.db_path)
            self._client._auth = auth
            self._last_refresh = datetime.now()
            if self._on_token_refresh: self._on_token_refresh()
            logger.info("Token refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh: {e}")
            if self._on_error: self._on_error(e)
