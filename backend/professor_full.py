"""Injectable SQL orchestration for the unauthenticated /full professor page.

Pure functions that take `query`/`query_one` (the chat_retrieve.py pattern) so
they can be unit-tested without a live DB. server.py wires these to the real
query helpers. This collapses the old cold-path round-trips:

  before: 2x professors_catalog, 2x trace_courses, 3x trace_scores scans,
          rmp_reviews, trace_comments, reddit  (~10 sequential trips)
  after:  1x professors_catalog, 1x trace_courses, 1x trace_scores scan,
          rmp_reviews, trace_comments, reddit  (~6 sequential trips)

Only the unauthenticated branch is extracted here; the authenticated profile
path (full scores + radar) stays in server.py.
"""

import re

from prof_aliases import ALIAS_MAP


def _resolve_professor(slug, query_one):
    """One catalog lookup, slug then name_key fallback. Returns the row or None."""
    prof = query_one("SELECT * FROM professors_catalog WHERE slug = %s", (slug,))
    if not prof:
        name_key = slug.strip().lower().replace("-", " ")
        name_key = ALIAS_MAP.get(name_key, name_key)
        prof = query_one("SELECT * FROM professors_catalog WHERE name_key = %s", (name_key,))
    return prof


def trace_key(prof):
    """TRACE-side name_key for a catalog row.

    Fuzzy-matched professors carry their TRACE scores under a different name
    than RMP uses; precompute records which one in trace_name_key. NULL (exact
    match, or a catalog built before the column existed) falls back to the
    professor's own key. RMP-side lookups must keep using prof["name_key"].
    """
    return prof.get("trace_name_key") or prof["name_key"]


def _course_code(display_name):
    dn = str(display_name or "")
    m = re.match(r"^([A-Z]+\d+)", dn)
    return (m.group(1) if m else dn.split(":")[0].split(" ")[0]).upper()


def _scan_trace_scores(name_key, query):
    """Single scan over the professor's challenge/overall/hours TRACE scores.

    Replaces the three near-identical JOIN scans the unauthed path used to run.
    Returns (challeng_by_ct, hours_by_ct, rating_dist_by_course,
             challeng_sum, challeng_weight) — exactly the aggregates the old
    three scans produced, computed with the same weighted-mean / mean-fallback
    math.
    """
    rows = query("""
        SELECT tc.display_name, ts.course_id, ts.term_id, ts.question, ts.mean,
               ts.completed, ts.count_1, ts.count_2, ts.count_3, ts.count_4, ts.count_5
        FROM trace_scores ts
        JOIN trace_courses tc
          ON ts.course_id = tc.course_id
         AND ts.instructor_id = tc.instructor_id
         AND ts.term_id = tc.term_id
        WHERE tc.name_key = %s
          AND (lower(ts.question) LIKE '%%challeng%%'
               OR (lower(ts.question) LIKE '%%overall%%'
                   AND lower(ts.question) != 'overall effectiveness')
               OR lower(ts.question) LIKE '%%hours%%')
    """, (name_key,))

    challeng_by_ct = {}
    hours_by_ct = {}
    rating_dist_by_course = {}
    challeng_sum, challeng_weight = 0.0, 0

    for s in rows:
        q = str(s["question"] or "").lower()
        c1 = int(s["count_1"] or 0); c2 = int(s["count_2"] or 0)
        c3 = int(s["count_3"] or 0); c4 = int(s["count_4"] or 0)
        c5 = int(s["count_5"] or 0)
        total_resp = c1 + c2 + c3 + c4 + c5
        key = (int(s["course_id"]), int(s["term_id"] or 0))

        if "challeng" in q:
            if key not in challeng_by_ct:
                challeng_by_ct[key] = {"sum": 0.0, "weight": 0}
            if total_resp > 0:
                computed_mean = (1*c1 + 2*c2 + 3*c3 + 4*c4 + 5*c5) / total_resp
                challeng_sum += computed_mean * total_resp
                challeng_weight += total_resp
                challeng_by_ct[key]["sum"] += computed_mean * total_resp
                challeng_by_ct[key]["weight"] += total_resp
            elif s["mean"]:
                challeng_sum += float(s["mean"])
                challeng_weight += 1
                challeng_by_ct[key]["sum"] += float(s["mean"])
                challeng_by_ct[key]["weight"] += 1

        if "hours" in q:
            if key not in hours_by_ct:
                hours_by_ct[key] = {"sum": 0.0, "weight": 0}
            if total_resp > 0:
                hours_by_ct[key]["sum"] += (1*c1 + 3.5*c2 + 6*c3 + 9*c4 + 12*c5)
                hours_by_ct[key]["weight"] += total_resp
            elif s["mean"]:
                hours_by_ct[key]["sum"] += float(s["mean"])
                hours_by_ct[key]["weight"] += 1

        # Law sections carry two overall questions; ratings use 'Overall Course' only.
        # Exact match: the Bluera label also contains the word "effectiveness".
        if "overall" in q and q != "overall effectiveness":
            code = _course_code(s["display_name"])
            if code not in rating_dist_by_course:
                rating_dist_by_course[code] = {"count1": 0, "count2": 0, "count3": 0,
                                               "count4": 0, "count5": 0, "completed": 0}
            rating_dist_by_course[code]["count1"] += c1
            rating_dist_by_course[code]["count2"] += c2
            rating_dist_by_course[code]["count3"] += c3
            rating_dist_by_course[code]["count4"] += c4
            rating_dist_by_course[code]["count5"] += c5
            rating_dist_by_course[code]["completed"] += int(s["completed"] or 0)

    return challeng_by_ct, hours_by_ct, rating_dist_by_course, challeng_sum, challeng_weight


def build_profile_unauthed(prof, trace_course_rows, query, blend_fields=None):
    """Build the unauthenticated profile dict from an already-fetched catalog
    row and trace_courses rows (no further catalog/course lookups).

    `blend_fields` is server._rating_blend_fields' output for this professor —
    rmpAdjusted and the parameters the course-filtered card pools a subset with.
    Injected rather than computed here for the same reason every query is: this
    module stays free of the calibration fit and its catalog scan, so the two
    payload builders serve one set of fields from one implementation.
    """
    profile = {
        "name": prof["name"],
        "department": prof["department"],
        "rmpRating": round(prof["rmp_rating"], 2) if prof["rmp_rating"] else None,
        "traceRating": round(prof["trace_rating"], 2) if prof["trace_rating"] else None,
        # None, not 0.0: precompute leaves avg_rating NULL for a professor with
        # no RMP ratings and no responses to TRACE's overall question, and 0 is
        # not a rating — the scale starts at 1, so the card rendered "0.00" under
        # five empty stars while Total Ratings beside it read "—". Matches every
        # other producer of this field (server.py:879, server.py:1102,
        # bookmarks.py); this was the only one that coalesced.
        "avgRating": round(prof["avg_rating"], 2) if prof["avg_rating"] else None,
        "wouldTakeAgainPct": round(prof["would_take_again_pct"], 1) if prof["would_take_again_pct"] else None,
        "difficulty": round(prof["difficulty"], 2) if prof["difficulty"] else None,
        "totalRatings": prof["total_reviews"],
        "professorUrl": prof["professor_url"],
        "imageUrl": prof["image_url"],
        "focusX": prof.get("focus_x") if prof.get("focus_x") is not None else 50.0,
        "focusY": prof.get("focus_y") if prof.get("focus_y") is not None else 30.0,
        "hoursPerWeek": round(prof["avg_hours"], 1) if prof["avg_hours"] else None,
    }
    profile.update(blend_fields or {})

    # TRACE scores are filed under the TRACE spelling of the name, which is not
    # prof["name_key"] for a fuzzy-matched professor. See trace_key.
    (challeng_by_ct, hours_by_ct, rating_dist_by_course,
     challeng_sum, challeng_weight) = _scan_trace_scores(trace_key(prof), query)

    trace_avg_difficulty = round(challeng_sum / challeng_weight, 2) if challeng_weight > 0 else None
    profile["traceRatingCounts"] = rating_dist_by_course
    profile["radarData"] = None

    trace_course_list = []
    for c in trace_course_rows:
        cid = int(c["course_id"]); tid = int(c["term_id"]) if c["term_id"] else 0
        h = hours_by_ct.get((cid, tid))
        course_hours = round(h["sum"] / h["weight"], 1) if h and h["weight"] > 0 else None
        ch = challeng_by_ct.get((cid, tid))
        trace_course_list.append({
            "courseId": cid,
            "termId": tid,
            "termTitle": str(c["term_title"] or ""),
            "departmentName": str(c["department_name"] or ""),
            "displayName": str(c["display_name"] or ""),
            "hoursPerWeek": course_hours,
            "challengeWeightedSum": ch["sum"] if ch and ch["weight"] > 0 else None,
            "challengeResponses": ch["weight"] if ch and ch["weight"] > 0 else None,
        })

    rmp_diff = round(prof["difficulty"], 2) if prof["difficulty"] else None
    if rmp_diff is not None and trace_avg_difficulty is not None:
        profile["difficulty"] = round((rmp_diff + trace_avg_difficulty) / 2, 2)
    elif trace_avg_difficulty is not None:
        profile["difficulty"] = trace_avg_difficulty

    profile["traceCourses"] = trace_course_list
    return profile


def build_reviews(slug, prof, trace_course_rows, query, sanitize,
                  fetch_reddit_mentions, is_authed):
    """Build reviews/traceComments/redditMentions from already-fetched rows.

    `trace_course_rows` are the same rows used by the profile build, so no
    second trace_courses fetch happens.
    """
    name_key = prof["name_key"]

    review_rows = query("""
        SELECT course, quality, difficulty, date, tags, attendance, grade,
               textbook, online_class, comment
        FROM rmp_reviews WHERE name_key = %s
    """, (name_key,))
    reviews = []
    for r in review_rows:
        reviews.append({
            "course": str(r["course"] or ""),
            "quality": int(r["quality"]) if r["quality"] else 0,
            "difficulty": int(r["difficulty"]) if r["difficulty"] else 0,
            "date": str(r["date"] or ""),
            "tags": str(r["tags"] or ""),
            "attendance": str(r["attendance"] or ""),
            "grade": str(r["grade"] or ""),
            "textbook": str(r["textbook"] or ""),
            "online_class": str(r["online_class"] or ""),
            "comment": sanitize(r["comment"]) if r["comment"] else "",
        })

    comments = []
    if trace_course_rows:
        keys = set()
        for c in trace_course_rows:
            keys.add((int(c["course_id"]), int(c["instructor_id"]),
                      int(c["term_id"]) if c["term_id"] else 0))
        if keys:
            comment_rows = query(
                "SELECT tc_term_id, tc_course_id, question, comment FROM trace_comments "
                "WHERE (tc_course_id, tc_instructor_id, tc_term_id) IN %s",
                (tuple(keys),)
            )
            by_question = {}
            for c in comment_rows:
                comment_text = sanitize(c["comment"]) if c["comment"] else ""
                if not comment_text.strip():
                    continue
                q = str(c["question"] or "")
                by_question.setdefault(q, []).append({
                    "question": q,
                    "comment": comment_text,
                    "termId": int(c["tc_term_id"]) if c["tc_term_id"] else 0,
                    "courseId": int(c["tc_course_id"]) if c["tc_course_id"] else 0,
                })

            def _normalize(s):
                return re.sub(r'\s+', ' ', s.lower()).strip()

            def _dedup_group(items):
                seen = set()
                result = []
                for item in items:
                    prefix_key = _normalize(item["comment"])[:80]
                    if prefix_key in seen:
                        continue
                    seen.add(prefix_key)
                    result.append(item)
                return result

            for q, items in by_question.items():
                for item in _dedup_group(items):
                    comments.append({
                        "question": item["question"],
                        "comment": item["comment"] if is_authed else "",
                        "termId": item["termId"],
                        "courseId": item["courseId"],
                    })

    reddit_mentions = fetch_reddit_mentions(slug, query)
    for m in reddit_mentions:
        m["body"] = sanitize(m["body"]) if m["body"] else ""

    return {"reviews": reviews, "traceComments": comments, "redditMentions": reddit_mentions}


def build_trace_course_rows(name_key, query):
    """Single trace_courses fetch shared by profile + reviews. Selects the
    union of columns both consumers need."""
    return query("""
        SELECT course_id, term_id, term_title, department_name, display_name,
               section, enrollment, instructor_id
        FROM trace_courses WHERE name_key = %s
        ORDER BY term_id DESC
    """, (name_key,))


def build_full(slug, query, query_one, sanitize,
               fetch_reddit_mentions=None, is_authed=False, blend_fields=None):
    """Orchestrate the unauthenticated /full payload with shared lookups.

    Returns the combined profile+reviews dict, or None if the professor does
    not exist (caller maps None to a 404).

    `blend_fields` is a callable taking the resolved catalog row — the professor
    is resolved here, so the caller cannot compute it up front. See
    build_profile_unauthed.
    """
    if fetch_reddit_mentions is None:
        def fetch_reddit_mentions(_slug, _q):
            return []

    prof = _resolve_professor(slug, query_one)
    if not prof:
        return None

    # trace_key, not name_key: these rows are the professor's course list and the
    # (course, instructor, term) keys every TRACE comment is looked up through, so
    # a fuzzy-matched professor gets an empty page under the RMP spelling.
    trace_course_rows = build_trace_course_rows(trace_key(prof), query)

    profile = build_profile_unauthed(
        prof, trace_course_rows, query,
        blend_fields(prof) if blend_fields else None)
    reviews = build_reviews(slug, prof, trace_course_rows, query, sanitize,
                            fetch_reddit_mentions, is_authed)

    profile["reviews"] = reviews["reviews"]
    profile["traceComments"] = reviews["traceComments"]
    profile["redditMentions"] = reviews["redditMentions"]
    return profile
