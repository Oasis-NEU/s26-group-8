"""A weekly re-scrape has to be able to change a professor's numbers.

rmp_professors loaded with ON CONFLICT DO NOTHING plus a client-side skip on
(name, department), which between them made the table insert-only: after a
professor's first appearance their rating, num_ratings, would_take_again_pct and
level_of_difficulty were frozen forever. Measured across two real consecutive
scrapes, of 3,861 professors present in both, rating moved for 315, num_ratings
for 533, would_take_again_pct for 480 and level_of_difficulty for 331 — so the
table was wrong for roughly one row in seven after a single week.

The reviews table is genuinely append-only (a rating node never changes once
posted, and prune_rmp_reviews handles removals), so it keeps DO NOTHING and its
client-side filter. Only the professor summary rows need overwriting.
"""
import os
import sys

import pytest

os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import migrate_to_crdb as M  # noqa: E402

MUTABLE = ("rating", "num_ratings", "would_take_again_pct", "level_of_difficulty")


# ── table configuration ─────────────────────────────────────────────────────

def test_rmp_professors_upserts_instead_of_ignoring_conflicts():
    clause = M.TABLES["rmp_professors"]["on_conflict"].upper()
    assert "DO UPDATE SET" in clause
    assert "DO NOTHING" not in clause


def test_every_field_a_rescrape_can_change_is_overwritten():
    clause = M.TABLES["rmp_professors"]["on_conflict"]
    for col in MUTABLE:
        assert f"{col} = EXCLUDED.{col}" in clause, f"{col} would stay frozen"


def test_the_conflict_target_matches_the_unique_constraint():
    # DO UPDATE needs a real arbiter index or CockroachDB rejects the statement.
    assert M.UNIQUE_CONSTRAINTS["rmp_professors"][1] == "(name, department)"
    assert "(name, department)" in M.TABLES["rmp_professors"]["on_conflict"]


def test_rmp_professors_is_not_filtered_client_side():
    # The skip-what-the-DB-already-has optimisation is exactly what stopped the
    # refreshed rows from ever reaching the upsert.
    assert not M.TABLES["rmp_professors"].get("key_columns")


def test_rmp_reviews_still_ignores_conflicts_and_filters():
    # Append-only by nature; nothing to regress here.
    assert "DO NOTHING" in M.TABLES["rmp_reviews"]["on_conflict"].upper()
    assert M.TABLES["rmp_reviews"]["key_columns"] == ["professor_name", "course", "date"]


@pytest.mark.parametrize("table", [t for t in ("trace_comments", "trace_courses",
                                               "trace_scores", "professor_photos")])
def test_other_tables_keep_do_nothing(table):
    assert "DO NOTHING" in M.TABLES[table]["on_conflict"].upper()


# ── upload behaviour ────────────────────────────────────────────────────────

class FakeCursor:
    def __init__(self):
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append(sql)
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.cur = FakeCursor()
        self.commits = 0
    def cursor(self):
        return self.cur
    def commit(self):
        self.commits += 1


@pytest.fixture
def sent(monkeypatch):
    """Capture the rows and SQL handed to execute_values."""
    calls = []
    monkeypatch.setattr(M, "execute_values",
                        lambda cur, sql, batch, page_size=None: calls.append((sql, batch)))
    return calls


def _professors_csv(tmp_path, *names):
    p = tmp_path / "rmp_professors.csv"
    lines = ["name,department,rating,num_ratings,would_take_again_pct,"
             "level_of_difficulty,professor_url"]
    for i, n in enumerate(names):
        lines.append(f"{n},Computer Science,4.{i},{10 + i},9{i}%,3.{i},http://x/{i}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _upload(conn, tmp_path, sent, names, existing):
    conf = M.TABLES["rmp_professors"]
    M.upload_csv(conn, "rmp_professors", conf["columns"],
                 _professors_csv(tmp_path, *names),
                 conf.get("transform"), conf.get("on_conflict", ""),
                 conf.get("key_columns"), existing)
    return [row for _, batch in sent for row in batch]


def test_a_professor_already_in_the_db_is_still_sent(tmp_path, sent):
    # The regression that mattered: with key_columns set this row was skipped.
    rows = _upload(FakeConn(), tmp_path, sent, ["Ann Lee"],
                   {("Ann Lee", "Computer Science")})
    assert len(rows) == 1, "an existing professor must still be re-sent to be updated"


def test_all_rows_are_sent_regardless_of_what_the_db_holds(tmp_path, sent):
    rows = _upload(FakeConn(), tmp_path, sent, ["Ann Lee", "Bob Roe", "Cy Poe"],
                   {("Ann Lee", "Computer Science"), ("Bob Roe", "Computer Science")})
    assert len(rows) == 3


def test_the_upsert_clause_reaches_the_insert_statement(tmp_path, sent):
    _upload(FakeConn(), tmp_path, sent, ["Ann Lee"], set())
    sql = sent[0][0]
    assert "ON CONFLICT (name, department) DO UPDATE SET" in sql


def test_values_are_still_typed_by_the_transform(tmp_path, sent):
    rows = _upload(FakeConn(), tmp_path, sent, ["Ann Lee"], set())
    cols = M.TABLES["rmp_professors"]["columns"]
    row = dict(zip(cols, rows[0]))
    assert row["rating"] == 4.0 and isinstance(row["rating"], float)
    assert row["num_ratings"] == 10 and isinstance(row["num_ratings"], int)
    assert row["would_take_again_pct"] == "90%"


# ── duplicate conflict targets ──────────────────────────────────────────────
#
# DO UPDATE is stricter than DO NOTHING: if one INSERT ... VALUES carries the
# same conflict key twice, CockroachDB raises "ON CONFLICT DO UPDATE command
# cannot affect row a second time" and the whole statement fails. DO NOTHING
# silently ignored the second row, so this never mattered before. It does now —
# the real CSV has one such pair today (two RMP profile pages for Hamid Nayeb
# Hashemi in Mechanical Engineering, one of them an empty 0-rating stub), and at
# BATCH_SIZE 25,000 all 3,892 rows go out in a single statement.

def test_duplicate_conflict_keys_collapse_to_one_row():
    cols = ["name", "department", "num_ratings"]
    rows = [("Ann Lee", "CS", 5), ("Hamid", "ME", 26), ("Hamid", "ME", 0)]
    out = M.dedupe_rows(rows, cols, ["name", "department"], "num_ratings")
    assert len(out) == 2


def test_the_row_with_more_ratings_wins():
    cols = ["name", "department", "num_ratings"]
    rows = [("Hamid", "ME", 0), ("Hamid", "ME", 26)]
    assert M.dedupe_rows(rows, cols, ["name", "department"], "num_ratings") == \
        [("Hamid", "ME", 26)]


def test_the_winner_is_order_independent():
    cols = ["name", "department", "num_ratings"]
    a = M.dedupe_rows([("H", "ME", 26), ("H", "ME", 0)], cols, ["name", "department"], "num_ratings")
    b = M.dedupe_rows([("H", "ME", 0), ("H", "ME", 26)], cols, ["name", "department"], "num_ratings")
    assert a == b


def test_a_null_rating_count_loses_to_a_real_one():
    cols = ["name", "department", "num_ratings"]
    rows = [("H", "ME", None), ("H", "ME", 3)]
    assert M.dedupe_rows(rows, cols, ["name", "department"], "num_ratings") == \
        [("H", "ME", 3)]


def test_dedupe_keeps_first_appearance_order():
    cols = ["name", "department", "num_ratings"]
    rows = [("A", "CS", 1), ("H", "ME", 0), ("B", "CS", 1), ("H", "ME", 9)]
    out = M.dedupe_rows(rows, cols, ["name", "department"], "num_ratings")
    assert [r[0] for r in out] == ["A", "H", "B"]


def test_distinct_departments_are_not_collapsed():
    cols = ["name", "department", "num_ratings"]
    rows = [("H", "ME", 1), ("H", "CS", 2)]
    assert len(M.dedupe_rows(rows, cols, ["name", "department"], "num_ratings")) == 2


def test_rmp_professors_is_configured_to_dedupe():
    conf = M.TABLES["rmp_professors"]
    assert conf["dedupe_on"] == ["name", "department"]
    assert conf["dedupe_prefer"] == "num_ratings"


def test_the_real_duplicate_pair_reaches_the_db_once(tmp_path, sent):
    p = tmp_path / "rmp_professors.csv"
    p.write_text(
        "name,department,rating,num_ratings,would_take_again_pct,"
        "level_of_difficulty,professor_url\n"
        "Hamid Nayeb Hashemi,Mechanical Engineering,2.8,26,54%,3.6,http://x/1\n"
        "Hamid Nayeb Hashemi,Mechanical Engineering,0.0,0,,0.0,http://x/2\n"
        "Ann Lee,Computer Science,4.9,12,90%,3.0,http://x/3\n",
        encoding="utf-8")
    conf = M.TABLES["rmp_professors"]
    M.upload_csv(FakeConn(), "rmp_professors", conf["columns"], str(p),
                 conf["transform"], conf["on_conflict"], conf.get("key_columns"),
                 set(), conf.get("dedupe_on"), conf.get("dedupe_prefer"))
    rows = [r for _, batch in sent for r in batch]
    assert len(rows) == 2, "the duplicate would abort the whole INSERT"
    hamid = [r for r in rows if r[0] == "Hamid Nayeb Hashemi"][0]
    cols = conf["columns"]
    assert dict(zip(cols, hamid))["num_ratings"] == 26, "kept the stub, not the real page"


def test_tables_without_dedupe_send_every_row(tmp_path, sent):
    # Reviews legitimately repeat a professor; nothing may be collapsed there.
    p = tmp_path / "rmp_reviews.csv"
    p.write_text(
        "professor_name,department,overall_rating,course,quality,difficulty,"
        "date,tags,attendance,grade,textbook,online_class,comment\n"
        "Ann Lee,CS,4.9,CS1000,5,3,2026-01-01,,Mandatory,,No,No,one\n"
        "Ann Lee,CS,4.9,CS1000,5,3,2026-01-01,,Mandatory,,No,No,two\n",
        encoding="utf-8")
    conf = M.TABLES["rmp_reviews"]
    M.upload_csv(FakeConn(), "rmp_reviews", conf["columns"], str(p),
                 conf["transform"], conf["on_conflict"], None, set(),
                 conf.get("dedupe_on"), conf.get("dedupe_prefer"))
    assert len([r for _, batch in sent for r in batch]) == 2


def test_reviews_still_skip_rows_the_db_already_has(tmp_path, sent):
    # Guard the RU-saving path the reviews table depends on.
    p = tmp_path / "rmp_reviews.csv"
    p.write_text(
        "professor_name,department,overall_rating,course,quality,difficulty,"
        "date,tags,attendance,grade,textbook,online_class,comment\n"
        "Ann Lee,CS,4.9,CS1000,5,3,2026-01-01,,Mandatory,,No,No,old\n"
        "Ann Lee,CS,4.9,CS2000,5,3,2026-02-02,,Mandatory,,No,No,new\n",
        encoding="utf-8")
    conf = M.TABLES["rmp_reviews"]
    M.upload_csv(FakeConn(), "rmp_reviews", conf["columns"], str(p),
                 conf["transform"], conf["on_conflict"], conf["key_columns"],
                 {("Ann Lee", "CS1000", "2026-01-01")})
    rows = [r for _, batch in sent for r in batch]
    assert len(rows) == 1, "the already-loaded review should not be re-sent"
