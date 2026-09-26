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


@pytest.fixture(scope="session")
def reminder_source():
    configured = os.environ.get("TASK_CONSOLE_TEST_REMINDER_SOURCE")
    if not configured:
        pytest.fail("Set TASK_CONSOLE_TEST_REMINDER_SOURCE for owner integration tests")
    return Path(configured)


@pytest.fixture(scope="session")
def demand_source():
    configured = os.environ.get("TASK_CONSOLE_TEST_DEMAND_SOURCE")
    if not configured:
        pytest.fail("Set TASK_CONSOLE_TEST_DEMAND_SOURCE for owner integration tests")
    return Path(configured)
