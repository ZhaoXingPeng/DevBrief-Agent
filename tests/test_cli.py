from __future__ import annotations

import sys

from pytest import CaptureFixture, MonkeyPatch

from devbrief import cli


def test_cli_returns_two_for_user_facing_input_errors(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["devbrief", "draft", "missing-plan.json"])

    assert cli.main() == 2
    assert "devbrief error:" in capsys.readouterr().err
