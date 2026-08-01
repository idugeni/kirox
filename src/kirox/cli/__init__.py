"""CLI entry point."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any, Optional

from kirox._version import __version__

UPDATE_CHECK_INTERVAL = 3600
UPDATE_CACHE_FILE = Path.home() / ".kirox" / ".update_cache"
STOP_TIMEOUT = 5.0
STOP_POLL_INTERVAL = 0.1


def _get_latest_version() -> Optional[str]:
    try:
        import httpx

        response = httpx.get(
            "https://pypi.org/pypi/kirox/json",
            timeout=5,
            follow_redirects=True,
        )
        if response.status_code == 200:
            return response.json()["info"]["version"]
    except Exception:
        pass
    return None


def _should_check_update() -> bool:
    if not UPDATE_CACHE_FILE.exists():
        return True
    try:
        last_check = float(UPDATE_CACHE_FILE.read_text().strip())
        return (time.time() - last_check) > UPDATE_CHECK_INTERVAL
    except Exception:
        return True


def _update_cache() -> None:
    UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CACHE_FILE.write_text(str(time.time()))


def _check_update(silent: bool = False) -> Optional[str]:
    if not _should_check_update():
        return None
    latest = _get_latest_version()
    _update_cache()
    if latest and latest != __version__:
        if not silent:
            print(f"\n  Update available: {__version__} -> {latest}")
            print("  Run: pip install --upgrade kirox\n")
        return latest
    return None


def _do_update() -> None:
    print("Updating kirox...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "kirox"],
            check=True,
        )
        print("Update complete!")
    except subprocess.CalledProcessError as error:
        print(f"Update failed: {error}")


def cmd_run(args: argparse.Namespace) -> None:
    from kirox.service.daemon import KiroxService
    from kirox.utils.config import load_config
    from kirox.utils.logging import setup_logging

    config = load_config()
    log_file = Path(config.log_file).expanduser() if config.log_file else None
    setup_logging(
        level="DEBUG" if args.verbose else config.log_level,
        log_file=log_file,
    )
    if not args.no_update:
        threading.Thread(target=_check_update, daemon=True).start()

    service = KiroxService(config)
    try:
        service.start()
        print(f"Kirox v{__version__} running on {service.url}")
        print("Press Ctrl+C to stop\n")
        if args.no_tray:
            _wait_for_interrupt(service)
            return

        from kirox.service.tray import TRAY_UNAVAILABLE_MESSAGE, KiroTray

        if not KiroTray(config, service=service).start():
            print(TRAY_UNAVAILABLE_MESSAGE)
            _wait_for_interrupt(service)
    finally:
        service.stop()


def _wait_for_interrupt(service: Any) -> None:
    previous_handlers: dict[signal.Signals, Any] = {}

    def handler(signum: int, frame: Optional[FrameType]) -> None:
        del signum, frame
        print("\nStopping...")
        service.stop()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)
    try:
        service.wait()
    except KeyboardInterrupt:
        service.stop()
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def cmd_status(args: argparse.Namespace) -> None:
    del args
    import httpx

    from kirox.service.state import read_state

    latest = _check_update(silent=True)
    state = read_state()
    print(f"Kirox v{__version__}")
    if latest:
        print(f"Update available: {latest}")
    print()
    if state is None:
        print("Status:   STOPPED")
        return

    try:
        response = httpx.get(f"{state.url}/health", timeout=5)
        if response.status_code != 200:
            print("Status:   ERROR")
            return
        print("Status:   RUNNING")
        print(f"PID:      {state.pid}")
        print(f"URL:      {state.url}")
        token_response = httpx.get(f"{state.url}/api/token/status", timeout=5)
        if token_response.status_code == 200:
            data = token_response.json()
            print(f"Auth:     {'OK' if data.get('authenticated') else 'NO'}")
            print(f"Profile:  {'OK' if data.get('has_profile') else 'NO'}")
    except Exception:
        print("Status:   STOPPED")


def _wait_until_stopped(state: Any, timeout: float) -> bool:
    import httpx

    from kirox.service.process_identity import (
        ProcessIdentityUnavailable,
        capture_process_identity,
    )
    from kirox.service.state import clear_state, read_state

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        current = read_state()
        if current is None or current != state:
            return True
        try:
            httpx.get(f"{state.url}/health", timeout=0.5)
        except Exception:
            if state.process_identity is not None:
                try:
                    actual_identity = capture_process_identity(state.pid)
                except ProcessLookupError:
                    clear_state(state)
                    return True
                except (OSError, ProcessIdentityUnavailable):
                    pass
                else:
                    if actual_identity != state.process_identity:
                        clear_state(state)
                        return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(STOP_POLL_INTERVAL)


def _force_stop_pid(pid: int, process_identity: Any) -> None:
    from kirox.service.process_identity import terminate_process

    if type(pid) is not int or pid <= 0:
        raise ValueError("refusing to stop an invalid PID")
    if pid == os.getpid():
        raise RuntimeError("refusing to force-stop the current process")
    terminate_process(pid, process_identity)


def cmd_stop(args: argparse.Namespace) -> int:
    import httpx

    from kirox.service.server import CONTROL_SHUTDOWN_PATH, CONTROL_TOKEN_HEADER
    from kirox.service.state import clear_state, read_state

    state = read_state()
    if state is None:
        print("Kirox is not running")
        return 0

    print("Stopping kirox...")
    graceful_accepted = False
    try:
        response = httpx.post(
            f"{state.url}{CONTROL_SHUTDOWN_PATH}",
            headers={CONTROL_TOKEN_HEADER: state.control_token},
            timeout=5,
        )
        graceful_accepted = 200 <= response.status_code < 300
    except Exception:
        graceful_accepted = True

    if graceful_accepted and _wait_until_stopped(state, STOP_TIMEOUT):
        print("Stopped")
        return 0

    if not getattr(args, "force", False):
        print("Graceful stop failed; retry with --force", file=sys.stderr)
        return 1

    current = read_state()
    if current is None:
        print("Stopped")
        return 0
    if current != state:
        print("Service ownership changed; refusing to force-stop", file=sys.stderr)
        return 1
    if current.process_identity is None:
        print(
            "Force stop failed: service state has no verifiable process identity",
            file=sys.stderr,
        )
        return 1

    try:
        _force_stop_pid(current.pid, current.process_identity)
    except ProcessLookupError:
        clear_state(current)
        print("Stopped")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Force stop failed: {error}", file=sys.stderr)
        return 1

    if _wait_until_stopped(current, STOP_TIMEOUT):
        print("Stopped")
        return 0
    print("Force stop timed out", file=sys.stderr)
    return 1


def cmd_update(args: argparse.Namespace) -> None:
    latest = _get_latest_version()
    if latest == __version__:
        print(f"Already up to date (v{__version__})")
        return
    if latest:
        print(f"New version: {__version__} -> {latest}")
    if not args.yes and input("Update now? [y/N] ").strip().lower() != "y":
        print("Cancelled")
        return
    _do_update()


def cmd_models(args: argparse.Namespace) -> None:
    del args
    from kirox.core.client import AssistantClient

    client = AssistantClient.auto()
    try:
        models = client.list_models()
        print(f"{'Model ID':<25} {'Name':<20} {'Rate':>6} {'Thinking':>8}")
        print("-" * 65)
        for model in models:
            thinking = "yes" if model.supports_thinking else "-"
            print(
                f"{model.model_id:<25} {model.model_name:<20} "
                f"{model.rate_multiplier:>5.1f}x {thinking:>8}"
            )
    finally:
        client.close()


def cmd_chat(args: argparse.Namespace) -> None:
    from kirox.core.client import AssistantClient

    client = AssistantClient.auto()
    try:
        print(f"Kirox Chat (model: {args.model})\nType 'quit' to exit.\n")
        while True:
            try:
                message = input("You: ").strip()
            except EOFError:
                break
            if not message or message.lower() in ("quit", "exit", "q"):
                break
            print("AI: ", end="", flush=True)
            for event in client.chat(message, model_id=args.model):
                if event.content:
                    print(event.content, end="", flush=True)
            print("\n")
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        client.close()


def cmd_ask(args: argparse.Namespace) -> None:
    from kirox.core.client import AssistantClient

    client = AssistantClient.auto()
    try:
        message = args.message or sys.stdin.read()
        print(client.chat_simple(message, model_id=args.model))
    finally:
        client.close()


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kirox", description="Kirox — AI coding assistant SDK")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Start service + tray (default)")
    run_parser.add_argument("--no-tray", action="store_true")
    run_parser.add_argument("--no-update", action="store_true")
    run_parser.add_argument("-v", "--verbose", action="store_true")
    subparsers.add_parser("status", help="Check status")
    stop_parser = subparsers.add_parser("stop", help="Stop service")
    stop_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("models", help="List models")
    update_parser = subparsers.add_parser("update", help="Update to latest")
    update_parser.add_argument("-y", "--yes", action="store_true")
    chat_parser = subparsers.add_parser("chat", help="Interactive chat")
    chat_parser.add_argument("-m", "--model", default="auto")
    ask_parser = subparsers.add_parser("ask", help="One-shot question")
    ask_parser.add_argument("message", nargs="?")
    ask_parser.add_argument("-m", "--model", default="auto")
    return parser


def main(argv: Optional[list[str]] = None) -> Optional[int]:
    args = create_parser().parse_args(argv)
    if not args.command:
        args.command = "run"
        args.no_tray = False
        args.no_update = False
        args.verbose = False
    commands = {
        "run": cmd_run,
        "status": cmd_status,
        "stop": cmd_stop,
        "models": cmd_models,
        "chat": cmd_chat,
        "ask": cmd_ask,
        "update": cmd_update,
    }
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
