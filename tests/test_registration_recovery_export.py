"""Recovery follows current authority and refuses missing or moving dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import export_registration_recovery as recovery
import make_fixtures


def test_current_closure_excludes_cleaned_history_and_is_deterministic(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    first = recovery.export(*paths)
    before = paths[-1].read_bytes()
    second = recovery.export(*paths)
    assert first == second and paths[-1].read_bytes() == before
    assert first["required_refs"] == 1 and first["pending_journals"] == 0
    with zipfile.ZipFile(paths[-1]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(archive.namelist()) == 4
        assert manifest["machine_bound"] is True
        assert len(manifest["protected_references"]) == 1


def test_unfinished_journal_adds_its_required_reference(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path, pending=True)
    result = recovery.export(*paths)
    assert result["required_refs"] == 2 and result["pending_journals"] == 1
    with zipfile.ZipFile(paths[-1]) as archive:
        assert any(name.startswith("state/journals/") for name in archive.namelist())


@pytest.mark.parametrize("pending", [False, True])
def test_missing_required_reference_preserves_previous_archive(tmp_path, pending):
    paths = make_fixtures.registration_recovery_case(tmp_path, pending=pending)
    recovery.export(*paths)
    before = paths[-1].read_bytes()
    obj = ("f" if pending else "d") * 32
    next(paths[2].glob("*-" + obj + ".cred")).unlink()
    with pytest.raises(FileNotFoundError):
        recovery.export(*paths)
    assert paths[-1].read_bytes() == before


def test_missing_cleaned_historical_reference_does_not_expand_closure(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    next(paths[2].glob("*-" + "f" * 32 + ".cred")).unlink()
    assert recovery.export(*paths)["required_refs"] == 1


def test_tampered_receipt_refuses_without_creating_output(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    receipt = next((paths[1] / "receipts").glob("*.json"))
    value = json.loads(receipt.read_text())
    value["input_reference"] = "invalid"
    receipt.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="does not match"):
        recovery.export(*paths)
    assert not paths[-1].exists()


def test_source_change_during_archive_write_preserves_previous_archive(tmp_path, monkeypatch):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    recovery.export(*paths)
    before = paths[-1].read_bytes()
    original = zipfile.ZipFile.writestr
    def changed(archive, info, data, *args, **kwargs):
        result = original(archive, info, data, *args, **kwargs)
        if info.filename == "manifest.json":
            (paths[1] / "current.json").write_bytes(b"{}")
        return result
    monkeypatch.setattr(zipfile.ZipFile, "writestr", changed)
    with pytest.raises(ValueError, match="changed before"):
        recovery.export(*paths)
    assert paths[-1].read_bytes() == before
    assert not list(tmp_path.glob("registration-*.zip"))


def test_relative_output_and_source_alias_are_refused(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    for output in (Path("relative.zip"), paths[1] / "export.zip"):
        with pytest.raises(ValueError):
            recovery.export(*paths[:3], output)


def test_busy_authority_refuses_before_capture(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    with recovery.authority_lock(paths[1]):
        with pytest.raises(OSError):
            recovery.export(*paths)
    assert not paths[-1].exists()


def test_empty_linked_journal_inventory_is_refused(tmp_path):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    journals = paths[1] / "journals"
    for path in journals.iterdir():
        path.unlink()
    journals.rmdir()
    target = tmp_path / "empty-journals"
    target.mkdir()
    try:
        journals.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(["cmd", "/c", "mklink", "/J", str(journals), str(target)],
                       check=True, capture_output=True)
    with pytest.raises(ValueError, match="Reparse|symbolic"):
        recovery.export(*paths)
    assert not paths[-1].exists()


def test_other_worktree_output_requires_private_visibility(tmp_path, monkeypatch):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    other = tmp_path / "other-worktree"
    (other / ".git").mkdir(parents=True)
    def refuse(path):
        assert path == other
        raise ValueError("PUBLIC or unknown destination")
    monkeypatch.setattr(recovery, "private_data_root", refuse)
    with pytest.raises(ValueError, match="PUBLIC or unknown"):
        recovery.export(*paths[:3], other / "recovery.zip")
    assert not (other / "recovery.zip").exists()


@pytest.mark.parametrize("fault", ["input-missing", "wrong-domain", "empty-ciphertext"])
def test_invalid_current_dependency_fails_closed(tmp_path, fault):
    paths = make_fixtures.registration_recovery_case(tmp_path)
    receipt_path = next((paths[1] / "receipts").glob("*.json"))
    receipt = json.loads(receipt_path.read_text())
    if fault == "input-missing":
        receipt.pop("input_reference")
    elif fault == "wrong-domain":
        receipt["input_reference"] = receipt["input_reference"].replace("b" * 32, "0" * 32)
    else:
        next(paths[2].glob("*-" + "d" * 32 + ".cred")).write_bytes(b"")
    receipt_path.write_bytes(recovery.encode(receipt))
    pointer = paths[1] / "current.json"
    value = json.loads(pointer.read_text())
    value["receipt_digest"] = recovery.digest(recovery.encode(receipt))
    pointer.write_bytes(recovery.encode(value))
    with pytest.raises(ValueError):
        recovery.export(*paths)
    assert not paths[-1].exists()
