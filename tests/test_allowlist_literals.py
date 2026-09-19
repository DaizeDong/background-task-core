"""Literal parsing and retirement must agree without executing PowerShell."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "task_console"))
import allowlist as AL
import retire as R


@pytest.mark.parametrize("text, expected", [
    ("$TaskNames = @(\n 'AcmeA' # 'AcmeFake'\n 'AcmeB'\n)", {"AcmeA", "AcmeB"}),
    ('''$TaskNames = @('Acme#A', 'Acme''B', "Acme`\"C", 'Acme)D')''',
     {"Acme#A", "Acme'B", 'Acme"C', "Acme)D"}),
    ('''# $TaskNames = @('AcmeFake')\n$TaskNames = @("AcmeA")''', {"AcmeA"}),
    ('''$text = '$TaskNames = @(''AcmeFake'')'\n$TaskNames = @('AcmeA')''', {"AcmeA"}),
    ("$tasknames = @('AcmeA' <# 'AcmeFake' #>, 'AcmeB')", {"AcmeA", "AcmeB"}),
])
def test_only_literal_names_are_parsed(text, expected):
    names, reason = AL.parse_names(text)
    assert names == expected, reason


@pytest.mark.parametrize("text", [
    "# absent", "$TaskNames = @()", "$TaskNames = @('AcmeA'", "$TaskNames = @('unterminated)",
    '''$TaskNames = @('AcmeA', "$env:NAME")''',
    '''$TaskNames = @('AcmeA', "$(Get-Example)")''',
    "$TaskNames = @('AcmeA', (Get-Example))", "$TaskNames = @('AcmeA' + 'AcmeB')",
    "$TaskNames = @('AcmeA'); $TaskNames = @('AcmeB')",
    "$TaskNames = @('AcmeA') + @('AcmeB')", "$TaskNames = @('AcmeA',)",
])
def test_unchecked_input_never_becomes_a_partial_or_empty_allowlist(text):
    names, reason = AL.parse_names(text)
    assert names is None and reason


@pytest.mark.parametrize("body", [
    "'AcmeA', 'AcmeB', 'AcmeAB'",
    "\n 'AcmeB', 'AcmeA', # 'AcmeFake'\n 'AcmeAB'\n",
    '"AcmeA", "AcmeB", "AcmeAB"',
])
def test_retire_uses_the_reader_for_inline_and_commented_lists(tmp_path, monkeypatch, body):
    path = tmp_path / "names.ps1"
    path.write_text("# keep header\n$TaskNames = @(" + body + ")\n# keep trailer\n", encoding="utf-8")
    monkeypatch.setenv("TASK_CONSOLE_ALLOWLIST", str(path))
    monkeypatch.delenv("TASK_CONSOLE_HEALTH", raising=False)
    monkeypatch.setattr(R, "_task_state", lambda name: "Ready")
    steps = {s["step"]: s for s in R.plan("AcmeA", "synthetic retirement")["steps"]}
    assert steps["allowlist"]["state"] == "will-change"
    assert R._rewrite_allowlist(path, "AcmeA")
    result = path.read_text(encoding="utf-8")
    assert AL.parse_names(result) == ({"AcmeB", "AcmeAB"}, None)
    assert result.startswith("# keep header\n") and result.endswith("# keep trailer\n")
    assert not R._rewrite_allowlist(path, "AcmeA")


def test_malformed_allowlist_blocks_retirement_before_disable(tmp_path, monkeypatch):
    path = tmp_path / "names.ps1"
    original = "$TaskNames = @('AcmeA', (Get-Example))"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("TASK_CONSOLE_ALLOWLIST", str(path))
    monkeypatch.delenv("TASK_CONSOLE_HEALTH", raising=False)
    monkeypatch.setattr(R, "_task_state", lambda name: "Ready")
    assert "allowlist" in R.plan("AcmeA", "synthetic retirement")["blocked"]
    assert not R._rewrite_allowlist(path, "AcmeA")
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("suffix", ["; $TaskNames = Get-Example", "; $TaskNames += 'AcmeB'",
                                  "; $TaskNames[0] = 'AcmeB'", "; $TaskNames = @($other)"])
def test_later_nonliteral_mutation_does_not_leave_a_trusted_partial_list(suffix):
    names, reason = AL.parse_names("$TaskNames = @('AcmeA')" + suffix)
    assert names is None and reason


def test_an_unrelated_here_string_cannot_supply_a_fake_assignment():
    text = '$message = @"\none"quote\n$TaskNames = @(\'AcmeFake\')\n"@\n$TaskNames = @(\'AcmeA\')'
    assert AL.parse_names(text) == ({"AcmeA"}, None)


def test_removing_duplicates_and_the_last_name_preserves_comments():
    text = "$TaskNames = @('AcmeA', 'AcmeA' # Keep this comment\n)"
    rewritten, changed = AL.remove_name(text, "AcmeA")
    assert changed and AL.literal_names(rewritten) == []
    assert "# Keep this comment" in rewritten
    assert AL.parse_names(rewritten)[0] is None
    assert AL.remove_name(rewritten, "AcmeA") == (rewritten, False)


def test_server_reader_agrees_with_retirement_for_quoted_comments(tmp_path, monkeypatch):
    import server
    path = tmp_path / "names.ps1"
    path.write_text('$TaskNames = @(\n "AcmeA", # \'AcmeFake\'\n \'AcmeB\'\n)', encoding="utf-8")
    monkeypatch.setenv("TASK_CONSOLE_ALLOWLIST", str(path))
    assert server.load_allowlist() == ({"AcmeA", "AcmeB"}, None)
    assert R._rewrite_allowlist(path, "AcmeA")
    assert server.load_allowlist() == ({"AcmeB"}, None)
