"""Storage owners protect current state without admitting unreviewed copies."""
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import make_fixtures


@pytest.fixture
def contract():
    return json.loads((REPO / "storage.contract.json").read_text(encoding="utf-8"))


def storage_owners(contract, root, relative):
    """Resolve generated paths with pathlib rather than copying the shared matcher."""
    target = root / relative
    found = []
    for row in contract["artifacts"]:
        pattern = row["path_pattern"]
        if pattern.endswith("/**"):
            candidates = root.glob(pattern[:-3])
            matched = any(target == path or path in target.parents for path in candidates)
        else:
            matched = target in root.glob(pattern)
        if matched:
            found.append(row)
    return found


def test_dated_declarations_have_one_core_owner(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    for relative in case["declarations"]:
        for path in (relative, "/".join(relative.split("/")[:2])):
            found = storage_owners(contract, tmp_path, path)
            assert [row["artifact_id"] for row in found] == ["declarations"]
            assert found[0]["retention_rule"]["class"] == "core"


@pytest.mark.parametrize("category", ["retired", "core", "rebuildable"])
def test_reviewed_artifacts_have_one_expected_owner(tmp_path, contract, category):
    case = make_fixtures.storage_contract_case(tmp_path)
    for relative in case[category]:
        found = storage_owners(contract, tmp_path, relative)
        assert len(found) == 1, relative
        assert found[0]["retention_rule"]["class"] == category


def test_unreviewed_namespaces_stay_undeclared(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    for relative in case["unknown"]:
        assert storage_owners(contract, tmp_path, relative) == [], relative


def test_legacy_scopes_preserve_the_total_review_budget(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    scopes = {row["artifact_id"]: row for relative in case["legacy_scopes"]
              for row in storage_owners(contract, tmp_path, relative)}
    assert len(scopes) == len(case["legacy_scopes"])
    assert sum(row["max_bytes"] for row in scopes.values()) == 16 * 1024 * 1024
    larger = {identifier for identifier, row in scopes.items()
              if row["max_bytes"] == 4 * 1024 * 1024}
    assert larger == {"legacy-console-repair", "legacy-convo-chain-inspection"}
    assert all(row["max_bytes"] == 1024 * 1024
               for identifier, row in scopes.items() if identifier not in larger)


def test_recovery_archive_and_receipt_are_exact_protected_core(tmp_path, contract):
    make_fixtures.storage_contract_case(tmp_path)
    protected = set(contract["protected_paths"])
    for suffix in ("", "-wal", "-shm", "-journal"):
        assert "data/task-console/console.sqlite3" + suffix in protected
    for relative in ("data/task-console/recovery/current.zip",
                     "data/task-console/recovery/current.json"):
        assert relative in protected
        assert storage_owners(contract, tmp_path, relative)[0]["retention_rule"]["class"] == "core"
    assert "data/task-console/**" not in protected
    assert "data/task-console/recovery/**" not in {
        row["path_pattern"] for row in contract["artifacts"]}
