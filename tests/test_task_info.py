"""Optional metadata must survive assembly without changing the legacy description."""
import json
import re

import pytest

from test_panel_parity import server
from tools.make_fixtures import automation_info_case


@pytest.fixture
def payload(tmp_path, monkeypatch):
    case = automation_info_case()
    raw = {"tasks": [case["row"]], "generated": "synthetic"}
    monkeypatch.setattr(server, "run_ps", lambda *a, **k: (0, json.dumps(raw), ""))
    monkeypatch.setattr(server, "load_health", lambda: ({}, None))
    monkeypatch.setattr(server, "load_allowlist", lambda: (None, None))
    monkeypatch.setattr(server, "load_from_db", lambda: (
        {"tasks": {}, "available": False}, {"tasks": {}, "available": False}, None))
    path = tmp_path / "categories.json"
    monkeypatch.setenv("TASK_CONSOLE_CATEGORIES", str(path))

    def build(categories):
        path.write_text(json.dumps({"categories": categories}), encoding="utf-8")
        return server.build_payload()

    return build


def category(info):
    return {"name": "Synthetic", "tasks": ["AcmeSync"],
            "taskDesc": {"AcmeSync": "Override: legacy text"}, "taskInfo": info}


def row(data):
    return data["groups"][0]["rows"][0]


def test_valid_info_is_exposed_and_unknown_keys_are_dropped_and_named(payload):
    info = automation_info_case()["info"]
    data = payload([category({"AcmeSync": {**info, "extra": [1, 2]}})])
    assert row(data)["info"] == info
    assert "infoInvalid" not in row(data)
    assert row(data)["desc"] == "Override: legacy text"
    # A misspelt known key (asof, advise) lands here too, so it has to be said, not swallowed.
    assert data["warnings"] == ["分类配置里的 taskInfo 有不认识的字段,这些字段已忽略:AcmeSync(extra)。"]
    json.dumps(data)


def test_complete_info_raises_no_warning(payload):
    data = payload([category({"AcmeSync": automation_info_case()["info"]})])
    assert row(data)["info"] == automation_info_case()["info"]
    assert not data["warnings"]


@pytest.mark.parametrize("info", [{}, {"title": "Acme title"}, {"summary": ""}])
def test_partial_info_preserves_only_supplied_fields(payload, info):
    data = payload([category({"AcmeSync": info})])
    assert row(data)["info"] == info
    assert not data["warnings"]


def test_missing_info_and_uncategorized_task_are_not_dropped(payload):
    data = payload([])
    assert data["groups"][0]["cat"] == "未分类"
    assert row(data)["name"] == "AcmeSync"
    assert row(data)["info"] == {}
    assert "infoInvalid" not in row(data)
    assert row(data)["desc"] == "Legacy title: legacy summary"


def test_malformed_entries_are_skipped_named_and_marked_on_the_row(payload):
    data = payload([category({"AcmeSync": {"title": "discard me", "status": None},
                             "AcmeOther": [], "AcmeThird": "bad",
                             "AcmeValid": {"title": "valid", "extra": None}})])
    assert row(data)["info"] == {}
    # Broken is not missing: the row says so, and says what is broken, instead of rendering as not
    # filled in. The summary warning lives on the diagnostics page, which the automation tabs never show.
    assert row(data)["infoInvalid"] == "这些字段不是文字:status"
    assert data["warnings"] == [
        "分类配置里有 3 条 taskInfo 写法不对,已整条忽略:AcmeSync、AcmeOther、AcmeThird。",
        "分类配置里的 taskInfo 有不认识的字段,这些字段已忽略:AcmeValid(extra)。",
        "分类配置里有 3 条 taskInfo 对不上计划程序里的任何任务,没有显示:AcmeOther、AcmeThird、AcmeValid。",
    ]


def test_a_non_object_entry_says_so_on_its_row(payload):
    data = payload([category({"AcmeSync": ["not", "an", "object"]})])
    assert row(data)["infoInvalid"] == "整条不是一个对象"


def test_info_for_a_task_the_scheduler_does_not_have_is_named(payload):
    info = automation_info_case()["info"]
    data = payload([category({"AcmeSync": info, "AcmeSnyc": {"title": "typo"}})])
    assert row(data)["info"] == info
    assert data["warnings"] == ["分类配置里有 1 条 taskInfo 对不上计划程序里的任何任务,没有显示:AcmeSnyc。"]


@pytest.mark.parametrize("verdict", ["", "  "])
def test_an_empty_verdict_is_not_written_rather_than_unrecognized(payload, verdict):
    data = payload([category({"AcmeSync": {"title": "Acme title", "verdict": verdict}})])
    assert row(data)["info"] == {"title": "Acme title"}
    assert not data["warnings"]


@pytest.mark.parametrize("bad_map", [None, [], "bad", 0])
def test_non_object_map_is_one_invalid_entry(payload, bad_map):
    data = payload([category(bad_map)])
    assert row(data)["info"] == {}
    assert "infoInvalid" not in row(data)
    assert data["warnings"] == ["分类配置里有 1 条 taskInfo 写法不对,已整条忽略:Synthetic 的整张 taskInfo。"]


@pytest.mark.parametrize("verdict", ["future", "Urgent", "urgent ", "急修"])
def test_unknown_verdict_is_kept_as_unrecognized_and_warned(payload, verdict):
    data = payload([category({"AcmeSync": {"title": "Acme title", "verdict": verdict}})])
    assert row(data)["info"] == {"title": "Acme title", "verdictUnrecognized": verdict}
    assert data["warnings"] == [f"分类配置里有 1 个建议值认不出,页面上显示为「建议无法识别」:AcmeSync({verdict})。"]


@pytest.mark.parametrize("field", ["title", "summary", "cadence", "status", "advice", "verdict", "asOf"])
def test_every_recognized_field_requires_a_string(payload, field):
    data = payload([category({"AcmeSync": {"title": "discard me", field: False}})])
    assert row(data)["info"] == {}
    assert row(data)["infoInvalid"] == f"这些字段不是文字:{field}"
    assert data["warnings"] == ["分类配置里有 1 条 taskInfo 写法不对,已整条忽略:AcmeSync。"]


def test_metadata_replacement_and_warning_count_across_categories(payload):
    first = category({"AcmeSync": {"title": "first", "summary": "first summary"}, "AcmeBad": 0})
    second = {"name": "Other", "tasks": [], "taskInfo": {"AcmeSync": {"title": "second"}, "AcmeBad": None}}
    data = payload([first, second])
    assert row(data)["info"] == {"title": "second"}
    assert data["groups"][0]["cat"] == "Synthetic"
    assert row(data)["desc"] == "Override: legacy text"
    assert data["warnings"] == ["分类配置里有 2 条 taskInfo 写法不对,已整条忽略:AcmeBad。",
                                "分类配置里有 1 条 taskInfo 对不上计划程序里的任何任务,没有显示:AcmeBad。"]


def test_a_valid_entry_elsewhere_clears_the_invalid_marker_but_not_the_warning(payload):
    first = category({"AcmeSync": {"title": False}})
    second = {"name": "Other", "tasks": [], "taskInfo": {"AcmeSync": {"title": "second"}}}
    data = payload([first, second])
    assert row(data)["info"] == {"title": "second"}
    assert "infoInvalid" not in row(data)
    assert data["warnings"] == ["分类配置里有 1 条 taskInfo 写法不对,已整条忽略:AcmeSync。"]


@pytest.mark.parametrize("verdict", ["keep", "fix", "urgent", "adjust", "decide", "remove", "disabled"])
def test_all_declared_verdicts_survive(payload, verdict):
    assert row(payload([category({"AcmeSync": {"verdict": verdict}})]))["info"] == {"verdict": verdict}


def test_server_and_page_agree_on_the_verdict_values():
    # Two tables name the verdicts. A value added on one side only would pass the server and then
    # render as something else on the page, so the two are reconciled here rather than trusted.
    source = (server.HERE / "static" / "panels" / "tasks.js").read_text(encoding="utf-8")
    block = re.search(r"const TASK_VERDICTS = \{(.*?)\n\};", source, re.S).group(1)
    page = set(re.findall(r"^\s*(\w+):\{label:", block, re.M))
    assert page == set(server.TASK_INFO_VERDICTS)
