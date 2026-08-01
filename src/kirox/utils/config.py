"""Configuration management."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    region: str = "us-east-1"
    server_port: int = 8420
    server_host: str = "127.0.0.1"
    auto_refresh: bool = True
    refresh_interval: int = 3000
    log_level: str = "INFO"
    log_file: Optional[str] = None
    token: Optional[str] = None
    profile_arn: Optional[str] = None
    db_path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_file(cls, path: Path) -> Config:
        if path.exists():
            with open(path) as f:
                return cls.from_dict(json.load(f))
        return cls()

    def to_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.__dict__, f, indent=2)


def load_config(config_path: Optional[Path] = None) -> Config:
    if config_path is None:
        config_path = Path.home() / ".kirox" / "config.json"
    config = Config.from_file(config_path)
    if os.environ.get("KIROX_TOKEN"):
        config.token = os.environ["KIROX_TOKEN"]
    if os.environ.get("KIROX_PROFILE_ARN"):
        config.profile_arn = os.environ["KIROX_PROFILE_ARN"]
    if os.environ.get("KIROX_REGION"):
        config.region = os.environ["KIROX_REGION"]
    return config
