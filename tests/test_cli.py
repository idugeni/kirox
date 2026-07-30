"""Tests for CLI."""

import pytest
from kirox.cli import create_parser


def test_parser_ask():
    args = create_parser().parse_args(["ask", "-m", "test", "hello"])
    assert args.model == "test" and args.message == "hello"


def test_parser_status():
    assert create_parser().parse_args(["status"]).command == "status"


def test_parser_models():
    assert create_parser().parse_args(["models"]).command == "models"


def test_parser_update():
    args = create_parser().parse_args(["update", "-y"])
    assert args.command == "update" and args.yes is True


def test_run_with_no_tray():
    args = create_parser().parse_args(["run", "--no-tray", "--no-update"])
    assert args.no_tray is True and args.no_update is True
