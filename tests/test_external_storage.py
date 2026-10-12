"""Native defaults never recreate retired home directories; writes prove PRIVATE storage."""
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task_console"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import console_store
import llmstats
import server
import task_delete
from tools.make_fixtures import external_categories_case


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for key in ("TASK_CONSOLE_LLMCALL_LEDGER", "TASK_CONSOLE_LLMCALL_CHAIN", "LLMCALL_CHAIN",
                "LLMCALL_LEDGER", "LLMCALL_DATA_DIR", "LLMCALL_CONFIG", "TASK_CONSOLE_CATEGORIES"):
        monkeypatch.delenv(key, raising=False)


def rejected(*args, **kwargs):
    raise ValueError("synthetic PRIVATE proof refusal")


def test_unconfigured_reads_and_write_refusal_do_not_touch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert llmstats.ledger_path() is None
    assert llmstats.chain_file_path() is None
    records, meta = llmstats.read()
    assert records == [] and meta["read_error"] and meta["path"] is None
    assert llmstats.chain_config()["file_path"] is None
    with pytest.raises(llmstats.Refused) as error:
        llmstats.write_chain(["cc"])
    assert error.value.code == "storage_unconfigured"
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("selector,suffix", [("LLMCALL_CONFIG", "data/runtime"),
                                             ("LLMCALL_DATA_DIR", "runtime")])
def test_producer_storage_selectors(monkeypatch, tmp_path, selector, suffix):
    monkeypatch.setenv(selector, str(tmp_path))
    assert llmstats.ledger_path() == tmp_path / suffix / "ledger.jsonl"
    assert llmstats.chain_file_path() == tmp_path / suffix / "chain.txt"


def test_producer_ledger_override_does_not_redirect_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMCALL_CONFIG", str(tmp_path / "companion"))
    monkeypatch.setenv("LLMCALL_LEDGER", str(tmp_path / "custom" / "ledger.jsonl"))
    assert llmstats.ledger_path() == tmp_path / "custom" / "ledger.jsonl"
    assert llmstats.chain_file_path() == tmp_path / "companion" / "data" / "runtime" / "chain.txt"
    monkeypatch.setenv("TASK_CONSOLE_LLMCALL_LEDGER", str(tmp_path / "console" / "ledger.jsonl"))
    assert llmstats.chain_file_path() == tmp_path / "console" / "chain.txt"


@pytest.mark.parametrize("value", ["", "0", "off", "false"])
def test_producer_ledger_disable(monkeypatch, value):
    monkeypatch.setenv("LLMCALL_LEDGER", value)
    assert llmstats.ledger_path() is None


def test_chain_write_rejects_unproven_storage_before_mkdir(monkeypatch, tmp_path):
    path = tmp_path / "refused" / "chain.txt"
    monkeypatch.setenv("TASK_CONSOLE_LLMCALL_CHAIN", str(path))
    monkeypatch.setattr(console_store, "_load_storage", lambda: SimpleNamespace(prove_private_companion=rejected))
    with pytest.raises(llmstats.Refused) as error:
        llmstats.write_chain(["cc"])
    assert error.value.code == "storage_refused"
    assert not path.parent.exists()


def test_chain_write_admitted_by_private_proof(monkeypatch, tmp_path):
    path = tmp_path / "private" / "chain.txt"
    monkeypatch.setenv("TASK_CONSOLE_LLMCALL_CHAIN", str(path))
    monkeypatch.setattr(console_store, "_load_storage", lambda: SimpleNamespace(
        prove_private_companion=lambda parent: SimpleNamespace(root=tmp_path)))
    assert llmstats.write_chain(["cc"])["effective"] == ("cc",)
    assert path.read_bytes() == b"cc\n"


def test_categories_use_companion_or_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setattr(console_store, "companion_root", lambda: tmp_path)
    assert server.categories_path() == tmp_path / "categories.json"
    monkeypatch.setattr(console_store, "companion_root", lambda: None)
    assert server.categories_path() is None
    selected = tmp_path / "explicit" / "categories.json"
    monkeypatch.setenv("TASK_CONSOLE_CATEGORIES", str(selected))
    assert server.categories_path() == selected


def test_category_mutation_rechecks_private_proof(monkeypatch, tmp_path):
    path = external_categories_case(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(console_store, "authorize_private_write", rejected)
    assert task_delete._inspect_categories(path, "AcmeSync")["editable"] is False
    result = task_delete._edit_categories(path, "AcmeSync", hashlib.sha256(before).hexdigest())
    assert result["status"] == "failed"
    assert path.read_bytes() == before
