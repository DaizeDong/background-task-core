"""Storage ownership stays unique without admitting unreviewed dated paths."""
import fnmatch
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


def namespace_owners(contract, relative):
    """Check the recursive namespace subset without duplicating a path matcher."""
    root = relative.split("/", 1)[0]
    found = []
    for row in contract["artifacts"]:
        namespace, _, descendant_pattern = row["path_pattern"].partition("/")
        if fnmatch.fnmatchcase(root, namespace):
            assert descendant_pattern == "**", "Namespace ownership must include all descendants"
            found.append(row)
    return found


def test_dated_declarations_have_one_core_owner(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    for relative in case["declarations"]:
        for path in (relative, relative.split("/", 1)[0]):
            found = namespace_owners(contract, path)
            assert [row["artifact_id"] for row in found] == ["declarations"]
            assert found[0]["retention_rule"]["class"] == "core"


def test_reviewed_legacy_namespaces_keep_one_retired_owner(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    for relative in case["retired"]:
        for path in (relative, relative.split("/", 1)[0]):
            found = namespace_owners(contract, path)
            assert len(found) == 1
            assert found[0]["retention_rule"]["class"] == "retired"


def test_unreviewed_namespaces_stay_undeclared(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    for relative in case["unknown"]:
        assert namespace_owners(contract, relative) == []


def test_legacy_scopes_preserve_the_total_review_budget(tmp_path, contract):
    case = make_fixtures.storage_contract_case(tmp_path)
    scopes = {row["artifact_id"]: row for relative in case["retired"]
              for row in namespace_owners(contract, relative)}
    assert len(scopes) == len(case["retired"])
    assert sum(row["max_bytes"] for row in scopes.values()) == 16 * 1024 * 1024
    larger = {identifier for identifier, row in scopes.items()
              if row["max_bytes"] == 4 * 1024 * 1024}
    assert larger == {"legacy-console-repair", "legacy-convo-chain-inspection"}
    assert all(row["max_bytes"] == 1024 * 1024
               for identifier, row in scopes.items() if identifier not in larger)
