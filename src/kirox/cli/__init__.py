"""CLI entry point."""

import argparse
import sys
import signal
import time
import subprocess
import threading
from pathlib import Path
from kirox._version import __version__


UPDATE_CHECK_INTERVAL = 3600
UPDATE_CACHE_FILE = Path.home() / ".kuro" / ".update_cache"


def _get_latest_version() -> str | None:
    try:
        import httpx
        resp = httpx.get("https://pypi.org/pypi/kirox/json", timeout=5, follow_redirects=True)
        if resp.status_code == 200:
            return resp.json()["info"]["version"]
    except Exception:
        pass
    return None


def _should_check_update() -> bool:
    if not UPDATE_CACHE_FILE.exists(): return True
    try:
        last_check = float(UPDATE_CACHE_FILE.read_text().strip())
        return (time.time() - last_check) > UPDATE_CHECK_INTERVAL
    except Exception:
        return True


def _update_cache():
    UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CACHE_FILE.write_text(str(time.time()))


def _check_update(silent: bool = False) -> str | None:
    if not _should_check_update(): return None
    latest = _get_latest_version()
    _update_cache()
    if latest and latest != __version__:
        if not silent:
            print(f"\n  Update available: {__version__} -> {latest}")
            print(f"  Run: pip install --upgrade kirox\n")
        return latest
    return None


def _do_update():
    print("Updating kirox...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "kirox"], check=True)
        print("Update complete!")
    except subprocess.CalledProcessError as e:
        print(f"Update failed: {e}")


def cmd_run(args):
    from kirox.utils.logging import setup_logging
    from kirox.utils.config import load_config
    setup_logging(level="DEBUG" if args.verbose else "INFO")
    config = load_config()
    if not args.no_update:
        threading.Thread(target=_check_update, daemon=True).start()
    from kirox.service.daemon import KuroService
    service = KuroService(config)
    service.start()
    print(f"Kirox v{__version__} running on {config.server_host}:{config.server_port}")
    print("Press Ctrl+C to stop\n")
    if not args.no_tray:
        try:
            from kirox.service.tray import KiroTray
            tray = KiroTray(config)
            tray._service = service
            tray.start()
        except ImportError:
            print("pystray not installed. Run: pip install kirox[service]")
            _wait_for_interrupt(service)
    else:
        _wait_for_interrupt(service)


def _wait_for_interrupt(service):
    def handler(sig, frame):
        print("\nStopping...")
        service.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    while True:
        try: time.sleep(1)
        except KeyboardInterrupt:
            service.stop()
            break


def cmd_status(args):
    import httpx
    from kirox.utils.config import load_config
    latest = _check_update(silent=True)
    config = load_config()
    url = f"http://{config.server_host}:{config.server_port}"
    print(f"Kirox v{__version__}")
    if latest: print(f"Update available: {latest}")
    print()
    try:
        resp = httpx.get(f"{url}/health", timeout=5)
        if resp.status_code == 200:
            print(f"Status:   RUNNING")
            print(f"URL:      {url}")
            resp = httpx.get(f"{url}/token/status", timeout=5)
            if resp.status_code == 200:
                d = resp.json()
                print(f"Auth:     {'OK' if d.get('authenticated') else 'NO'}")
                print(f"Profile:  {'OK' if d.get('has_profile') else 'NO'}")
        else:
            print(f"Status:   ERROR")
    except Exception:
        print(f"Status:   STOPPED")


def cmd_stop(args):
    print("Stopping kirox...")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "kirox.exe"], capture_output=True, check=False)
        print("Stopped")
    except Exception as e:
        print(f"Error: {e}")


def cmd_update(args):
    latest = _get_latest_version()
    if latest == __version__:
        print(f"Already up to date (v{__version__})")
        return
    if latest: print(f"New version: {__version__} -> {latest}")
    if not args.yes:
        if input("Update now? [y/N] ").strip().lower() != "y":
            print("Cancelled")
            return
    _do_update()


def cmd_models(args):
    from kirox.core.client import AssistantClient
    client = AssistantClient.auto()
    try:
        models = client.list_models()
        print(f"{'Model ID':<25} {'Name':<20} {'Rate':>6} {'Thinking':>8}")
        print("-" * 65)
        for m in models:
            t = "yes" if m.supports_thinking else "-"
            print(f"{m.model_id:<25} {m.model_name:<20} {m.rate_multiplier:>5.1f}x {t:>8}")
    finally:
        client.close()


def cmd_chat(args):
    from kirox.core.client import AssistantClient
    client = AssistantClient.auto()
    try:
        print(f"Kirox Chat (model: {args.model})\nType 'quit' to exit.\n")
        while True:
            try: msg = input("You: ").strip()
            except EOFError: break
            if not msg or msg.lower() in ("quit", "exit", "q"): break
            print("AI: ", end="", flush=True)
            for e in client.chat(msg, model_id=args.model):
                if e.content: print(e.content, end="", flush=True)
            print("\n")
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        client.close()


def cmd_ask(args):
    from kirox.core.client import AssistantClient
    client = AssistantClient.auto()
    try:
        msg = args.message or sys.stdin.read()
        print(client.chat_simple(msg, model_id=args.model))
    finally:
        client.close()


def create_parser():
    p = argparse.ArgumentParser(prog="kirox", description="Kirox — AI coding assistant SDK")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")
    run_p = sub.add_parser("run", help="Start service + tray (default)")
    run_p.add_argument("--no-tray", action="store_true")
    run_p.add_argument("--no-update", action="store_true")
    run_p.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("status", help="Check status")
    sub.add_parser("stop", help="Stop service")
    sub.add_parser("models", help="List models")
    update_p = sub.add_parser("update", help="Update to latest")
    update_p.add_argument("-y", "--yes", action="store_true")
    chat_p = sub.add_parser("chat", help="Interactive chat")
    chat_p.add_argument("-m", "--model", default="auto")
    ask_p = sub.add_parser("ask", help="One-shot question")
    ask_p.add_argument("message", nargs="?")
    ask_p.add_argument("-m", "--model", default="auto")
    return p


def main(argv=None):
    args = create_parser().parse_args(argv)
    if not args.command:
        args.command = "run"
        args.no_tray = False
        args.no_update = False
        args.verbose = False
    commands = {"run": cmd_run, "status": cmd_status, "stop": cmd_stop, "models": cmd_models, "chat": cmd_chat, "ask": cmd_ask, "update": cmd_update}
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
