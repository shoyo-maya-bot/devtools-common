"""run_main・load_structured: 壊れた設定ファイルのエラーで、該当行（api_key など）を標準エラーに出さない. キーは架空."""

from __future__ import annotations

import pytest

from devtools_common.cli import ExitCode, UsageError, run_main
from devtools_common.config import load_structured

KEY = "sk-" + "ant-api03-" + "Z" * 30


def test_broken_yaml_error_does_not_echo_the_line(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text(f"model: claude-haiku-4-5\napi_key: {KEY}\n  bad: [unclosed\n", encoding="utf-8")

    def main(argv):
        load_structured(p)
        return 0

    assert run_main(main, []) == ExitCode.USAGE
    err = capsys.readouterr().err
    assert "cannot parse config.yaml" in err
    assert "line " in err  # 位置は出す
    assert KEY not in err and "api_key" not in err


def test_broken_yaml_error_message_has_position_but_no_content(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text(f"api_key: '{KEY}\n", encoding="utf-8")  # 閉じ引用符なし
    with pytest.raises(UsageError) as ei:
        load_structured(p)
    assert KEY not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_broken_json_error_does_not_echo_the_value(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"api_key": "' + KEY + '",}', encoding="utf-8")
    with pytest.raises(UsageError) as ei:
        load_structured(p)
    assert KEY not in str(ei.value) and "line 1" in str(ei.value)


@pytest.mark.parametrize("exc", [UsageError, RuntimeError])
def test_run_main_redacts_secrets_in_error_messages(exc, capsys):
    def main(argv):
        raise exc(f"rejected key {KEY}")

    run_main(main, [])
    err = capsys.readouterr().err
    assert KEY not in err and "error:" in err
