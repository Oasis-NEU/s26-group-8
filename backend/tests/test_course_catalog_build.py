"""The course_catalog build: which display_names parse, and what each code shows.

Both halves of this were narrowed by the reset and rebuilt without their guards,
and both are measurable against the real export
(backend/Better_Scraper/output_data/trace_courses.csv, 105,376 rows):

  - the parse regex was rebuilt as `^([A-Z]+\\d+):\\d+\\s+\\((.+?)\\)`, which
    requires a *numeric* section and requires a section at all. 10,405 rows and
    186 course codes stop parsing — those codes get no course_catalog row and so
    no course page at all.
  - the course_code backfill was rebuilt on SPLIT_PART(display_name, ':', 1),
    and 3,819 rows carry no colon, so the whole string became the "code":
    INTB1203INTLBUSANDSOCIALRESPIFFATKHAN. Those rows can never match
    `WHERE course_code = 'INTB1203'`.

The title/department/search_text rules are pinned here too, because
drop_duplicates() picked whichever CSV row came first and dropped every
historical title from search_text — and search_text is what the course search
queries.

No database: every function under test is pure.
"""

import pathlib
import re

import pytest

from precompute import (
    build_course_rows,
    course_code_from_display_name,
    parse_display_name,
)

PRECOMPUTE_PY = pathlib.Path(__file__).resolve().parents[1] / "precompute.py"


# ── parse_display_name: which rows get a course page at all ─────────────────

@pytest.mark.parametrize("display_name,code,title", [
    ("CS2500:1 (Fundamentals of Computer Science 1)", "CS2500",
     "Fundamentals of Computer Science 1"),
    # An alphanumeric section. The rebuilt regex required \d+ and dropped these.
    ("IE7215:V35 (Simulation Analysis)", "IE7215", "Simulation Analysis"),
    # No section at all — the Law and Fall-2015 shape, 3,819 rows.
    ("LAW7651 (Human Rights in the United States)", "LAW7651",
     "Human Rights in the United States"),
    ("INTB1203 (Intl Bus and Social Resp)", "INTB1203", "Intl Bus and Social Resp"),
    # Lowercase prefixes appear in the export; the code is normalized up.
    ("cs2500:1 (Fundamentals)", "CS2500", "Fundamentals"),
])
def test_display_names_that_must_parse(display_name, code, title):
    assert parse_display_name(display_name) == (code, title)


@pytest.mark.parametrize("display_name", ["", None, "no code here", "1234 (Numbers first)"])
def test_display_names_that_must_not_parse(display_name):
    assert parse_display_name(display_name) == (None, None)


# ── course_code_from_display_name: the SQL backfill's rule ──────────────────

@pytest.mark.parametrize("display_name,code", [
    ("CS2500:1 (Fundamentals)", "CS2500"),
    # The 3,819 colon-less rows. SPLIT_PART returned the entire string here.
    ("LAW7651 (Human Rights in the United States) - Martha Davis", "LAW7651"),
    ("INTB1203 (Intl Bus and Social Resp) - Iffat Khan", "INTB1203"),
    ("lS6150 (Law and Organizational Management)", "LS6150"),
    ("", ""),
    (None, ""),
])
def test_course_code_from_display_name(display_name, code):
    assert course_code_from_display_name(display_name) == code


def _backfill_sql():
    """The course_code backfill statement, comments excluded.

    Pinned at the source because it runs in CRDB, not here. Read as the SQL
    string alone so that prose *about* SPLIT_PART in the surrounding comment
    does not read as a use of it.
    """
    src = PRECOMPUTE_PY.read_text()
    for chunk in src.split('cur.execute("""')[1:]:
        stmt = chunk[:chunk.index('"""')]
        if "UPDATE trace_courses" in stmt and "course_code" in stmt:
            return stmt
    pytest.fail("no course_code backfill statement found in precompute.py")


def test_the_backfill_does_not_split_on_a_colon():
    """SPLIT_PART(display_name, ':', 1) is a no-op on a row with no colon, and a
    no-op there means the course code becomes the whole display_name."""
    assert "SPLIT_PART" not in _backfill_sql(), \
        "the course_code backfill still splits on ':' (3,819 rows have none)"


def test_the_backfill_repairs_rows_it_already_got_wrong():
    """`WHERE course_code IS NULL` can only ever fill blanks.

    Every row corrupted by the SPLIT_PART version is non-NULL, so a backfill
    gated on IS NULL leaves them corrupted forever and no re-run can fix them.
    """
    assert "IS DISTINCT FROM" in _backfill_sql(), \
        "the backfill cannot repair a course_code it previously wrote wrong"


# ── build_course_rows: what each code displays ──────────────────────────────

def _rows(records):
    return {r[0]: r for r in build_course_rows(records)}


def test_a_code_gets_one_row_per_code():
    rows = build_course_rows([
        ("CS2500:1 (Fundamentals)", "Fall 2024", "Khoury"),
        ("CS2500:2 (Fundamentals)", "Fall 2024", "Khoury"),
    ])
    assert len(rows) == 1
    assert rows[0][0] == "CS2500"


def test_the_title_comes_from_the_most_recent_term():
    """drop_duplicates() took whichever CSV row landed first."""
    rows = _rows([
        ("ME2350:1 (Statics)", "Fall 2019", "Engineering"),
        ("ME2350:1 (Statics and Dynamics)", "Fall 2024", "Engineering"),
    ])
    assert rows["ME2350"][1] == "Statics and Dynamics"


def test_the_department_comes_from_the_most_recent_term():
    rows = _rows([
        ("ME2350:1 (Statics)", "Fall 2019", "Mechanical Engineering"),
        ("ME2350:1 (Statics)", "Fall 2024", "Engineering"),
    ])
    assert rows["ME2350"][2] == "Engineering"


def test_search_text_keeps_every_historical_title():
    """A student who took ME2350 as "Statics" searches for "Statics"."""
    rows = _rows([
        ("ME2350:1 (Statics)", "Fall 2019", "Engineering"),
        ("ME2350:1 (Statics and Dynamics)", "Fall 2024", "Engineering"),
    ])
    search = rows["ME2350"][3]
    assert "statics" in search
    assert "statics and dynamics" in search
    assert search.startswith("me2350")


def test_a_code_running_unrelated_titles_in_one_term_is_topics():
    rows = _rows([
        ("HONR3310:1 (Election 2024)", "Fall 2024", "Honors"),
        ("HONR3310:2 (Language and Power)", "Fall 2024", "Honors"),
    ])
    assert rows["HONR3310"][4] is True


def test_a_renamed_course_is_not_topics():
    """Different titles in *different* terms is a rename, not a container."""
    rows = _rows([
        ("ME2350:1 (Statics)", "Fall 2019", "Engineering"),
        ("ME2350:1 (Engineering Mechanics)", "Fall 2024", "Engineering"),
    ])
    assert rows["ME2350"][4] is False


def test_title_variants_in_one_term_are_not_topics():
    """One title spelled two ways is one course; see titles_are_variants."""
    rows = _rows([
        ("MGMT1000:1 (Org Behavior)", "Fall 2024", "DMSB"),
        ("MGMT1000:2 (Organizational Behavior)", "Fall 2024", "DMSB"),
    ])
    assert rows["MGMT1000"][4] is False


def test_unparseable_rows_are_skipped_not_crashed():
    rows = build_course_rows([
        ("no code here", "Fall 2024", "X"),
        ("CS2500:1 (Fundamentals)", "Fall 2024", "Khoury"),
    ])
    assert [r[0] for r in rows] == ["CS2500"]


def test_a_missing_department_is_empty_not_nan():
    """department_name is NaN for some rows; "nan" must not reach the page."""
    rows = _rows([("CS2500:1 (Fundamentals)", "Fall 2024", float("nan"))])
    assert rows["CS2500"][2] == ""


# ── the real export ─────────────────────────────────────────────────────────

def test_the_parse_rule_admits_the_shapes_the_export_actually_contains():
    """Guards the 10,405 rows and 186 codes the narrowed regex dropped.

    Measured rather than asserted as a count, so a re-scrape does not fail it:
    the property is that a colon-less or alphanumeric-section row still parses.
    """
    for dn in ["IE7215:V35 (Simulation Analysis)",
               "LAW7651 (Human Rights in the United States) - Martha Davis",
               "ACCT6248 (Financial Reporting)",
               "ARCH2230:A (Design Studio)"]:
        code, _title = parse_display_name(dn)
        assert code is not None, f"{dn!r} no longer produces a course page"


def test_the_catalog_build_actually_calls_the_builder():
    """A correct helper the pipeline does not call is the bug intact.

    build_course_rows lives outside main() precisely so it can be tested; main()
    previously carried its own inline regex, and that copy is what shipped.
    """
    src = PRECOMPUTE_PY.read_text()
    assert "build_course_rows(" in src, "main() does not call build_course_rows"
    assert not re.search(r'r"\^\(\[A-Z\]\+\\d\+\):\\d\+', src), \
        "the narrowed inline course regex is still in precompute.py"
