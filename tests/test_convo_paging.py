"""Pagination finds older conversations without moving the first-page boundary."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "task_console"))
from tools.make_fixtures import synthetic_conversation
import convos


def populate(root, count=95):
    project = root / "C--Acme-project"
    project.mkdir()
    for number in range(1, count + 1):
        sid, body = synthetic_conversation(number, title=f"Example {number}")
        path = project / f"{sid}.jsonl"
        path.write_text(body, encoding="utf-8")
        os.utime(path, (1_800_000_000, 1_800_000_000))
    return project


def test_cursor_loads_every_equal_timestamp_session_once(tmp_path):
    populate(tmp_path)
    page = convos.scan(root=str(tmp_path), limit_per_group=17)
    group = page["groups"][0]
    found = [row["id"] for row in group["shown"]]
    assert group["hasMore"] and group["nextCursor"]
    while group["nextCursor"]:
        page = convos.scan(root=str(tmp_path), limit_per_group=17,
                          group=group["id"], cursor=group["nextCursor"])
        group = page["groups"][0]
        found.extend(row["id"] for row in group["shown"])
    assert len(found) == len(set(found)) == 95
    assert group["hasMore"] is False


def test_search_happens_before_page_limit(tmp_path):
    populate(tmp_path)
    result = convos.scan(root=str(tmp_path), query="Example 95", limit_per_group=10)
    assert [r["title"] for g in result["groups"] for r in g["shown"]] == ["Example 95"]
    assert result["summary"]["matched"] == 1
    assert result["summary"]["files"] == 95


def test_cursor_is_bound_to_filters_and_project(tmp_path):
    populate(tmp_path, 4)
    group = convos.scan(root=str(tmp_path), limit_per_group=2)["groups"][0]
    with pytest.raises(ValueError, match="cursor"):
        convos.scan(root=str(tmp_path), group=group["id"], cursor=group["nextCursor"], query="different")
    with pytest.raises(ValueError, match="cursor"):
        convos.scan(root=str(tmp_path), group=group["id"], cursor="not-a-cursor")


def test_storage_groups_do_not_reclassify_a_fork_from_its_first_retained_cwd(tmp_path):
    project = populate(tmp_path, 1)
    sid, body = synthetic_conversation(2, cwd="C:/Acme/project/nested")
    (project / f"{sid}.jsonl").write_text(body, encoding="utf-8")
    result = convos.scan(root=str(tmp_path))
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["id"] == project.name and group["cwd"] == "C:/Acme/project"
    assert all(row["storageDir"] == project.name for row in group["shown"])
    assert "C:/Acme/project/nested" in {row["sourceCwd"] for row in group["shown"]}


def test_empty_project_remains_a_move_destination(tmp_path):
    populate(tmp_path, 1)
    empty = tmp_path / "C--Acme-empty"
    empty.mkdir()
    result = convos.scan(root=str(tmp_path), query="no matches")
    assert not result["groups"]
    assert {r["id"] for r in result["locations"]} == {"C--Acme-project", empty.name}


def test_project_page_does_not_read_other_projects_or_discard_their_cache(tmp_path, monkeypatch):
    project = populate(tmp_path, 4)
    other = tmp_path / "C--Acme-other"
    other.mkdir()
    sid, body = synthetic_conversation(999, cwd="C:/Acme/other", title="Other example")
    other_file = other / f"{sid}.jsonl"
    other_file.write_text(body, encoding="utf-8")
    cache = tmp_path / "metadata-cache.json"
    initial = convos.scan(root=str(tmp_path), cache=str(cache), limit_per_group=2)
    group = next(g for g in initial["groups"] if g["id"] == project.name)
    saved_other = json.loads(cache.read_text(encoding="utf-8"))[str(other_file)]
    original_info = convos.project_info

    def current_project_only(directory, **kwargs):
        assert Path(directory) == project, "a page request inspected an unrelated project"
        return original_info(directory, **kwargs)

    monkeypatch.setattr(convos, "project_info", current_project_only)
    page = convos.scan(root=str(tmp_path), cache=str(cache), group=project.name,
                      cursor=group["nextCursor"], limit_per_group=2)
    assert page["summary"]["scope"] == "project"
    assert page["summary"]["files"] == 4
    assert len(page["groups"][0]["shown"]) == 2
    assert json.loads(cache.read_text(encoding="utf-8"))[str(other_file)] == saved_other
