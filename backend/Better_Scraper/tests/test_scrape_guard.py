"""The guard between a finished scrape and the DB / data-store writes.

Static row floors do not survive the school growing. The floor that shipped was
`< 3000` against a normal haul of 3,892 professors — four pages of 1,000 — so
losing the final 892-row page left exactly 3,000 and passed by one row. Worse,
the gap widens: at 5,000 professors a whole lost 1,000-row page still clears a
3,000 floor. The fix is to measure against what the data store held before this
run, which the workflow clones anyway.
"""
import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import scrape_guard as G  # noqa: E402


def _csv(path, rows, header=("a", "b")):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i in range(rows):
            w.writerow([f"r{i}", "x"])


def _store(tmp_path, profs=3892, reviews=44536):
    """A data store directory holding every file precompute needs."""
    d = tmp_path / "output_data"
    d.mkdir()
    _csv(d / "rmp_professors.csv", profs)
    _csv(d / "rmp_reviews.csv", reviews)
    for name in ("trace_courses.csv", "trace_scores.csv",
                 "professor_photos.csv", "trace_comments.csv"):
        _csv(d / name, 5)
    return str(d)


# ── row counting ────────────────────────────────────────────────────────────

def test_row_count_excludes_the_header(tmp_path):
    p = tmp_path / "x.csv"
    _csv(p, 10)
    assert G.count_rows(str(p)) == 10


def test_row_count_handles_newlines_inside_quoted_comments(tmp_path):
    # RMP review text contains newlines; `wc -l` would over-count these.
    p = tmp_path / "x.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["professor_name", "comment"])
        w.writerow(["A", "line one\nline two\nline three"])
        w.writerow(["B", "plain"])
    assert G.count_rows(str(p)) == 2


# ── required files ──────────────────────────────────────────────────────────

def test_missing_required_file_is_reported(tmp_path):
    d = _store(tmp_path)
    os.remove(os.path.join(d, "trace_scores.csv"))
    assert any("trace_scores.csv" in p for p in G.problems(d, {}))


def test_trace_comments_zip_satisfies_the_csv_requirement(tmp_path):
    # precompute falls back to the .zip, so either form is complete.
    d = _store(tmp_path)
    os.rename(os.path.join(d, "trace_comments.csv"),
              os.path.join(d, "trace_comments.zip"))
    assert G.problems(d, {}) == []


def test_missing_trace_comments_entirely_is_reported(tmp_path):
    d = _store(tmp_path)
    os.remove(os.path.join(d, "trace_comments.csv"))
    assert any("trace_comments" in p for p in G.problems(d, {}))


# ── absolute floors (backstop for a first run with no baseline) ─────────────

def test_absolute_professor_floor_is_enforced_without_a_baseline(tmp_path):
    d = _store(tmp_path, profs=2500)
    assert any("rmp_professors.csv" in p and "2,500" in p for p in G.problems(d, {}))


def test_absolute_review_floor_is_enforced_without_a_baseline(tmp_path):
    d = _store(tmp_path, reviews=12000)
    assert any("rmp_reviews.csv" in p for p in G.problems(d, {}))


def test_a_healthy_scrape_with_no_baseline_passes(tmp_path):
    assert G.problems(_store(tmp_path), {}) == []


# ── relative floors: the case the static floor missed ───────────────────────

def test_a_lost_final_search_page_is_caught(tmp_path):
    # The exact scenario: 3,892 professors, last page of 892 lost. Clears the
    # 3000 absolute floor by one row; must not clear the relative one.
    baseline = {"rmp_professors.csv": 3892, "rmp_reviews.csv": 44536}
    d = _store(tmp_path, profs=3000, reviews=34300)
    found = G.problems(d, baseline)
    assert any("rmp_professors.csv" in p for p in found)
    assert any("3,892" in p for p in found), "must name the count it compared against"


def test_normal_week_over_week_growth_passes(tmp_path):
    # Measured drift between two real scrapes: +30 professors, +1,863 reviews.
    baseline = {"rmp_professors.csv": 3862, "rmp_reviews.csv": 42673}
    assert G.problems(_store(tmp_path, profs=3892, reviews=44536), baseline) == []


def test_a_small_legitimate_shrink_passes(tmp_path):
    # Professors do leave RMP; a 1% dip must not block the refresh.
    baseline = {"rmp_professors.csv": 3892, "rmp_reviews.csv": 44536}
    assert G.problems(_store(tmp_path, profs=3853, reviews=44100), baseline) == []


def test_a_shrink_past_the_tolerance_is_caught(tmp_path):
    baseline = {"rmp_professors.csv": 3892, "rmp_reviews.csv": 44536}
    d = _store(tmp_path, profs=3892, reviews=40000)  # -10% reviews, profs fine
    found = G.problems(d, baseline)
    assert any("rmp_reviews.csv" in p for p in found)
    assert not any("rmp_professors.csv" in p for p in found)


def test_a_zero_baseline_does_not_block_a_first_run(tmp_path):
    # Empty store: nothing to compare against, absolute floors still apply.
    assert G.problems(_store(tmp_path), {"rmp_professors.csv": 0,
                                         "rmp_reviews.csv": 0}) == []


# ── baseline capture ────────────────────────────────────────────────────────

def test_baseline_round_trips(tmp_path):
    d = _store(tmp_path, profs=3892, reviews=44536)
    out = str(tmp_path / "baseline.json")
    G.write_baseline(d, out)
    assert G.read_baseline(out) == {"rmp_professors.csv": 3892,
                                    "rmp_reviews.csv": 44536}


def test_baseline_of_an_empty_store_records_zeros(tmp_path):
    # First ever run: the clone has no RMP CSVs yet.
    d = str(tmp_path / "empty")
    os.mkdir(d)
    out = str(tmp_path / "baseline.json")
    assert G.write_baseline(d, out) == {"rmp_professors.csv": 0,
                                        "rmp_reviews.csv": 0}


def test_a_missing_baseline_file_reads_as_empty(tmp_path):
    assert G.read_baseline(str(tmp_path / "nope.json")) == {}


def test_a_corrupt_baseline_file_reads_as_empty(tmp_path):
    p = tmp_path / "baseline.json"
    p.write_text("not json{")
    assert G.read_baseline(str(p)) == {}


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_check_exits_nonzero_and_annotates_on_failure(tmp_path, capsys):
    d = _store(tmp_path, profs=2000)
    rc = G.main(["check", "--data-dir", d])
    assert rc == 1
    assert "::error::" in capsys.readouterr().out


def test_check_exits_zero_on_a_healthy_scrape(tmp_path):
    assert G.main(["check", "--data-dir", _store(tmp_path)]) == 0


def test_baseline_subcommand_writes_the_file(tmp_path):
    d = _store(tmp_path, profs=3892, reviews=44536)
    out = str(tmp_path / "b.json")
    assert G.main(["baseline", "--data-dir", d, "--out", out]) == 0
    assert json.loads(open(out).read())["rmp_professors.csv"] == 3892


def test_check_uses_the_baseline_when_one_is_given(tmp_path):
    d = _store(tmp_path, profs=3892, reviews=44536)
    out = str(tmp_path / "b.json")
    G.main(["baseline", "--data-dir", d, "--out", out])
    _csv(os.path.join(d, "rmp_professors.csv"), 3000)  # simulate the lost page
    assert G.main(["check", "--data-dir", d, "--baseline", out]) == 1
