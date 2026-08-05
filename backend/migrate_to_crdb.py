"""
Migrate CSV data into CockroachDB.
Idempotent — safe to re-run. Pre-filters rows client-side to avoid sending
data the DB already has, minimizing Request Units.

Run:  python backend/migrate_to_crdb.py all
"""

import os, csv, sys, time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATABASE_URL = os.getenv("CRDB_DATABASE_URL")

if not DATABASE_URL:
    sys.exit("Missing CRDB_DATABASE_URL in backend/.env")

import psycopg2
from psycopg2.extras import execute_values

DATA_DIR = os.path.join(os.path.dirname(__file__), "Better_Scraper", "output_data")
BATCH_SIZE = 25000  # smaller batches to stay under CockroachDB lock-tracking budget


def get_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


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
    except Exception:
        conn.rollback()
        return set()


def dedupe_rows(rows: list[tuple], columns: list[str],
                dedupe_on: list[str], prefer: str) -> list[tuple]:
    """Collapse rows sharing a conflict key, keeping the largest `prefer` value.

    ON CONFLICT DO UPDATE is stricter than DO NOTHING: one INSERT carrying the
    same conflict key twice fails outright with "cannot affect row a second
    time", where DO NOTHING quietly dropped the repeat. The professor CSV has
    such a pair today — two RMP profile pages for Hamid Nayeb Hashemi in
    Mechanical Engineering, one a 0-rating stub — and every row goes out in one
    statement at BATCH_SIZE 25,000, so the whole weekly load would fail.

    Preferring the higher rating count picks the real page over the stub and
    matches how precompute.merge_rmp_aliases chooses a primary among an RMP
    professor's duplicate profiles. First-appearance order is preserved.
    """
    key_idx = [columns.index(c) for c in dedupe_on]
    prefer_idx = columns.index(prefer)
    best: dict = {}
    for row in rows:
        key = tuple(row[i] for i in key_idx)
        current = best.get(key)
        if current is None or (row[prefer_idx] or 0) > (current[prefer_idx] or 0):
            best[key] = row
    return list(best.values())


def upload_csv(conn, table: str, columns: list[str], csv_path: str,
               transform=None, on_conflict: str = "",
               key_columns: list[str] = None, existing_keys: set = None,
               dedupe_on: list[str] = None, dedupe_prefer: str = None):
    if not os.path.exists(csv_path):
        print(f"  File not found: {csv_path}")
        return

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
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

        for row in reader:
            if transform:
                row = transform(row)
            if row is None:
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

            # Deduping needs the whole file in hand, so those tables buffer
            # instead of streaming. Only rmp_professors does this (~3.9k rows).
            if dedupe_on:
                continue

            if len(batch) >= BATCH_SIZE:
                with conn.cursor() as cur:
                    execute_values(cur, insert_sql, batch, page_size=BATCH_SIZE)
                conn.commit()
                total += len(batch)
                print(f"  Uploaded {total:,} new rows (skipped {skipped:,} existing)...", end="\r")
                batch = []

        if dedupe_on:
            before = len(batch)
            batch = dedupe_rows(batch, columns, dedupe_on, dedupe_prefer)
            if before != len(batch):
                print(f"  Collapsed {before - len(batch):,} duplicate "
                      f"{'/'.join(dedupe_on)} rows before upserting")

        if batch:
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, batch, page_size=BATCH_SIZE)
            conn.commit()
            total += len(batch)
    print(f"  Done: {total:,} rows inserted, {skipped:,} skipped (already in DB).")


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
        # No key_columns on purpose. Every other table here is append-only, so
        # skipping rows the DB already has is free RUs. These rows are a summary
        # that the weekly re-scrape is supposed to move: filtering them out
        # client-side is what made the table insert-only, freezing each
        # professor's numbers at whatever they were the first time we saw them.
        # One scrape's drift across 3,861 professors present in both: rating
        # moved for 315, num_ratings 533, would_take_again_pct 480,
        # level_of_difficulty 331. ~3.9k rows a week is a rounding error in RUs.
        "csv": "rmp_professors.csv",
        "dedupe_on": ["name", "department"],
        "dedupe_prefer": "num_ratings",
        "on_conflict": (
            "ON CONFLICT (name, department) DO UPDATE SET "
            "rating = EXCLUDED.rating, "
            "num_ratings = EXCLUDED.num_ratings, "
            "would_take_again_pct = EXCLUDED.would_take_again_pct, "
            "level_of_difficulty = EXCLUDED.level_of_difficulty, "
            "professor_url = EXCLUDED.professor_url"
        ),
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
        # Lightweight proxy: skip by (professor, course, date) — avoids pulling
        # every stored comment back over the wire just to diff it.
        #
        # Deliberately coarser than the unique constraint, which also includes
        # comment. The gap: a genuinely new review sharing professor, course and
        # date with one already loaded is skipped client-side even though the DB
        # would accept it. RMP timestamps carry seconds, so this is rare — 4
        # such pairs in the current 44,536-row CSV, and both halves of a pair
        # load fine on the run that first sees them (existing_keys is a snapshot
        # taken before the run, so neither filters the other out). Only a review
        # posted in a later week at the same second as an earlier one is lost.
        # Widening the key means fetching ~44k comment bodies every run.
        "key_columns": ["professor_name", "course", "date"],
        "key_query": "SELECT DISTINCT professor_name, course, date FROM rmp_reviews",
        "csv": "rmp_reviews.csv",
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
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["trace_comments"]

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

    conn = get_connection()
    print("Connected to CockroachDB!")

    for table_name in targets:
        if table_name not in TABLES:
            print(f"Unknown table: {table_name}")
            continue

        conf = TABLES[table_name]
        csv_path = os.path.join(DATA_DIR, conf["csv"])

        print(f"\n{'='*50}")
        print(f"Uploading: {table_name}")
        print(f"{'='*50}")

        print(f"  Creating table if not exists...")
        create_table(conn, conf["create_sql"])

        # Pre-fetch existing keys to filter client-side (saves RUs)
        key_columns = conf.get("key_columns")
        existing_keys = set()
        if key_columns:
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
            key_columns, existing_keys,
            conf.get("dedupe_on"), conf.get("dedupe_prefer")
        )
        elapsed = time.time() - start
        print(f"  Time: {elapsed:.1f}s")

    conn.close()
    print("\nDone! All tables migrated.")


if __name__ == "__main__":
    main()
