"""Interruptible token refresh scheduler."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from kirox.core.auth import AuthManager
from kirox.core.client import AssistantClient
from kirox.core.errors import APIError
from kirox.utils.config import Config

logger = logging.getLogger(__name__)

AuthResolver = Callable[[], AuthManager]


class TokenScheduler:
    def __init__(
        self,
        client: AssistantClient,
        config: Config,
        on_token_refresh: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        resolver: Optional[AuthResolver] = None,
    ) -> None:
        self._client = client
        self._config = config
        self._on_token_refresh = on_token_refresh
        self._on_error = on_error
        self._resolver = resolver or (lambda: AuthManager.from_cli_db(self._config.db_path))
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_refresh: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        """Start one worker; repeated calls while running are no-ops."""
        with self._lock:
            if self._running and self._thread is not None and self._thread.is_alive():
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._running = True
            thread = threading.Thread(
                target=self._run,
                name="kirox-token-scheduler",
                daemon=False,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._thread = None
                self._running = False
                self._stop_event.set()
                raise
        logger.info("Token scheduler started")

    def stop(self) -> None:
        """Interrupt waits and join the worker exactly once."""
        with self._lock:
            thread = self._thread
            if not self._running and (thread is None or not thread.is_alive()):
                return
            self._running = False
            self._stop_event.set()

        if thread is not None and thread is not threading.current_thread():
            thread.join()
            with self._lock:
                if self._thread is thread:
                    self._thread = None
        logger.info("Token scheduler stopped")

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                delay = max(0.0, float(self._config.refresh_interval))
                try:
                    self._check_and_refresh()
                except Exception as error:
                    logger.error("Scheduler error: %s", error)
                    self._notify_error(error)
                    delay = 60.0
                if self._stop_event.wait(delay):
                    break
        finally:
            with self._lock:
                self._running = False
                if self._thread is threading.current_thread():
                    self._thread = None

    def _notify_error(self, error: Exception) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(error)
        except Exception:
            logger.exception("Token scheduler error callback failed")

    def _check_and_refresh(self) -> None:
        if not self._config.auto_refresh:
            return
        try:
            self._client.list_models()
            self._last_refresh = datetime.now()
            if self._on_token_refresh is not None:
                self._on_token_refresh()
        except Exception as error:
            if isinstance(error, APIError) and error.status in (401, 403):
                self._refresh_token()
                self._client.list_models()
                return
            message = str(error).lower()
            if not isinstance(error, APIError) and ("expired" in message or "invalid" in message):
                self._refresh_token()
                return
            raise

    def _refresh_token(self) -> None:
        auth = self._resolver()
        self._client.replace_auth(auth)
        self._last_refresh = datetime.now()
        if self._on_token_refresh is not None:
            self._on_token_refresh()
        logger.info("Token refreshed")
