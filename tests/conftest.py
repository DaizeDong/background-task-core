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


OWNER_SOURCE_FIXTURES = frozenset({"config_source", "reminder_source", "demand_source"})


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "owner_integration: needs a private owner source (TASK_CONSOLE_TEST_*_SOURCE); "
        "never available on a hosted runner")


def pytest_collection_modifyitems(config, items):
    # The marker is derived from the fixtures a test actually requests, not written by hand on
    # each test, so a new test that binds a private source cannot forget it. It only labels: a run
    # without `-m "not owner_integration"` still fails those tests loudly when the source is unset.
    for item in items:
        if OWNER_SOURCE_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.owner_integration)
