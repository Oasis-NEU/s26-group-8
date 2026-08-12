"""The data-refresh guardrails, as a tested module instead of inline bash.

A broken or rate-limited scrape must not overwrite good data. The floors that
enforce that lived in a bash block inside data-refresh.yml, where nothing ran
them until the weekly cron fired — and the one cron that has fired, failed.

Two layers, both checked before any DB or data-store write:
  - absolute floors catch catastrophic failures and cover the first run, when
    there is no baseline to compare against,
  - a relative floor against the pre-scrape baseline catches degraded scrapes
    that clear the absolute floor anyway. 3,100 professors beats the 3,000
    floor and would then force-push over the only copy of good data.

The relative floor is 98%, not the 95% that shipped: the observed week-over-week
delta is +49 reviews on 44,508, so 95% tolerates losing 2,225 real reviews.

Counting (I/O) is separate from the floor policy (pure), so the policy tests
carry realistic row counts without writing 1.5M lines to disk.

No database and no network.
"""

import zipfile

from Better_Scraper.scrape_guard import (
    ABSOLUTE_FLOORS,
    RELATIVE_FLOOR_PCT,
    check,
    collect_counts,
    count_rows,
    resolve_path,
)

# The real store as of the 2026-08-09 refresh.
HEALTHY = {
    "rmp_professors": 3889,
    "rmp_reviews": 44508,
    "trace_courses": 99784,
    "trace_scores": 813731,
    "professor_photos": 2855,
    "trace_comments": 1529226,
}


def write_csv(path, rows, header="a,b"):
    body = "".join(f"{i},x\n" for i in range(rows))
    path.write_text(header + "\n" + body)
    return path


# ── counting ────────────────────────────────────────────────────────────────

def test_count_rows_counts_data_rows(tmp_path):
    # A 3-row file is 4 lines on disk; the floors compare data rows.
    assert count_rows(write_csv(tmp_path / "f.csv", 3)) == 3


def test_count_rows_on_header_only_file_is_zero(tmp_path):
    assert count_rows(write_csv(tmp_path / "f.csv", 0)) == 0


def test_count_rows_reads_the_csv_inside_a_zip(tmp_path):
    inner = write_csv(tmp_path / "trace_comments.csv", 5)
    zpath = tmp_path / "trace_comments.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(inner, arcname="trace_comments.csv")
    inner.unlink()
    assert count_rows(zpath) == 5


def test_count_rows_handles_embedded_newlines_in_quoted_comments(tmp_path):
    # TRACE comments and RMP review text contain newlines inside quotes; a
    # line count would over-report and let a truncated file clear the floor.
    path = tmp_path / "f.csv"
    path.write_text('comment,x\n"line one\nline two",1\n"another",2\n')
    assert count_rows(path) == 2


# ── locating files ──────────────────────────────────────────────────────────

def test_trace_comments_is_accepted_as_csv(tmp_path):
    write_csv(tmp_path / "trace_comments.csv", 1)
    assert resolve_path(tmp_path, "trace_comments").name == "trace_comments.csv"


def test_trace_comments_is_accepted_as_zip(tmp_path):
    # The uncompressed file is ~415MB and exceeds GitHub's 100MB limit, so the
    # data store tracks only the zip; precompute.py reads it directly.
    (tmp_path / "trace_comments.zip").write_bytes(b"")
    assert resolve_path(tmp_path, "trace_comments").name == "trace_comments.zip"


def test_trace_scores_is_accepted_as_csv(tmp_path):
    write_csv(tmp_path / "trace_scores.csv", 1)
    assert resolve_path(tmp_path, "trace_scores").name == "trace_scores.csv"


def test_trace_scores_is_accepted_as_zip(tmp_path):
    # 95.5MB at the 2026-08-11 export against GitHub's 100MB cap, after a 35%
    # jump in one scrape — the store will have to zip it, so the guard has to
    # count it either way or the refresh fails on a file that is simply there.
    (tmp_path / "trace_scores.zip").write_bytes(b"")
    assert resolve_path(tmp_path, "trace_scores").name == "trace_scores.zip"


def test_missing_file_resolves_to_none(tmp_path):
    assert resolve_path(tmp_path, "rmp_professors") is None


def test_collect_counts_reports_none_for_a_missing_file(tmp_path):
    write_csv(tmp_path / "rmp_professors.csv", 7)
    counts = collect_counts(tmp_path)
    assert counts["rmp_professors"] == 7
    assert counts["rmp_reviews"] is None


# ── absolute floors ─────────────────────────────────────────────────────────

def test_healthy_store_has_no_problems():
    assert check(HEALTHY, baseline=HEALTHY) == []


def test_file_below_its_absolute_floor_is_a_problem():
    counts = dict(HEALTHY, rmp_professors=2999)
    problems = check(counts, baseline=None)
    assert len(problems) == 1
    assert "rmp_professors" in problems[0]
    assert "3000" in problems[0]


def test_file_exactly_at_its_absolute_floor_passes():
    assert check(dict(HEALTHY, rmp_professors=3000), baseline=None) == []


def test_missing_file_is_a_problem_even_when_every_other_file_is_healthy():
    # precompute.py reads all six files; a missing one crashes it mid-run,
    # after the RMP load has already written to the DB.
    problems = check(dict(HEALTHY, trace_scores=None), baseline=HEALTHY)
    assert len(problems) == 1
    assert "trace_scores" in problems[0]
    assert "issing" in problems[0]


def test_every_file_gets_an_absolute_floor():
    # The shipped bash checked counts for rmp_professors and rmp_reviews only;
    # the other four got an existence check and nothing more.
    assert set(ABSOLUTE_FLOORS) == set(HEALTHY)


# ── relative floor ──────────────────────────────────────────────────────────

def test_scrape_that_clears_the_absolute_floor_but_drops_vs_baseline_is_a_problem():
    # The case the absolute floors miss: 3,100 professors beats the 3,000 floor
    # and would force-push over the only good copy.
    problems = check(dict(HEALTHY, rmp_professors=3100), baseline=HEALTHY)
    assert len(problems) == 1
    assert "3100" in problems[0]
    assert "3889" in problems[0]


def test_count_exactly_at_the_relative_floor_passes():
    floor = HEALTHY["rmp_reviews"] * RELATIVE_FLOOR_PCT // 100
    assert check(dict(HEALTHY, rmp_reviews=floor), baseline=HEALTHY) == []


def test_count_one_below_the_relative_floor_is_a_problem():
    floor = HEALTHY["rmp_reviews"] * RELATIVE_FLOOR_PCT // 100
    problems = check(dict(HEALTHY, rmp_reviews=floor - 1), baseline=HEALTHY)
    assert len(problems) == 1


def test_growth_over_baseline_passes():
    assert check(dict(HEALTHY, rmp_reviews=44557), baseline=HEALTHY) == []


def test_relative_floor_applies_to_every_file_not_just_the_rmp_pair():
    problems = check(dict(HEALTHY, trace_scores=700000), baseline=HEALTHY)
    assert len(problems) == 1
    assert "trace_scores" in problems[0]


def test_missing_baseline_degrades_to_absolute_floors(tmp_path):
    # First run, or a rebuilt data store: no baseline to compare against must
    # not block the run.
    assert check(dict(HEALTHY, rmp_reviews=31000), baseline=None) == []


def test_baseline_entry_of_zero_is_ignored(tmp_path):
    # A file absent from last week's store records 0; comparing against it
    # would pass everything, and treating it as a drop would block forever.
    assert check(HEALTHY, baseline=dict(HEALTHY, rmp_reviews=0)) == []


def test_accept_lower_skips_the_relative_floor():
    counts = dict(HEALTHY, rmp_professors=3100)
    assert check(counts, baseline=HEALTHY, accept_lower=True) == []


def test_accept_lower_still_enforces_absolute_floors():
    # The escape hatch is for a real drop, not for a broken scrape.
    counts = dict(HEALTHY, rmp_professors=2999)
    assert len(check(counts, baseline=HEALTHY, accept_lower=True)) == 1


def test_all_problems_are_reported_not_just_the_first():
    counts = dict(HEALTHY, rmp_professors=10, rmp_reviews=10, trace_scores=None)
    assert len(check(counts, baseline=HEALTHY)) == 3
