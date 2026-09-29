"""Authenticated file operations against generated temporary sessions."""
import json
from urllib.parse import quote

import pytest

from tools.make_fixtures import synthetic_conversation
from test_console_security import srv, call, TOKEN


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    source, target = root / "C--Acme-source", root / "C--Acme-target"
    source.mkdir(parents=True)
    target.mkdir()
    for n in range(1, 47):
        sid, text = synthetic_conversation(n, cwd="C:/Acme/source")
        (source / (sid + ".jsonl")).write_text(text, encoding="utf-8")
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", str(root))
    monkeypatch.setenv("TASK_CONSOLE_CONVO_CACHE", str(tmp_path / "metadata.json"))
    return root, source, target, synthetic_conversation(1)[0]


def test_real_pagination_search_and_file_operations(srv, sessions):
    root, source, target, sid = sessions
    status, raw = call(srv, "GET", "/api/convos?limit=20", token=TOKEN)
    assert status == 200
    first = next(g for g in json.loads(raw)["groups"] if g["id"] == source.name)
    assert len(first["shown"]) == 20 and first["hasMore"]
    status, raw = call(srv, "GET", "/api/convos?limit=20&group=" + source.name + "&cursor=" + quote(first["nextCursor"]), token=TOKEN)
    assert status == 200
    second = json.loads(raw)["groups"][0]
    assert not {r["id"] for r in first["shown"]} & {r["id"] for r in second["shown"]}
    status, raw = call(srv, "POST", "/api/convo/rename", token=TOKEN,
                       body={"id": sid, "title": "合成重命名 🧪", "expectedProject": source.name})
    assert status == 200, raw
    renamed = (source / (sid + ".jsonl")).read_bytes()
    status, raw = call(srv, "POST", "/api/convo/move", token=TOKEN,
                       body={"id": sid, "targetProject": target.name, "expectedProject": source.name})
    assert status == 200, raw
    assert (target / (sid + ".jsonl")).read_bytes() == renamed
    assert not (source / (sid + ".jsonl")).exists()
    status, raw = call(srv, "POST", "/api/convo/move", token=TOKEN,
                       body={"id": sid, "targetProject": target.name, "expectedProject": source.name})
    assert status == 200 and json.loads(raw)["unchanged"]
    status, raw = call(srv, "GET", "/api/convos?q=" + quote("合成重命名"), token=TOKEN)
    assert status == 200 and json.loads(raw)["summary"]["matched"] == 1


@pytest.mark.parametrize("path,body", [
    ("/api/convo/rename", {"id": "../outside", "title": "Example"}),
    ("/api/convo/move", {"id": "../outside", "targetProject": "../outside"})])
def test_edits_auth_host_and_shape_gates(srv, sessions, path, body):
    assert call(srv, "POST", path, body=body)[0] == 403
    assert call(srv, "POST", path, host="outside.example", token=TOKEN, body=body)[0] == 400
    status, raw = call(srv, "POST", path, token=TOKEN, body=body)
    assert status == 400 and json.loads(raw)["code"] == "bad_id"


def test_read_only_and_stale_source_cannot_change_a_session(srv, sessions, monkeypatch):
    root, source, target, sid = sessions
    body = {"id": sid, "title": "Example name", "expectedProject": "C--Acme-other"}
    status, raw = call(srv, "POST", "/api/convo/rename", token=TOKEN, body=body)
    assert status == 409 and json.loads(raw)["code"] == "conflict"
    monkeypatch.setenv("TASK_CONSOLE_READ_ONLY", "1")
    assert call(srv, "POST", "/api/convo/rename", token=TOKEN, body=body)[0] == 403


def delete_body(srv, sessions):
    _, source, _, sid = sessions
    body = {"id": sid, "expectedProject": source.name}
    status, raw = call(srv, "POST", "/api/convo/delete-plan", token=TOKEN, body=body)
    assert status == 200, raw
    return {**body, "fingerprint": json.loads(raw)["fingerprint"],
            "requestId": "00000001-0000-4000-8000-000000000099", "confirmed": True}


def test_delete_requires_confirmation_then_removes_only_one_session(srv, sessions):
    _, source, _, sid = sessions
    body = delete_body(srv, sessions)
    for confirmed in (None, False, "true", 1):
        status, raw = call(srv, "POST", "/api/convo/delete", token=TOKEN, body={**body, "confirmed": confirmed})
        assert status == 400 and json.loads(raw)["code"] == "confirmation_required"
    assert len(list(source.glob("*.jsonl"))) == 46
    status, raw = call(srv, "POST", "/api/convo/delete", token=TOKEN, body=body)
    assert status == 200 and json.loads(raw)["deleted"] is True
    assert not (source / (sid + ".jsonl")).exists() and len(list(source.glob("*.jsonl"))) == 45
    status, raw = call(srv, "POST", "/api/convo/delete", token=TOKEN, body=body)
    assert status == 200 and json.loads(raw)["unchanged"] is True


def test_delete_stale_preview_preserves_session(srv, sessions):
    _, source, _, sid = sessions
    body = delete_body(srv, sessions)
    path = source / (sid + ".jsonl")
    with path.open("ab") as stream:
        stream.write(b"\n")
    status, raw = call(srv, "POST", "/api/convo/delete", token=TOKEN, body=body)
    assert status == 409 and json.loads(raw)["code"] == "conflict" and path.exists()


@pytest.mark.parametrize("route", ["delete-plan", "delete"])
def test_delete_auth_host_readonly_and_unknown_keys(srv, sessions, monkeypatch, route):
    body = delete_body(srv, sessions)
    if route == "delete-plan":
        body = {k: body[k] for k in ("id", "expectedProject")}
    url = "/api/convo/" + route
    assert call(srv, "POST", url, body=body)[0] == 403
    assert call(srv, "POST", url, host="outside.example", token=TOKEN, body=body)[0] == 400
    status, raw = call(srv, "POST", url, token=TOKEN, body={**body, "surprise": True})
    assert status == 400 and json.loads(raw)["code"] == "bad_body"
    monkeypatch.setenv("TASK_CONSOLE_READ_ONLY", "1")
    assert call(srv, "POST", url, token=TOKEN, body=body)[0] == 403
