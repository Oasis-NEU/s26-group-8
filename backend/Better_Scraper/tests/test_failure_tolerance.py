"""One flaky professor must not cost everyone else a week of freshness.

The scraper exited non-zero if a single professor's reviews failed after three
passes. Under `bash -e` that fails the workflow step, so the DB load, precompute
and the data-store push are all skipped — roughly 3,900 professors stay stale
for seven days because one request didn't come back. The CSVs it had just
written die with the runner, and the "re-run to fill the gaps" in the exit
message has nothing to re-run it.

Tolerating a failure needs a second piece, though. precompute counts
num_ratings from the review rows actually present in the CSV
(apply_counted_num_ratings), and catalog_row emits rmp_rating=None when
num_ratings is 0. So a professor tolerated with zero rows doesn't just go stale
— their RMP rating disappears from the site entirely. Carrying their previous
rows forward is what makes the tolerance safe rather than merely quieter.
"""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import fetch_lite as F  # noqa: E402
from models import Professor  # noqa: E402

# Bound before any test monkeypatches F.RMPSchool with a factory.
REAL_SCHOOL = F.RMPSchool


# ── how many failures are tolerable ─────────────────────────────────────────

def test_a_single_failure_out_of_thousands_is_tolerated():
    assert F.failure_tolerance(3300) >= 1


def test_the_tolerance_scales_with_the_school():
    assert F.failure_tolerance(3300) > F.failure_tolerance(300)


def test_a_mass_failure_is_not_tolerated():
    # Half the school failing is a block or an API change, not flakiness.
    assert 1650 > F.failure_tolerance(3300)


def test_a_small_school_still_gets_a_floor():
    # 1% of 50 rounds to nothing; a tiny school shouldn't be stricter.
    assert F.failure_tolerance(50) >= F.MIN_TOLERATED_REVIEW_FAILURES


def test_tolerance_of_an_empty_run_is_the_floor():
    assert F.failure_tolerance(0) == F.MIN_TOLERATED_REVIEW_FAILURES


def test_the_real_scale_tolerates_well_under_two_percent():
    # ~3,300 professors carry ratings in the real corpus.
    assert F.failure_tolerance(3300) <= 66


# ── carrying forward the previous rows for a failed professor ───────────────

def _prev(*rows):
    """Previous-CSV rows as (professor_name, course, comment) triples."""
    return [{"professor_name": n, "department": "CS", "overall_rating": "4.0",
             "course": c, "quality": "4", "difficulty": "3", "date": "2026-01-01",
             "tags": "", "attendance": "", "grade": "", "textbook": "",
             "online_class": "", "comment": m} for n, c, m in rows]


def test_a_failed_professors_previous_reviews_are_carried_forward():
    prev = _prev(("Ann Lee", "CS1000", "a"), ("Bob Roe", "CS2000", "b"))
    out = F.carried_forward_rows(prev, fresh_names={"Bob Roe"}, failed_names={"Ann Lee"})
    assert [r["professor_name"] for r in out] == ["Ann Lee"]


def test_all_of_a_failed_professors_rows_come_back():
    prev = _prev(("Ann Lee", "CS1000", "a"), ("Ann Lee", "CS2000", "b"),
                 ("Ann Lee", "CS3000", "c"))
    out = F.carried_forward_rows(prev, fresh_names=set(), failed_names={"Ann Lee"})
    assert len(out) == 3


def test_a_professor_who_succeeded_is_not_carried_forward():
    # Their fresh rows are authoritative; re-adding old ones would resurrect
    # exactly the reviews the prune step exists to remove.
    prev = _prev(("Ann Lee", "CS1000", "deleted last week"))
    assert F.carried_forward_rows(prev, {"Ann Lee"}, set()) == []


def test_a_professor_with_no_failure_and_no_rows_is_not_carried_forward():
    # The phantom case: RMP claims a rating and serves none. Genuinely empty.
    prev = _prev(("Beth Cohen", "CS1000", "old"))
    assert F.carried_forward_rows(prev, set(), set()) == []


def test_a_namesake_that_succeeded_blocks_the_carry_forward():
    # Two profile pages share a name and the CSV has no id column, so carrying
    # forward by name while one namesake succeeded would duplicate their rows.
    prev = _prev(("Rick Arrowood", "MGT1000", "old"))
    assert F.carried_forward_rows(prev, {"Rick Arrowood"}, {"Rick Arrowood"}) == []


def test_carry_forward_of_an_absent_previous_file_is_empty(tmp_path):
    assert F.load_previous_reviews(str(tmp_path / "nope.csv")) == []


def test_previous_reviews_are_read_back_as_rows(tmp_path):
    p = tmp_path / "rmp_reviews.csv"
    p.write_text(
        "professor_name,department,overall_rating,course,quality,difficulty,"
        "date,tags,attendance,grade,textbook,online_class,comment\n"
        "Ann Lee,CS,4.9,CS1000,5,3,2026-01-01,,Mandatory,,No,No,nice\n",
        encoding="utf-8")
    rows = F.load_previous_reviews(str(p))
    assert len(rows) == 1 and rows[0]["professor_name"] == "Ann Lee"


# ── the two halves together, through the CSV writer ─────────────────────────

def _school_with(profs, failed_ids):
    school = REAL_SCHOOL.__new__(REAL_SCHOOL)
    school.professors_list = profs
    school.failed_review_fetches = {i: "boom" for i in failed_ids}
    school.review_fetch_attempts = len(profs)
    return school


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_the_written_csv_keeps_a_failed_professor_whole(tmp_path):
    from models import Review
    good = Professor(name="Bob Roe", department="CS", rating="4.0",
                     graphql_id="VGVhY2hlci0x")
    good.reviews = [Review(course="CS2000", comment="fresh")]
    bad = Professor(name="Ann Lee", department="CS", rating="4.5",
                    graphql_id="VGVhY2hlci0y")

    path = str(tmp_path / "rmp_reviews.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["professor_name", "department", "overall_rating", "course",
                    "quality", "difficulty", "date", "tags", "attendance",
                    "grade", "textbook", "online_class", "comment"])
        w.writerow(["Ann Lee", "CS", "4.5", "CS1000", "5", "3", "2026-01-01",
                    "", "", "", "", "", "last week"])

    school = _school_with([good, bad], ["VGVhY2hlci0y"])
    carried = F.carried_forward_rows(
        F.load_previous_reviews(path),
        fresh_names={"Bob Roe"}, failed_names={"Ann Lee"})
    school.dump_reviews_to_csv(path, extra_rows=carried)

    rows = _read(path)
    names = sorted(r["professor_name"] for r in rows)
    assert names == ["Ann Lee", "Bob Roe"], \
        "the failed professor must not vanish — precompute would blank their rating"
    assert [r["comment"] for r in rows if r["professor_name"] == "Ann Lee"] == ["last week"]


# ── main(): what the workflow step actually sees ────────────────────────────

def _run_main(monkeypatch, tmp_path, school_factory):
    """Drive main() with the network replaced by `school_factory`."""
    path = tmp_path / "rmp_professors.csv"
    monkeypatch.setattr(F, "RMPSchool", school_factory)
    monkeypatch.setattr(sys, "argv",
                        ["fetch_lite.py", "-s", "696", "--file_path", str(path)])
    try:
        F.main()
        return 0
    except SystemExit as e:
        return e.code


def _fake_school(profs, failed_ids, attempts=None):
    def factory(sid, scrape_reviews=True):
        s = REAL_SCHOOL.__new__(REAL_SCHOOL)
        s.school_id = sid
        s.school_name = "Northeastern University"
        s.professors_list = profs
        s.failed_review_fetches = {i: f"{i}: boom" for i in failed_ids}
        s.review_fetch_attempts = attempts if attempts is not None else len(profs)
        s.close = lambda: None
        return s
    return factory


def _profs(n_ok, n_failed):
    from models import Review
    out = []
    for i in range(n_ok):
        p = Professor(name=f"Ok {i}", department="CS", rating="4.0",
                      graphql_id=f"ok-{i}")
        p.reviews = [Review(course="CS1000", comment="fresh")]
        out.append(p)
    for i in range(n_failed):
        out.append(Professor(name=f"Bad {i}", department="CS", rating="4.0",
                             graphql_id=f"bad-{i}"))
    return out


def test_main_exits_zero_when_a_few_professors_fail(monkeypatch, tmp_path):
    profs = _profs(3000, 2)
    rc = _run_main(monkeypatch, tmp_path,
                   _fake_school(profs, ["bad-0", "bad-1"], attempts=3002))
    assert rc == 0, "two flaky professors must not cost everyone a week"
    assert (tmp_path / "rmp_reviews.csv").exists()


def test_main_exits_nonzero_when_failures_exceed_the_tolerance(monkeypatch, tmp_path):
    failed = [f"bad-{i}" for i in range(200)]
    profs = _profs(3000, 200)
    rc = _run_main(monkeypatch, tmp_path, _fake_school(profs, failed, attempts=3200))
    assert rc != 0
    assert "over the tolerance" in str(rc)


def test_main_carries_previous_rows_for_the_failed_professor(monkeypatch, tmp_path):
    reviews = tmp_path / "rmp_reviews.csv"
    reviews.write_text(
        "professor_name,department,overall_rating,course,quality,difficulty,"
        "date,tags,attendance,grade,textbook,online_class,comment\n"
        "Bad 0,CS,4.0,CS9000,5,3,2026-01-01,,,,,,kept from last week\n",
        encoding="utf-8")

    profs = _profs(10, 1)
    rc = _run_main(monkeypatch, tmp_path, _fake_school(profs, ["bad-0"], attempts=11))
    assert rc == 0

    rows = _read(str(reviews))
    bad = [r for r in rows if r["professor_name"] == "Bad 0"]
    assert len(bad) == 1 and bad[0]["comment"] == "kept from last week"
    assert len([r for r in rows if r["professor_name"].startswith("Ok")]) == 10


def test_main_writes_nothing_when_the_professor_search_was_truncated(monkeypatch, tmp_path):
    def factory(sid, scrape_reviews=True):
        raise F.ProfessorFetchError("teacher search failed on page 4 (3000 professors)")

    rc = _run_main(monkeypatch, tmp_path, factory)
    assert rc != 0
    assert not (tmp_path / "rmp_professors.csv").exists(), \
        "a truncated search must never reach the CSV"
    assert not (tmp_path / "rmp_reviews.csv").exists()


def test_main_still_refuses_an_empty_scrape(monkeypatch, tmp_path):
    rc = _run_main(monkeypatch, tmp_path, _fake_school([], []))
    assert rc != 0
    assert "0 professors" in str(rc)


def test_dump_without_extra_rows_is_unchanged(tmp_path):
    from models import Review
    p = Professor(name="Bob Roe", department="CS", rating="4.0")
    p.reviews = [Review(course="CS2000", comment="fresh")]
    school = _school_with([p], [])
    path = str(tmp_path / "r.csv")
    school.dump_reviews_to_csv(path)
    assert len(_read(path)) == 1
