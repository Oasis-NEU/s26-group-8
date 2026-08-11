"""
One-time precomputation script. Run locally to build derived tables in CockroachDB.
This runs on your local machine (needs pandas/numpy) so the deployed server doesn't.

Usage: python precompute.py
"""

import os, re, time, unicodedata
from html import unescape
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

CRDB_URL = os.getenv("NEW_CRDB_DATABASE_URL") or os.getenv("CRDB_DATABASE_URL")
if not CRDB_URL:
    raise RuntimeError("NEW_CRDB_DATABASE_URL required in .env")

# TRACE data only changes on a manual TRACE re-scrape, so the TRACE-side DB
# maintenance (course_catalog rebuild + name_key/course_code/total_responses/
# comment-ID backfills) is wasted work on weekly RMP-only refreshes. Set
# REFRESH_TRACE=false to skip it. The professors_catalog / stats_cache rebuild
# and the rmp_reviews name_key update always run (they depend on RMP data).
REFRESH_TRACE = os.getenv("REFRESH_TRACE", "true").strip().lower() not in ("0", "false", "no", "off")


def _connect(attempts=20):
    """The local resolver flakes on *.cockroachlabs.cloud; retry on DNS failure."""
    last = None
    for i in range(1, attempts + 1):
        try:
            return psycopg2.connect(CRDB_URL, sslmode="require")
        except psycopg2.OperationalError as e:
            if "could not translate host name" not in str(e):
                raise
            last = str(e)
            print(f"  DNS lookup flaked; retrying ({i}/{attempts})...")
            time.sleep(3)
    raise RuntimeError(f"Could not resolve CRDB host after {attempts} attempts.\n{last}")


def normalize_name(name):
    s = str(name).strip().lower()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def name_to_slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def upgrade_image_url(url):
    return re.sub(r'-\d+x\d+(?=\.\w+$)', '', str(url))


from prof_aliases import ALIAS_MAP

COLLEGE_MAP = {
    "Computer Science": "Khoury", "Information Science": "Khoury",
    "Information Systems": "Khoury", "Computer & Informational Tech.": "Khoury",
    "Computer amp Informational Tech.": "Khoury", "Computer  Informational Tech.": "Khoury",
    "Computer Engineering": "Khoury", "Cybersecurity": "Khoury",
    "Data Science": "Khoury", "Computer Information Systm": "Khoury",
    "Grad Engineering - Multidiscpl": "Engineering",
    "Engineering": "Engineering", "Electrical Engineering": "Engineering",
    "Mechanical Engineering": "Engineering", "Civil Engineering": "Engineering",
    "Chemical Engineering": "Engineering", "Industrial Engineering": "Engineering",
    "Materials Engineering": "Engineering", "Engineering Technology": "Engineering",
    "Electronics": "Engineering", "Electrical & Computer Engr": "Engineering",
    "Mechanical & Industrial Eng": "Engineering", "Civil & Environmental Eng": "Engineering",
    "Bioengineering": "Engineering", "Industrial Technology": "Engineering",
    "Business": "Business", "Business Administration": "Business",
    "Finance": "Business", "Finance & Insurance": "Business",
    "Accounting": "Business", "Accounting & Finance": "Business",
    "Marketing": "Business", "Management": "Business",
    "Entrepreneurship": "Business", "International Business": "Business",
    "Supply Chain Management": "Business", "Operations Management": "Business",
    "Managerial Science": "Business", "Organizational Behavior": "Business",
    "Organizational Leadership": "Business", "Human Resources Management": "Business",
    "Leadership": "Business",
    "Dean of College of Sciences": "Science",
    "Mathematics": "Science", "Physics": "Science", "Chemistry": "Science",
    "Biology": "Science", "Biochemistry": "Science",
    "Environmental Science": "Science", "Environmental Studies": "Science",
    "Marine Sciences": "Science", "Marine Biology": "Science",
    "Microbiology": "Science", "Biotechnology": "Science",
    "Geology": "Science", "Earth Science": "Science",
    "Biomedical": "Science", "Science": "Science", "Math": "Science",
    "Behavioral Neuroscience": "Science",
    "Art": "CAMD", "Art History": "CAMD", "Architecture": "CAMD",
    "Communication Studies": "CAMD", "Communication": "CAMD",
    "Communications": "CAMD", "Journalism": "CAMD",
    "Media": "CAMD", "Media Studies": "CAMD",
    "Graphic Design": "CAMD", "Design": "CAMD",
    "Music": "CAMD", "Music Technology": "CAMD", "Music Business": "CAMD",
    "Theater": "CAMD", "Game Design": "CAMD", "Fine Arts": "CAMD",
    "Visual Arts": "CAMD", "Cinema": "CAMD", "Photography": "CAMD",
    "Multimedia": "CAMD", "Creative Studies": "CAMD",
    "Health Science": "Health Sciences", "Health Sciences": "Health Sciences",
    "Nursing": "Health Sciences", "Pharmacy": "Health Sciences",
    "Physical Therapy": "Health Sciences",
    "Speech & Hearing Sciences": "Health Sciences",
    "Speech Language Pathology": "Health Sciences",
    "Health Management": "Health Sciences",
    "Health  Physical Education": "Health Sciences",
    "Medicine": "Health Sciences", "Regulatory Affairs": "Health Sciences",
    "Counseling Psychology": "Health Sciences", "Applied Psychology": "Health Sciences",
    "Political Science": "CSSH", "Economics": "CSSH", "History": "CSSH",
    "Psychology": "CSSH", "Sociology": "CSSH", "Philosophy": "CSSH",
    "English": "CSSH", "Writing": "CSSH", "Literature": "CSSH",
    "Linguistics": "CSSH", "Languages": "CSSH", "Modern Languages": "CSSH",
    "Spanish": "CSSH", "French": "CSSH", "Arabic": "CSSH",
    "Sign Language": "CSSH", "World Languages Center": "CSSH",
    "Criminal Justice": "CSSH", "Anthropology": "CSSH",
    "Human Services": "CSSH", "Religious Studies": "CSSH",
    "Judaic Studies": "CSSH", "International Studies": "CSSH",
    "International Affairs": "CSSH", "International Politics": "CSSH",
    "East Asian Studies": "CSSH", "Latin American Studies": "CSSH",
    "African-American Studies": "CSSH", "Women's Studies": "CSSH",
    "Women": "CSSH", "Social Science": "CSSH",
    "Public Policy": "CSSH", "Public Administration": "CSSH",
    "Urban Studies": "CSSH", "Humanities": "CSSH",
    "Education": "Professional Studies", "Professional Studies": "Professional Studies",
    "Col of Professional Studies": "Professional Studies",
    "Counseling & Educational Psych": "Professional Studies",
    "Counseling amp Educational Psych": "Professional Studies",
    "Counseling  Educational Psych": "Professional Studies",
    "Law": "Law",
}


def get_college(dept):
    if not isinstance(dept, str):
        return "Other"
    return COLLEGE_MAP.get(dept, "Other")


def chunk_insert(cur, sql, rows, page_size=5000):
    for i in range(0, len(rows), page_size):
        execute_values(cur, sql, rows[i:i + page_size])


def swap_in(conn, table):
    """Replace <table> with the freshly-built <table>_new without a
    missing-table window.

    CRDB v25.1+ autocommits before every DDL (autocommit_before_ddl=on), so a
    DROP+CREATE+INSERT rebuild exposes live readers to a missing/empty table and
    a crash mid-rebuild leaves no table at all. Instead: build into _new, then
    swap via two renames committed together (renames are metadata-only and
    allowed transactionally once the DDL autocommit is off for the session).
    A stray _old/_new from a crashed run is cleaned by the next run's DROPs.
    """
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {table}_old")
    conn.commit()
    try:
        cur.execute("SET autocommit_before_ddl = off")
        conn.commit()
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,)
        )
        if cur.fetchone():
            cur.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        cur.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.commit()  # both renames land together — no missing-table window
    except Exception as e:
        conn.rollback()
        cur = conn.cursor()
        # Fallback: per-statement renames (millisecond window, still crash-safe
        # — worst case is a stray _old plus one rename to redo, never a
        # missing table for more than an instant).
        print(f"  swap_in: transactional swap failed for {table} ({e}); using per-statement renames")
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,)
        )
        if cur.fetchone():
            cur.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            conn.commit()
        cur.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.commit()
    finally:
        try:
            cur.execute("RESET autocommit_before_ddl")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  swap_in: RESET autocommit_before_ddl failed for {table} ({e}); "
                  "later DDL in this run may fail")
    cur.execute(f"DROP TABLE IF EXISTS {table}_old")
    conn.commit()
    cur.close()


# (table, column) pairs whose NULLs mean migrate-inserted rows the TRACE
# maintenance backfills haven't processed.
TRACE_BACKFILL_PROBES = (
    ("trace_courses", "name_key"),
    ("trace_courses", "course_code"),
    ("trace_scores", "total_responses"),
    ("trace_comments", "tc_course_id"),
)


def trace_needs_maintenance(conn):
    """Safety net for REFRESH_TRACE=false: detect un-processed TRACE rows.

    migrate_to_crdb inserts new TRACE rows WITHOUT the precompute-added columns
    (name_key / course_code / total_responses / parsed comment IDs), so a NULL in
    any of them means TRACE data landed but was never backfilled. If a column
    doesn't exist yet (first run), maintenance is obviously needed. Cheap: a few
    EXISTS probes, run only when the flag would otherwise skip.
    """
    for table, col in TRACE_BACKFILL_PROBES:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {col} IS NULL)")
            if cur.fetchone()[0]:
                print(f"  Safety net: {table}.{col} has NULLs — forcing TRACE maintenance.")
                return True
        except Exception:
            conn.rollback()
            print(f"  Safety net: {table}.{col} not present — forcing TRACE maintenance.")
            return True
        finally:
            cur.close()
    return False


def trace_maintenance_leftovers(conn, cap=1000):
    """NULL counts that survived a full maintenance pass. Such rows are
    unprocessable by the backfills (e.g. comments with unparseable URLs) and
    re-trigger the safety net — and the heavy TRACE path — on every weekly
    run. Counts are capped so the probe stays cheap on multi-million-row
    tables."""
    leftovers = []
    for table, col in TRACE_BACKFILL_PROBES:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE {col} IS NULL LIMIT {cap + 1}) AS probe"
            )
            n = cur.fetchone()[0]
            if n:
                leftovers.append((table, col, n))
        except Exception as e:
            conn.rollback()
            print(f"::warning::{table}.{col}: leftover probe failed ({e}); "
                  "could not verify the maintenance pass cleared the NULLs.")
        finally:
            cur.close()
    return leftovers


TITLE_STOPWORDS = frozenset(
    {"and", "the", "of", "in", "to", "for", "a", "an", "with", "on", "at"})

# How much of a word an abbreviation has to keep before it identifies that word.
# "org" -> "organizational" is an abbreviation; "p" -> "physical" is a letter
# that prefix-matches a large part of the catalog.
TITLE_ABBREV_MIN = 3


def _title_tokens(title):
    """Content words of a course title, lowercased and stripped of punctuation."""
    words = re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).split()
    return [w for w in words if w not in TITLE_STOPWORDS]


def titles_are_variants(a, b):
    """True if two titles are one title written two ways.

    The test is the same content words in the same order, each pair agreeing up
    to abbreviation: "Intro to Psych" and "Introduction to Psychology" are one
    course, "Election 2024" and "Language and Power" are two.

    This decides is_topics, and the two directions of error are not symmetric. A
    false negative leaves a mediocre blended average on display — the behaviour
    that existed before the flag. A false positive *removes* a real course's
    rating. So the rule has to recognise the ways TRACE rewrites one title, not
    merely the punctuation differences a normalised string comparison can see:
    abbreviations are common across terms and share no normalised form at all.
    """
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return not ta and not tb
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if x == y:
            continue
        n = min(len(x), len(y))
        if n < TITLE_ABBREV_MIN or x[:n] != y[:n]:
            return False
    return True


def _has_unrelated_titles(titles):
    """True if any two of these titles are different courses rather than one
    course written two ways. Terms carry a handful of titles, so pairwise is
    cheap — and it has to be pairwise, because "written the same way as" is not
    transitive and a representative title would make the answer order-dependent."""
    titles = list(titles)
    return any(not titles_are_variants(titles[i], titles[j])
               for i in range(len(titles)) for j in range(i + 1, len(titles)))


def apply_counted_num_ratings(rmp_profs, review_keys):
    """Replace RMP's numRatings counter with the ratings we actually hold.

    numRatings is a denormalised aggregate RMP does not recalculate when a rating
    is added or removed, so it disagrees with the rating nodes RMP serves for 392
    professors — 376 low (by 1-3 apiece) and 16 high, 5 of whom claim a rating and
    serve none. Everything downstream counts from this field (total_reviews, the
    GOATED review floor, the shrinkage weight, n_rmp in the blend), so trusting
    the counter meant the displayed count disagreed with the reviews listed.

    Must run after merge_rmp_aliases — that folds RMP's duplicate profile pages
    onto one _name_key, and the reviews from all of them carry that same key — and
    before total_reviews is derived. Modifies rmp_profs in place; returns how many
    professors were corrected.
    """
    if rmp_profs.empty:
        return 0
    counts = pd.Series(list(review_keys), dtype=object).value_counts()
    before = pd.to_numeric(rmp_profs["num_ratings"], errors="coerce").fillna(0).astype(int)
    rmp_profs["num_ratings"] = (
        rmp_profs["_name_key"].map(counts).fillna(0).astype(int))
    return int((rmp_profs["num_ratings"] != before).sum())


def trace_review_counts(overall_merged):
    """TRACE ratings per instructor: responses to the overall question.

    Takes the frame the TRACE rating is averaged from (overall-question rows
    joined to a name_key) and sums the same weights, so `trace_rating` and
    `trace_reviews` describe one set of responses — the same pairing
    apply_counted_num_ratings and apply_counted_rmp_rating enforce on the RMP
    side. Everything downstream reads the sum as a precision: the GOATED review
    floor, the shrinkage weight, and the "Ratings" column beside the rating.

    It used to sum `completed` off one arbitrary question row per section
    (`drop_duplicates` keeps whichever row the frame happened to hold first),
    which was wrong three ways. `completed` counts students who submitted the
    survey, not students who answered the overall question. It is not constant
    across a section's question rows — 5,554 of 55,049 sections carry more than
    one value, some rows reporting 0 — so the total moved with row order. And
    sections whose survey form has no overall item at all counted in full toward
    a rating they contribute nothing to: 79 of Susan Sieloff's 83 sections, whose
    338 became 21.

    Measured on the 2026-08-03 corpus, this moves 3,328 of 5,441 professors
    (mean -4, worst -918) and takes 32 below BOARD_MIN_REVIEWS, which is the
    honest answer for professors who never had 30 ratings.

    total_responses rather than count_1..count_5 summed: the two agree on all
    54,265 overall rows in the corpus, and this is the column the mean is
    weighted by. They are the same column by then — main() rebuilds
    total_responses from the star counts above — except for rows carrying a mean
    with no distribution, where it falls back to `completed`. No overall row in
    the corpus does that today; if applyweb starts shipping one, this count will
    exceed what the profile page's rating distribution can draw, because there
    are no stars to draw.
    """
    if overall_merged.empty:
        return {}
    responses = pd.to_numeric(
        overall_merged["total_responses"], errors="coerce").fillna(0)
    return {k: int(v) for k, v in
            responses.groupby(overall_merged["name_key"]).sum().items()}


def apply_counted_rmp_rating(rmp_profs, review_keys, review_quality):
    """Recompute `rating` as the mean of the ratings we actually hold.

    The partner of apply_counted_num_ratings, and it has to be: the blend reads
    `rating` as the RMP measurement and num_ratings as its precision, so the two
    must describe one set of rows. Recounting the ratings while leaving RMP's
    stale average in place left the blend weighting one population by the size of
    another. It also supersedes the counter-weighted average merge_rmp_aliases
    builds across an RMP professor's duplicate profile pages — the stored ratings
    from all of those pages already carry the merged key, so averaging them is
    the same quantity measured directly instead of reconstructed.

    Quality outside 1-5 is a missing score rather than a score of zero, so it is
    dropped from the mean — but the rating node still exists, so it stays in the
    count. A professor with no usable quality keeps whatever RMP reported; there
    is no measurement to replace it with. Modifies rmp_profs in place; returns
    how many means moved.
    """
    if rmp_profs.empty:
        return 0
    quality = pd.DataFrame({
        "k": list(review_keys),
        "q": pd.to_numeric(pd.Series(list(review_quality)), errors="coerce"),
    }).dropna()
    quality = quality[(quality["q"] >= 1) & (quality["q"] <= 5)]
    means = quality.groupby("k")["q"].mean()
    before = pd.to_numeric(rmp_profs["rating"], errors="coerce")
    counted = rmp_profs["_name_key"].map(means)
    rmp_profs["rating"] = counted.where(counted.notna(), before)
    after = rmp_profs["rating"]
    # NaN != NaN in pandas, so a professor who had no rating before and has none
    # now would otherwise be reported as corrected.
    same = (after == before) | (after.isna() & before.isna())
    return int((~same).sum())


# ── Rating blend: calibrate, then pool by precision ──────────────────────────
# See docs/rating-blend-calibration.md for the measurements behind this.
#
# RMP and TRACE measure the same thing (corr +0.87 among well-evidenced
# professors) on different scales: RMP runs ~0.8 lower and is 2.4x wider, because
# it is voluntary and negatively self-selected while TRACE is administered to
# everyone. So the blend is two steps:
#
#   1. project RMP onto the TRACE scale using a fit refit from the data
#   2. pool the two by inverse variance, weighting each side by how many
#      responses it actually has and how precise a response is *on the TRACE
#      scale* (w = n * slope^2 / sigma^2)
#
# The old rule was (rmp + trace) / 2, which did neither: one RMP review carried
# the same weight as 300 TRACE responses, so a single 1-star could drag a
# well-liked professor to 2.81.
#
# Applies only to professors with *both* sources. Single-source professors keep
# their raw source rating — calibrating them would move 5,269 more ratings with
# no second source to check against.
#
# Both steps have to agree about which scale they are on, and getting that wrong
# is silent: an earlier version fitted with ordinary least squares and weighted
# with w = n / sigma^2, leaving sigma^2_rmp measured in RMP units while the value
# it weighted had already been divided by the slope. That understated RMP's
# precision by slope^2 (~3.6x) and collapsed the blend to TRACE-with-a-nudge —
# RMP moved the displayed number for 1.5% of two-source professors. Validated by
# hold-out (see the doc): the pair of fixes cuts RMSE against a well-measured
# TRACE truth by 36% at thin TRACE evidence, and improves 445 of 622 professors.
# Re-exported, not redefined: server.py fits the same mapping from the catalog to
# show a reader the projected RMP value the blend actually used, and two copies of
# a threshold that has to match is how the tooltip drifted out of agreement with
# the number beside it in the first place. Names stay on this module so
# measure_calibration and test_rating_blend read them where they always did.
from rating_scale import (                                        # noqa: E402
    FALLBACK_CALIBRATION,      # rmp ~ slope * trace + intercept
    CALIBRATION_MIN_RMP,       # what counts as well-evidenced for the fit
    CALIBRATION_MIN_TRACE,
    CALIBRATION_MIN_POINTS,    # too few pairs -> keep the fallback fit
    CALIBRATION_MIN_SLOPE,     # a flat slope makes the inverse explode
    CALIBRATION_MIN_CORR,      # unrelated (or inverted) scales -> no fit
    fit_rma,
)

FALLBACK_VARIANCES = (1.644, 0.534)    # per-response variance: RMP, TRACE


def fit_calibration(trace_ratings, rmp_ratings):
    """Fit `rmp ~ slope * trace + intercept`; returns (slope, intercept).

    Refit every run rather than hardcoded, because both scales drift with each
    re-scrape. Falls back to the measured constants when there is too little
    well-evidenced overlap to fit, or when the fit comes out degenerate (a slope
    at or below CALIBRATION_MIN_SLOPE would blow up the inverse projection).

    Slope is the ratio of standard deviations (reduced major axis), not the OLS
    coefficient, because this fit exists to be *inverted*. OLS minimises error in
    rmp given trace, so inverting it over-disperses: it stretches the projected
    values by 1/corr (measured 1.42x wider than TRACE's own spread). Matching the
    two spreads is what "project onto the TRACE scale" has to mean for the
    inverse-variance weights downstream to be in the same units.

    RMA takes its sign from the correlation, so unlike OLS it cannot notice an
    inverted relationship on its own — hence the explicit CALIBRATION_MIN_CORR
    guard, which also catches the zero-variance case where corr is undefined.

    The arithmetic and every threshold now live in rating_scale, because
    server.py needs this same mapping at request time and carries no pandas. What
    stays here is the coercion: this is fed raw frame columns that may hold
    strings or NaN, and pd.to_numeric is what makes them a pair of clean numeric
    vectors. rating_scale.fit_rma returns None for "do not trust this", so the
    fallback — and the warning measure_calibration prints about it — stays on
    this side.
    """
    x = pd.to_numeric(pd.Series(trace_ratings), errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(pd.Series(rmp_ratings), errors="coerce").to_numpy(dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    fit = fit_rma(x[keep].tolist(), y[keep].tolist())
    return FALLBACK_CALIBRATION if fit is None else fit


def trace_response_variance(counts):
    """Variance of one TRACE response, pooled across sections.

    `counts` is an (n_sections, 5) array of how many students picked 1..5. Only
    within-section spread counts: between-section differences are real signal
    (professors do differ), not response noise.
    """
    counts = np.asarray(counts, dtype=float)
    if counts.ndim != 2 or counts.shape[1] != 5:
        return None
    n = counts.sum(axis=1)
    counts = counts[n > 1]  # a 1-response section carries no spread information
    if len(counts) == 0:
        return None
    n = counts.sum(axis=1)
    scale = np.arange(1, 6, dtype=float)
    means = (counts @ scale) / n
    ss = (counts * (scale - means[:, None]) ** 2).sum()
    dof = n.sum() - len(counts)
    if dof <= 0 or ss <= 0:
        return None
    return float(ss / dof)


def rmp_response_variance(quality, name_keys):
    """Variance of one RMP rating, pooled within professor.

    Same reasoning as trace_response_variance: differences *between* professors
    are signal, so only the spread of reviews about the same professor is noise.
    """
    df = pd.DataFrame({
        "q": pd.to_numeric(pd.Series(list(quality)), errors="coerce"),
        "k": list(name_keys),
    }).dropna()
    df = df[(df["q"] >= 1) & (df["q"] <= 5)]
    df = df[df.groupby("k")["q"].transform("size") > 1]
    if df.empty:
        return None
    dev = df["q"] - df.groupby("k")["q"].transform("mean")
    dof = len(df) - df["k"].nunique()
    ss = float((dev ** 2).sum())
    if dof <= 0 or ss <= 0:
        return None
    return ss / dof


def calibrate_rmp(rmp_rating, calibration):
    """Project an RMP rating onto the TRACE scale, clipped to the 1-5 range.

    Inverse of the fit: the fit predicts RMP *from* TRACE, and we need the
    other direction. Clipping matters because RMP's wider spread projects the
    extremes past the ends of the scale.
    """
    slope, intercept = calibration
    return np.clip((np.asarray(rmp_rating, dtype=float) - intercept) / slope, 1.0, 5.0)


def blend_ratings(rmp_rating, n_rmp, trace_rating, n_trace, calibration, variances):
    """Inverse-variance pool of both sources, on the TRACE scale.

    Vectorised over numpy/pandas input; also accepts scalars. Callers must pass
    rows where both sources exist with n > 0 — a professor with no responses on
    either side has nothing to pool.

    `var_rmp` is measured in RMP units but weights a value calibrate_rmp has
    already divided by the slope, so it has to be converted the same way: the
    variance of the projected mean is var_rmp / (n * slope^2), making the
    precision n * slope^2 / var_rmp. Skipping the slope^2 leaves the two weights
    on different scales and silently mutes RMP.
    """
    slope, _ = calibration
    var_rmp, var_trace = variances
    w_rmp = np.asarray(n_rmp, dtype=float) * slope ** 2 / var_rmp
    w_trace = np.asarray(n_trace, dtype=float) / var_trace
    rmp_cal = calibrate_rmp(rmp_rating, calibration)
    trace = np.asarray(trace_rating, dtype=float)
    return (w_rmp * rmp_cal + w_trace * trace) / (w_rmp + w_trace)


def has_rmp_data(rmp_profs):
    return (rmp_profs["num_ratings"] > 0) & (rmp_profs["rating"] > 0)


def has_trace_data(rmp_profs):
    return rmp_profs["trace_overall"].notna() & (rmp_profs["trace_reviews"] > 0)


def measure_calibration(rmp_profs):
    """Refit the RMP->TRACE mapping from this run's own data.

    Only well-evidenced professors are used: thin samples on either side are
    mostly noise, and including them flattens the slope toward zero, which would
    understate how much wider the RMP scale is.
    """
    fit_rows = (has_rmp_data(rmp_profs) & has_trace_data(rmp_profs)
                & (rmp_profs["num_ratings"] >= CALIBRATION_MIN_RMP)
                & (rmp_profs["trace_reviews"] >= CALIBRATION_MIN_TRACE))
    calibration = fit_calibration(rmp_profs.loc[fit_rows, "trace_overall"],
                                  rmp_profs.loc[fit_rows, "rating"])
    if calibration == FALLBACK_CALIBRATION:
        print(f"  WARNING: calibration fell back to {FALLBACK_CALIBRATION} "
              f"({int(fit_rows.sum())} well-evidenced professors available)")
    else:
        print(f"Calibration fit on {int(fit_rows.sum())} professors: "
              f"rmp = {calibration[0]:.3f} * trace + {calibration[1]:.3f}")
    return calibration


def measure_variances(rmp_quality, rmp_keys, trace_counts):
    """Per-response variance of each source, measured from this run's data."""
    var_rmp = rmp_response_variance(rmp_quality, rmp_keys)
    var_trace = trace_response_variance(trace_counts)
    if var_rmp is None or var_trace is None:
        print(f"  WARNING: response variance not measurable, using {FALLBACK_VARIANCES}")
        return FALLBACK_VARIANCES
    # Deliberately not reported as a ratio: these are measured on each source's
    # own scale, and RMP's is ~2.4x wider, so "RMP is 3x noisier" would be an
    # artifact of the scales rather than a fact about the responses.
    # blend_ratings converts var_rmp with slope^2 before the two ever meet.
    print(f"Per-response variance: RMP {var_rmp:.3f} (RMP scale), "
          f"TRACE {var_trace:.3f} (TRACE scale)")
    return (var_rmp, var_trace)


def apply_blended_rating(rmp_profs, calibration, variances):
    """Write avg_rating in place; returns how many professors were *blended*.

    Two-source professors get the pooled, calibrated rating. Single-source
    professors get their own source's number put on the TRACE scale — for a
    TRACE-only professor that is already the case, and for an RMP-only professor
    it is calibrate_rmp.

    Calibration applies to them for the same reason it applies inside the blend:
    avg_rating is one column that professors are sorted, compared and ranked in,
    so every number in it has to mean the same thing. RMP runs ~0.8 lower and
    2.4x wider than TRACE, so leaving RMP-only professors raw showed them as
    meaningfully worse than TRACE-only professors of identical standing.

    The projection is a unit conversion, not an evidence-weighted estimate, and
    needs no second source to be valid — that is what separates it from the
    pooling below, which does. The return value counts only the pooling, since a
    one-sided conversion is not a blend.

    Visible consequence, and it is the intended one: RMP's range compresses onto
    TRACE's, so an RMP-only professor at 1.0 displays near 3.0 rather than 1.0.
    That is where the bottom of the RMP scale sits once measured against TRACE,
    and it is already what two-source professors have always shown.
    """
    if rmp_profs.empty:
        rmp_profs["avg_rating"] = pd.Series(dtype=float)
        return 0
    has_rmp, has_trace = has_rmp_data(rmp_profs), has_trace_data(rmp_profs)
    both = has_rmp & has_trace
    rmp_profs["avg_rating"] = np.where(
        has_trace, rmp_profs["trace_overall"].round(2),
        np.where(has_rmp,
                 np.round(calibrate_rmp(rmp_profs["rating"], calibration), 2),
                 np.nan))
    if both.any():
        blended = blend_ratings(
            rmp_profs.loc[both, "rating"], rmp_profs.loc[both, "num_ratings"],
            rmp_profs.loc[both, "trace_overall"], rmp_profs.loc[both, "trace_reviews"],
            calibration, variances)
        rmp_profs.loc[both, "avg_rating"] = np.round(blended, 2)
    # Carried over from the original blend. On a float column pandas keeps this
    # as NaN rather than None; the catalog insert is what converts it to NULL
    # (`float(...) if pd.notna(...) else None`), so unrated professors are safe.
    rmp_profs["avg_rating"] = rmp_profs["avg_rating"].where(
        rmp_profs["avg_rating"].notna(), other=None)
    print(f"Blended {int(both.sum())} two-source professors "
          f"({int((has_rmp & ~has_trace).sum())} RMP-only calibrated onto the "
          f"TRACE scale, {int(has_trace.sum() - both.sum())} TRACE-only already on it)")
    return int(both.sum())


def main():
    conn = _connect()

    # Effective TRACE decision: honor REFRESH_TRACE, but never skip when there is
    # un-backfilled TRACE data in the DB (self-heals a forgotten full run after a
    # manual TRACE re-scrape). The DB probe runs only when the flag says skip.
    do_trace = REFRESH_TRACE or trace_needs_maintenance(conn)
    if not REFRESH_TRACE:
        print(f"REFRESH_TRACE=false; TRACE maintenance {'forced by safety net' if do_trace else 'skipped'}")

    # Read from local CSVs (much faster than downloading from CRDB)
    csv_dir = os.path.join(os.path.dirname(__file__), "Better_Scraper", "output_data")
    print("Loading from local CSVs...")
    rmp_profs = pd.read_csv(os.path.join(csv_dir, "rmp_professors.csv"))
    print(f"  rmp_professors: {len(rmp_profs)}")
    rmp_reviews = pd.read_csv(os.path.join(csv_dir, "rmp_reviews.csv"))
    print(f"  rmp_reviews: {len(rmp_reviews)}")
    tc = pd.read_csv(os.path.join(csv_dir, "trace_courses.csv"))
    print(f"  trace_courses: {len(tc)}")
    ts = pd.read_csv(os.path.join(csv_dir, "trace_scores.csv"))
    print(f"  trace_scores: {len(ts)}")
    tcomments_path = os.path.join(csv_dir, "trace_comments.csv")
    if not os.path.exists(tcomments_path):
        tcomments_path = os.path.join(csv_dir, "trace_comments.zip")
    tcomments = pd.read_csv(tcomments_path)
    print(f"  trace_comments: {len(tcomments)}")
    photos = pd.read_csv(os.path.join(csv_dir, "professor_photos.csv"))
    print(f"  professor_photos: {len(photos)}")

    # CSVs use camelCase — rename to snake_case to match DB schema
    tc.rename(columns={
        "courseId": "course_id", "schoolCode": "school_code", "termId": "term_id",
        "termTitle": "term_title", "instructorId": "instructor_id",
        "termEndDate": "term_end_date", "instructorFirstName": "instructor_first_name",
        "instructorLastName": "instructor_last_name", "departmentName": "department_name",
        "displayName": "display_name",
    }, inplace=True)
    tc["department_name"] = tc["department_name"].astype(str).apply(unescape)

    # Backfill missing departments using course prefix (e.g. "ENGW" -> "English")
    # Affects future terms scraped before department metadata was populated
    tc["_prefix"] = tc["display_name"].str.extract(r"^([A-Z]+)\d")
    prefix_dept_map = (
        tc[tc["department_name"].notna() & (tc["department_name"] != "nan")]
        .groupby("_prefix")["department_name"]
        .agg(lambda x: x.value_counts().index[0])
    )
    missing = tc["department_name"].isna() | (tc["department_name"] == "nan")
    tc.loc[missing, "department_name"] = tc.loc[missing, "_prefix"].map(prefix_dept_map)
    tc.drop(columns=["_prefix"], inplace=True)
    ts.rename(columns={
        "courseId": "course_id", "instructorId": "instructor_id", "termId": "term_id",
    }, inplace=True)

    # ── Photo lookup ──
    photos["_key"] = photos["name"].astype(str).apply(normalize_name)
    photos["_url"] = photos["image_url"].astype(str).apply(upgrade_image_url)
    photo_lookup = dict(zip(photos["_key"], photos["_url"]))
    # Also map alias sources → canonical targets so both names find the photo
    for alias_src, alias_tgt in ALIAS_MAP.items():
        if alias_src in photo_lookup and alias_tgt not in photo_lookup:
            photo_lookup[alias_tgt] = photo_lookup[alias_src]
        elif alias_tgt in photo_lookup and alias_src not in photo_lookup:
            photo_lookup[alias_src] = photo_lookup[alias_tgt]

    # ── Focus lookups (default 50/30 when missing) ──
    def _focus_col(col):
        if col in photos.columns:
            return pd.to_numeric(photos[col], errors="coerce")
        return pd.Series([np.nan] * len(photos))

    photos["_fx"] = _focus_col("focus_x").fillna(50.0)
    photos["_fy"] = _focus_col("focus_y").fillna(30.0)
    focus_x_lookup = dict(zip(photos["_key"], photos["_fx"]))
    focus_y_lookup = dict(zip(photos["_key"], photos["_fy"]))
    for alias_src, alias_tgt in ALIAS_MAP.items():
        for lk in (focus_x_lookup, focus_y_lookup):
            if alias_src in lk and alias_tgt not in lk:
                lk[alias_tgt] = lk[alias_src]
            elif alias_tgt in lk and alias_src not in lk:
                lk[alias_src] = lk[alias_tgt]

    # ── Clean RMP data ──
    rmp_profs["rating"] = pd.to_numeric(rmp_profs["rating"], errors="coerce")
    rmp_profs["num_ratings"] = pd.to_numeric(rmp_profs["num_ratings"], errors="coerce")
    rmp_profs.dropna(subset=["rating", "num_ratings"], inplace=True)
    rmp_profs["name"] = rmp_profs["name"].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    rmp_profs["department"] = rmp_profs["department"].astype(str).str.replace(r'\bamp\b', '&', regex=True)

    # ── Fix TRACE scores ──
    for col in ["count_1", "count_2", "count_3", "count_4", "count_5", "completed"]:
        ts[col] = pd.to_numeric(ts[col], errors="coerce").fillna(0).astype(int)
    ts["total_responses"] = ts["count_1"] + ts["count_2"] + ts["count_3"] + ts["count_4"] + ts["count_5"]
    ts["_weighted_sum"] = 1*ts["count_1"] + 2*ts["count_2"] + 3*ts["count_3"] + 4*ts["count_4"] + 5*ts["count_5"]
    # Preserve the original CSV mean when individual counts are all zeros (newer data may only have mean/median)
    ts["_csv_mean"] = pd.to_numeric(ts["mean"], errors="coerce")
    is_hours = ts["question"].str.lower().str.contains("hours", na=False)
    ts["mean"] = np.where(
        is_hours,
        ts["_csv_mean"],  # always preserve scraper-computed mean for hours (uses real hour midpoints)
        np.where(
            ts["total_responses"] > 0,
            ts["_weighted_sum"] / ts["total_responses"],
            ts["_csv_mean"]
        )
    )
    # Use completed count as total_responses when individual counts are missing but mean exists
    ts["total_responses"] = np.where(
        (ts["total_responses"] == 0) & ts["mean"].notna(),
        ts["completed"],
        ts["total_responses"]
    )

    # ── Merge RMP aliases ──
    def merge_rmp_aliases(df):
        df["_name_key"] = df["name"].apply(normalize_name)
        df["_name_key"] = df["_name_key"].replace(ALIAS_MAP)
        rows = []
        for nk, g in df.groupby("_name_key"):
            if len(g) == 1:
                rows.append(g.iloc[0])
                continue
            g = g.sort_values("num_ratings", ascending=False)
            primary = g.iloc[0].copy()
            tot = g["num_ratings"].sum()
            if tot > 0:
                primary["rating"] = (g["rating"] * g["num_ratings"]).sum() / tot
                if "level_of_difficulty" in g.columns:
                    diffs = pd.to_numeric(g["level_of_difficulty"], errors="coerce")
                    if diffs.notna().any():
                        primary["level_of_difficulty"] = (diffs.fillna(0) * g["num_ratings"]).sum() / g.loc[diffs.notna(), "num_ratings"].sum()
                if "would_take_again_pct" in g.columns:
                    wtas = pd.to_numeric(
                        g["would_take_again_pct"].astype(str).str.replace("%", "").replace({"N/A": None, "": None}),
                        errors="coerce"
                    )
                    if wtas.notna().any():
                        val = (wtas.fillna(0) * g["num_ratings"]).sum() / g.loc[wtas.notna(), "num_ratings"].sum()
                        primary["would_take_again_pct"] = f"{round(val, 1)}%"
            primary["num_ratings"] = tot
            primary["name"] = nk.title()
            rows.append(primary)
        return pd.DataFrame(rows).reset_index(drop=True)

    rmp_profs = merge_rmp_aliases(rmp_profs)
    rmp_profs["college"] = rmp_profs["department"].apply(get_college)

    # ── TRACE name keys ──
    tc["_first"] = tc["instructor_first_name"].apply(normalize_name)
    tc["_last"] = tc["instructor_last_name"].apply(normalize_name)
    tc["name_key"] = (tc["_first"] + " " + tc["_last"]).apply(normalize_name)
    tc["term_id"] = pd.to_numeric(tc["term_id"], errors="coerce")

    # ── TRACE department lookup ──
    dept_sorted = tc.sort_values("term_id", ascending=False).drop_duplicates(subset=["name_key"])
    trace_dept_lookup = dict(zip(dept_sorted["name_key"], dept_sorted["department_name"]))

    # ── TRACE proper name lookup ──
    name_sorted = tc.sort_values("term_id", ascending=False).drop_duplicates(subset=["name_key"])
    name_sorted["_full"] = (name_sorted["instructor_first_name"].astype(str).str.strip() + " " + name_sorted["instructor_last_name"].astype(str).str.strip()).str.title()
    valid = name_sorted["instructor_first_name"].astype(str).str.strip().ne("") & name_sorted["instructor_last_name"].astype(str).str.strip().ne("")
    trace_name_lookup = dict(zip(name_sorted.loc[valid, "name_key"], name_sorted.loc[valid, "_full"]))

    # ── TRACE overall rating (weighted avg of "overall" questions) ──
    # Law sections carry two overall questions; ratings use 'Overall Course' only. The
    # exclusion is exact-match because the Bluera label also contains "effectiveness".
    ts["question"] = ts["question"].astype(str)
    _q_lower = ts["question"].str.lower()
    overall = ts[_q_lower.str.contains("overall", na=False) & (_q_lower != "overall effectiveness")].copy()
    overall.dropna(subset=["mean"], inplace=True)

    instructor_courses = tc[["course_id", "instructor_id", "name_key"]].drop_duplicates()
    merged = overall.merge(instructor_courses, on=["course_id", "instructor_id"], how="inner")

    def weighted_avg(group):
        w = group["total_responses"]
        v = group["mean"]
        total_w = w.sum()
        return (v * w).sum() / total_w if total_w > 0 else np.nan

    trace_avg = merged.groupby("name_key").apply(weighted_avg, include_groups=False).reset_index().rename(columns={0: "trace_overall"})
    trace_lookup = dict(zip(trace_avg["name_key"], trace_avg["trace_overall"]))
    print(f"Matched {len(trace_lookup)} instructors to TRACE overall scores")

    # ── TRACE hours per week (weighted avg of hours question) ──
    hours_q = ts[ts["question"].str.lower().str.contains("hours per week", na=False)].copy()
    hours_q.dropna(subset=["mean"], inplace=True)
    hours_merged = hours_q.merge(instructor_courses, on=["course_id", "instructor_id"], how="inner")
    hours_avg = hours_merged.groupby("name_key").apply(weighted_avg, include_groups=False).reset_index()
    if 0 in hours_avg.columns:
        hours_lookup = dict(zip(hours_avg["name_key"], hours_avg.rename(columns={0: "avg_hours"})["avg_hours"]))
    else:
        hours_lookup = {}

    # ── TRACE review counts ──
    # Counted off the same overall-question rows the rating is averaged from, so
    # `trace_rating` and `trace_reviews` describe one set of responses. See
    # trace_review_counts for what summing `completed` per section got wrong.
    trace_reviews_lookup = trace_review_counts(merged)

    # ── Attach TRACE data to RMP ──
    rmp_profs["trace_overall"] = rmp_profs["_name_key"].map(trace_lookup)
    rmp_profs["trace_reviews"] = rmp_profs["_name_key"].map(trace_reviews_lookup).fillna(0).astype(int)
    rmp_profs["trace_dept"] = rmp_profs["_name_key"].map(trace_dept_lookup)
    rmp_profs["avg_hours"] = rmp_profs["_name_key"].map(hours_lookup)

    # Fuzzy match unmatched
    trace_by_last = {}
    for tn in trace_lookup.keys():
        parts = tn.split()
        if len(parts) >= 2:
            trace_by_last.setdefault(parts[-1], []).append(tn)

    unmatched = rmp_profs["trace_overall"].isna()
    for idx in rmp_profs[unmatched].index:
        rmp_key = rmp_profs.at[idx, "_name_key"]
        rmp_parts = rmp_key.split()
        if len(rmp_parts) < 2:
            continue
        rmp_first, rmp_last = rmp_parts[0], rmp_parts[-1]
        for tc_name in trace_by_last.get(rmp_last, []):
            tc_first = tc_name.split()[0]
            if tc_first.startswith(rmp_first) or rmp_first.startswith(tc_first):
                rmp_profs.at[idx, "trace_overall"] = trace_lookup.get(tc_name)
                rmp_profs.at[idx, "trace_reviews"] = trace_reviews_lookup.get(tc_name, 0)
                rmp_profs.at[idx, "trace_dept"] = trace_dept_lookup.get(tc_name)
                if rmp_profs.at[idx, "avg_hours"] != rmp_profs.at[idx, "avg_hours"]:  # isnan
                    rmp_profs.at[idx, "avg_hours"] = hours_lookup.get(tc_name)
                break

    # RMP's numRatings counter is a stale aggregate, so count the ratings we
    # actually hold instead. Runs before total_reviews and the blend, both of
    # which count from this field.
    rmp_rev_keys = rmp_reviews["professor_name"].apply(normalize_name).replace(ALIAS_MAP)
    recounted = apply_counted_num_ratings(rmp_profs, rmp_rev_keys)
    print(f"Recounted num_ratings from stored ratings: {recounted} professors corrected")
    # And the mean over the same rows, so `rating` and num_ratings describe one
    # population. Must precede measure_calibration, which fits on `rating`.
    remeaned = apply_counted_rmp_rating(rmp_profs, rmp_rev_keys, rmp_reviews["quality"])
    print(f"Recomputed rmp rating from stored ratings: {remeaned} professors corrected")

    rmp_profs["trace_reviews"] = rmp_profs["trace_reviews"].fillna(0).astype(int)
    rmp_profs["total_reviews"] = rmp_profs["num_ratings"].astype(int) + rmp_profs["trace_reviews"]

    # ── Blended rating: calibrate RMP onto the TRACE scale, then pool by
    # precision. See the blend section near the top of this file.
    calibration = measure_calibration(rmp_profs)
    variances = measure_variances(
        rmp_reviews["quality"], rmp_rev_keys,
        overall[["count_1", "count_2", "count_3", "count_4", "count_5"]].to_numpy())
    apply_blended_rating(rmp_profs, calibration, variances)

    # ── Comment counts per name_key ──
    # RMP comments
    rmp_rev = rmp_reviews[rmp_reviews["comment"].notna() & (rmp_reviews["comment"].astype(str).str.strip() != "")].copy()
    rmp_rev["_name_key"] = rmp_rev["professor_name"].apply(normalize_name).replace(ALIAS_MAP)
    rmp_comment_counts = rmp_rev.groupby("_name_key").size()

    # TRACE comments
    tc_id_cols = tc[["course_id", "instructor_id", "term_id", "name_key"]].drop_duplicates()
    tcomments_parsed = tcomments[tcomments["comment"].notna() & (tcomments["comment"].astype(str).str.strip() != "")].copy()
    tcomments_parsed[["_cid", "_iid", "_tid"]] = tcomments_parsed["course_url"].str.extractall(r"sp=(\d+)").unstack().droplevel(0, axis=1)[[0, 1, 2]].astype(float)
    tcomments_parsed = tcomments_parsed.dropna(subset=["_cid", "_iid", "_tid"])
    tcomments_parsed[["_cid", "_iid", "_tid"]] = tcomments_parsed[["_cid", "_iid", "_tid"]].astype(int)
    trace_with_nk = tcomments_parsed.merge(
        tc_id_cols, left_on=["_cid", "_iid", "_tid"], right_on=["course_id", "instructor_id", "term_id"], how="inner"
    )
    trace_comment_counts = trace_with_nk.groupby("name_key").size()

    # Combine
    comment_counts_lookup = (rmp_comment_counts.add(trace_comment_counts, fill_value=0)).fillna(0).astype(int).to_dict()
    print(f"Computed comment counts for {len(comment_counts_lookup)} professors")

    # ── Build catalog rows ──
    catalog_rows = []
    rmp_name_keys = set(rmp_profs["_name_key"].values)
    seen_slugs = set()

    for _, row in rmp_profs.iterrows():
        has_rmp = int(row["num_ratings"]) > 0 and float(row["rating"]) > 0
        has_trace = pd.notna(row["trace_overall"]) and int(row["trace_reviews"]) > 0
        rmp_dept = str(row["department"])
        trace_dept_val = str(row["trace_dept"]) if pd.notna(row["trace_dept"]) else None
        # Prefer trace_dept, but fall back to RMP dept if trace would move the professor to a different college
        if trace_dept_val and get_college(trace_dept_val) != "Other" and get_college(trace_dept_val) == get_college(rmp_dept):
            dept = trace_dept_val
        elif get_college(rmp_dept) != "Other":
            dept = rmp_dept
        else:
            dept = trace_dept_val or rmp_dept
        college = get_college(dept)

        wta = None
        wta_raw = str(row.get("would_take_again_pct", "")).strip().replace("%", "")
        try:
            if wta_raw and wta_raw.lower() not in ("nan", "n/a", ""):
                wta = round(float(wta_raw), 1)
                if wta < 0:
                    wta = None
        except (ValueError, TypeError):
            pass

        difficulty = None
        if "level_of_difficulty" in row.index:
            try:
                val = float(row["level_of_difficulty"])
                if pd.notna(val) and val > 0:
                    difficulty = round(val, 2)
            except (ValueError, TypeError):
                pass

        display_name = trace_name_lookup.get(row["_name_key"], row["name"])
        slug = name_to_slug(row["_name_key"])
        _base, _n = slug, 2
        while slug in seen_slugs:
            slug = f"{_base}-{_n}"
            _n += 1
        seen_slugs.add(slug)

        avg_hours = None
        if pd.notna(row.get("avg_hours")) and float(row["avg_hours"]) > 0:
            avg_hours = round(float(row["avg_hours"]), 2)

        catalog_rows.append((
            slug, display_name, row["_name_key"], dept, college,
            float(row["avg_rating"]) if pd.notna(row["avg_rating"]) else None,
            round(float(row["rating"]), 2) if has_rmp else None,
            round(float(row["trace_overall"]), 2) if has_trace else None,
            int(row["num_ratings"]), int(row["trace_reviews"]), int(row["total_reviews"]),
            wta, difficulty,
            (row["professor_url"] if isinstance(row.get("professor_url"), str) and row["professor_url"] else None),
            photo_lookup.get(row["_name_key"], None),
            float(focus_x_lookup.get(row["_name_key"], 50.0)),
            float(focus_y_lookup.get(row["_name_key"], 30.0)),
            avg_hours,
            comment_counts_lookup.get(row["_name_key"], 0),
        ))

    # TRACE-only professors
    trace_unique = tc[["name_key", "department_name"]].drop_duplicates(subset=["name_key"])
    for _, row in trace_unique.iterrows():
        nk = row["name_key"]
        if nk in rmp_name_keys:
            continue
        display_name = trace_name_lookup.get(nk, nk.title())
        dept = str(row["department_name"]) if pd.notna(row["department_name"]) else ""
        trace_rat = trace_lookup.get(nk)
        has_trace = trace_rat is not None and pd.notna(trace_rat)
        avg = round(float(trace_rat), 2) if has_trace else None
        t_rev = int(trace_reviews_lookup.get(nk, 0))
        slug = name_to_slug(nk)
        _base, _n = slug, 2
        while slug in seen_slugs:
            slug = f"{_base}-{_n}"
            _n += 1
        seen_slugs.add(slug)
        avg_hours_t = round(float(hours_lookup[nk]), 2) if nk in hours_lookup and pd.notna(hours_lookup[nk]) else None
        catalog_rows.append((
            slug, display_name, nk, dept, get_college(dept),
            avg, None, avg,
            0, t_rev, t_rev,
            None, None, None,
            photo_lookup.get(nk, None),
            float(focus_x_lookup.get(nk, 50.0)),
            float(focus_y_lookup.get(nk, 30.0)),
            avg_hours_t,
            comment_counts_lookup.get(nk, 0),
        ))

    print(f"Built catalog with {len(catalog_rows)} professors")

    # ── Build course catalog ──
    def parse_course(dn):
        m = re.match(r"^([A-Z]+\d+):\d+\s+\((.+?)\)", str(dn))
        return (m.group(1), m.group(2)) if m else (None, None)

    tc["_parsed"] = tc["display_name"].apply(parse_course)
    tc["_code"] = tc["_parsed"].apply(lambda x: x[0])
    tc["_cname"] = tc["_parsed"].apply(lambda x: x[1])
    course_df = tc[tc["_code"].notna()][["_code", "_cname", "department_name"]].drop_duplicates(subset=["_code"])

    # A topics code runs under more than one unrelated title *inside a single
    # term* — HONR3310 as "Election 2024", "Honors Seminar" and "Language and
    # Power" simultaneously. Averaging their TRACE scores produces a number that
    # describes nothing, so server.py suppresses the course-level rating (and the
    # AggregateRating JSON-LD keyed off it) for these codes. Within a term rather
    # than across all terms: a code whose title merely changed in 2019 is one
    # course that got renamed, not a container for unrelated ones.
    per_term = {}
    coded = tc[tc["_code"].notna()]
    for code, term, title in zip(coded["_code"], coded["term_id"], coded["_cname"]):
        per_term.setdefault(code, {}).setdefault(term, set()).add(title)
    topics_codes = {code for code, terms in per_term.items()
                    if any(_has_unrelated_titles(v) for v in terms.values())}
    print(f"Flagged {len(topics_codes)} topics codes (multiple unrelated titles in one term)")

    course_rows = [
        (r["_code"], r["_cname"], str(r["department_name"]) if pd.notna(r["department_name"]) else "", r["_code"].lower() + " " + str(r["_cname"]).lower(), r["_code"] in topics_codes)
        for _, r in course_df.iterrows()
    ]

    # ── Compute stats ──
    all_prof_names = set(rmp_profs["_name_key"].unique()) | set(tc["name_key"].unique())
    all_prof_names = {n.strip() for n in all_prof_names if isinstance(n, str) and n.strip()}
    stat_professors = len(all_prof_names)
    tc["_course_code"] = tc["display_name"].astype(str).str.split(":").str[0]
    stat_courses = tc["_course_code"].str.upper().nunique()
    stat_comments = len(rmp_reviews) + len(tcomments)
    stat_departments = tc["department_name"].str.lower().str.strip().nunique()

    # ══════════════════════════════════════════════
    #  Write everything to CockroachDB
    # ══════════════════════════════════════════════
    cur = conn.cursor()

    # 1. professors_catalog
    print("Creating professors_catalog...")
    cur.execute("DROP TABLE IF EXISTS professors_catalog_new")
    cur.execute("""
        CREATE TABLE professors_catalog_new (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            department TEXT,
            college TEXT,
            avg_rating FLOAT,
            rmp_rating FLOAT,
            trace_rating FLOAT,
            num_ratings INT DEFAULT 0,
            trace_reviews INT DEFAULT 0,
            total_reviews INT DEFAULT 0,
            would_take_again_pct FLOAT,
            difficulty FLOAT,
            professor_url TEXT,
            image_url TEXT,
            focus_x FLOAT,
            focus_y FLOAT,
            avg_hours FLOAT,
            total_comments INT DEFAULT 0
        )
    """)
    chunk_insert(cur, """
        INSERT INTO professors_catalog_new
        (slug, name, name_key, department, college, avg_rating, rmp_rating, trace_rating,
         num_ratings, trace_reviews, total_reviews, would_take_again_pct, difficulty,
         professor_url, image_url, focus_x, focus_y, avg_hours, total_comments)
        VALUES %s
    """, catalog_rows)
    cur.execute("CREATE INDEX idx_pc_name_key ON professors_catalog_new (name_key)")
    cur.execute("CREATE INDEX idx_pc_college ON professors_catalog_new (college)")
    cur.execute("CREATE INDEX idx_pc_dept ON professors_catalog_new (department)")
    conn.commit()
    swap_in(conn, "professors_catalog")
    print(f"  Inserted {len(catalog_rows)} rows")

    # 2. course_catalog (TRACE-derived — only rebuild when TRACE changed)
    if do_trace:
        print("Creating course_catalog...")
        cur.execute("DROP TABLE IF EXISTS course_catalog_new")
        cur.execute("""
            CREATE TABLE course_catalog_new (
                code TEXT PRIMARY KEY,
                name TEXT,
                department TEXT,
                search_text TEXT,
                avg_rating FLOAT,
                num_responses INT,
                is_topics BOOL NOT NULL DEFAULT false
            )
        """)
        chunk_insert(cur, "INSERT INTO course_catalog_new (code, name, department, search_text, is_topics) VALUES %s", course_rows)
        cur.execute("CREATE INDEX idx_cc_dept ON course_catalog_new (department)")
        conn.commit()
        swap_in(conn, "course_catalog")
        print(f"  Inserted {len(course_rows)} courses")
    else:
        print("Skipping course_catalog rebuild (REFRESH_TRACE=false)")

    # 3. stats_cache
    print("Updating stats_cache...")
    cur.execute("CREATE TABLE IF NOT EXISTS stats_cache (key TEXT PRIMARY KEY, value INT)")
    cur.execute(
        "UPSERT INTO stats_cache VALUES ('professors', %s), ('courses', %s), ('comments', %s), ('departments', %s)",
        (stat_professors, stat_courses, stat_comments, stat_departments)
    )
    conn.commit()

    # Steps 4/4b operate on the TRACE tables — skip on RMP-only refreshes.
    if do_trace:
        # Reconnect with fresh connection for the update phase
        conn.close()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SET experimental_enable_temp_tables = 'on'")

        # 4. Add name_key to trace_courses (batch via temp table)
        print("Adding name_key to trace_courses...")
        try:
            cur.execute("ALTER TABLE trace_courses ADD COLUMN name_key TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()

        cur.execute("SET experimental_enable_temp_tables = 'on'")

        unique_instructors = tc[["instructor_first_name", "instructor_last_name", "name_key"]].drop_duplicates()
        mapping_rows = [
            (r["instructor_first_name"], r["instructor_last_name"], r["name_key"])
            for _, r in unique_instructors.iterrows()
        ]

        cur.execute("CREATE TEMP TABLE _nk_map (first_name TEXT, last_name TEXT, name_key TEXT)")
        chunk_insert(cur, "INSERT INTO _nk_map (first_name, last_name, name_key) VALUES %s", mapping_rows)
        cur.execute("""
            UPDATE trace_courses tc SET name_key = m.name_key
            FROM _nk_map m
            WHERE tc.instructor_first_name = m.first_name AND tc.instructor_last_name = m.last_name
              AND tc.name_key IS NULL
        """)
        cur.execute("DROP TABLE _nk_map")
        conn.commit()

        try:
            cur.execute("CREATE INDEX idx_tc_name_key ON trace_courses (name_key)")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()
        print(f"  Updated {len(unique_instructors)} unique instructors")

        # 4b. Add precomputed course_code to trace_courses
        print("Adding course_code to trace_courses...")
        try:
            cur.execute("ALTER TABLE trace_courses ADD COLUMN course_code TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()

        cur.execute("""
            UPDATE trace_courses SET course_code = UPPER(REGEXP_REPLACE(
                SPLIT_PART(display_name, ':', 1), '[^A-Za-z0-9]', '', 'g'
            ))
            WHERE course_code IS NULL AND display_name IS NOT NULL
        """)
        conn.commit()

        try:
            cur.execute("CREATE INDEX idx_tc_course_code ON trace_courses (course_code)")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()
        print("  Done")
    else:
        print("Skipping trace_courses name_key/course_code backfill (REFRESH_TRACE=false)")

    # 5. Add name_key to rmp_reviews (batch via temp table)
    print("Adding name_key to rmp_reviews...")
    conn.close()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SET experimental_enable_temp_tables = 'on'")

    try:
        cur.execute("ALTER TABLE rmp_reviews ADD COLUMN name_key TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()

    cur.execute("SET experimental_enable_temp_tables = 'on'")

    unique_rev_names = rmp_reviews["professor_name"].dropna().unique()
    rev_mapping_rows = []
    for name in unique_rev_names:
        nk = normalize_name(name)
        nk = ALIAS_MAP.get(nk, nk)
        rev_mapping_rows.append((name, nk))

    cur.execute("CREATE TEMP TABLE _rev_nk_map (professor_name TEXT, name_key TEXT)")
    chunk_insert(cur, "INSERT INTO _rev_nk_map (professor_name, name_key) VALUES %s", rev_mapping_rows)
    cur.execute("""
        UPDATE rmp_reviews r SET name_key = m.name_key
        FROM _rev_nk_map m
        WHERE r.professor_name = m.professor_name
    """)
    cur.execute("DROP TABLE _rev_nk_map")
    conn.commit()

    try:
        cur.execute("CREATE INDEX idx_rr_name_key ON rmp_reviews (name_key)")
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()
    print(f"  Updated {len(unique_rev_names)} unique review names")

    # 6. Fix trace_scores mean and add total_responses (single SQL statements)
    if do_trace:
        print("Fixing trace_scores mean and adding total_responses...")
        conn.close()
        conn = _connect()
        cur = conn.cursor()

        try:
            cur.execute("ALTER TABLE trace_scores ADD COLUMN total_responses INT")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()

        BATCH_SIZE = 10000
        print("  Updating total_responses (batched)...")
        while True:
            cur.execute("""
                UPDATE trace_scores SET
                    total_responses = COALESCE(count_1,0) + COALESCE(count_2,0) + COALESCE(count_3,0) + COALESCE(count_4,0) + COALESCE(count_5,0)
                WHERE total_responses IS NULL
                LIMIT %s
            """, (BATCH_SIZE,))
            updated = cur.rowcount
            conn.commit()
            if updated == 0:
                break
            print(f"    updated {updated} rows...")

        print("  Updating mean (batched)...")
        cur.execute("""
            UPDATE trace_scores SET
                mean = NULL
            WHERE COALESCE(count_1,0) + COALESCE(count_2,0) + COALESCE(count_3,0) + COALESCE(count_4,0) + COALESCE(count_5,0) = 0
              AND mean IS NOT NULL
        """)
        conn.commit()
        while True:
            cur.execute("""
                UPDATE trace_scores SET
                    mean = (1.0*COALESCE(count_1,0) + 2.0*COALESCE(count_2,0) + 3.0*COALESCE(count_3,0) + 4.0*COALESCE(count_4,0) + 5.0*COALESCE(count_5,0))
                         / (COALESCE(count_1,0) + COALESCE(count_2,0) + COALESCE(count_3,0) + COALESCE(count_4,0) + COALESCE(count_5,0))
                WHERE total_responses > 0
                  AND mean IS NULL
                LIMIT %s
            """, (BATCH_SIZE,))
            updated = cur.rowcount
            conn.commit()
            if updated == 0:
                break
            print(f"    updated {updated} rows...")

        try:
            cur.execute("CREATE INDEX idx_ts_ids ON trace_scores (course_id, instructor_id, term_id)")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()

        # Near-empty once backfilled; trace_needs_maintenance's IS NULL probe
        # matches this predicate exactly, making the weekly probe O(1).
        try:
            cur.execute(
                "CREATE INDEX idx_ts_total_resp_null ON trace_scores (total_responses) "
                "WHERE total_responses IS NULL"
            )
            conn.commit()
            print("  Created idx_ts_total_resp_null")
        except Exception:
            conn.rollback()
            cur = conn.cursor()
        print("  Done")
    else:
        print("Skipping trace_scores mean/total_responses backfill (REFRESH_TRACE=false)")

    # 7. Add parsed course_id, instructor_id, term_id columns to trace_comments
    if do_trace:
        print("Adding parsed ID columns to trace_comments...")
        conn.close()
        conn = _connect()
        cur = conn.cursor()
        for col in ["tc_course_id", "tc_instructor_id", "tc_term_id"]:
            try:
                cur.execute(f"ALTER TABLE trace_comments ADD COLUMN {col} INT")
                conn.commit()
            except Exception:
                conn.rollback()
                cur = conn.cursor()

        # Parse URLs from the CSV we already loaded, build a url→ids mapping
        url_map = {}
        for url in tcomments["course_url"].dropna().unique():
            sp_matches = re.findall(r"sp=(\d+)", str(url))
            if len(sp_matches) >= 3:
                url_map[str(url)] = (int(sp_matches[0]), int(sp_matches[1]), int(sp_matches[2]))

        # Create helper table with url→ids mapping
        cur.execute("SET experimental_enable_temp_tables = 'on'")
        cur.execute("CREATE TEMP TABLE _url_ids (course_url TEXT, cid INT, iid INT, tid INT)")
        mapping_rows = [(url, cid, iid, tid) for url, (cid, iid, tid) in url_map.items()]
        chunk_insert(cur, "INSERT INTO _url_ids (course_url, cid, iid, tid) VALUES %s", mapping_rows)
        print(f"  Parsed {len(mapping_rows)} unique URLs")

        # Batch join-update (smaller batches to avoid CockroachDB serialization failures)
        COMMENT_BATCH = 5000
        while True:
            try:
                cur.execute("""
                    UPDATE trace_comments tc SET
                        tc_course_id = m.cid,
                        tc_instructor_id = m.iid,
                        tc_term_id = m.tid
                    FROM _url_ids m
                    WHERE tc.course_url = m.course_url
                      AND tc.tc_course_id IS NULL
                    LIMIT %s
                """, (COMMENT_BATCH,))
                updated = cur.rowcount
                conn.commit()
            except Exception as e:
                conn.rollback()
                cur = conn.cursor()
                if "restart transaction" in str(e).lower() or "serialization" in str(e).lower():
                    print(f"    retry (serialization conflict)...")
                    continue
                raise
            if updated == 0:
                break
            print(f"    updated {updated} rows...")

        cur.execute("DROP TABLE _url_ids")
        conn.commit()

        try:
            cur.execute("CREATE INDEX idx_tc_comment_ids ON trace_comments (tc_course_id, tc_instructor_id, tc_term_id)")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()
        print("  Done")
    else:
        print("Skipping trace_comments ID-column backfill (REFRESH_TRACE=false)")

    # 8. Precompute course_catalog avg_rating (overall-question weighted mean).
    # Depends on trace_courses.course_code (step 4b) and trace_scores.total_responses (step 6).
    if do_trace:
        print("Precomputing course_catalog avg_rating...")
        conn.close()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            UPDATE course_catalog cc SET
                avg_rating = agg.avg_rating,
                num_responses = agg.total_responses
            FROM (
                SELECT
                    tc.course_code,
                    SUM(CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT))
                        / NULLIF(SUM(CAST(ts.total_responses AS FLOAT)), 0) AS avg_rating,
                    SUM(ts.total_responses) AS total_responses
                FROM trace_courses tc
                JOIN trace_scores ts
                    ON tc.course_id = ts.course_id
                    AND tc.instructor_id = ts.instructor_id
                    AND tc.term_id = ts.term_id
                WHERE LOWER(ts.question) LIKE '%%overall%%'
                  AND LOWER(ts.question) != 'overall effectiveness'
                  AND tc.course_code IS NOT NULL
                GROUP BY tc.course_code
            ) agg
            WHERE cc.code = agg.course_code
              AND NOT cc.is_topics
        """)
        conn.commit()
        try:
            cur.execute("CREATE INDEX idx_cc_rating ON course_catalog (avg_rating)")
            conn.commit()
        except Exception:
            conn.rollback()
            cur = conn.cursor()
        print("  Done")
    else:
        print("Skipping course_catalog avg_rating precompute (REFRESH_TRACE=false)")

    # If the safety net forced this pass, verify it actually cleared the NULLs.
    # Anything left is unprocessable and will re-force the heavy TRACE path on
    # every weekly run — surface it as a GitHub Actions warning annotation.
    if do_trace and not REFRESH_TRACE:
        for table, col, n in trace_maintenance_leftovers(conn):
            shown = "1000+" if n > 1000 else str(n)
            print(f"::warning::{table}.{col}: {shown} NULL rows survived full TRACE "
                  "maintenance; the safety net will re-force this heavy path every run until they are fixed.")

    conn.close()
    print(f"\nPrecompute complete!")
    print(f"  {len(catalog_rows)} professors in catalog")
    if do_trace:
        print(f"  {len(course_rows)} courses in catalog")
    print(f"  Stats: {stat_professors} professors, {stat_courses} courses, {stat_comments} comments, {stat_departments} departments")


if __name__ == "__main__":
    main()
