"""Injectable SQL orchestration for the bookmarks feature.

Pure functions that take `query`/`query_one` (read) and `write` (execute+commit,
the chat_question.py `log_fn`/`_chat_write` pattern) so they can be
unit-tested without a live DB. server.py wires these to the real query/write
helpers and owns all HTTP/auth concerns.
"""

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


def add_bookmark(user_sub, item_type, item_key, query_one, write):
    """Idempotently bookmark an item for a user.

    Returns "ok" once the bookmark exists, "not_found" if item_type/item_key
    don't resolve to a real professor/course (caller maps to 404), or
    "limit_reached" if the user already holds BOOKMARK_CAP bookmarks
    (caller maps to 400).
    """
    if not item_exists(item_type, item_key, query_one):
        return "not_found"
    count_row = query_one("SELECT count(*) AS cnt FROM bookmarks WHERE user_sub = %s", (user_sub,))
    if count_row and count_row["cnt"] >= BOOKMARK_CAP:
        return "limit_reached"
    write(
        "INSERT INTO bookmarks (user_sub, item_type, item_key) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_sub, item_type, item_key) DO NOTHING",
        (user_sub, item_type, item_key),
    )
    return "ok"


def remove_bookmark(user_sub, item_type, item_key, write):
    """Idempotently remove a bookmark (no-op if it didn't exist)."""
    write(
        "DELETE FROM bookmarks WHERE user_sub = %s AND item_type = %s AND item_key = %s",
        (user_sub, item_type, item_key),
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
    precompute.py catalog rebuild) are silently skipped.
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
        prof_table_rows = query("SELECT * FROM professors_catalog WHERE slug = ANY(%s)", (prof_keys,))
        professors = [
            _professor_row(row, bookmarked_at[("professor", row["slug"])])
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
