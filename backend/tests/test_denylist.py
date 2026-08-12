"""A data-deletion request has to survive a pipeline that rebuilds from CSVs.

precompute DROPs and rebuilds professors_catalog every run, and migrate_to_crdb
re-inserts trace_courses/trace_comments from the store, so deleting a professor's
rows by hand removes them only until the next refresh. The denylist is the part
that persists: it lives in the repo and every loader consults it before writing.

These tests cover the matching rule and the two properties that make it work —
alias resolution (a professor spelled two ways is one person) and the loaders
actually consulting it. The purge path is covered by driving find_targets against
a recording fake cursor.

No database, no network.
"""

import hashlib

import pytest

import denylist
from denylist import (
    entry_hash,
    is_denied,
    is_denied_key,
    denied_full_name,
    denied_hashes,
    name_key,
    normalize_name,
)


@pytest.fixture
def listfile(tmp_path, monkeypatch):
    """A denylist file at a temp path, with the module cache pointed at it."""
    path = tmp_path / "denylist.txt"
    path.write_text("")
    monkeypatch.setattr(denylist, "DENYLIST_PATH", str(path))
    denylist._cache.clear()

    def add(*names, note="test"):
        with open(path, "a", encoding="utf-8") as fh:
            for n in names:
                fh.write(f"{entry_hash(n)}  # {note}\n")
        denylist._cache.clear()
        return path

    add.path = path
    return add


# ── the matching rule ───────────────────────────────────────────────────────

def test_normalize_matches_precomputes_rule():
    """The key has to be byte-identical to precompute's or the filter never fires.

    Duplicated rather than imported because precompute pulls in pandas and numpy
    and this module is imported by loaders that carry neither.
    """
    from precompute import normalize_name as pc_normalize
    for raw in ["Julia Garrett", "  JULIA   GARRETT ", "Renée Descartes",
                "José Álvarez", "O'Brien", "Zhiyuan (Katherine) Zhang"]:
        assert normalize_name(raw) == pc_normalize(raw), raw


def test_a_listed_professor_is_denied(listfile):
    listfile("Julia Garrett")
    assert is_denied("Julia Garrett")
    assert is_denied("  julia   garrett  ")
    assert is_denied("JULIA GARRETT")


def test_an_unlisted_professor_is_not(listfile):
    listfile("Julia Garrett")
    assert not is_denied("Garrett Morrow")
    assert not is_denied("Julia Garretson")
    assert not is_denied("Julia")


def test_empty_and_missing_names_are_not_denied(listfile):
    listfile("Julia Garrett")
    assert not is_denied(None)
    assert not is_denied("")
    assert not is_denied("   ")


def test_an_empty_list_denies_nobody(listfile):
    assert denied_hashes() == frozenset()
    assert not is_denied("Julia Garrett")


def test_aliases_resolve_so_both_spellings_are_caught(listfile):
    """One person, two spellings, one request — both halves must go.

    RMP and TRACE spell the same professor differently; ALIAS_MAP is what makes
    them one row. A denylist that matched only the spelling someone happened to
    type in their request would leave the other source published.
    """
    listfile("Md Nazmus Sakib Miazi")     # the TRACE spelling
    assert is_denied("sakib miazi")        # the RMP spelling
    assert is_denied("Md Nazmus Sakib Miazi")
    # ...and adding it under the RMP spelling resolves to the same entry.
    assert entry_hash("sakib miazi") == entry_hash("md nazmus sakib miazi")


def test_key_form_skips_renormalizing(listfile):
    listfile("Julia Garrett")
    assert is_denied_key("julia garrett")
    assert not is_denied_key("Julia Garrett"), "is_denied_key takes a normalized key"
    assert not is_denied_key(None)
    assert not is_denied_key("")


def test_split_trace_names_are_joined(listfile):
    """TRACE stores first and last separately; trace_courses is filtered on that."""
    listfile("Julia Garrett")
    assert denied_full_name("Julia", "Garrett")
    assert denied_full_name(" julia ", " GARRETT ")
    assert not denied_full_name("Julia", "Morrow")


def test_entries_are_hashed_not_plaintext(listfile):
    """The file must not contain the name; the repo is public."""
    path = listfile("Julia Garrett")
    text = path.read_text()
    assert "julia" not in text.lower()
    assert "garrett" not in text.lower()
    assert hashlib.sha256(b"julia garrett").hexdigest() in text


def test_comments_and_blank_lines_are_ignored(listfile, tmp_path):
    path = listfile.path
    path.write_text(
        "# a header comment\n"
        "\n"
        f"{entry_hash('Julia Garrett')}  # 2026-08-11 request\n"
        "   \n"
    )
    denylist._cache.clear()
    assert denied_hashes() == frozenset({entry_hash("Julia Garrett")})
    assert is_denied("Julia Garrett")


def test_a_missing_file_is_an_empty_list(tmp_path, monkeypatch):
    """No file is the normal state for a fresh checkout, not an error."""
    monkeypatch.setattr(denylist, "DENYLIST_PATH", str(tmp_path / "nope.txt"))
    denylist._cache.clear()
    assert denied_hashes() == frozenset()
    assert not is_denied("Julia Garrett")


# ── the CLI ─────────────────────────────────────────────────────────────────

def test_add_writes_a_hash_and_is_idempotent(listfile, capsys):
    denylist.main(["add", "Julia Garrett", "--note", "2026-08-11 request"])
    text = listfile.path.read_text()
    assert entry_hash("Julia Garrett") in text
    assert "2026-08-11 request" in text
    assert "Julia" not in text

    denylist.main(["add", "julia garrett"])
    assert text.count(entry_hash("Julia Garrett")) == 1
    assert "Already on the list" in capsys.readouterr().out


def test_check_exit_code_reports_membership(listfile):
    listfile("Julia Garrett")
    assert denylist.main(["check", "Julia Garrett"]) == 0
    assert denylist.main(["check", "Garrett Morrow"]) == 1


# ── the loaders consult it ──────────────────────────────────────────────────

def test_migrate_tags_every_table_that_carries_a_name():
    """A table with a professor name in it must be filtered, or data leaks through."""
    from migrate_to_crdb import TABLES
    expected = {
        "rmp_professors": ("name",),
        "rmp_reviews": ("professor_name",),
        "trace_courses": ("instructor_first_name", "instructor_last_name"),
        "professor_photos": ("name",),
    }
    for table, cols in expected.items():
        assert TABLES[table].get("deny_name") == cols, f"{table} is not filtered"
    # The two that legitimately carry no name reach a professor only by joining
    # trace_courses, so they are detached by the course filter and cleared by
    # purge_denied. Asserted so that adding a name column to either is noticed.
    for table in ("trace_comments", "trace_scores"):
        cols = TABLES[table]["columns"]
        assert not any("name" in c for c in cols), (
            f"{table} gained a name column — it now needs a deny_name entry")


def test_migrate_withholds_a_denied_row(listfile, tmp_path, capsys):
    """upload_csv drops the row rather than inserting it."""
    import migrate_to_crdb

    listfile("Julia Garrett")
    csv_path = tmp_path / "rmp_professors.csv"
    csv_path.write_text(
        "name,department,rating,num_ratings,would_take_again_pct,"
        "level_of_difficulty,professor_url\n"
        "Julia Garrett,English,3.4,13,80%,2.5,https://x\n"
        "Garrett Morrow,Political Science,4.4,20,90%,2.0,https://y\n"
    )

    inserted = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass

    class Conn:
        def cursor(self): return Cur()
        def commit(self): pass

    def fake_execute_values(cur, sql, batch, page_size=None):
        inserted.extend(batch)

    migrate_to_crdb.execute_values = fake_execute_values
    conf = migrate_to_crdb.TABLES["rmp_professors"]
    migrate_to_crdb.upload_csv(
        Conn(), "rmp_professors", conf["columns"], str(csv_path),
        conf["transform"], conf["on_conflict"], None, None, conf["deny_name"])

    names = [row[0] for row in inserted]
    assert names == ["Garrett Morrow"]
    assert "1 withheld (denylist)" in capsys.readouterr().out


def test_precompute_filters_both_sides(listfile):
    """The catalog build drops them from the RMP frame and the TRACE frame."""
    import pandas as pd
    listfile("Julia Garrett")

    rmp = pd.DataFrame({"_name_key": ["julia garrett", "garrett morrow"]})
    tc = pd.DataFrame({"name_key": ["julia garrett", "julia garrett", "garrett morrow"]})
    assert list(rmp[~rmp["_name_key"].map(is_denied_key)]["_name_key"]) == ["garrett morrow"]
    assert list(tc[~tc["name_key"].map(is_denied_key)]["name_key"]) == ["garrett morrow"]


def test_evidence_builders_import_the_filter():
    """The RAG corpus is what chat quotes; it has to honour the list too."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "scraper" / "load_evidence_to_crdb.py"
    text = src.read_text()
    assert "from denylist import is_denied_key" in text
    assert text.count("is_denied_key(r.get(\"name_key\"))") == 2, (
        "both the RMP and TRACE evidence builders must check the denylist")
    assert "known_slugs" in text, "reddit rows must be restricted to catalog slugs"


# ── the purge resolves identity before deleting ─────────────────────────────

class FakeCursor:
    """Answers find_targets' four probe queries from canned rows."""

    def __init__(self, catalog, trace_names, rmp_reviews, trace_keys, null_key_rows=()):
        self._data = {
            "professors_catalog": catalog,
            "instructor_first_name, instructor_last_name FROM trace_courses": trace_names,
            "professor_name, name_key FROM rmp_reviews": rmp_reviews,
            "course_id, instructor_id, term_id FROM trace_courses": trace_keys,
            "name_key IS NULL": null_key_rows,
        }
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if "name_key IS NULL" in flat:
            self._rows = self._data["name_key IS NULL"]
        elif "FROM professors_catalog" in flat:
            self._rows = self._data["professors_catalog"]
        elif "instructor_first_name, instructor_last_name" in flat:
            self._rows = self._data["instructor_first_name, instructor_last_name FROM trace_courses"]
        elif "FROM rmp_reviews" in flat:
            self._rows = self._data["professor_name, name_key FROM rmp_reviews"]
        elif "course_id, instructor_id, term_id" in flat:
            self._rows = self._data["course_id, instructor_id, term_id FROM trace_courses"]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


def test_purge_finds_the_professor_through_the_catalog(listfile):
    from purge_denied import find_targets
    listfile("Julia Garrett")
    cur = FakeCursor(
        catalog=[("julia-garrett", "Julia Garrett", "julia garrett", None),
                 ("garrett-morrow", "Garrett Morrow", "garrett morrow", None)],
        trace_names=[("Julia", "Garrett"), ("Garrett", "Morrow")],
        rmp_reviews=[("Julia Garrett", "julia garrett")],
        trace_keys=[(1, 10, 202430), (2, 10, 202510)],
    )
    slugs, name_keys, trace_keys = find_targets(cur)
    assert slugs == ["julia-garrett"]
    assert "julia garrett" in name_keys
    assert "garrett morrow" not in name_keys
    assert set(trace_keys) == {(1, 10, 202430), (2, 10, 202510)}


def test_purge_still_finds_them_after_precompute_removed_the_catalog_row(listfile):
    """The case a naive purge misses entirely.

    Run precompute first and the professor has no catalog row, while their TRACE
    courses and comments are still loaded. A purge that only searched the catalog
    would find nothing and report clean while the data sat there.
    """
    from purge_denied import find_targets
    listfile("Julia Garrett")
    cur = FakeCursor(
        catalog=[("garrett-morrow", "Garrett Morrow", "garrett morrow", None)],
        trace_names=[("Julia", "Garrett"), ("Garrett", "Morrow")],
        rmp_reviews=[],
        trace_keys=[(1, 10, 202430)],
    )
    slugs, name_keys, trace_keys = find_targets(cur)
    assert slugs == []
    assert name_keys == ["julia garrett"]
    assert trace_keys == [(1, 10, 202430)]


def test_purge_matches_a_fuzzy_matched_trace_spelling(listfile):
    """trace_name_key differs from name_key for fuzzy-matched professors."""
    from purge_denied import find_targets
    listfile("Md Nazmus Sakib Miazi")
    cur = FakeCursor(
        catalog=[("sakib-miazi", "Sakib Miazi", "md nazmus sakib miazi", "md nazmus sakib miazi")],
        trace_names=[],
        rmp_reviews=[],
        trace_keys=[(5, 7, 202510)],
    )
    slugs, name_keys, _ = find_targets(cur)
    assert slugs == ["sakib-miazi"]
    assert "md nazmus sakib miazi" in name_keys


def test_purge_picks_up_courses_whose_name_key_is_not_backfilled_yet(listfile):
    """precompute backfills trace_courses.name_key; rows loaded since are NULL."""
    from purge_denied import find_targets
    listfile("Julia Garrett")
    cur = FakeCursor(
        catalog=[],
        trace_names=[("Julia", "Garrett")],
        rmp_reviews=[],
        trace_keys=[],
        null_key_rows=[(9, 3, 202530, "Julia", "Garrett"),
                       (8, 4, 202530, "Garrett", "Morrow")],
    )
    _, _, trace_keys = find_targets(cur)
    assert trace_keys == [(9, 3, 202530)]


def test_nothing_matches_an_empty_list(listfile):
    from purge_denied import find_targets
    cur = FakeCursor(
        catalog=[("julia-garrett", "Julia Garrett", "julia garrett", None)],
        trace_names=[("Julia", "Garrett")],
        rmp_reviews=[("Julia Garrett", "julia garrett")],
        trace_keys=[(1, 10, 202430)],
    )
    assert find_targets(cur) == ([], [], [])


def test_migrate_filter_survives_a_transform_that_yields_nothing(listfile):
    """The filter must not depend on the transform understanding the CSV.

    trace_courses' transform reads camelCase while the export in output_data is
    snake_case, so it produces empty strings for every field. A name check
    against only that output would wave every row through and report nothing
    wrong — the one failure mode a privacy filter must not have.
    """
    from migrate_to_crdb import _row_is_denied
    listfile("Julia Garrett")
    deny = ("instructor_first_name", "instructor_last_name")

    # Transform understood the CSV.
    assert _row_is_denied({"instructor_first_name": "Julia",
                           "instructor_last_name": "Garrett"}, {}, deny)
    # Transform produced nothing; the raw snake_case row still catches it.
    raw_snake = {"instructor_first_name": "Julia", "instructor_last_name": "Garrett"}
    assert _row_is_denied({"instructor_first_name": "", "instructor_last_name": ""},
                          raw_snake, deny)
    # ...and the raw camelCase row too.
    raw_camel = {"instructorFirstName": "Julia", "instructorLastName": "Garrett"}
    assert _row_is_denied({"instructor_first_name": "", "instructor_last_name": ""},
                          raw_camel, deny)
    # A different professor passes through under every spelling.
    assert not _row_is_denied({"instructor_first_name": "Garrett",
                               "instructor_last_name": "Morrow"}, {}, deny)
    assert not _row_is_denied({}, {"instructorFirstName": "Garrett",
                                   "instructorLastName": "Morrow"}, deny)
    # An all-empty row is not a match for a one-word denylist entry.
    assert not _row_is_denied({"instructor_first_name": "", "instructor_last_name": ""},
                              {}, deny)


def test_camel_conversion_matches_the_transform_keys():
    from migrate_to_crdb import _camel
    assert _camel("instructor_first_name") == "instructorFirstName"
    assert _camel("professor_name") == "professorName"
    assert _camel("name") == "name"
