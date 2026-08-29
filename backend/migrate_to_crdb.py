"""
Migrate CSV data into CockroachDB.
Idempotent — safe to re-run. Pre-filters rows client-side to avoid sending
data the DB already has, minimizing Request Units.

Run:  python backend/migrate_to_crdb.py all
"""

import os, csv, sys, time
from dotenv import load_dotenv

import csv_store
from denylist import denied_hashes, is_denied

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATABASE_URL = os.getenv("NEW_CRDB_DATABASE_URL") or os.getenv("CRDB_DATABASE_URL")

if not DATABASE_URL:
    sys.exit("Missing NEW_CRDB_DATABASE_URL / CRDB_DATABASE_URL in backend/.env")

import psycopg2
from psycopg2.extras import execute_values

DATA_DIR = os.path.join(os.path.dirname(__file__), "Better_Scraper", "output_data")
BATCH_SIZE = 25000  # smaller batches to stay under CockroachDB lock-tracking budget


def get_connection(attempts=20):
    """The local resolver flakes on *.cockroachlabs.cloud; retry on DNS failure."""
    for i in range(attempts):
        try:
            return psycopg2.connect(DATABASE_URL, sslmode="require")
        except psycopg2.OperationalError as e:
            if "could not translate host name" not in str(e) or i == attempts - 1:
                raise
            time.sleep(2)


def create_table(conn, sql: str):
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def fetch_existing_keys(conn, table: str, key_columns: list[str], key_query: str = None) -> set:
    """
    Fetch existing keys from the DB so we can skip them client-side.
    Uses key_query if provided (for lightweight proxy keys like DISTINCT course_url),
    otherwise builds a SELECT from key_columns.
    """
    try:
        sql = key_query or f"SELECT DISTINCT {', '.join(key_columns)} FROM {table}"
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        # Unwrap single-column tuples for simpler lookup
        if len(key_columns) == 1:
            return {row[0] for row in rows}
        return {row for row in rows}
    except Exception as e:
        conn.rollback()
        # Returning an empty set silently disables the client-side pre-filter and
        # re-sends the whole CSV through server-side conflict checks — an
        # unattended RU burn. Fail loud; the weekly run self-heals next attempt.
        raise RuntimeError(f"fetch_existing_keys failed for {table}: {e}") from e


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(p.title() for p in rest)


def _row_is_denied(row, raw, deny_name) -> bool:
    """Does this row belong to a professor on the denylist?

    Checks the transformed row and, independently, the raw CSV row under both
    naming conventions — because the transforms are not format-agnostic and a
    filter that silently stops matching is the worst failure mode this code has.
    trace_courses' transform reads camelCase (`instructorFirstName`) while the
    export in output_data is snake_case, so it yields empty strings for every
    field; a name check against that output would pass every row through while
    reporting nothing wrong.

    Fail-safe on purpose: any spelling that matches drops the row. The cost of a
    false positive is one professor's rows missing from a load that can be re-run;
    the cost of a false negative is publishing data someone asked to have removed.
    """
    for source in (row, raw):
        if not source:
            continue
        for keys in (deny_name, tuple(_camel(c) for c in deny_name)):
            parts = [str(source.get(k) or "").strip() for k in keys]
            if any(parts) and is_denied(" ".join(p for p in parts if p)):
                return True
    return False


def upload_csv(conn, table: str, columns: list[str], csv_path: str,
               transform=None, on_conflict: str = "",
               key_columns: list[str] = None, existing_keys: set = None,
               deny_name: tuple = None):
    if not os.path.exists(csv_path):
        print(f"  File not found: {csv_path}")
        return

    # csv_store, not open(): the big TRACE exports may ship zipped, and
    # DictReader cannot open a .zip the way pandas can.
    with csv_store.open_text(csv_path) as f:
        reader = csv.DictReader(f)
        batch = []
        total = 0
        skipped = 0

        col_names = ", ".join(columns)
        insert_sql = f"INSERT INTO {table} ({col_names}) VALUES %s{' ' + on_conflict if on_conflict else ''}"

        # Find key column indices for client-side filtering
        key_indices = None
        single_key = False
        if key_columns and existing_keys:
            key_indices = [columns.index(k) for k in key_columns]
            single_key = len(key_columns) == 1

        # Data-deletion requests: drop the row before it is ever inserted.
        # `deny_name` names the post-transform columns holding the professor's
        # name; joined with a space, that is the same string precompute builds a
        # name_key from. Tables without a name (trace_scores, trace_comments)
        # reach a professor only through trace_courses, so dropping the course
        # rows detaches them — purge_denied.py deletes the orphans.
        denied_active = bool(deny_name) and bool(denied_hashes())
        denied_rows = 0

        for row in reader:
            raw = row
            if transform:
                row = transform(row)
            if row is None:
                continue
            if denied_active and _row_is_denied(row, raw, deny_name):
                denied_rows += 1
                continue
            values = tuple(row[col] for col in columns)

            # Skip rows that already exist in DB (saves RUs on conflict checks)
            if key_indices is not None and existing_keys:
                if single_key:
                    key = values[key_indices[0]]
                else:
                    key = tuple(values[i] for i in key_indices)
                if key in existing_keys:
                    skipped += 1
                    continue

            batch.append(values)

            if len(batch) >= BATCH_SIZE:
                with conn.cursor() as cur:
                    execute_values(cur, insert_sql, batch, page_size=BATCH_SIZE)
                conn.commit()
                total += len(batch)
                print(f"  Uploaded {total:,} new rows (skipped {skipped:,} existing)...", end="\r")
                batch = []

        if batch:
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, batch, page_size=BATCH_SIZE)
            conn.commit()
            total += len(batch)
    print(f"  Done: {total:,} rows inserted, {skipped:,} skipped (already in DB)."
          + (f" {denied_rows:,} withheld (denylist)." if denied_rows else ""))


# ──────────────────────────────────────────────
#  Table definitions & transforms
# ──────────────────────────────────────────────

TABLES = {
    "trace_comments": {
        "create_sql": """
            CREATE TABLE IF NOT EXISTS trace_comments (
                id INT8 DEFAULT unique_rowid() PRIMARY KEY,
                course_url TEXT NOT NULL,
                question TEXT,
                comment TEXT,
                UNIQUE (course_url, question, comment)
            );
        """,
        "columns": ["course_url", "question", "comment"],
        # Use just course_url as lightweight filter — skips entire sections cheaply
        "key_columns": ["course_url"],
        "key_query": "SELECT DISTINCT course_url FROM trace_comments",
        "csv": "trace_comments.csv",
        "on_conflict": "ON CONFLICT (course_url, question, comment) DO NOTHING",
        "transform": lambda row: {
            "course_url": row.get("course_url", ""),
            "question": row.get("question", ""),
            "comment": row.get("comment", ""),
        },
    },
    "rmp_professors": {
        "create_sql": """
            CREATE TABLE IF NOT EXISTS rmp_professors (
                id INT8 DEFAULT unique_rowid() PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT,
                rating REAL,
                num_ratings INT,
                would_take_again_pct TEXT,
                level_of_difficulty REAL,
                professor_url TEXT,
                UNIQUE (name, department)
            );
        """,
        "columns": ["name", "department", "rating", "num_ratings", "would_take_again_pct", "level_of_difficulty", "professor_url"],
        "key_columns": ["name", "department"],
        "csv": "rmp_professors.csv",
        "deny_name": ("name",),
        "on_conflict": "ON CONFLICT (name, department) DO NOTHING",
        "transform": lambda row: {
            "name": row.get("name", ""),
            "department": row.get("department", ""),
            "rating": float(row["rating"]) if row.get("rating") else None,
            "num_ratings": int(row["num_ratings"]) if row.get("num_ratings") else None,
            "would_take_again_pct": row.get("would_take_again_pct", ""),
            "level_of_difficulty": float(row["level_of_difficulty"]) if row.get("level_of_difficulty") else None,
            "professor_url": row.get("professor_url", ""),
        },
    },
    "rmp_reviews": {
        "create_sql": """
            CREATE TABLE IF NOT EXISTS rmp_reviews (
                id INT8 DEFAULT unique_rowid() PRIMARY KEY,
                professor_name TEXT NOT NULL,
                department TEXT,
                overall_rating REAL,
                course TEXT,
                quality REAL,
                difficulty REAL,
                date TEXT,
                tags TEXT,
                attendance TEXT,
                grade TEXT,
                textbook TEXT,
                online_class TEXT,
                comment TEXT,
                UNIQUE (professor_name, course, date, comment)
            );
        """,
        "columns": ["professor_name", "department", "overall_rating", "course", "quality", "difficulty", "date", "tags", "attendance", "grade", "textbook", "online_class", "comment"],
        # Lightweight proxy: skip by (professor, course, date) — avoids fetching full comment text
        "key_columns": ["professor_name", "course", "date"],
        "key_query": "SELECT DISTINCT professor_name, course, date FROM rmp_reviews",
        "csv": "rmp_reviews.csv",
        "deny_name": ("professor_name",),
        "on_conflict": "ON CONFLICT (professor_name, course, date, comment) DO NOTHING",
        "transform": lambda row: {
            "professor_name": row.get("professor_name", ""),
            "department": row.get("department", ""),
            "overall_rating": float(row["overall_rating"]) if row.get("overall_rating") else None,
            "course": row.get("course", ""),
            "quality": float(row["quality"]) if row.get("quality") else None,
            "difficulty": float(row["difficulty"]) if row.get("difficulty") else None,
            "date": row.get("date", ""),
            "tags": row.get("tags", ""),
            "attendance": row.get("attendance", ""),
            "grade": row.get("grade", ""),
            "textbook": row.get("textbook", ""),
            "online_class": row.get("online_class", ""),
            "comment": row.get("comment", ""),
        },
    },
    "trace_courses": {
        "create_sql": """
            CREATE TABLE IF NOT EXISTS trace_courses (
                id INT8 DEFAULT unique_rowid() PRIMARY KEY,
                course_id INT,
                school_code TEXT,
                term_id INT,
                term_title TEXT,
                instructor_id INT,
                term_end_date TEXT,
                instructor_first_name TEXT,
                instructor_last_name TEXT,
                department_name TEXT,
                enrollment INT,
                display_name TEXT,
                section TEXT,
                UNIQUE (course_id, instructor_id, term_id)
            );
        """,
        "columns": ["course_id", "school_code", "term_id", "term_title", "instructor_id", "term_end_date", "instructor_first_name", "instructor_last_name", "department_name", "enrollment", "display_name", "section"],
        "key_columns": ["course_id", "instructor_id", "term_id"],
        "csv": "trace_courses.csv",
        "deny_name": ("instructor_first_name", "instructor_last_name"),
        "on_conflict": "ON CONFLICT (course_id, instructor_id, term_id) DO NOTHING",
        "transform": lambda row: {
            "course_id": int(row["courseId"]) if row.get("courseId") else None,
            "school_code": row.get("schoolCode", ""),
            "term_id": int(row["termId"]) if row.get("termId") else None,
            "term_title": row.get("termTitle", ""),
            "instructor_id": int(row["instructorId"]) if row.get("instructorId") else None,
            "term_end_date": row.get("termEndDate", ""),
            "instructor_first_name": row.get("instructorFirstName", ""),
            "instructor_last_name": row.get("instructorLastName", ""),
            "department_name": row.get("departmentName", ""),
            "enrollment": int(row["enrollment"]) if row.get("enrollment") else None,
            "display_name": row.get("displayName", ""),
            "section": row.get("section", ""),
        },
    },
    "trace_scores": {
        "create_sql": """
            CREATE TABLE IF NOT EXISTS trace_scores (
                id INT8 DEFAULT unique_rowid() PRIMARY KEY,
                course_id INT,
                instructor_id INT,
                term_id INT,
                enrollment INT,
                completed INT,
                question TEXT,
                count_5 INT,
                count_4 INT,
                count_3 INT,
                count_2 INT,
                count_1 INT,
                mean REAL,
                median REAL,
                std_dev REAL,
                dept_mean REAL,
                UNIQUE (course_id, instructor_id, term_id, question)
            );
        """,
        "columns": ["course_id", "instructor_id", "term_id", "enrollment", "completed", "question", "count_5", "count_4", "count_3", "count_2", "count_1", "mean", "median", "std_dev", "dept_mean"],
        # Lightweight proxy: skip by section-level key, avoids fetching question text
        "key_columns": ["course_id", "instructor_id", "term_id"],
        "key_query": "SELECT DISTINCT course_id, instructor_id, term_id FROM trace_scores",
        "csv": "trace_scores.csv",
        "on_conflict": "ON CONFLICT (course_id, instructor_id, term_id, question) DO NOTHING",
        "transform": lambda row: {
            "course_id": int(row["courseId"]) if row.get("courseId") else None,
            "instructor_id": int(row["instructorId"]) if row.get("instructorId") else None,
            "term_id": int(row["termId"]) if row.get("termId") else None,
            "enrollment": int(row["enrollment"]) if row.get("enrollment") else None,
            "completed": int(row["completed"]) if row.get("completed") else None,
            "question": row.get("question", ""),
            "count_5": int(row["count_5"]) if row.get("count_5") else None,
            "count_4": int(row["count_4"]) if row.get("count_4") else None,
            "count_3": int(row["count_3"]) if row.get("count_3") else None,
            "count_2": int(row["count_2"]) if row.get("count_2") else None,
            "count_1": int(row["count_1"]) if row.get("count_1") else None,
            "mean": float(row["mean"]) if row.get("mean") else None,
            "median": float(row["median"]) if row.get("median") else None,
            "std_dev": float(row["std_dev"]) if row.get("std_dev") else None,
            "dept_mean": float(row["dept_mean"]) if row.get("dept_mean") else None,
        },
    },
    "professor_photos": {
        "create_sql": """
            CREATE TABLE IF NOT EXISTS professor_photos (
                id INT8 DEFAULT unique_rowid() PRIMARY KEY,
                name TEXT NOT NULL,
                image_url TEXT,
                focus_x FLOAT,
                focus_y FLOAT,
                source_page TEXT,
                UNIQUE (name, source_page)
            );
            ALTER TABLE professor_photos ADD COLUMN IF NOT EXISTS focus_x FLOAT;
            ALTER TABLE professor_photos ADD COLUMN IF NOT EXISTS focus_y FLOAT;
        """,
        "columns": ["name", "image_url", "focus_x", "focus_y", "source_page"],
        "key_columns": ["name", "source_page"],
        "csv": "professor_photos.csv",
        "deny_name": ("name",),
        "on_conflict": "ON CONFLICT (name, source_page) DO NOTHING",
        "transform": lambda row: {
            "name": row.get("name", ""),
            "image_url": row.get("image_url", ""),
            "focus_x": row.get("focus_x", "") or None,
            "focus_y": row.get("focus_y", "") or None,
            "source_page": row.get("source_page", ""),
        },
    },
}


# Full-replace is only safe for a table whose CSV is a complete snapshot of the
# source each run AND whose ids nothing else references. TRACE/photo CSVs are
# cumulative artifacts — replacing from them would destroy data.
#
# rmp_reviews meets the snapshot half but fails the second: evidence.source_ref
# for RMP is the review's rowid (scraper/load_evidence_to_crdb.py:208), so
# truncating orphans the RMP half of the RAG corpus and forces a re-embed. The
# TRUNCATE also commits before the reload starts, emptying a table the site
# reads on every professor page. prune_rmp_reviews.py converges the table
# without either cost — use that instead.
REPLACE_ALLOWED = {"rmp_professors"}


UNIQUE_CONSTRAINTS = {
    "trace_courses": ("uq_trace_courses", "(course_id, instructor_id, term_id)"),
    "trace_scores": ("uq_trace_scores", "(course_id, instructor_id, term_id, question)"),
    "trace_comments": ("uq_trace_comments", "(course_url, question, comment)"),
    "rmp_professors": ("uq_rmp_professors", "(name, department)"),
    "rmp_reviews": ("uq_rmp_reviews", "(professor_name, course, date, comment)"),
    "professor_photos": ("uq_professor_photos", "(name, source_page)"),
}


def add_constraints(conn):
    """Add unique constraints to existing tables (idempotent — skips if already present)."""
    cur = conn.cursor()
    for table, (name, cols) in UNIQUE_CONSTRAINTS.items():
        try:
            cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE {cols}")
            conn.commit()
            print(f"  Added {name} to {table}")
        except Exception as e:
            conn.rollback()
            cur = conn.cursor()
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                print(f"  {name} already exists on {table}, skipping")
            else:
                print(f"  Warning: could not add {name} to {table}: {e}")
    cur.close()


def purge_new_scraper_rows(conn):
    """Delete rows added by transform_to_trace.py (courseId >= 500001) so they can be re-imported."""
    cur = conn.cursor()
    print("Purging new-scraper rows (course_id >= 500001) from trace_courses and trace_scores...")
    cur.execute("DELETE FROM trace_scores WHERE course_id >= 500001")
    print(f"  trace_scores: deleted {cur.rowcount:,} rows")
    cur.execute("DELETE FROM trace_courses WHERE course_id >= 500001")
    print(f"  trace_courses: deleted {cur.rowcount:,} rows")
    conn.commit()
    cur.close()


def main():
    args = sys.argv[1:]
    replace = "--replace" in args
    targets = [a for a in args if not a.startswith("--")] or ["trace_comments"]

    if targets == ["purge-new"]:
        conn = get_connection()
        print("Connected to CockroachDB!")
        purge_new_scraper_rows(conn)
        conn.close()
        print("Done! Now run: python backend/migrate_to_crdb.py trace_courses trace_scores")
        return

    if targets == ["add-constraints"]:
        conn = get_connection()
        print("Connected to CockroachDB!")
        print("\nAdding unique constraints...")
        add_constraints(conn)
        conn.close()
        print("Done!")
        return

    if targets == ["all"]:
        targets = list(TABLES.keys())

    if replace:
        bad = [t for t in targets if t not in REPLACE_ALLOWED]
        if bad:
            sys.exit(f"--replace only supported for {sorted(REPLACE_ALLOWED)}, got: {bad}")

    conn = get_connection()
    print("Connected to CockroachDB!")

    for table_name in targets:
        if table_name not in TABLES:
            print(f"Unknown table: {table_name}")
            continue

        conf = TABLES[table_name]
        csv_path = csv_store.resolve(DATA_DIR, conf["csv"])

        print(f"\n{'='*50}")
        print(f"Uploading: {table_name}")
        print(f"{'='*50}")

        print(f"  Creating table if not exists...")
        create_table(conn, conf["create_sql"])

        # Full replace: empty the table so RMP-side edits stop drifting, since
        # the upsert path never updates. Reachable only for rmp_professors (see
        # REPLACE_ALLOWED), whose columns are aggregates RMP recomputes and which
        # no request path reads. Runs only after the workflow's scrape floors
        # passed, so the CSV is known-good.
        if replace:
            if not os.path.exists(csv_path):
                sys.exit(f"--replace: {csv_path} missing; refusing to truncate {table_name}")
            print(f"  Truncating {table_name} for full replace...")
            with conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {table_name}")
            conn.commit()

        # Pre-fetch existing keys to filter client-side (saves RUs)
        key_columns = conf.get("key_columns")
        existing_keys = set()
        if key_columns and not replace:
            print(f"  Fetching existing keys for client-side filtering...")
            existing_keys = fetch_existing_keys(
                conn, table_name, key_columns, conf.get("key_query")
            )
            print(f"  Found {len(existing_keys):,} existing keys")

        print(f"  Reading {conf['csv']}...")
        start = time.time()
        upload_csv(
            conn, table_name, conf["columns"], csv_path,
            conf.get("transform"), conf.get("on_conflict", ""),
            key_columns, existing_keys, conf.get("deny_name")
        )
        elapsed = time.time() - start
        print(f"  Time: {elapsed:.1f}s")

    conn.close()
    print("\nDone! All tables migrated.")


if __name__ == "__main__":
    main()
