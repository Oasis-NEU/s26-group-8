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
    instructor_sections = tc[["course_id", "instructor_id", "term_id", "name_key"]].drop_duplicates(
        subset=["course_id", "instructor_id", "term_id"]
    )
    scores_deduped = ts.drop_duplicates(
        subset=["course_id", "instructor_id", "term_id"]
    )[["course_id", "instructor_id", "term_id", "completed"]]
    trace_rev_merged = scores_deduped.merge(instructor_sections, on=["course_id", "instructor_id", "term_id"], how="inner")
    trace_rev_counts = trace_rev_merged.groupby("name_key")["completed"].sum().reset_index().rename(columns={"completed": "trace_reviews"})
    trace_reviews_lookup = dict(zip(trace_rev_counts["name_key"], trace_rev_counts["trace_reviews"]))

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

    rmp_profs["trace_reviews"] = rmp_profs["trace_reviews"].fillna(0).astype(int)
    rmp_profs["total_reviews"] = rmp_profs["num_ratings"].astype(int) + rmp_profs["trace_reviews"]

    has_rmp = (rmp_profs["num_ratings"] > 0) & (rmp_profs["rating"] > 0)
    has_trace = rmp_profs["trace_overall"].notna() & (rmp_profs["trace_reviews"] > 0)
    rmp_profs["avg_rating"] = np.where(
        has_rmp & has_trace,
        ((rmp_profs["rating"] + rmp_profs["trace_overall"]) / 2).round(2),
        np.where(has_trace, rmp_profs["trace_overall"].round(2),
                 np.where(has_rmp, rmp_profs["rating"].round(2), np.nan))
    )
    rmp_profs["avg_rating"] = rmp_profs["avg_rating"].where(rmp_profs["avg_rating"].notna(), other=None)

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
    course_rows = [
        (r["_code"], r["_cname"], str(r["department_name"]) if pd.notna(r["department_name"]) else "", r["_code"].lower() + " " + str(r["_cname"]).lower())
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
                num_responses INT
            )
        """)
        chunk_insert(cur, "INSERT INTO course_catalog_new (code, name, department, search_text) VALUES %s", course_rows)
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
