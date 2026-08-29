"""load_catalog must read professors_catalog columns by name, not by position.

_CATALOG_COLS was a hand-maintained copy of the table's column order, and it
drifted: focus_x and focus_y were added to professors_catalog between image_url
and avg_hours and never added here, so every row's `total_comments` was read out
of the focus_y slot — a focus coordinate, around 30 for every professor, with
nothing to distinguish it from a plausible comment count.

The failure is silent by construction. The reader's only guard was
`len(row) < len(_CATALOG_COLS)`, which compares a real row against the length of
the very constant that was wrong, so a two-column drift made rows look *longer*
than expected rather than malformed.

Lives in backend/tests rather than scraper/tests because ci.yml runs
`pytest tests` from backend/ and nothing else — a test beside the module it
covers would not run anywhere.
"""

import gzip
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scraper"))

from match_professors import (  # noqa: E402
    _CATALOG_COLS,
    _CATALOG_REQUIRED,
    _catalog_index,
    load_catalog,
)

# The real table, as precompute.py creates it. Kept here as a literal so a
# reordering of that CREATE TABLE has to be reflected deliberately.
REAL_COLUMNS = [
    "slug", "name", "name_key", "department", "college", "avg_rating",
    "rmp_rating", "trace_rating", "num_ratings", "trace_reviews",
    "total_reviews", "would_take_again_pct", "difficulty", "professor_url",
    "image_url", "focus_x", "focus_y", "avg_hours", "total_comments",
    "trace_name_key",
]


def _row(slug, name, num_ratings, total_reviews, total_comments,
         focus_x=50.0, focus_y=30.0):
    """One VALUES tuple in the real column order."""
    return (
        f"('{slug}', '{name}', '{name.lower()}', 'Computer Science', 'Khoury', "
        f"4.5, 4.2, 4.6, {num_ratings}, 300, {total_reviews}, 88.0, 3.1, "
        f"'https://rmp/x', 'https://img/x', {focus_x}, {focus_y}, 9.5, "
        f"{total_comments}, NULL)"
    )


def _backup(tmp_path, statements):
    path = tmp_path / "backup.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("CREATE TABLE professors_catalog (...);\n")
        f.write("\n".join(statements))
    return str(path)


def _insert(rows, columns=REAL_COLUMNS):
    cols = f" ({', '.join(columns)})" if columns else ""
    return f"INSERT INTO professors_catalog{cols} VALUES {', '.join(rows)};\n"


def test_hardcoded_fallback_matches_the_real_table():
    """The fallback order is the table's order — this is what drifted."""
    assert _CATALOG_COLS == REAL_COLUMNS


def test_required_columns_are_the_ones_actually_read():
    """Every field Professor is built from is declared required."""
    for col in ("slug", "name", "name_key", "department", "college",
                "num_ratings", "total_reviews", "total_comments"):
        assert col in _CATALOG_REQUIRED


def test_columns_come_from_the_statement_not_the_constant(tmp_path):
    """A column list in the INSERT wins, so a schema change cannot shift fields.

    The columns here are deliberately in a different order from _CATALOG_COLS.
    Reading positionally would pull `department` out of the num_ratings slot.
    """
    reordered = ["total_comments", "num_ratings", "total_reviews", "slug",
                 "name", "name_key", "department", "college"]
    stmt = _insert(["(41, 12, 312, 'ada-lovelace', 'Ada Lovelace', "
                    "'ada lovelace', 'Computer Science', 'Khoury')"],
                   columns=reordered)
    profs = load_catalog(_backup(tmp_path, [stmt]))
    assert len(profs) == 1
    assert profs[0].slug == "ada-lovelace"
    assert profs[0].num_ratings == 12
    assert profs[0].total_reviews == 312
    assert profs[0].total_comments == 41


def test_total_comments_is_not_the_focus_coordinate(tmp_path):
    """The regression itself: focus_y is 30, total_comments is 2780.

    Read positionally against the stale constant, every professor came back with
    total_comments == 30.
    """
    stmt = _insert([_row("john-doe", "John Doe", num_ratings=1104,
                         total_reviews=1400, total_comments=2780,
                         focus_x=50.0, focus_y=30.0)])
    profs = load_catalog(_backup(tmp_path, [stmt]))
    assert len(profs) == 1
    assert profs[0].total_comments == 2780
    assert profs[0].num_ratings == 1104
    assert profs[0].total_reviews == 1400


def test_falls_back_to_the_constant_when_no_columns_are_listed(tmp_path):
    """`INSERT INTO t VALUES (...)` carries no names; the constant has to serve."""
    assert _catalog_index("INSERT INTO professors_catalog VALUES ('a')") is None
    stmt = _insert([_row("jane-roe", "Jane Roe", 20, 400, 55)], columns=None)
    profs = load_catalog(_backup(tmp_path, [stmt]))
    assert len(profs) == 1
    assert profs[0].total_comments == 55


def test_short_rows_are_skipped_against_the_widest_column_read(tmp_path):
    """A truncated row is skipped rather than read past its end.

    The width comes from the last column actually read, not from a constant's
    length — that comparison is what let a two-column drift pass unnoticed.
    """
    good = _row("ok-prof", "Ok Prof", 10, 200, 77)
    short = "('short-prof', 'Short Prof', 'short prof')"
    profs = load_catalog(_backup(tmp_path, [_insert([good, short])]))
    assert [p.slug for p in profs] == ["ok-prof"]


def test_a_backup_missing_a_required_column_fails_loudly(tmp_path):
    """Better to stop than to build a matcher on columns that are not there."""
    stmt = _insert(["('a', 'A', 'a', 'CS', 'Khoury')"],
                   columns=["slug", "name", "name_key", "department", "college"])
    with pytest.raises(ValueError, match="total_comments|missing"):
        load_catalog(_backup(tmp_path, [stmt]))


# ── parse_sql_values ────────────────────────────────────────────────────────

def test_quoted_values_do_not_keep_the_whitespace_before_their_quote():
    """"(1, 'ada')" parses 'ada', not ' ada'.

    A quoted token is deliberately not stripped, because a space inside quotes is
    real data. The whitespace *outside* the quotes is not, and it used to be
    accumulated into the same buffer — so any backup written with a space after
    the comma produced a slug and name_key that matched nothing, silently.
    """
    from match_professors import parse_sql_values
    assert parse_sql_values("(1, 'ada', 2)") == [["1", "ada", "2"]]
    assert parse_sql_values("('ada' , 'b')") == [["ada", "b"]]
    assert parse_sql_values("(  'ada'  ,  'b'  )") == [["ada", "b"]]
    # Space inside the quotes survives; that is data.
    assert parse_sql_values("(' ada ')") == [[" ada "]]
    # NULL and unquoted numerics are unaffected.
    assert parse_sql_values("(NULL, 3.5, 'x')") == [[None, "3.5", "x"]]
    # Escaped quotes still work.
    assert parse_sql_values("(1, 'O''Brien')") == [["1", "O'Brien"]]
