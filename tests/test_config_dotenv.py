"""config.load_dotenv: BOM 付きの .env と、引用符の外の行末コメント."""

from __future__ import annotations

import os

import pytest

from devtools_common import config


@pytest.fixture
def clean_env():
    names = ["DT3_A", "DT3_B", "DT3_C", "DT3_D", "DT3_E", "DT3_F"]
    saved = {n: os.environ.pop(n, None) for n in names}
    yield
    for n, v in saved.items():
        os.environ.pop(n, None)
        if v is not None:
            os.environ[n] = v


def test_dotenv_with_bom_keeps_first_key(tmp_path, clean_env):
    p = tmp_path / ".env"
    p.write_bytes("DT3_A=1\nDT3_B=2\n".encode("utf-8-sig"))
    loaded = config.load_dotenv(p)
    assert loaded == {"DT3_A": "1", "DT3_B": "2"}
    assert os.environ["DT3_A"] == "1"


def test_dotenv_strips_inline_comment_outside_quotes(tmp_path, clean_env):
    p = tmp_path / ".env"
    p.write_text(
        "DT3_A=abc # コメント\n"
        'DT3_B="x # y" # 引用符の外のコメント\n'
        "DT3_C='p#q'\n"
        "DT3_D=a#b\n"
        "DT3_E=   # 値なし\n"
        'DT3_F="unterminated\n',
        encoding="utf-8",
    )
    assert config.load_dotenv(p) == {
        "DT3_A": "abc",
        "DT3_B": "x # y",
        "DT3_C": "p#q",
        "DT3_D": "a#b",
        "DT3_E": "",
        "DT3_F": '"unterminated',
    }
