"""Tests for logging."""

import tempfile
from pathlib import Path
from kirox.utils.logging import setup_logging


def test_setup_logging():
    logger = setup_logging()
    assert logger.name == "kirox" and logger.level == 20


def test_verbose():
    assert setup_logging(verbose=True).level == 10
