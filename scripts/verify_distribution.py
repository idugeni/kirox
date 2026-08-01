"""Inspect a built wheel and smoke-test it in an isolated virtual environment."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_version() -> str:
    version_file = ROOT / "src" / "kirox" / "_version.py"
    spec = importlib.util.spec_from_file_location("kirox_build_version", version_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load version from {version_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = getattr(module, "__version__", None)
    if not isinstance(version, str) or not version:
        raise RuntimeError("Source version is missing or invalid")
    return version


def find_wheel(version: str, directory: Path) -> Path:
    matches = sorted(directory.glob(f"kirox-{version}-*.whl"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Kirox {version} wheel in {directory}, found {matches}")
    return matches[0]


def inspect_wheel(wheel: Path, version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        if "kirox/py.typed" not in names:
            raise RuntimeError("Wheel does not contain kirox/py.typed")

        entry_points_files = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(entry_points_files) != 1 or len(metadata_files) != 1:
            raise RuntimeError("Wheel metadata layout is invalid")

        entry_points = archive.read(entry_points_files[0]).decode("utf-8")
        expected_entries = {
            "kirox = kirox.cli:main",
            "kirox-mcp = kirox.mcp.server:main",
        }
        missing_entries = sorted(expected_entries - set(entry_points.splitlines()))
        if missing_entries:
            raise RuntimeError(f"Wheel is missing console scripts: {missing_entries}")

        metadata = archive.read(metadata_files[0]).decode("utf-8")
        if f"Version: {version}" not in metadata.splitlines():
            raise RuntimeError(f"Wheel metadata version does not match {version}")


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _venv_script(environment: Path, name: str) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / f"{name}.exe"
    return environment / "bin" / name


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def smoke_install(wheel: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="kirox-dist-check-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _venv_python(environment)
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(wheel.resolve()),
            ]
        )
        smoke_code = f"""
import importlib.metadata as metadata
import kirox
import kirox.mcp.server

assert kirox.__version__ == {version!r}
assert metadata.version("kirox") == {version!r}
entries = {{
    entry.name: entry
    for entry in metadata.entry_points(group="console_scripts")
    if entry.dist is not None and entry.dist.name.lower() == "kirox"
}}
assert {{"kirox", "kirox-mcp"}} <= set(entries)
assert callable(entries["kirox"].load())
assert callable(entries["kirox-mcp"].load())
"""
        _run([str(python), "-c", smoke_code])
        cli = _run([str(_venv_script(environment, "kirox")), "--version"])
        if cli.stdout.strip() != f"kirox {version}":
            raise RuntimeError(f"Unexpected CLI version output: {cli.stdout!r}")
        mcp = subprocess.run(
            [str(_venv_script(environment, "kirox-mcp"))],
            check=False,
            capture_output=True,
            text=True,
        )
        if mcp.returncode != 1 or "pip install" not in mcp.stderr:
            raise RuntimeError(f"Unexpected missing-MCP response: {mcp!r}")
        _run([str(python), "-m", "pip", "check"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--inspect-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    version = source_version()
    wheel = find_wheel(version, args.dist)
    inspect_wheel(wheel, version)
    if not args.inspect_only:
        smoke_install(wheel, version)
    print(f"verified {wheel.name}: version={version}, typed=yes, console_scripts=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
