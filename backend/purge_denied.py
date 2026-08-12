"""Delete rows already loaded for professors on the denylist.

The loaders' filters govern the next write; they cannot reach what is already in
the database. A deletion request needs both — the filters so a refresh never
restores the professor, this so the data goes now.

Deletion order is forced by how the tables reference each other:

  1. resolve identity   trace_courses holds the only mapping from a name to the
                        (course_id, instructor_id, term_id) keys that
                        trace_scores and trace_comments are filed under, so the
                        keys must be collected BEFORE the course rows go. Delete
                        trace_courses first and the scores and comments become
                        unreachable orphans that no later run can find.
  2. evidence_embeddings then evidence   the embedding references the evidence
                        row; the other order leaves vectors joined to nothing.
  3. everything else

Run with --dry-run first. It reports the same counts without deleting.

    python purge_denied.py --dry-run
    python purge_denied.py
"""

import argparse
import sys

from denylist import denied_hashes, is_denied, is_denied_key


def find_targets(cur):
    """Everything the denylist matches, resolved to ids before anything is deleted.

    Returns (slugs, name_keys, trace_keys). Matching happens in Python rather
    than SQL because the list holds hashes, not names — there is no WHERE clause
    that can express "sha256 of this column is in this set", and the alternative
    is shipping the plaintext names into the query the file deliberately avoids
    storing.
    """
    slugs, name_keys = set(), set()

    cur.execute("SELECT slug, name, name_key, trace_name_key FROM professors_catalog")
    for slug, name, name_key, trace_name_key in cur.fetchall():
        if is_denied(name) or is_denied_key(name_key) or is_denied_key(trace_name_key):
            slugs.add(slug)
            name_keys.update(k for k in (name_key, trace_name_key) if k)

    # The catalog is not enough on its own. Once precompute has run with the
    # denylist the professor has no catalog row at all, while their TRACE course
    # and comment rows are still loaded — so the raw tables have to be searched
    # by name too, or a purge after a refresh would find nothing and report clean.
    cur.execute("SELECT DISTINCT instructor_first_name, instructor_last_name FROM trace_courses")
    for first, last in cur.fetchall():
        if is_denied(f"{first or ''} {last or ''}"):
            name_keys.add(f"{(first or '').strip().lower()} {(last or '').strip().lower()}".strip())

    cur.execute("SELECT DISTINCT professor_name, name_key FROM rmp_reviews")
    for professor_name, name_key in cur.fetchall():
        if is_denied(professor_name) or is_denied_key(name_key):
            if name_key:
                name_keys.add(name_key)

    trace_keys = []
    if name_keys:
        cur.execute(
            "SELECT DISTINCT course_id, instructor_id, term_id FROM trace_courses "
            "WHERE name_key = ANY(%s)", (list(name_keys),))
        trace_keys = [tuple(r) for r in cur.fetchall()]
        # name_key is backfilled by precompute and may be NULL on rows loaded
        # since the last run, so match on the split names as well.
        cur.execute("SELECT DISTINCT course_id, instructor_id, term_id, "
                    "instructor_first_name, instructor_last_name FROM trace_courses "
                    "WHERE name_key IS NULL")
        for cid, iid, tid, first, last in cur.fetchall():
            if is_denied(f"{first or ''} {last or ''}"):
                trace_keys.append((cid, iid, tid))

    return sorted(slugs), sorted(name_keys), sorted(set(trace_keys))


def purge(conn, dry_run=False):
    """Delete every row belonging to a denied professor. Returns per-table counts."""
    if not denied_hashes():
        print("Denylist is empty — nothing to purge.")
        return {}

    cur = conn.cursor()
    slugs, name_keys, trace_keys = find_targets(cur)
    print(f"Matched {len(slugs)} catalog slugs, {len(name_keys)} name keys, "
          f"{len(trace_keys)} TRACE (course, instructor, term) keys")
    if not (slugs or name_keys or trace_keys):
        print("Nothing to purge.")
        return {}

    # (label, sql, params) in dependency order. Counted with SELECT first so a
    # dry run reports exactly what the real run would remove.
    steps = []
    if slugs:
        steps.append((
            "evidence_embeddings",
            "DELETE FROM evidence_embeddings WHERE evidence_id IN "
            "(SELECT id FROM evidence WHERE professor_slug = ANY(%s))",
            "SELECT count(*) FROM evidence_embeddings WHERE evidence_id IN "
            "(SELECT id FROM evidence WHERE professor_slug = ANY(%s))",
            (slugs,)))
        steps.append((
            "evidence",
            "DELETE FROM evidence WHERE professor_slug = ANY(%s)",
            "SELECT count(*) FROM evidence WHERE professor_slug = ANY(%s)",
            (slugs,)))
        steps.append((
            "reddit_sentiment",
            "DELETE FROM reddit_sentiment WHERE professor_slug = ANY(%s)",
            "SELECT count(*) FROM reddit_sentiment WHERE professor_slug = ANY(%s)",
            (slugs,)))
        steps.append((
            "reddit_mentions",
            "DELETE FROM reddit_mentions WHERE professor_slug = ANY(%s)",
            "SELECT count(*) FROM reddit_mentions WHERE professor_slug = ANY(%s)",
            (slugs,)))
        steps.append((
            "professors_catalog",
            "DELETE FROM professors_catalog WHERE slug = ANY(%s)",
            "SELECT count(*) FROM professors_catalog WHERE slug = ANY(%s)",
            (slugs,)))
    if trace_keys:
        cids = [k[0] for k in trace_keys]
        iids = [k[1] for k in trace_keys]
        tids = [k[2] for k in trace_keys]
        # Tuple-wise IN over three parallel arrays: CRDB takes (a,b,c) IN
        # (SELECT unnest, unnest, unnest), which keeps this one statement rather
        # than one per course-term.
        tup = ("(course_id, instructor_id, term_id) IN "
               "(SELECT unnest(%s::INT[]), unnest(%s::INT[]), unnest(%s::INT[]))")
        steps.append((
            "trace_comments",
            "DELETE FROM trace_comments WHERE (tc_course_id, tc_instructor_id, tc_term_id) IN "
            "(SELECT unnest(%s::INT[]), unnest(%s::INT[]), unnest(%s::INT[]))",
            "SELECT count(*) FROM trace_comments WHERE (tc_course_id, tc_instructor_id, tc_term_id) IN "
            "(SELECT unnest(%s::INT[]), unnest(%s::INT[]), unnest(%s::INT[]))",
            (cids, iids, tids)))
        steps.append((
            "trace_scores",
            f"DELETE FROM trace_scores WHERE {tup}",
            f"SELECT count(*) FROM trace_scores WHERE {tup}",
            (cids, iids, tids)))
        steps.append((
            "trace_courses",
            f"DELETE FROM trace_courses WHERE {tup}",
            f"SELECT count(*) FROM trace_courses WHERE {tup}",
            (cids, iids, tids)))
    if name_keys:
        steps.append((
            "rmp_reviews",
            "DELETE FROM rmp_reviews WHERE name_key = ANY(%s)",
            "SELECT count(*) FROM rmp_reviews WHERE name_key = ANY(%s)",
            (name_keys,)))

    results = {}
    for label, delete_sql, count_sql, params in steps:
        try:
            cur.execute(count_sql, params)
            n = cur.fetchone()[0]
        except Exception as exc:
            conn.rollback()
            cur = conn.cursor()
            print(f"  {label:22s} skipped ({str(exc).splitlines()[0][:60]})")
            continue
        results[label] = n
        if n and not dry_run:
            cur.execute(delete_sql, params)
            conn.commit()
        print(f"  {label:22s} {n:>7,} rows" + (" (dry run)" if dry_run and n else ""))

    # rmp_professors keys on the display name, not name_key.
    try:
        cur.execute("SELECT id, name FROM rmp_professors")
        ids = [pid for pid, name in cur.fetchall() if is_denied(name)]
        results["rmp_professors"] = len(ids)
        if ids and not dry_run:
            cur.execute("DELETE FROM rmp_professors WHERE id = ANY(%s)", (ids,))
            conn.commit()
        print(f"  {'rmp_professors':22s} {len(ids):>7,} rows"
              + (" (dry run)" if dry_run and ids else ""))
    except Exception as exc:
        conn.rollback()
        print(f"  rmp_professors skipped ({str(exc).splitlines()[0][:60]})")

    total = sum(results.values())
    print(f"\n{'Would delete' if dry_run else 'Deleted'} {total:,} rows across "
          f"{len([k for k, v in results.items() if v])} tables")
    if dry_run:
        print("Dry run — nothing was deleted. Re-run without --dry-run to apply.")
    else:
        print("The loaders' filters keep them out of future refreshes; see denylist.py.")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted, delete nothing")
    args = parser.parse_args(argv)

    from migrate_to_crdb import get_connection
    conn = get_connection()
    try:
        purge(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
