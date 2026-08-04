"""Injectable SQL orchestration for the bookmarks feature.

Pure functions that take `query`/`query_one` (read) and `write` (execute+commit,
the chat_question.py `log_fn`/`_chat_write` pattern) so they can be
unit-tested without a live DB. server.py wires these to the real query/write
helpers and owns all HTTP/auth concerns.

add_bookmark additionally takes `write_all`, which commits several statements as
one transaction — it mutates two rows and they must land together. See
_apply_writes for what happens when a caller cannot supply one.
"""

from prof_aliases import canonical_slug, retired_slugs

_CATALOG_TABLES = {
    "professor": ("professors_catalog", "slug"),
    "course": ("course_catalog", "code"),
}

BOOKMARK_CAP = 200  # max bookmarks per user (professors + courses combined)


def item_exists(item_type, item_key, query_one):
    """Check that item_key resolves to a real row in the relevant catalog table."""
    table_info = _CATALOG_TABLES.get(item_type)
    if table_info is None:
        return False
    table, key_col = table_info
    row = query_one(f"SELECT 1 FROM {table} WHERE {key_col} = %s", (item_key,))
    return row is not None


def _apply_writes(statements, write, write_all):
    """Run add_bookmark's mutations, as one transaction when the caller can.

    `write_all` commits the whole list atomically. Without one the statements run
    and commit individually, which is why the INSERT is ordered first: a failure
    part-way can then only leave a *duplicate* row under a retired slug — already
    the steady state for anyone who bookmarked before a rename, invisible in the
    list, and collapsed by the next add of that professor. Deleting first risked
    the one outcome that is not recoverable, a bookmark the user loses because the
    insert meant to replace it never ran.
    """
    if write_all is not None:
        write_all(statements)
        return
    for sql, params in statements:
        write(sql, params)


def add_bookmark(user_sub, item_type, item_key, query_one, write, write_all=None):
    """Idempotently bookmark an item for a user.

    Returns "ok" once the bookmark exists, "not_found" if item_type/item_key
    don't resolve to a real professor/course (caller maps to 404), or
    "limit_reached" if the user already holds BOOKMARK_CAP bookmarks
    (caller maps to 400).

    `write_all` makes the collapse and the insert atomic; omitting it falls back
    to sequential writes, which is safe but not atomic (see _apply_writes).
    """
    if item_type == "professor" and not item_exists(item_type, item_key, query_one):
        # A page loaded before a rename can still be offering the retired slug;
        # store the current one so the bookmark doesn't start out dangling.
        current = canonical_slug(item_key)
        if current:
            item_key = current
    if not item_exists(item_type, item_key, query_one):
        return "not_found"

    # Rows this add supersedes: ones the user still holds under a slug that has
    # since been retired onto item_key. list_bookmarks renders them as the same
    # professor, so they are invisible — but each occupies one of their
    # BOOKMARK_CAP slots, and nothing else ever cleared them.
    stale = retired_slugs(item_key) if item_type == "professor" else []

    # The cap is checked against what will exist once this add settles, so the
    # count excludes the rows about to be collapsed *and* item_key itself. That
    # replaces counting after an early DELETE, which is what forced the delete to
    # run before the insert in the first place. Excluding item_key also makes
    # re-adding something already bookmarked return "ok" at exactly the cap
    # instead of a spurious "limit_reached" for a bookmark the user already has.
    count_row = query_one(
        "SELECT count(*) AS cnt FROM bookmarks WHERE user_sub = %s "
        "AND NOT (item_type = %s AND item_key = ANY(%s))",
        (user_sub, item_type, [item_key] + stale),
    )
    if count_row and count_row["cnt"] >= BOOKMARK_CAP:
        return "limit_reached"

    statements = [(
        "INSERT INTO bookmarks (user_sub, item_type, item_key) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_sub, item_type, item_key) DO NOTHING",
        (user_sub, item_type, item_key),
    )]
    if stale:
        statements.append((
            "DELETE FROM bookmarks WHERE user_sub = %s AND item_type = %s "
            "AND item_key = ANY(%s)",
            (user_sub, item_type, stale),
        ))
    _apply_writes(statements, write, write_all)
    return "ok"


def remove_bookmark(user_sub, item_type, item_key, write):
    """Idempotently remove a bookmark (no-op if it didn't exist).

    Matches retired slugs too. list_bookmarks reports a renamed professor under
    their current slug, so that is the key the client sends back to unbookmark,
    while the stored row still holds whichever slug was current when the bookmark
    was made — without this the delete would silently match nothing and the
    bookmark would reappear.
    """
    keys = [item_key]
    if item_type == "professor":
        keys += retired_slugs(item_key)
    write(
        "DELETE FROM bookmarks WHERE user_sub = %s AND item_type = %s AND item_key = ANY(%s)",
        (user_sub, item_type, keys),
    )


def _professor_row(row, bookmarked_at):
    return {
        "name": row["name"],
        "slug": row["slug"],
        "department": row["department"],
        "college": row["college"],
        "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
        "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
        "traceRating": round(row["trace_rating"], 2) if row["trace_rating"] else None,
        "totalReviews": row["total_reviews"],
        "totalComments": row.get("total_comments", 0) or 0,
        "wouldTakeAgainPct": round(row["would_take_again_pct"], 1) if row["would_take_again_pct"] else None,
        "imageUrl": row["image_url"],
        "focusX": row.get("focus_x") if row.get("focus_x") is not None else 50.0,
        "focusY": row.get("focus_y") if row.get("focus_y") is not None else 30.0,
        "bookmarkedAt": bookmarked_at.isoformat(),
    }


def _course_row(row, bookmarked_at):
    return {
        "code": row["code"],
        "name": row["name"],
        "department": row["department"],
        "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] is not None else None,
        "bookmarkedAt": bookmarked_at.isoformat(),
    }


def list_bookmarks(user_sub, query):
    """Fetch a user's bookmarks, denormalized into full display rows shaped
    like the catalog list endpoints (CatalogProfessor/CatalogCourse), newest
    first, plus a bookmarkedAt timestamp on each row.

    Rows whose item_key no longer resolves (e.g. removed in a later
    precompute.py catalog rebuild) are silently skipped — but a professor whose
    slug was merely *renamed* by an ALIAS_MAP addition is followed to its current
    slug rather than dropped, so the bookmark survives the rename.
    """
    bookmark_rows = query(
        "SELECT item_type, item_key, created_at FROM bookmarks "
        "WHERE user_sub = %s ORDER BY created_at DESC",
        (user_sub,),
    )

    prof_keys = [r["item_key"] for r in bookmark_rows if r["item_type"] == "professor"]
    course_keys = [r["item_key"] for r in bookmark_rows if r["item_type"] == "course"]
    bookmarked_at = {(r["item_type"], r["item_key"]): r["created_at"] for r in bookmark_rows}

    professors = []
    if prof_keys:
        # catalog slug -> the bookmark key it should report a timestamp for. Every
        # bookmarked slug is registered first so a still-live slug always wins,
        # then retired slugs add their current name (a professor's slug changes
        # when an ALIAS_MAP entry renames their name_key).
        by_catalog_slug = {k: k for k in prof_keys}
        for k in prof_keys:
            current = canonical_slug(k)
            if current:
                by_catalog_slug.setdefault(current, k)
        prof_table_rows = query(
            "SELECT * FROM professors_catalog WHERE slug = ANY(%s)",
            (list(by_catalog_slug),),
        )
        professors = [
            _professor_row(row, bookmarked_at[("professor", by_catalog_slug[row["slug"]])])
            for row in prof_table_rows
        ]
        professors.sort(key=lambda p: p["bookmarkedAt"], reverse=True)

    courses = []
    if course_keys:
        course_table_rows = query("SELECT * FROM course_catalog WHERE code = ANY(%s)", (course_keys,))
        courses = [
            _course_row(row, bookmarked_at[("course", row["code"])])
            for row in course_table_rows
        ]
        courses.sort(key=lambda c: c["bookmarkedAt"], reverse=True)

    return {"professors": professors, "courses": courses}
