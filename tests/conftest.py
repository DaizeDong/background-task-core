"""Explicit source bindings for cross-owner integration checks."""
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def config_source():
    configured = os.environ.get("TASK_CONSOLE_TEST_CONFIG_SOURCE")
    if not configured:
        pytest.fail("Set TASK_CONSOLE_TEST_CONFIG_SOURCE for owner integration tests")
    return Path(configured)
