"""devtools_common.excel のテスト（表示形式・結合セル・複数段の見出し・数式の計算結果が無いブック）."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from devtools_common.excel import (
    Merge,
    MergeMap,
    header_depth,
    header_names,
    looks_numeric,
    render_cell,
    render_value,
)

# --- 表示形式 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v", "fmt", "want"),
    [
        (1, "000", "001"),
        (42, "0000", "0042"),
        (12345, "000", "12345"),
        (1.5, "0.00", "1.50"),
        (2.345, "0.00", "2.35"),  # 四捨五入（Excel と同じく 0.5 は切り上げ）
        (-2.345, "0.00", "-2.35"),
        (1234567, "#,##0", "1,234,567"),
        (1234.5, "#,##0.00", "1,234.50"),
        (0.1234, "0.0%", "12.3%"),
        (0.5, "0%", "50%"),
        (7, "@", "7"),
        ("007", "@", "007"),
        (1234.5, "General", "1234.5"),
        (3.0, "General", "3"),
        (-1500, "#,##0;[Red]-#,##0", "-1,500"),
        (-1500, "#,##0_);(#,##0)", "(1,500)"),
        (0, '#,##0;-#,##0;"-"', "-"),
        (1500, '"¥"#,##0', "¥1,500"),
        (1500, "[$¥-411]#,##0", "¥1,500"),
        (1500, '#,##0"円"', "1,500円"),
        (0.25, "#.##", ".25"),
        (2, "0.0#", "2.0"),
        (1234567, "000-0000", "123-4567"),
        (1500000, "#,##0,", "1,500"),
        (0.5, "0.0_ ", "0.5"),
        (-0.001, "0.00", "0.00"),  # 丸めて 0 になった負数に - は付けない
        (True, "0", "TRUE"),
        (None, "000", ""),
    ],
)
def test_render_number_formats(v, fmt, want):
    assert render_value(v, fmt) == (want, True)


@pytest.mark.parametrize(
    ("fmt", "want"),
    [
        ("yyyy/mm/dd", "2026/04/05"),
        ("yyyy-mm-dd", "2026-04-05"),
        ("yyyy/m/d", "2026/4/5"),
        ("m/d", "4/5"),
        ('yyyy"年"m"月"d"日"', "2026年4月5日"),
        ("yyyy年m月d日", "2026年4月5日"),
        ("h:mm", "9:07"),
        ("hh:mm:ss", "09:07:03"),
        ("yyyy/mm/dd hh:mm", "2026/04/05 09:07"),
        ("m月d日(aaa)", "4月5日(日)"),
        ("h:mm AM/PM", "9:07 AM"),
        ('[$-ja-JP]ggge"年"m"月"d"日"', "令和8年4月5日"),
        ("[$-411]ge.m.d", "R8.4.5"),
    ],
)
def test_render_date_formats(fmt, want):
    assert render_value(dt.datetime(2026, 4, 5, 9, 7, 3), fmt) == (want, True)


def test_render_dates_misc():
    assert render_value(dt.time(13, 5), "h:mm") == ("13:05", True)
    assert render_value(dt.datetime(2019, 4, 30), "ggge年") == ("平成31年", True)
    assert render_value(dt.datetime(2019, 5, 3), '[$-ja-JP-x-gannen]ggge"年"') == ("令和元年", True)
    assert render_value("山田", '@" 様"') == ("山田 様", True)
    # 地域設定に従う既定の日付（組み込み書式 14）は ISO 形式
    assert render_value(dt.datetime(2026, 4, 5), "mm-dd-yy") == ("2026-04-05", True)
    assert render_value(dt.datetime(2026, 4, 5, 9, 30), "General") == ("2026-04-05 09:30", True)


@pytest.mark.parametrize(
    ("v", "fmt", "want"),
    [
        (12345.678, "0.00E+00", "12345.678"),
        (0.5, "# ?/?", "0.5"),
        (150, '[>=100]"多";"少"', "150"),
        (dt.datetime(2026, 4, 5), "[$-F800]dddd, mmmm dd, yyyy", "2026-04-05"),
    ],
)
def test_unsupported_formats_fall_back(v, fmt, want):
    assert render_value(v, fmt) == (want, False)


def test_render_cell_uses_number_format():
    class Cell:
        value = 7
        number_format = "000"

    assert render_cell(Cell()) == ("007", True)


@pytest.mark.parametrize(
    ("s", "want"), [("1,500", True), ("12%", True), ("¥300", True), ("(1,500)", True), ("型", False), ("", False)]
)
def test_looks_numeric(s, want):
    assert looks_numeric(s) is want


# --- 結合セル ---------------------------------------------------------------------------------------


def test_merge_map():
    mm = MergeMap([Merge(1, 2, 1, 3), Merge(1, 1, 2, 1)])
    assert len(mm) == 2
    assert mm.get(1, 3) == Merge(1, 2, 1, 3) and mm.get(1, 3).width == 2
    assert mm.anchor(2, 1) == (1, 1) and mm.anchor(5, 5) == (5, 5)
    assert mm.is_covered(1, 3) and not mm.is_covered(1, 2) and not mm.is_covered(4, 4)
    assert mm.get(3, 3) is None


# --- 複数段の見出し ---------------------------------------------------------------------------------


def _sheet(cells: dict[tuple[int, int], object], merges: list[Merge]):
    """(行, 列) → 値 の辞書から、own_text（結合の左上以外は ""）と filled_text（左上の値で埋める）を作る."""
    mm = MergeMap(merges)

    def own(r: int, c: int) -> str:
        return "" if mm.is_covered(r, c) else str(cells.get((r, c), "") or "")

    def filled(r: int, c: int) -> str:
        return str(cells.get(mm.anchor(r, c), "") or "")

    return mm, own, filled


def _two_row(vertical: bool):
    """1 段目: 項目名・型情報（B:C 横結合）・備考、2 段目: 型・桁、3 行目からデータ."""
    cells = {(1, 1): "項目名", (1, 2): "型情報", (1, 4): "備考", (2, 2): "型", (2, 3): "桁"}
    cells |= {(3, 1): "id", (3, 2): "数値", (3, 3): 10, (4, 1): "name", (4, 2): "文字列", (4, 3): 50, (4, 4): "氏名"}
    merges = [Merge(1, 2, 1, 3)] + ([Merge(1, 1, 2, 1), Merge(1, 4, 2, 4)] if vertical else [])
    return _sheet(cells, merges)


@pytest.mark.parametrize("vertical", [True, False])
def test_two_row_header(vertical):
    mm, own, filled = _two_row(vertical)
    depth = header_depth(mm, own, 1, first_col=1, last_col=4, last_row=4)
    assert depth == 2
    assert header_names(filled, 1, depth, range(1, 5)) == ["項目名", "型情報/型", "型情報/桁", "備考"]


def test_header_rows_override():
    mm, own, filled = _two_row(False)
    assert header_depth(mm, own, 1, first_col=1, last_col=4, last_row=4, header_rows=1) == 1
    assert header_names(filled, 1, 1, range(1, 5)) == ["項目名", "型情報", "型情報", "備考"]


def test_data_rows_merged_like_header_stay_single():
    """見出しが横結合でも、下の行も同じ形に結合したデータなら 1 段."""
    cells = {(1, 1): "No", (1, 2): "説明", (2, 1): 1, (2, 2): "最初", (3, 1): 2, (3, 2): "次"}
    mm, own, _ = _sheet(cells, [Merge(r, 2, r, 3) for r in (1, 2, 3)])
    assert header_depth(mm, own, 1, first_col=1, last_col=3, last_row=3) == 1


def test_numbers_under_merged_header_are_data():
    """横結合した見出しの下が数値なら、子見出しではなくデータ."""
    cells = {(1, 1): "No", (1, 2): "金額", (2, 1): 1, (2, 2): "1,500", (2, 3): "300"}
    mm, own, _ = _sheet(cells, [Merge(1, 2, 1, 3)])
    assert header_depth(mm, own, 1, first_col=1, last_col=3, last_row=2) == 1


def test_vertical_merge_without_subheader_is_single():
    """縦結合が下に伸びていても、横の行が数値ならデータ（見出しは 1 段）."""
    cells = {(1, 1): "区分", (1, 2): "金額", (2, 2): 100}
    mm, own, _ = _sheet(cells, [Merge(1, 1, 2, 1)])
    assert header_depth(mm, own, 1, first_col=1, last_col=2, last_row=2) == 1


def test_three_row_header_and_limits():
    cells = {(1, 1): "項目", (1, 2): "金額", (2, 2): "国内", (2, 4): "海外"}
    cells |= {(3, 2): "税抜", (3, 3): "税込", (3, 4): "税抜", (3, 5): "税込"}
    cells |= {(4, 1): "a", (4, 2): 1, (4, 3): 2, (4, 4): 3, (4, 5): 4}
    merges = [Merge(1, 1, 3, 1), Merge(1, 2, 1, 5), Merge(2, 2, 2, 3), Merge(2, 4, 2, 5)]
    mm, own, filled = _sheet(cells, merges)
    depth = header_depth(mm, own, 1, first_col=1, last_col=5, last_row=4)
    assert depth == 3
    names = header_names(filled, 1, depth, range(1, 6))
    assert names == ["項目", "金額/国内/税抜", "金額/国内/税込", "金額/海外/税抜", "金額/海外/税込"]
    assert header_depth(mm, own, 1, first_col=1, last_col=5, last_row=4, max_rows=2) == 2
    assert header_depth(mm, own, 1, first_col=1, last_col=5, last_row=2) == 2  # 範囲の最後の行で打ち切る


# --- ブックの読み込み -------------------------------------------------------------------------------


def test_merge_map_from_worksheet(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.merge_cells("B1:C2")
    assert MergeMap.from_worksheet(ws).get(2, 3) == Merge(1, 2, 2, 3)
    assert len(MergeMap.from_worksheet(object())) == 0


def test_formula_fallback(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    from devtools_common.excel import load_workbook_with_formula_fallback

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S1"
    ws["A1"], ws["B1"], ws["B2"] = 100, "=A1*2", "=SUM(A1:B1)"
    ws["B1"].number_format = "#,##0"
    wb.save(tmp_path / "a.xlsx")
    book, missing = load_workbook_with_formula_fallback(tmp_path / "a.xlsx")
    assert missing == ["S1!B1", "S1!B2"]
    assert book["S1"]["B1"].value == "=A1*2" and book["S1"]["A1"].value == 100
    assert book["S1"]["B1"].number_format == "#,##0"

    plain = openpyxl.Workbook()
    plain.active["A1"] = "x"
    plain.save(tmp_path / "plain.xlsx")
    assert load_workbook_with_formula_fallback(tmp_path / "plain.xlsx")[1] == []
    with pytest.raises(FileNotFoundError):
        load_workbook_with_formula_fallback(tmp_path / "none.xlsx")
