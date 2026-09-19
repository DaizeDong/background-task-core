"""Regression evidence for T11 review findings; every payload is synthetic data."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
from make_fixtures import example_request, installation_request, powershell_repair_cases
from task_console import allowlist
from task_console.compiler import plan
from task_console.contracts import ContractError


def test_recommended_timeout_survives_effective_override_and_plan():
    request = example_request()
    request["machine"]["overrides"]["acme-maintenance/sync"] = {"timeout_seconds": 0}
    result = plan(request)
    assert result["task_specs"][0].get("recommended_timeout_seconds") == 300
    assert result["task_specs"][0]["timeout_seconds"] == 0
    request["components"][0]["tasks"][0]["timeout_seconds"] = 600
    changed = plan(request)
    assert changed["task_specs"][0]["recommended_timeout_seconds"] == 600
    assert changed["task_specs"][0]["timeout_seconds"] == 0
    assert changed["changes"] == result["changes"]


@pytest.mark.parametrize("case,text", powershell_repair_cases().items())
def test_powershell_literals_are_complete_or_explicitly_unreadable(case, text):
    if case == "read_reference" or (case.startswith("here_") and not case.startswith("here_mutation_")):
        assert allowlist.parse_names(text) == ({"AcmeA"}, None)
        rewritten, changed = allowlist.remove_name(text, "AcmeA")
        assert changed and allowlist.literal_names(rewritten) == []
    else:
        names, reason = allowlist.parse_names(text)
        assert names is None and reason
        assert reason == "Nonliteral TaskNames assignment or mutation"
        assert allowlist.remove_name(text, "AcmeA") == (text, False)


@pytest.mark.parametrize("suffix", ["; ${OtherTaskNames} += 'AcmeB'", "; ${TaskNamesBackup} = 'AcmeB'",
                                     " # ${TaskNames} += 'AcmeB'", "; $text = '${TaskNames} += AcmeB'"])
def test_unrelated_or_quoted_braced_variables_do_not_block(suffix):
    assert allowlist.parse_names("$TaskNames = @('AcmeA')" + suffix) == ({"AcmeA"}, None)


def test_checked_empty_scheduler_snapshot_proposes_creation_without_authority():
    request = example_request()
    request["baseline"]["tasks"] = []
    result = plan(request)
    assert result["parity"]["scheduler"]["status"] == "different"
    assert result["parity"]["scheduler"]["missing"] == []
    assert result["parity"]["scheduler"]["absent_tasks"] == 1
    assert result["changes"] == [{
        "kind": "scheduler-proposal", "operation": "create", "task_id": "acme-maintenance/sync",
        "name": "AcmeSync", "before": None, "after": result["task_specs"][0], "blocked": True,
        "reason_code": "task_absent_requires_review",
    }]
    assert result["applicable"] is False
    assert result["authority"] == {"mode": "legacy", "epoch": 0, "migrated_tasks": []}
    assert all(f["replacement_safe"] is False for f in result["generated_files"].values())


@pytest.mark.parametrize("source", [None, "omit"])
def test_unchecked_scheduler_does_not_propose_creation(source):
    request = example_request()
    request["baseline"]["tasks"] = source
    if source == "omit":
        del request["baseline"]["tasks"]
    result = plan(request)
    assert result["parity"]["scheduler"] == {"status": "not-checked", "reason_code": "missing_source"}
    assert result["changes"] == []


def test_private_namespaces_preserve_identical_public_ids_and_structured_identity():
    request = installation_request()
    before = deepcopy(request)
    result = plan(request)
    tasks = result["task_specs"]
    assert [t["task_id"] for t in tasks] == ["acme-user/acme-maintenance/sync", "acme-project/acme-maintenance/sync"]
    assert [t["component"] for t in tasks] == ["acme-maintenance", "acme-maintenance"]
    assert [t["installation_namespace"] for t in tasks] == ["acme-user", "acme-project"]
    for task in tasks:
        assert task["installation_identity"] == request["bindings"]["installations"][task["installation_namespace"]]["identity"]
        assert isinstance(task["installation_identity"]["metadata"]["origin"], dict)
    assert request == before
    assert json.loads(json.dumps(result)) == result
    rows = json.loads(result["generated_files"]["task-health.json"]["content"])["tasks"]
    assert {r["task_id"] for r in rows} == {t["task_id"] for t in tasks}
    assert {c["task_id"] for c in result["check_coverage"]["checks"]} == {t["task_id"] for t in tasks}
    assert result["generated_files"]["TaskNames.ps1"]["owners"] == [t["task_id"] for t in tasks]
    assert "selected_version" not in result["generated_files"]["task-health.json"]["content"]


@pytest.mark.parametrize("field,value", [("marketplace", "acme-other"), ("scope", "project"),
                                        ("client", "acme-other"), ("source_id", "acme-other")])
def test_each_catalog_identity_dimension_keeps_installations_distinct(field, value):
    request = installation_request()
    identities = request["bindings"]["installations"]
    identities["acme-project"]["identity"] = deepcopy(identities["acme-user"]["identity"])
    identities["acme-project"]["identity"][field] = value
    assert len(plan(request)["task_specs"]) == 2


@pytest.mark.parametrize("mutation,code", [
    (lambda r: r["bindings"]["installations"].pop("acme-user"), "missing_installation"),
    (lambda r: r["components"][1].update(namespace="acme-user"), "duplicate_namespace"),
    (lambda r: r["bindings"]["installations"]["acme-user"].update(component="acme-other"), "installation_component_mismatch"),
    (lambda r: r["bindings"]["installations"]["acme-user"]["identity"].pop("scope"), "missing_field"),
    (lambda r: r["bindings"]["installations"]["acme-user"]["identity"].update(client={"private": "not-a-client"}), "invalid_type"),
    (lambda r: r["bindings"]["installations"]["acme-project"].update(identity=deepcopy(r["bindings"]["installations"]["acme-user"]["identity"])), "duplicate_installation_identity"),
    (lambda r: r["bindings"]["installations"].update(unused=deepcopy(r["bindings"]["installations"]["acme-user"])), "unused_installation"),
])
def test_ambiguous_installations_fail_closed_without_echoing_metadata(mutation, code):
    request = installation_request()
    mutation(request)
    with pytest.raises(ContractError) as error:
        plan(request)
    assert error.value.code == code
    assert "not-a-client" not in json.dumps(error.value.to_dict())


def test_legacy_component_id_stays_compatible_and_duplicate_manifest_stays_ambiguous():
    request = example_request()
    assert plan(request)["task_specs"][0]["task_id"] == "acme-maintenance/sync"
    request["components"].append(deepcopy(request["components"][0]))
    with pytest.raises(ContractError) as error:
        plan(request)
    assert error.value.code == "duplicate_component"


def test_mixing_unqualified_and_installed_same_component_requires_explicit_identity():
    request = installation_request()
    legacy = example_request()
    request["components"].extend(legacy["components"])
    request["bindings"]["tasks"].update(legacy["bindings"]["tasks"])
    with pytest.raises(ContractError) as error:
        plan(request)
    assert error.value.code == "ambiguous_component"


def test_unqualified_binding_never_falls_back_for_namespaced_task():
    request = installation_request()
    request["bindings"]["tasks"]["acme-maintenance/sync"] = request["bindings"]["tasks"].pop("acme-user/acme-maintenance/sync")
    with pytest.raises(ContractError) as error:
        plan(request)
    assert error.value.code == "missing_binding"


def test_installation_specific_manifests_and_overrides_keep_their_own_values():
    request = installation_request()
    request["components"][1]["manifest"]["tasks"][0]["timeout_seconds"] = 900
    request["machine"]["overrides"]["acme-project/acme-maintenance/sync"] = {"timeout_seconds": 1200}
    result = plan(request)
    assert [(t["recommended_timeout_seconds"], t["timeout_seconds"]) for t in result["task_specs"]] == [(300, 420), (900, 1200)]


def test_direct_compiler_rejects_non_json_identity_metadata():
    from task_console.components import compile_tasks
    request = installation_request()
    request["bindings"]["installations"]["acme-user"]["identity"]["metadata"] = {"unsupported": {1, 2}}
    with pytest.raises(ContractError) as error:
        compile_tasks(request["components"], request["bindings"], request["machine"])
    assert error.value.code == "invalid_json"


def test_same_identity_with_different_revision_is_still_ambiguous():
    request = installation_request()
    identities = request["bindings"]["installations"]
    identities["acme-project"]["identity"] = deepcopy(identities["acme-user"]["identity"])
    identities["acme-project"]["identity"]["metadata"]["selected_version"] = "2.0"
    with pytest.raises(ContractError) as error:
        plan(request)
    assert error.value.code == "duplicate_installation_identity"


def test_incomplete_existing_scheduler_row_is_not_absence():
    request = example_request()
    request["baseline"]["tasks"] = [{"name": "AcmeSync"}]
    result = plan(request)
    assert result["parity"]["scheduler"]["status"] == "not-checked"
    assert result["parity"]["scheduler"]["absent_tasks"] == 0
    assert result["changes"] == []
