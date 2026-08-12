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
    FLOOR_BASIS_PCT,
    HEALTHY_COUNTS,
    RELATIVE_FLOOR_PCT,
    STALE_FLOOR_RATIO,
    check,
    collect_counts,
    count_rows,
    resolve_path,
    stale_floors,
)

# The real store as of the 2026-08-12 export, which is what ABSOLUTE_FLOORS is
# derived from. Kept as the module's own record so a re-measure updates one place.
HEALTHY = dict(HEALTHY_COUNTS)


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
    counts = dict(HEALTHY, rmp_professors=3099)
    problems = check(counts, baseline=None)
    assert len(problems) == 1
    assert "rmp_professors" in problems[0]
    assert "3100" in problems[0]


def test_file_exactly_at_its_absolute_floor_passes():
    assert check(dict(HEALTHY, rmp_professors=3100), baseline=None) == []


def test_missing_file_is_a_problem_even_when_every_other_file_is_healthy():
    # precompute.py reads all six files; a missing one crashes it mid-run,
    # after the RMP load has already written to the DB.
    problems = check(dict(HEALTHY, trace_scores=None), baseline=HEALTHY)
    assert len(problems) == 1
    assert "trace_scores" in problems[0]
    assert "issing" in problems[0]


def test_absolute_floors_track_the_healthy_counts():
    """A floor left behind by a growing store stops being the ~80% it claims.

    The first set was derived from the 2026-08-09 store and never re-measured.
    By 2026-08-12 the store had grown 35%, putting trace_scores at 59% of its
    floor's basis and professor_photos at 56% — so either could have lost 40% of
    its rows on a baseline-less run and been waved through as healthy. Nothing
    failed, because the relative floor covers every run that has a baseline and
    the absolute floors only decide the runs that do not.
    """
    lo, hi = FLOOR_BASIS_PCT
    for name, floor in ABSOLUTE_FLOORS.items():
        pct = floor * 100.0 / HEALTHY_COUNTS[name]
        assert lo <= pct <= hi, (
            f"{name}: floor {floor} is {pct:.0f}% of the recorded healthy "
            f"{HEALTHY_COUNTS[name]}, outside the {lo}-{hi}% band. Re-measure "
            "both together."
        )


def test_healthy_store_trips_no_staleness_warning():
    # The floors are current as of HEALTHY_COUNTS, so the store they were
    # measured from must not be reported as having outgrown them.
    assert stale_floors(HEALTHY) == []


def test_a_store_that_has_outgrown_its_floor_is_reported():
    grown = dict(HEALTHY, trace_scores=int(
        ABSOLUTE_FLOORS["trace_scores"] * STALE_FLOOR_RATIO) + 1)
    notes = stale_floors(grown)
    assert len(notes) == 1
    assert "trace_scores" in notes[0]
    assert "re-measure" in notes[0].lower()


def test_staleness_is_advisory_not_a_violation():
    # Growth is the healthy direction; it must never fail the run, or a growing
    # corpus would block its own refresh.
    grown = dict(HEALTHY, trace_scores=ABSOLUTE_FLOORS["trace_scores"] * 10)
    assert stale_floors(grown) != []
    assert check(grown, baseline=None) == []


def test_a_missing_file_is_not_reported_as_stale():
    # None means absent, which `check` already reports as a hard problem; a
    # second advisory line about it would be noise.
    assert stale_floors(dict(HEALTHY, trace_scores=None)) == []


def test_every_file_gets_an_absolute_floor():
    # The shipped bash checked counts for rmp_professors and rmp_reviews only;
    # the other four got an existence check and nothing more.
    assert set(ABSOLUTE_FLOORS) == set(HEALTHY)


# ── relative floor ──────────────────────────────────────────────────────────

def test_scrape_that_clears_the_absolute_floor_but_drops_vs_baseline_is_a_problem():
    # The case the absolute floors miss: 3,200 professors beats the 3,100 floor
    # and would force-push over the only good copy.
    problems = check(dict(HEALTHY, rmp_professors=3200), baseline=HEALTHY)
    assert len(problems) == 1
    assert "3200" in problems[0]
    assert str(HEALTHY["rmp_professors"]) in problems[0]


def test_count_exactly_at_the_relative_floor_passes():
    floor = HEALTHY["rmp_reviews"] * RELATIVE_FLOOR_PCT // 100
    assert check(dict(HEALTHY, rmp_reviews=floor), baseline=HEALTHY) == []


def test_count_one_below_the_relative_floor_is_a_problem():
    floor = HEALTHY["rmp_reviews"] * RELATIVE_FLOOR_PCT // 100
    problems = check(dict(HEALTHY, rmp_reviews=floor - 1), baseline=HEALTHY)
    assert len(problems) == 1


def test_growth_over_baseline_passes():
    assert check(dict(HEALTHY, rmp_reviews=HEALTHY["rmp_reviews"] + 49), baseline=HEALTHY) == []


def test_relative_floor_applies_to_every_file_not_just_the_rmp_pair():
    problems = check(dict(HEALTHY, trace_scores=900000), baseline=HEALTHY)
    assert len(problems) == 1
    assert "trace_scores" in problems[0]


def test_missing_baseline_degrades_to_absolute_floors(tmp_path):
    # First run, or a rebuilt data store: no baseline to compare against must
    # not block the run.
    assert check(dict(HEALTHY, rmp_reviews=36000), baseline=None) == []


def test_baseline_entry_of_zero_is_ignored(tmp_path):
    # A file absent from last week's store records 0; comparing against it
    # would pass everything, and treating it as a drop would block forever.
    assert check(HEALTHY, baseline=dict(HEALTHY, rmp_reviews=0)) == []


def test_accept_lower_skips_the_relative_floor():
    counts = dict(HEALTHY, rmp_professors=3200)
    assert check(counts, baseline=HEALTHY, accept_lower=True) == []


def test_accept_lower_still_enforces_absolute_floors():
    # The escape hatch is for a real drop, not for a broken scrape.
    counts = dict(HEALTHY, rmp_professors=3099)
    assert len(check(counts, baseline=HEALTHY, accept_lower=True)) == 1


def test_all_problems_are_reported_not_just_the_first():
    counts = dict(HEALTHY, rmp_professors=10, rmp_reviews=10, trace_scores=None)
    assert len(check(counts, baseline=HEALTHY)) == 3
