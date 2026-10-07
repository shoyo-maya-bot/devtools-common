"""結合セルと複数段の見出し.

openpyxl のシート（`ws.merged_cells.ranges` を持つもの）から結合の索引を作り、
「結合の左上の値で埋める」「見出しが何段あるか」「親/子 の列名」を求める.
セルの値は呼び出し側が関数で渡す（表示形式の反映や正規化はツールごとに違うため）.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

MAX_HEADER_ROWS = 3

CellText = Callable[[int, int], str]
"""(行, 列) → セルの文字列（空なら ""）. 行・列は 1 始まり."""


@dataclass(frozen=True)
class Merge:
    """結合範囲（行・列は 1 始まり、両端を含む）."""

    r0: int
    c0: int
    r1: int
    c1: int

    @property
    def width(self) -> int:
        return self.c1 - self.c0 + 1

    @property
    def anchor(self) -> tuple[int, int]:
        """値を持つ左上のセル."""
        return self.r0, self.c0


class MergeMap:
    """結合範囲の索引. 範囲内のどのセルからでも、その結合を O(1) で引ける."""

    def __init__(self, merges: Iterable[Merge] = ()):
        self.merges: list[Merge] = list(merges)
        self._at: dict[tuple[int, int], Merge] = {}
        for m in self.merges:
            for r in range(m.r0, m.r1 + 1):
                for c in range(m.c0, m.c1 + 1):
                    self._at[(r, c)] = m

    @classmethod
    def from_worksheet(cls, ws) -> MergeMap:
        """openpyxl のシートから作る（読み取り専用モードのシートは結合を持たないので空になる）."""
        ranges = getattr(getattr(ws, "merged_cells", None), "ranges", ()) or ()
        return cls(Merge(r.min_row, r.min_col, r.max_row, r.max_col) for r in ranges)

    def __iter__(self) -> Iterator[Merge]:
        return iter(self.merges)

    def __len__(self) -> int:
        return len(self.merges)

    def get(self, row: int, col: int) -> Merge | None:
        """(row, col) を含む結合. 結合していなければ None."""
        return self._at.get((row, col))

    def anchor(self, row: int, col: int) -> tuple[int, int]:
        """値を読むべきセル: 結合内なら左上、そうでなければ自分."""
        m = self._at.get((row, col))
        return m.anchor if m else (row, col)

    def is_covered(self, row: int, col: int) -> bool:
        """結合の左上以外（値を持たない）セルか."""
        m = self._at.get((row, col))
        return m is not None and (row, col) != m.anchor


def looks_numeric(s: str) -> bool:
    """数値に見える文字列か. 表示形式で付いた桁区切り・%・通貨記号・括弧（会計の負数）は数値の一部とみなす."""
    try:
        float(s.replace(",", "").strip().strip("()¥￥$円%").strip())
        return True
    except ValueError:
        return False


def header_depth(
    merges: MergeMap,
    own_text: CellText,
    head_row: int,
    *,
    first_col: int,
    last_col: int,
    last_row: int,
    header_rows: int | None = None,
    max_rows: int = MAX_HEADER_ROWS,
) -> int:
    """見出しが何段か（結合セルから判断する）.

    `own_text` は結合で埋めないセル自身の値（結合の左上以外は ""）. `header_rows` を渡すとそれをそのまま返す.
    見出しの最後の行の次の行も見出しとみなすのは、次のどちらかのとき（列は first_col〜last_col の範囲だけを見る）:

    - 見出し行から始まる結合セルが、次の行まで縦に続いていて、次の行の（上からの結合で覆われていない）セルが
      1 つ以上あり、すべて文字（数値でない）
      例: 縦に結合した「項目名」の横に、子見出し「型」「桁」がある
    - 見出しの最後の行で横に結合したセル（例: 「金額」）の下の各列に、どれも文字の値がある（例: 「税抜」「税込」）

    データ行も同じように横に結合しているだけの表は 1 段のまま. 段数は max_rows と last_row までで打ち切る.
    """
    if header_rows:
        return header_rows

    def covered_from_above(r: int, c: int) -> bool:
        m = merges.get(r, c)
        return m is not None and m.r0 < r

    def looks_like_subheader(r: int) -> bool:
        free = [own_text(r, c) for c in range(first_col, last_col + 1) if not covered_from_above(r, c)]
        filled = [v for v in free if v]
        return bool(filled) and not any(looks_numeric(v) for v in filled)

    n = 1
    while n < max_rows:
        last, nxt = head_row + n - 1, head_row + n
        if nxt > last_row:
            break
        sub = False
        for m in merges:
            if not (head_row <= m.r0 <= last and first_col <= m.c0 <= last_col):
                continue
            if m.r1 >= nxt:
                if looks_like_subheader(nxt):
                    sub = True
                    break
            elif m.r1 == last and m.width > 1:
                below = [own_text(nxt, c) for c in range(m.c0, min(m.c1, last_col) + 1)]
                if all(below) and not any(looks_numeric(b) for b in below):
                    sub = True
                    break
        if not sub:
            break
        n += 1
    return n


def header_names(filled_text: CellText, head_row: int, depth: int, cols: Iterable[int], sep: str = "/") -> list[str]:
    """各列の列名. 複数段の見出しは上から順に sep でつなぐ（「金額/税抜」）. 同じ値が続く段（縦結合）は 1 つにまとめる.

    `filled_text` は結合の左上の値で埋めたセルの値. 戻り値は cols と同じ順・同じ数（見出しの無い列は ""）.
    """
    names = []
    for c in cols:
        parts: list[str] = []
        for r in range(head_row, head_row + depth):
            v = filled_text(r, c)
            if v and (not parts or parts[-1] != v):
                parts.append(v)
        names.append(sep.join(parts))
    return names
