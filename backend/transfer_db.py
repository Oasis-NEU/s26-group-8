"""
Transfer all tables from old CockroachDB cluster to new one.
Reads CRDB_DATABASE_URL (old) and NEW_CRDB_DATABASE_URL (new) from .env.
"""

import os, sys, time
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OLD_URL = os.getenv("CRDB_DATABASE_URL")
NEW_URL = os.getenv("NEW_CRDB_DATABASE_URL")

if not OLD_URL or not NEW_URL:
    sys.exit("Need both CRDB_DATABASE_URL and NEW_CRDB_DATABASE_URL in .env")

BATCH_SIZE = 5000


def connect_database(label, url, attempts=5):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg2.connect(url, sslmode="require")
        except psycopg2.OperationalError as exc:
            message = str(exc)
            if "could not translate host name" not in message:
                raise

            last_error = message
            if attempt < attempts:
                print(f"  DNS lookup failed for {label}; retrying ({attempt}/{attempts})...")
                time.sleep(2)

    sys.exit(
        f"Could not resolve the hostname in {label} after {attempts} attempts.\n"
        "The connection string may still be correct; this machine's DNS resolver is "
        "intermittently failing to resolve the CockroachDB Cloud hostname.\n\n"
        f"Original error:\n{last_error}"
    )


def get_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        return [row[0] for row in cur.fetchall()]


def get_create_statement(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SHOW CREATE TABLE {table}")
        return cur.fetchone()[1]


def get_indexes(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SHOW INDEXES FROM {table}")
        return cur.fetchall()


def transfer_table(old_conn, new_conn, table):
    # Get DDL from old
    create_sql = get_create_statement(old_conn, table)

    # Drop and recreate on new
    with new_conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(create_sql)
    new_conn.commit()

    # Count rows
    with old_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        total_rows = cur.fetchone()[0]
    print(f"  {total_rows:,} rows to transfer")

    if total_rows == 0:
        return

    # Get column names
    with old_conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} LIMIT 0")
        columns = [desc[0] for desc in cur.description]

    col_names = ", ".join(columns)
    insert_sql = f"INSERT INTO {table} ({col_names}) VALUES %s"

    # Stream data in batches using server-side cursor
    transferred = 0
    with old_conn.cursor("read_cursor") as read_cur:
        read_cur.itersize = BATCH_SIZE
        read_cur.execute(f"SELECT {col_names} FROM {table}")

        batch = []
        for row in read_cur:
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                with new_conn.cursor() as write_cur:
                    execute_values(write_cur, insert_sql, batch, page_size=BATCH_SIZE)
                new_conn.commit()
                transferred += len(batch)
                print(f"  {transferred:,} / {total_rows:,}...", end="\r")
                batch = []

        if batch:
            with new_conn.cursor() as write_cur:
                execute_values(write_cur, insert_sql, batch, page_size=BATCH_SIZE)
            new_conn.commit()
            transferred += len(batch)

    print(f"  {transferred:,} / {total_rows:,} done.")


def main():
    print("Connecting to old cluster...")
    old_conn = connect_database("CRDB_DATABASE_URL", OLD_URL)
    print("Connecting to new cluster...")
    new_conn = connect_database("NEW_CRDB_DATABASE_URL", NEW_URL)

    tables = get_tables(old_conn)
    print(f"Found {len(tables)} tables: {', '.join(tables)}\n")

    for table in tables:
        print(f"{'='*50}")
        print(f"Transferring: {table}")
        print(f"{'='*50}")
        transfer_table(old_conn, new_conn, table)
        print()

    old_conn.close()
    new_conn.close()
    print("Transfer complete!")


if __name__ == "__main__":
    main()
