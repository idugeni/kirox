"""Thread-safe owner for the Kirox background service lifecycle."""

from __future__ import annotations

import logging
import os
import secrets
import signal
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Callable, Optional

from kirox.core.auth import AuthManager
from kirox.core.client import AssistantClient
from kirox.service.process_identity import capture_process_identity
from kirox.service.scheduler import AuthResolver, TokenScheduler
from kirox.service.server import ManagedHTTPServer, create_app
from kirox.service.state import ServiceState, clear_state, write_state
from kirox.utils.config import Config, load_config

logger = logging.getLogger(__name__)


class KiroxService:
    """Own one client, scheduler, HTTP server, and persisted service identity."""

    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        client: Optional[AssistantClient] = None,
        state_path: Optional[Path] = None,
        auth_resolver: Optional[AuthResolver] = None,
    ) -> None:
        self._config = config or load_config()
        self._client = client
        self._state_path = state_path
        self._auth_resolver = auth_resolver
        self._scheduler: Optional[TokenScheduler] = None
        self._server: Optional[ManagedHTTPServer] = None
        self._state_owner: Optional[ServiceState] = None
        self._control_token: Optional[str] = None
        self._lifecycle = "new"
        self._client_closed = False
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._stopped.set()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._lifecycle == "running"

    @property
    def url(self) -> Optional[str]:
        with self._lock:
            if self._state_owner is not None:
                return self._state_owner.url
            return self._server.url if self._server is not None else None

    @property
    def control_token(self) -> Optional[str]:
        with self._lock:
            return self._control_token

    def _create_client(self) -> AssistantClient:
        auth = AuthManager.resolve(config=self._config)
        return AssistantClient(auth=auth, region=self._config.region)

    def start(self) -> None:
        """Start all owned components or roll every started component back."""
        with self._lock:
            if self._lifecycle == "running":
                return
            if self._lifecycle in {"stopping", "stopped"}:
                raise RuntimeError("Kirox service cannot be restarted after shutdown")

            logger.info("Starting Kirox service")
            self._lifecycle = "starting"
            self._stopped.clear()
            try:
                if self._client is None:
                    self._client = self._create_client()
                self._control_token = secrets.token_urlsafe(32)
                app = create_app(
                    self._config,
                    client=self._client,
                    shutdown_callback=self.stop,
                    control_token=self._control_token,
                )
                self._server = ManagedHTTPServer(
                    app,
                    host=self._config.server_host,
                    port=self._config.server_port,
                )
                self._scheduler = TokenScheduler(
                    self._client,
                    self._config,
                    on_token_refresh=self._on_token_refresh,
                    on_error=self._on_error,
                    resolver=self._auth_resolver,
                )
                self._scheduler.start()
                self._server.start()
                pid = os.getpid()
                owner = ServiceState(
                    pid=pid,
                    url=self._server.url,
                    started_at=time.time(),
                    control_token=self._control_token,
                    process_identity=capture_process_identity(pid),
                )
                self._state_owner = owner
                write_state(owner, self._state_path)
                self._lifecycle = "running"
            except BaseException:
                self._rollback_start()
                raise

            logger.info("Kirox service started on %s", self._state_owner.url)

    def _rollback_start(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.stop()
            except Exception:
                logger.exception("Failed to stop scheduler during startup rollback")
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                logger.exception("Failed to stop HTTP server during startup rollback")
        if self._state_owner is not None:
            try:
                clear_state(self._state_owner, self._state_path)
            except Exception:
                logger.exception("Failed to clear state during startup rollback")
        self._close_client_once()
        self._lifecycle = "stopped"
        self._stopped.set()

    def _close_client_once(self) -> None:
        if self._client is None or self._client_closed:
            return
        self._client_closed = True
        try:
            self._client.close()
        except Exception:
            logger.exception("Failed to close Kirox client")

    def stop(self) -> None:
        """Stop scheduler, server, state, and client in ownership order."""
        with self._lock:
            if self._lifecycle == "stopped":
                return
            self._lifecycle = "stopping"
            logger.info("Stopping Kirox service")
            try:
                if self._scheduler is not None:
                    try:
                        self._scheduler.stop()
                    except Exception:
                        logger.exception("Failed to stop token scheduler")
                if self._server is not None:
                    try:
                        self._server.stop()
                    except Exception:
                        logger.exception("Failed to stop HTTP server")
                if self._state_owner is not None:
                    try:
                        clear_state(self._state_owner, self._state_path)
                    except Exception:
                        logger.exception("Failed to clear service state")
                self._close_client_once()
            finally:
                self._lifecycle = "stopped"
                self._stopped.set()
                logger.info("Kirox service stopped")

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for shutdown and clean up if the HTTP serving thread exits."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            if deadline is None:
                interval = 0.1
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._stopped.is_set()
                interval = min(0.1, remaining)
            if self._stopped.wait(interval):
                return True
            with self._lock:
                server = self._server
                running = self._lifecycle == "running"
            if running and server is not None and server.wait(0):
                self.stop()

    def run(self) -> None:
        """Run until a signal, explicit stop, or unexpected server exit."""
        previous_handlers: dict[signal.Signals, Callable[..., object] | int | None] = {}

        def handler(signum: int, frame: Optional[FrameType]) -> None:
            del signum, frame
            self.stop()

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, handler)
        try:
            self.start()
            self.wait()
        finally:
            self.stop()
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)

    def _on_token_refresh(self) -> None:
        logger.debug("Token refreshed")

    def _on_error(self, error: Exception) -> None:
        logger.error("Service error: %s", error)


def main() -> None:
    from kirox.utils.logging import setup_logging

    config = load_config()
    log_file = Path(config.log_file).expanduser() if config.log_file else None
    setup_logging(level=config.log_level, log_file=log_file)
    KiroxService(config).run()
