import sys, re, argparse

# A course code looks like 2–5 letters then 4 digits, optionally space-separated
# (e.g. "DS3000", "CS 3000", "ENGW1111"). Used to route a gate hint to the course path.
_COURSE_CODE_RE = re.compile(r"^[A-Za-z]{2,5}\s?\d{4}$")

def _norm_course_code(text):
    return re.sub(r"\s+", "", str(text or "").upper())

def is_course_code(text):
    return bool(text) and bool(_COURSE_CODE_RE.match(str(text).strip()))

# Topic-listing questions name a subject, not a specific course/professor:
# "what database courses are there", "courses about machine learning".
_TOPIC_PATTERNS = [
    re.compile(r"(?:what|which|are there any|any|list|show me)\s+(?:are\s+)?(?:the\s+)?(.+?)\s+(?:courses|classes|electives)\b", re.I),
    re.compile(r"(?:courses|classes|electives)\s+(?:about|on|in|for|related to)\s+(.+)", re.I),
]
_TOPIC_STOPWORDS = {"a", "an", "the", "any", "some", "these", "those", "all"}
_TOPIC_RATING_ADJECTIVES = {"best", "top", "highest", "good", "great", "easiest", "hardest", "easy", "hard", "worst", "lowest"}
_RATINGS_RE = re.compile(r"\b(rated|rating|ratings|best|top|highest|good|easiest|hardest|which is)\b", re.I)

def is_course_topic_query(text):
    """Return the lowercased topic when the text asks to LIST courses by subject, else None.
    Rejects topics that are actually course codes (so 'what is CS3500' isn't hijacked)."""
    t = (text or "").strip()
    for pat in _TOPIC_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        topic = m.group(1).strip().strip("?.! ").lower()
        toks = topic.split()
        while toks and (toks[0] in _TOPIC_STOPWORDS or toks[0] in _TOPIC_RATING_ADJECTIVES):
            toks = toks[1:]
        topic = " ".join(toks)
        if topic and not is_course_code(topic):
            return topic
    return None

def wants_ratings(text):
    """True when the listing question also asks about quality/ratings."""
    return bool(_RATINGS_RE.search(text or ""))

def _course_overall_rating(code, query_fn):
    """Weighted overall TRACE rating for one course code (same pattern as fetch_course_facts)."""
    rows = query_fn("""
        SELECT
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS o_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS o_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        WHERE tc.course_code = %s
    """, (_norm_course_code(code),))
    if not rows:
        return None
    w = float(rows[0].get("o_w") or 0); r = float(rows[0].get("o_r") or 0)
    return round(w / r, 2) if r > 0 else None

def fetch_courses_by_topic(topic, query_fn, limit=8, with_ratings=False):
    """Catalog courses whose search_text matches the topic. Reuses the /api/search course
    query. Per-course rating lookups happen ONLY when with_ratings is True."""
    # search_text is stored lowercased (precompute builds code.lower()+" "+name.lower()), and
    # LIKE is case-sensitive — so the term MUST be lowercased or a capitalized hint like
    # "Discrete Structures" matches nothing.
    topic = re.sub(r"[%_]", " ", str(topic or "")).strip().lower()
    if len(topic) < 2:
        return []
    like = f"%{topic}%"
    rows = query_fn("""
        SELECT code, name, department FROM course_catalog
        WHERE search_text LIKE %s
        ORDER BY CASE WHEN lower(code) LIKE %s THEN 0 ELSE 1 END, code
        LIMIT %s
    """, (like, f"{topic}%", limit))
    courses = [{"code": r["code"], "name": r["name"], "department": r.get("department")} for r in rows]
    if with_ratings:
        for c in courses:
            c["rating"] = _course_overall_rating(c["code"], query_fn)
        courses.sort(key=lambda c: (c.get("rating") is not None, c.get("rating") or 0), reverse=True)
    return courses

def _distinct_courses(matches):
    """De-dupe catalog matches by code, keeping first occurrence (the LIKE query may return
    the same course once; this also collapses any incidental dupes)."""
    seen, out = set(), []
    for m in matches:
        code = m.get("code")
        if code and code not in seen:
            seen.add(code); out.append(m)
    return out

def resolve_course_by_name(name, query_fn, limit=6):
    """A course named by TITLE ('Discrete Structures') rather than code. Search the catalog,
    then keep only matches whose course NAME actually contains the hint as a word-anchored
    phrase (matches at a word boundary, not a bare substring) — so a row that matched solely
    via the code portion of search_text, or a professor surname that happens to be a substring
    of some course name (e.g. "lee" inside "Sleep and Cognition"), isn't treated as a name hit.
    Returns the list of distinct matching courses."""
    term = str(name or "").strip().lower()
    if len(term) < 2:
        return []
    matches = fetch_courses_by_topic(name, query_fn, limit=limit)
    pat = re.compile(r"\b" + re.escape(term))
    named = [m for m in matches if pat.search((m.get("name") or "").lower())]
    return _distinct_courses(named)

# Superlative / ranking questions: "which CS course has the highest rating", "easiest math
# course", "hardest cs class". metric + direction come from the ranking word; subject is the
# course-prefix token. The metric maps to the TRACE question LIKE term.
_METRIC_LIKE = {"rating": "%overall%", "difficulty": "%challeng%", "hours": "%hours%"}
# (pattern, metric, direction) — direction "desc" = bigger is the answer, "asc" = smaller is.
_RANK_WORDS = [
    (re.compile(r"\b(highest[- ]?rated|highest rating|best[- ]?rated|best|top[- ]?rated|top)\b", re.I), "rating", "desc"),
    (re.compile(r"\b(lowest[- ]?rated|lowest rating|worst[- ]?rated|worst)\b", re.I), "rating", "asc"),
    (re.compile(r"\b(hardest|most difficult|most challenging|toughest)\b", re.I), "difficulty", "desc"),
    (re.compile(r"\b(easiest|least difficult|least challenging)\b", re.I), "difficulty", "asc"),
    (re.compile(r"\b(most work|most hours|heaviest|most workload)\b", re.I), "hours", "desc"),
    (re.compile(r"\b(least work|fewest hours|lightest|least workload)\b", re.I), "hours", "asc"),
]
# subject token sitting right before "course"/"class" (e.g. "CS course", "math class").
_SUBJECT_RE = re.compile(r"\b([A-Za-z]{2,5})\s+(?:course|class|courses|classes)\b", re.I)

def parse_course_superlative(query):
    """Return {subject, metric, direction} for a superlative course question, else None.
    Requires a ranking word AND a subject token before 'course'/'class'."""
    q = str(query or "")
    m = _SUBJECT_RE.search(q)
    if not m:
        return None
    subject = m.group(1).upper()
    for pat, metric, direction in _RANK_WORDS:
        if pat.search(q):
            return {"subject": subject, "metric": metric, "direction": direction}
    return None

def rank_courses_by_metric(subject, metric, direction, query_fn, limit=5, min_responses=30):
    """Rank a subject's courses by a weighted TRACE metric. Filters out courses with fewer than
    min_responses (tiny-sample noise) and returns the top `limit` in the asked direction."""
    like = _METRIC_LIKE.get(metric)
    if not like:
        return []
    rows = query_fn("""
        SELECT tc.course_code AS code, cc.name AS name, cc.department AS department,
          SUM(CASE WHEN lower(ts.question) LIKE %s THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS m_w,
          SUM(CASE WHEN lower(ts.question) LIKE %s THEN CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS m_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        LEFT JOIN course_catalog cc ON cc.code = tc.course_code
        WHERE tc.course_code LIKE %s AND tc.course_code ~ %s
        GROUP BY tc.course_code, cc.name, cc.department
    """, (like, like, f"{subject}%", f"^{subject}[0-9]"))
    ranked = []
    for r in rows:
        resp = float(r.get("m_r") or 0)
        if resp < min_responses:
            continue
        val = round(float(r.get("m_w") or 0) / resp, 2)
        ranked.append({"code": r.get("code"), "name": r.get("name"),
                       "department": r.get("department"), "value": val, "responses": int(resp)})
    ranked.sort(key=lambda c: c["value"], reverse=(direction == "desc"))
    return ranked[:limit]

def _clean_course_label(display_name):
    """trace_courses.display_name looks like 'ENGW3302:09 (Advanced Writing in Tech Prof)
    - Laurie Nardone'. For "courses taught" we want just the code + course name, with no
    section number, term, or instructor — so the same course collapses to one entry."""
    dn = str(display_name or "").strip()
    if not dn:
        return ""
    code = re.split(r"[:\s]", dn, 1)[0].strip().rstrip(":")
    m = re.search(r"\(([^)]*)\)", dn)  # course name lives inside the first ( )
    name = (m.group(1) if m else "").strip()
    return f"{code} {name}".strip() if name else code

def resolve_entity(query, hint, prof_search_fn, limit=1):
    for term in (hint, query):
        if not term:
            continue
        rows = prof_search_fn(term, limit=limit)
        if rows:
            return rows[0]
    return None

def fetch_facts(slug, query_one_fn, query_fn):
    prof = query_one_fn("""
        SELECT slug, name_key, trace_name_key, name, department, rmp_rating, trace_rating,
               avg_rating, difficulty, would_take_again_pct, total_reviews, avg_hours
        FROM professors_catalog WHERE slug = %s
    """, (slug,))
    if not prof:
        return {}
    name_key = prof.get("name_key")
    # Fuzzy-matched professors carry their TRACE rows under a different name than
    # RMP uses. Without this, Ask reports "no courses" and undercounts comments for
    # exactly the professors whose profile page resolves them correctly.
    trace_nk = prof.get("trace_name_key") or name_key
    course_rows = query_fn("""
        SELECT DISTINCT display_name FROM trace_courses
        WHERE name_key = %s AND display_name IS NOT NULL
        ORDER BY display_name LIMIT 25
    """, (trace_nk,))
    seen, courses = set(), []
    for c in course_rows:
        label = _clean_course_label(c.get("display_name"))
        if label and label not in seen:
            seen.add(label); courses.append(label)
    # total written comments = RMP review comments + TRACE comments (same buckets the
    # professor page counts), so Ask reports the same number the profile shows.
    cc = query_one_fn("""
        SELECT COALESCE(SUM(cnt), 0) AS cnt FROM (
          SELECT COUNT(*) AS cnt FROM rmp_reviews
            WHERE name_key = %s AND comment IS NOT NULL AND comment != ''
          UNION ALL
          SELECT COUNT(*) AS cnt FROM trace_comments tc
            JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id
              AND tc.tc_instructor_id = tc2.instructor_id AND tc.tc_term_id = tc2.term_id
            WHERE tc2.name_key = %s AND tc.comment IS NOT NULL AND tc.comment != ''
        ) sub
    """, (name_key, trace_nk))   # RMP under the RMP name, TRACE under the TRACE name
    return {
        "kind": "professor",
        "name": prof.get("name"), "department": prof.get("department"),
        "rmp_rating": prof.get("rmp_rating"), "trace_rating": prof.get("trace_rating"),
        "avg_rating": prof.get("avg_rating"), "difficulty": prof.get("difficulty"),
        "would_take_again_pct": prof.get("would_take_again_pct"),
        "total_reviews": prof.get("total_reviews"),
        "hours_per_week": prof.get("avg_hours"),
        "total_comments": (cc or {}).get("cnt", 0),
        "courses": courses,
    }

def fetch_course_facts(code, query_one_fn, query_fn):
    """Compact course summary for Ask: overall rating, avg difficulty (challenge), avg
    hrs/week, last-taught term, and recent professor names. Reuses the trace_scores
    weighted-aggregation pattern from /api/courses/<code>, aggregated at course grain."""
    norm = _norm_course_code(code)
    cat = query_one_fn(
        "SELECT code, name, department FROM course_catalog WHERE code = %s", (norm,))
    if not cat:
        return {}
    agg = query_one_fn("""
        SELECT
          SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) AS o_w,
          SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN CAST(total_responses AS INT) ELSE 0 END) AS o_r,
          SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) AS c_w,
          SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(total_responses AS INT) ELSE 0 END) AS c_r,
          SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) AS h_w,
          SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(total_responses AS INT) ELSE 0 END) AS h_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        WHERE tc.course_code = %s
    """, (norm,))
    def _ratio(w, r):
        w = float((agg or {}).get(w) or 0); r = float((agg or {}).get(r) or 0)
        return round(w / r, 2) if r > 0 else None
    # recent professors + last-taught term, newest first by term sort key
    rows = query_fn("""
        SELECT DISTINCT instructor_first_name, instructor_last_name, term_title
        FROM trace_courses WHERE course_code = %s
    """, (norm,))
    def _term_key(t):
        # crude recency: a 4-digit year dominates, season breaks ties (Fall>Summer>Spring)
        t = (t or "").lower()
        yr = re.search(r"(\d{4})", t)
        season = 3 if "fall" in t else 2 if "summer" in t else 1 if "spring" in t else 0
        return (int(yr.group(1)) if yr else 0, season)
    last_taught, recent = "", []
    seen_names = set()
    for r in sorted(rows, key=lambda r: _term_key(r.get("term_title")), reverse=True):
        if not last_taught:
            last_taught = r.get("term_title") or ""
        nm = f"{(r.get('instructor_first_name') or '').strip()} {(r.get('instructor_last_name') or '').strip()}".strip()
        if nm and nm not in seen_names:
            seen_names.add(nm); recent.append(nm)
        if len(recent) >= 5:
            break
    # per-instructor breakdown: rating / difficulty / hrs-week for each professor who has
    # taught this course, same weighted-aggregation as the course-grain figures above.
    irows = query_fn("""
        SELECT
          tc.instructor_first_name AS fn, tc.instructor_last_name AS ln,
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS o_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS o_r,
          SUM(CASE WHEN lower(ts.question) LIKE '%%challeng%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS c_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%challeng%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS c_r,
          SUM(CASE WHEN lower(ts.question) LIKE '%%hours%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS h_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%hours%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS h_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        WHERE tc.course_code = %s
        GROUP BY tc.instructor_first_name, tc.instructor_last_name
    """, (norm,))
    def _row_ratio(row, w, r):
        w = float(row.get(w) or 0); r = float(row.get(r) or 0)
        return round(w / r, 2) if r > 0 else None
    breakdown = []
    for r in irows:
        nm = f"{(r.get('fn') or '').strip()} {(r.get('ln') or '').strip()}".strip()
        rating = _row_ratio(r, "o_w", "o_r")
        if not nm or rating is None:  # skip instructors with no overall-rating responses
            continue
        breakdown.append({
            "name": nm, "rating": rating,
            "difficulty": _row_ratio(r, "c_w", "c_r"),
            "hours_per_week": _row_ratio(r, "h_w", "h_r"),
        })
    breakdown.sort(key=lambda b: b["rating"], reverse=True)
    return {
        "kind": "course",
        "code": cat.get("code"), "name": cat.get("name"), "department": cat.get("department"),
        "avg_rating": _ratio("o_w", "o_r"),
        "avg_difficulty": _ratio("c_w", "c_r"),
        "hours_per_week": _ratio("h_w", "h_r"),
        "last_taught": last_taught,
        "recent_professors": recent,
        "instructor_breakdown": breakdown,
    }

def fetch_comments(slug, query_fn, limit=8):
    rows = query_fn("""
        SELECT t.source_id, t.body, t.subreddit, t.permalink, t.created_utc,
               t.score AS reddit_score, s.sentiment, s.score AS sentiment_score
        FROM reddit_mentions m
        JOIN reddit_text t ON t.source_id = m.source_id
        LEFT JOIN reddit_sentiment s
          ON s.source_id = t.source_id AND s.professor_slug = m.professor_slug
        WHERE m.professor_slug = %s AND t.flagged = false
        ORDER BY t.score DESC NULLS LAST
        LIMIT %s
    """, (slug, limit))
    out = []
    for r in rows:
        out.append({"source_id": r["source_id"], "body": r.get("body") or "",
                    "sentiment": r.get("sentiment"), "sentiment_score": r.get("sentiment_score"),
                    "score": r.get("reddit_score"),
                    "subreddit": r.get("subreddit"), "permalink": r.get("permalink"),
                    "created_utc": r.get("created_utc")})
    return out

def fetch_course_comments(code, query_fn, limit=8):
    """Reddit discussion for a course. Reddit text isn't linked to courses in the DB, so
    match the course code via full-text search. Require the code as a contiguous PHRASE
    (phraseto_tsquery uses FOLLOWED BY), not an AND of scattered tokens — otherwise an
    off-topic comment that merely contains 'cs' and '3100' somewhere matches as a bogus
    source. Reddit writes the code either way, so match the unspaced AND spaced spelling."""
    unspaced = re.sub(r"\s+", "", str(code).strip())
    spaced = re.sub(r"^([A-Za-z]{2,5})\s?(\d{4})$", r"\1 \2", unspaced)
    rows = query_fn("""
        SELECT t.source_id, t.body, t.subreddit, t.permalink, t.created_utc,
               t.score AS reddit_score,
               GREATEST(ts_rank(t.body_tsv, phraseto_tsquery('english', %s)),
                        ts_rank(t.body_tsv, phraseto_tsquery('english', %s))) AS rank
        FROM reddit_text t
        WHERE t.flagged = false
          AND (t.body_tsv @@ phraseto_tsquery('english', %s)
               OR t.body_tsv @@ phraseto_tsquery('english', %s))
        ORDER BY rank DESC
        LIMIT %s
    """, (unspaced, spaced, unspaced, spaced, limit))
    out = []
    for r in rows:
        out.append({"source_id": r["source_id"], "body": r.get("body") or "",
                    "sentiment": None, "sentiment_score": None,
                    "score": r.get("reddit_score"),
                    "subreddit": r.get("subreddit"), "permalink": r.get("permalink"),
                    "created_utc": r.get("created_utc")})
    return out

def _rrf_fuse(lexical, vector, k=60):
    # lexical/vector: lists of (id, rank_value) already in best-first order
    score = {}
    for rank, (i, _) in enumerate(lexical, 1):
        score[i] = score.get(i, 0) + 1.0 / (k + rank)
    for rank, (i, _) in enumerate(vector, 1):
        score[i] = score.get(i, 0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda kv: kv[1], reverse=True)

def _apply_source_floor(ranked_rows, limit=8, reddit_floor=2, rmp_floor=2):
    # ranked_rows already in fused-rank order. Reserve up to floor per source (when present),
    # then fill remaining by overall rank. Absent sources' reserves roll into the fill.
    picked, used = [], set()
    def take_floor(src, n):
        c = 0
        for r in ranked_rows:
            if c >= n: break
            if id(r) in used or r["source"] != src: continue
            picked.append(r); used.add(id(r)); c += 1
    take_floor("reddit", reddit_floor)
    take_floor("rmp", rmp_floor)
    for r in ranked_rows:
        if len(picked) >= limit: break
        if id(r) in used: continue
        picked.append(r); used.add(id(r))
    # restore overall fused order among the picked set
    rank_of = {id(r): n for n, r in enumerate(ranked_rows)}
    picked.sort(key=lambda r: rank_of[id(r)])
    return picked[:limit]

def _entity_filter(slug, code):
    """Build the evidence entity-filter clause + its params, split by which of slug/code is set
    (never both — callers pass one). Slug-only and code-only are separate predicates (no
    OR-with-a-NULL-param) so the planner can drive a lookup join off ev_prof/ev_course instead of
    post-filtering the global vector index or brute-forcing. The code-only variant additionally
    admits Reddit rows via body ILIKE, since Reddit evidence is stored with course_code='' and so
    never matches the equality leg (course questions would otherwise see zero Reddit evidence)."""
    if slug is not None:
        return "e.professor_slug = %s", (slug,)
    norm = re.sub(r"\s+", "", str(code or "").upper())
    m = re.match(r"^([A-Za-z]+)(\d+)$", norm)
    spaced = f"{m.group(1)} {m.group(2)}" if m else norm
    return ("(e.course_code = %s OR (e.source = 'reddit' AND (e.body ILIKE %s OR e.body ILIKE %s)))",
            (code, f"%{norm}%", f"%{spaced}%"))

def _lexical_candidates(where, entity_params, query, query_fn, limit=40):
    """Top lexical candidates for an entity-scoped evidence search, best-first [(id, ts_rank)].
    Shared by fetch_evidence and backend/rag/eval/pool_candidates.py — the eval pool must run the
    EXACT production SQL, so tuning here automatically flows into the eval harness."""
    if not (query and query.strip()):
        return []
    rows = query_fn(
        "SELECT e.id, ts_rank(e.body_tsv, plainto_tsquery('english', %s)) AS r "
        "FROM evidence e WHERE " + where +
        " AND e.flagged = false AND e.body_tsv @@ plainto_tsquery('english', %s) "
        "ORDER BY r DESC LIMIT " + str(int(limit)),
        (query,) + entity_params + (query,))
    return [(r["id"], r.get("r", 0)) for r in rows]

def _vector_candidates(where, entity_params, qv, query_fn, limit=40):
    """Top vector candidates by cosine, best-first [(id, similarity)]. qv is the precomputed
    query embedding; None (embed failed/skipped) -> no vector leg."""
    if qv is None:
        return []
    rows = query_fn(
        "SELECT e.id, 1 - (ee.embedding <=> %s::vector) AS sim "
        "FROM evidence_embeddings ee JOIN evidence e ON e.id = ee.evidence_id "
        "WHERE " + where + " AND e.flagged = false"
        " ORDER BY ee.embedding <=> %s::vector LIMIT " + str(int(limit)),
        (str(qv),) + entity_params + (str(qv),))
    return [(r["id"], r.get("sim", 0)) for r in rows]

def fetch_evidence(slug, code, query, embed_query_fn, query_fn, limit=8):
    where, entity_params = _entity_filter(slug, code)
    # 1. lexical
    lexical = _lexical_candidates(where, entity_params, query, query_fn)
    # 2. vector (skip if embed fails → lexical-only)
    qv = embed_query_fn(query) if (embed_query_fn and query) else None
    vector = _vector_candidates(where, entity_params, qv, query_fn)
    # 3. fuse (if only lexical, RRF over one list = lexical order)
    fused = _rrf_fuse(lexical, vector)
    if not fused:
        return []
    ids = [i for i, _ in fused]
    # 4. hydrate, preserve fused order, apply floor
    rows = query_fn(
        "SELECT id, source, body, sentiment, reddit_score, permalink, created_utc, subreddit "
        "FROM evidence WHERE id IN %s", (tuple(ids),))
    by_id = {r["id"]: r for r in rows}
    ranked = [by_id[i] for i in ids if i in by_id]
    picked = _apply_source_floor(ranked, limit=limit)
    out = []
    for r in picked:
        out.append({"source_id": r["id"], "body": r.get("body") or "",
                    "sentiment": r.get("sentiment"), "sentiment_score": None,
                    "score": r.get("reddit_score"), "subreddit": r.get("subreddit"),
                    "permalink": r.get("permalink"), "created_utc": r.get("created_utc"),
                    "source": r.get("source")})
    return out

def fetch_reddit_mentions(slug, query_fn):
    rows = query_fn("""
        SELECT t.body, t.subreddit, t.permalink, t.created_utc,
               t.score AS reddit_score, s.sentiment, s.score AS sentiment_score
        FROM reddit_mentions m
        JOIN reddit_text t ON t.source_id = m.source_id
        LEFT JOIN reddit_sentiment s
          ON s.source_id = t.source_id AND s.professor_slug = m.professor_slug
        WHERE m.professor_slug = %s AND t.flagged = false
        ORDER BY t.created_utc DESC NULLS LAST
    """, (slug,))
    out = []
    for r in rows:
        out.append({
            "body": r.get("body") or "",
            "sentiment": r.get("sentiment"),
            "sentiment_score": r.get("sentiment_score"),
            "score": r.get("reddit_score"),
            "subreddit": r.get("subreddit"),
            "permalink": r.get("permalink"),
            "created_utc": r.get("created_utc"),
        })
    return out

def retrieve(query, hint, query_fn, query_one_fn, prof_search_fn, limit=8, embed_query_fn=None):
    # Superlative / ranking question ("which CS course has the highest rating"). Keys on the
    # query text, so it wins even when the gate hands back a junk hint like "CS course" — but a
    # genuine professor hint must win (don't turn "which CS course did Guha call hardest" into a
    # global ranking that drops Guha). A junk subject hint won't resolve to a professor.
    sup = parse_course_superlative(query)
    if sup and hint and prof_search_fn(hint, limit=1):
        sup = None
    if sup:
        ranked = rank_courses_by_metric(sup["subject"], sup["metric"], sup["direction"], query_fn)
        if ranked:
            return {"kind": "course_ranking", "subject": sup["subject"], "metric": sup["metric"],
                    "direction": sup["direction"], "courses": ranked, "course_count": len(ranked),
                    "entity_key": f"rank:{sup['subject']}:{sup['metric']}", "course_code": None,
                    "professor_slug": None, "facts": {}, "comments": [], "comment_count": 0}
        # no qualifying courses (unknown subject / none clear the threshold) → fall through

    # Only treat as a topic-listing when the gate found NO specific entity hint — a named
    # professor/course must win ("what database courses does Guha teach" → answer about Guha).
    topic = is_course_topic_query(query) if not hint else None
    if topic:
        with_ratings = wants_ratings(query)
        courses = fetch_courses_by_topic(topic, query_fn, limit=limit, with_ratings=with_ratings)
        if courses:
            return {"kind": "course_list", "topic": topic, "courses": courses,
                    "course_count": len(courses), "with_ratings": with_ratings,
                    "entity_key": f"topic:{topic}", "course_code": None,
                    "professor_slug": None, "facts": {}, "comments": [], "comment_count": 0}
        # topic phrasing but no catalog match → fall through to normal resolution

    # Course path: a course-code hint (e.g. "DS3000") resolves to course facts + that
    # course's Reddit discussion, instead of trying to find a professor by that name.
    course_term = next((t for t in (hint, query) if is_course_code(t)), None)
    if course_term:
        cfacts = fetch_course_facts(course_term, query_one_fn, query_fn)
        if cfacts:
            code = cfacts["code"]
            comments = fetch_evidence(None, code, query, embed_query_fn, query_fn, limit=limit)
            return {"professor_slug": None, "course_code": code, "entity_key": code,
                    "entity_name": cfacts.get("name"), "facts": cfacts,
                    "comments": comments, "comment_count": len(comments)}
        # unknown course code → fall through to professor resolution

    # Course-by-NAME path: a hint that is a course TITLE ("Discrete Structures") rather than a
    # code. One clear match → answer that course; several distinct → disambiguate. A genuine
    # professor hint must win first (same guard pattern as the superlative branch above) — e.g.
    # "is professor Lee good" must not fall into "lee" substring-matching "Sleep and Cognition".
    if hint and not is_course_code(hint) and not prof_search_fn(hint, limit=1):
        named = resolve_course_by_name(hint, query_fn, limit=6)
        if len(named) == 1:
            cfacts = fetch_course_facts(named[0]["code"], query_one_fn, query_fn)
            if cfacts:
                code = cfacts["code"]
                comments = fetch_evidence(None, code, query, embed_query_fn, query_fn, limit=limit)
                return {"professor_slug": None, "course_code": code, "entity_key": code,
                        "entity_name": cfacts.get("name"), "facts": cfacts,
                        "comments": comments, "comment_count": len(comments)}
        elif len(named) > 1:
            return {"kind": "course_disambiguation", "matches": named,
                    "entity_key": None, "course_code": None, "professor_slug": None,
                    "facts": {}, "comments": [], "comment_count": 0}
        # 0 name matches (or facts missing) → fall through to professor resolution

    ent = resolve_entity(query, hint, prof_search_fn, limit=1)
    if not ent:
        return {"professor_slug": None, "course_code": None, "entity_key": None,
                "entity_name": None, "facts": {}, "comments": [], "comment_count": 0}
    slug = ent["slug"]
    comments = fetch_evidence(slug, None, query, embed_query_fn, query_fn, limit=limit)
    return {"professor_slug": slug, "course_code": None, "entity_key": slug,
            "professor_name": ent.get("name"), "entity_name": ent.get("name"),
            "facts": fetch_facts(slug, query_one_fn, query_fn),
            "comments": comments, "comment_count": len(comments)}

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    def prof_search_fn(q, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha", "name_key": "olin guha",
                 "department": "Khoury", "avg_rating": 4.2, "total_reviews": 31}]

    def query_one_fn(sql, params):
        check("facts query parameterized", "%s" in sql)
        if "professors_catalog" in sql:
            check("facts query selects avg_hours", "avg_hours" in sql)
            return {"slug": "guha-prof", "name_key": "olin guha", "name": "Olin Guha",
                    "department": "Khoury", "rmp_rating": 4.1, "trace_rating": 4.3,
                    "avg_rating": 4.2, "difficulty": 3.5, "would_take_again_pct": 88.0,
                    "total_reviews": 31, "avg_hours": 7.5}
        if "rmp_reviews" in sql or "trace_comments" in sql:  # comment-count UNION
            return {"cnt": 42}
        return None

    def query_fn(sql, params):
        if "DISTINCT display_name" in sql:
            check("course-list query keyed on name_key (not subquery)", "name_key = %s" in sql)
            # real display_name carries section + instructor; two sections of the SAME
            # course must collapse to one clean "CODE Name" entry (no section/term/instructor)
            return [{"display_name": "CS3500:01 (Object-Oriented Design) - Olin Guha"},
                    {"display_name": "CS3500:02 (Object-Oriented Design) - Olin Guha"}]
        if "reddit_mentions" in sql:
            # fetch_reddit_mentions still uses this path (professor profile page)
            check("comments exclude flagged rows", "flagged = false" in sql or "NOT flagged" in sql)
            check("comments never select author/username", "author" not in sql.lower())
            return [{"source_id": "c1", "body": "hard but fair", "sentiment": "positive",
                     "sentiment_score": 0.8, "reddit_score": 12, "subreddit": "NEU",
                     "permalink": "/r/x", "created_utc": None}]
        if "plainto_tsquery" in sql:  # fetch_evidence lexical
            check("evidence lexical uses entity filter", "flagged = false" in sql)
            return [{"id": "c1", "r": 0.9}]
        if "FROM evidence" in sql and "WHERE id IN" in sql:  # fetch_evidence hydrate
            return [{"id": "c1", "source": "reddit", "body": "hard but fair",
                     "sentiment": "positive", "reddit_score": 12, "subreddit": "NEU",
                     "permalink": "/r/x", "created_utc": None}]
        return []

    r = retrieve("is guha hard", "Guha", query_fn, query_one_fn, prof_search_fn, limit=8)
    check("resolved a professor", r["professor_slug"] == "guha-prof")
    check("entity_key is the slug for a professor", r["entity_key"] == "guha-prof")
    check("facts kind is professor", r["facts"]["kind"] == "professor")
    check("courses taught strip section/term/instructor + dedupe",
          r["facts"]["courses"] == ["CS3500 Object-Oriented Design"])
    # the raw section/instructor must NOT leak into the courses-taught list
    check("courses taught omit section number", ":01" not in r["facts"]["courses"][0])
    check("courses taught omit instructor name", "Guha" not in r["facts"]["courses"][0])
    check("facts carry difficulty", r["facts"]["difficulty"] == 3.5)
    check("facts carry hours_per_week", r["facts"]["hours_per_week"] == 7.5)
    check("facts carry total_comments", r["facts"]["total_comments"] == 42)
    check("comments retrieved", r["comment_count"] == 1 and r["comments"][0]["sentiment"] == "positive")
    check("comment score is the reddit upvote score", r["comments"][0]["score"] == 12)
    # fetch_evidence normalizes sentiment_score to None (numeric score lives in the evidence table)
    check("comment keeps numeric sentiment separately", r["comments"][0]["sentiment_score"] is None)

    none = retrieve("is guha hard", None, query_fn, lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("no entity resolves to empty result", none["professor_slug"] is None and none["comment_count"] == 0)

    # ── is_course_code ──
    check("is_course_code matches DS3000", is_course_code("DS3000") is True)
    check("is_course_code matches spaced 'CS 3000'", is_course_code("CS 3000") is True)
    check("is_course_code rejects a name", is_course_code("Olin Guha") is False)
    check("is_course_code rejects None", is_course_code(None) is False)

    # ── course path ──
    def course_query_one(sql, params):
        if "course_catalog" in sql:
            return {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}
        if "trace_scores" in sql:  # weighted aggregation
            check("course agg joins trace_courses on course_code", "course_code = %s" in sql)
            return {"o_w": 8.0, "o_r": 2, "c_w": 6.0, "c_r": 2, "h_w": 14.0, "h_r": 2}
        return None
    def course_query(sql, params):
        if "trace_scores" in sql and "GROUP BY" in sql:  # per-instructor breakdown
            check("breakdown groups per instructor", "GROUP BY tc.instructor_first_name" in sql)
            return [
                {"fn": "Jan", "ln": "Vitek", "o_w": 8.0, "o_r": 2, "c_w": 6.0, "c_r": 2, "h_w": 14.0, "h_r": 2},
                {"fn": "Nick", "ln": "Brown", "o_w": 3.0, "o_r": 1, "c_w": 5.0, "c_r": 1, "h_w": 9.0, "h_r": 1},
                # an instructor with no overall-rating responses must be dropped, not shown as unknown
                {"fn": "Ghost", "ln": "Prof", "o_w": 0.0, "o_r": 0, "c_w": 4.0, "c_r": 1, "h_w": 4.0, "h_r": 1},
            ]
        if "instructor_first_name" in sql and "course_code = %s" in sql:
            return [
                {"instructor_first_name": "Jan", "instructor_last_name": "Vitek", "term_title": "Fall 2024"},
                {"instructor_first_name": "Nick", "instructor_last_name": "Brown", "term_title": "Spring 2023"},
            ]
        if "plainto_tsquery" in sql:  # fetch_evidence lexical (replaces reddit_text for retrieve())
            check("evidence avoids unsupported websearch_to_tsquery", "websearch_to_tsquery" not in sql)
            return [{"id": "x1", "r": 0.8}]
        if "FROM evidence" in sql and "WHERE id IN" in sql:  # fetch_evidence hydrate
            return [{"id": "x1", "source": "reddit", "body": "DS3000 is a lot of work",
                     "reddit_score": 5, "subreddit": "NEU", "permalink": "/r/z",
                     "created_utc": None, "sentiment": None}]
        return []
    rc = retrieve("tell me about DS3000", "DS3000", course_query, course_query_one, prof_search_fn, limit=8)
    check("course path sets course_code", rc["course_code"] == "DS3000")
    check("course path entity_key is the code", rc["entity_key"] == "DS3000")
    check("course facts kind is course", rc["facts"]["kind"] == "course")
    check("course avg_rating computed (8/2)", rc["facts"]["avg_rating"] == 4.0)
    check("course avg_difficulty computed (6/2)", rc["facts"]["avg_difficulty"] == 3.0)
    check("course hours_per_week computed (14/2)", rc["facts"]["hours_per_week"] == 7.0)
    check("course last_taught is newest term", rc["facts"]["last_taught"] == "Fall 2024")
    check("course recent_professors newest-first", rc["facts"]["recent_professors"][0] == "Jan Vitek")
    check("course Reddit comments fetched", rc["comment_count"] == 1)

    # ── per-instructor breakdown (rating / difficulty / hrs-week) ──
    bd = rc["facts"]["instructor_breakdown"]
    check("breakdown drops instructors with no rating", len(bd) == 2)
    check("breakdown sorted by rating desc", bd[0]["name"] == "Jan Vitek" and bd[0]["rating"] == 4.0)
    check("breakdown carries difficulty (6/2)", bd[0]["difficulty"] == 3.0)
    check("breakdown carries hours/week (14/2)", bd[0]["hours_per_week"] == 7.0)
    check("breakdown second instructor", bd[1]["name"] == "Nick Brown" and bd[1]["rating"] == 3.0)

    # ── fetch_course_comments: exact-phrase matching for BOTH spellings ──
    # A comment is a valid source only if it contains the code as a contiguous phrase.
    # Reddit writes it either way ("CS3100" / "CS 3100"), so both phrasings must be queried.
    cap = {}
    def phrase_query(sql, params):
        cap["sql"] = sql; cap["params"] = list(params)
        return [{"source_id": "x1", "body": "CS3100 is rough", "reddit_score": 3,
                 "subreddit": "NEU", "permalink": "/r/p", "created_utc": None}]
    cc = fetch_course_comments("CS3100", phrase_query, limit=8)
    check("course comments fetched", len(cc) == 1)
    check("fetch_course_comments uses phraseto_tsquery only",
          "phraseto_tsquery" in cap["sql"] and "plainto_tsquery" not in cap["sql"])
    check("fetch_course_comments queries unspaced spelling 'CS3100'", "CS3100" in cap["params"])
    check("fetch_course_comments queries spaced spelling 'CS 3100'", "CS 3100" in cap["params"])

    # unknown course code falls through to professor resolution (still returns a prof here)
    def unknown_course_one(sql, params):
        if "course_catalog" in sql:
            return None
        return query_one_fn(sql, params)
    ru = retrieve("about ZZ9999", "ZZ9999", query_fn, unknown_course_one, prof_search_fn, limit=8)
    check("unknown course code falls through to professor", ru["professor_slug"] == "guha-prof")

    # ── fetch_reddit_mentions (professor-page path) ──
    captured = {}
    def capture_query_fn(sql, params):
        captured["sql"] = sql
        return query_fn(sql, params)
    rm = fetch_reddit_mentions("guha-prof", capture_query_fn)
    check("reddit mentions query parameterized", "%s" in captured["sql"])
    check("reddit mentions exclude flagged", "flagged = false" in captured["sql"])
    check("reddit mentions never select author", "author" not in captured["sql"].lower())
    check("reddit mentions ordered newest-first", "created_utc DESC" in captured["sql"])
    check("reddit mentions shape carries body", rm and rm[0]["body"] == "hard but fair")
    check("reddit mentions carry sentiment", rm[0]["sentiment"] == "positive")
    check("reddit mentions carry upvote score", rm[0]["score"] == 12)
    check("reddit mentions carry sentiment_score", rm[0]["sentiment_score"] == 0.8)
    check("reddit mentions omit source_id", "source_id" not in rm[0])

    def hint_only_search(term, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha"}] if term == "Guha" else []
    rh = retrieve("is the hard one good", "Guha", query_fn, query_one_fn, hint_only_search, limit=8)
    check("resolve_entity prefers the hint", rh["professor_slug"] == "guha-prof")

    def query_only_search(term, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha"}] if term == "olin guha review" else []
    rq = retrieve("olin guha review", None, query_fn, query_one_fn, query_only_search, limit=8)
    check("resolve_entity falls back to the raw query", rq["professor_slug"] == "guha-prof")

    # ── topic-course listing ──
    check("topic query: 'what database courses are there' -> 'database'",
          is_course_topic_query("what database courses are there") == "database")
    check("topic query: 'which CS classes are there' -> 'cs'",
          is_course_topic_query("which cs classes are there") == "cs")
    check("topic query: 'courses about machine learning' -> 'machine learning'",
          is_course_topic_query("courses about machine learning") == "machine learning")
    check("topic query: strips leading article",
          is_course_topic_query("what are the database courses") == "database")
    check("topic query: strips leading rating adjective 'best'",
          is_course_topic_query("what are the best database courses") == "database")
    check("topic query: strips leading 'top' adjective",
          is_course_topic_query("which top cs classes are there") == "cs")
    check("topic query: keeps subject when rating word is internal-only",
          is_course_topic_query("what machine learning courses are there") == "machine learning")
    check("topic query: rejects a specific course code question",
          is_course_topic_query("what is CS3500") is None)
    check("topic query: rejects a professor question", is_course_topic_query("is guha hard") is None)
    check("topic query: rejects bare name", is_course_topic_query("Olin Guha") is None)

    check("wants_ratings true on 'which is best'",
          wants_ratings("what database courses are there and which is best") is True)
    check("wants_ratings true on 'highest rated'", wants_ratings("highest rated cs courses") is True)
    check("wants_ratings false on plain listing", wants_ratings("what database courses are there") is False)

    # fetch_courses_by_topic: default = one catalog query, no per-course query
    topic_calls = []
    def topic_query(sql, params):
        topic_calls.append(sql)
        if "course_catalog" in sql:
            check("topic search uses search_text LIKE", "search_text LIKE %s" in sql)
            check("topic search orders code-prefix first", "lower(code) LIKE %s" in sql)
            return [{"code": "CS3200", "name": "Database Design", "department": "Khoury"},
                    {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}]
        raise AssertionError("default topic search must not issue per-course queries")
    courses = fetch_courses_by_topic("database", topic_query, limit=8)
    check("topic search returns matched courses", [c["code"] for c in courses] == ["CS3200", "DS3000"])
    check("default topic search issues exactly one query", len(topic_calls) == 1)
    check("default courses carry no rating", "rating" not in courses[0])

    # with_ratings=True: attaches a rating per course and re-sorts desc
    def topic_query_rated(sql, params):
        if "course_catalog" in sql:
            return [{"code": "CS3200", "name": "Database Design", "department": "Khoury"},
                    {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}]
        if "trace_scores" in sql:  # per-course overall rating
            code = params[0]
            return [{"o_w": 8.0, "o_r": 2}] if code == "CS3200" else [{"o_w": 9.0, "o_r": 2}]
        return []
    rated = fetch_courses_by_topic("database", topic_query_rated, limit=8, with_ratings=True)
    check("rated courses carry a rating", all("rating" in c for c in rated))
    check("rated courses sorted by rating desc", [c["code"] for c in rated] == ["DS3000", "CS3200"])
    check("rated values computed (8/2, 9/2)", rated[0]["rating"] == 4.5 and rated[1]["rating"] == 4.0)

    # retrieve() returns a course_list block on a topic query (hint is None — gate found no entity)
    def topic_retrieve_query(sql, params):
        if "course_catalog" in sql and "search_text LIKE" in sql:
            return [{"code": "CS3200", "name": "Database Design", "department": "Khoury"}]
        return []
    rb = retrieve("what database courses are there", None, topic_retrieve_query,
                  lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("retrieve returns course_list kind", rb["kind"] == "course_list")
    check("retrieve course_list topic", rb["topic"] == "database")
    check("retrieve course_list carries courses", rb["courses"][0]["code"] == "CS3200")
    check("retrieve course_list entity_key tagged", rb["entity_key"] == "topic:database")
    check("retrieve course_list with_ratings false by default", rb["with_ratings"] is False)

    # topic regex fires but 0 courses match -> fall through (NOT a course_list block)
    rb0 = retrieve("what zzzz courses are there", None,
                   lambda sql, params: [], lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("zero-match topic falls through (no course_list)", rb0.get("kind") != "course_list")

    # named entity wins over topic phrasing: retrieve with a hint must NOT return a course_list
    rb_hint = retrieve("what database courses does Guha teach", "Guha",
                       lambda sql, params: [{"code": "CS3200", "name": "Database Design", "department": "Khoury"}]
                                            if "course_catalog" in sql else [],
                       lambda s, p=None: None,
                       lambda q, limit=1: [{"slug": "guha-prof", "name": "Olin Guha"}], limit=8)
    check("named entity hint suppresses topic branch", rb_hint.get("kind") != "course_list")
    check("named entity hint resolves the professor", rb_hint.get("professor_slug") == "guha-prof")

    # degenerate topic guard: a 1-char or wildcard topic returns no courses and issues no query
    def must_not_query(sql, params):
        raise AssertionError("degenerate topic must not hit the DB")
    check("single-char topic returns empty", fetch_courses_by_topic("x", must_not_query) == [])
    check("wildcard-only topic returns empty", fetch_courses_by_topic("%", must_not_query) == [])
    # a normal topic still works and wildcards inside are neutralized
    def topic_q(sql, params):
        check("neutralized topic has no % or _ wildcard", "%_" not in params[0] and params[0] == "%data science%")
        return [{"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}]
    got = fetch_courses_by_topic("data_science", topic_q)
    check("underscore in topic neutralized to space", got[0]["code"] == "DS3000")

    # search_text is stored lowercase + LIKE is case-sensitive: a capitalized hint MUST be
    # lowercased before the query, else "Discrete Structures" matches nothing.
    cap = {}
    def case_q(sql, params):
        cap["like"] = params[0]
        return [{"code": "CS1800", "name": "Discrete Structures", "department": "Computer Science"}]
    fetch_courses_by_topic("Discrete Structures", case_q)
    check("topic search lowercases the LIKE term", cap["like"] == "%discrete structures%")

    # ── course-by-NAME resolution ──
    # single clear match by title -> resolves to that course (facts + comments), like the code path
    def name_one_query(sql, params):
        if "course_catalog" in sql:
            return [{"code": "CS1800", "name": "Discrete Structures", "department": "Khoury"}]
        if "trace_scores" in sql:
            return [{"o_w": 8.0, "o_r": 2, "c_w": 6.0, "c_r": 2, "h_w": 14.0, "h_r": 2}]
        if "instructor_first_name" in sql and "course_code = %s" in sql:
            return [{"instructor_first_name": "A", "instructor_last_name": "B", "term_title": "Fall 2024"}]
        if "plainto_tsquery" in sql:  # fetch_evidence lexical
            return [{"id": "x", "r": 0.7}]
        if "FROM evidence" in sql and "WHERE id IN" in sql:  # fetch_evidence hydrate
            return [{"id": "x", "source": "reddit", "body": "tough class", "reddit_score": 3,
                     "subreddit": "NEU", "permalink": "/r/x", "created_utc": None, "sentiment": None}]
        return []
    def name_one_query_one(sql, params):
        if "course_catalog" in sql:
            return {"code": "CS1800", "name": "Discrete Structures", "department": "Khoury"}
        if "trace_scores" in sql:
            return {"o_w": 8.0, "o_r": 2, "c_w": 6.0, "c_r": 2, "h_w": 14.0, "h_r": 2}
        return None
    rn = retrieve("How tough is Discrete Structures?", "Discrete Structures",
                  name_one_query, name_one_query_one, lambda q, limit=1: [], limit=8)
    check("course-by-name resolves to the course code", rn.get("course_code") == "CS1800")
    check("course-by-name facts kind is course", rn.get("facts", {}).get("kind") == "course")
    check("course-by-name pulls course comments", rn.get("comment_count") == 1)

    # several distinct matches by title -> course_disambiguation block (no resolution)
    def name_multi_query(sql, params):
        if "course_catalog" in sql:
            return [{"code": "DS2000", "name": "Intro to Data Science", "department": "Khoury"},
                    {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}]
        return []
    rd = retrieve("is data science hard", "Data Science",
                  name_multi_query, lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("course-by-name multiple -> course_disambiguation", rd.get("kind") == "course_disambiguation")
    check("course-by-name disambiguation lists both", {m["code"] for m in rd["matches"]} == {"DS2000", "DS3000"})

    # name filter: a row that matched only via code noise (name lacks the hint) is NOT a name hit
    def name_codenoise_query(sql, params):
        if "course_catalog" in sql:
            # search_text matched on the code, but the NAME doesn't contain "operating"
            return [{"code": "CS3650", "name": "Computer Systems", "department": "Khoury"}]
        return []
    nc = resolve_course_by_name("operating", name_codenoise_query)
    check("course-by-name drops code-only noise (name lacks hint)", nc == [])

    # ── extracted candidate fetchers (shared with backend/rag/eval pooling) ──
    cand_calls = []
    def cand_query(sql, params):
        cand_calls.append((sql, params))
        if "plainto_tsquery" in sql:
            return [{"id": "a", "r": 0.9}, {"id": "b", "r": 0.5}]
        if "evidence_embeddings" in sql:
            return [{"id": "b", "sim": 0.8}]
        return []
    w_c, p_c = _entity_filter("guha-prof", None)
    lex_c = _lexical_candidates(w_c, p_c, "is guha hard", cand_query)
    check("lexical candidates are (id, score) best-first", lex_c == [("a", 0.9), ("b", 0.5)])
    check("lexical candidates default depth 40", "LIMIT 40" in cand_calls[0][0])
    check("lexical candidates keep the entity filter", w_c in cand_calls[0][0])
    vec_c = _vector_candidates(w_c, p_c, [0.1, 0.2], cand_query)
    check("vector candidates are (id, sim) best-first", vec_c == [("b", 0.8)])
    check("blank query -> no lexical candidates", _lexical_candidates(w_c, p_c, "  ", cand_query) == [])
    check("None embedding -> no vector candidates", _vector_candidates(w_c, p_c, None, cand_query) == [])
    _lexical_candidates(w_c, p_c, "q", cand_query, limit=20)
    check("candidate depth is parameterized", "LIMIT 20" in cand_calls[-1][0])

    # professor-name hint that matches NO catalog name -> falls through to professor resolution
    def prof_fallthrough_query(sql, params):
        return []  # no catalog rows at all
    def prof_fallthrough_one(sql, params):
        if "professors_catalog" in sql:
            return {"slug": "guha-prof", "name_key": "olin guha", "name": "Olin Guha",
                    "department": "Khoury", "rmp_rating": 4.1, "trace_rating": 4.3,
                    "avg_rating": 4.2, "difficulty": 3.5, "would_take_again_pct": 88.0,
                    "total_reviews": 31, "avg_hours": 7.5}
        if "rmp_reviews" in sql or "trace_comments" in sql:
            return {"cnt": 5}
        return None
    rp = retrieve("is guha hard", "Guha", prof_fallthrough_query, prof_fallthrough_one,
                  lambda q, limit=1: [{"slug": "guha-prof", "name": "Olin Guha", "name_key": "olin guha"}], limit=8)
    check("non-course name hint falls through to professor", rp.get("professor_slug") == "guha-prof")
    check("professor fallthrough not a disambiguation", rp.get("kind") != "course_disambiguation")

    # ── Issue 10: a unique professor surname must win over a course-by-name substring hit ──
    # "is professor Lee good" — a course named "Sleep and Cognition" would otherwise substring-
    # match "lee" inside "sleep"; professor resolution must run first and win.
    def lee_query(sql, params):
        if "course_catalog" in sql:
            return [{"code": "PSYC2500", "name": "Sleep and Cognition", "department": "Psychology"}]
        return []
    def lee_query_one(sql, params):
        if "professors_catalog" in sql:
            return {"slug": "lee-prof", "name_key": "j lee", "name": "J. Lee", "department": "Khoury",
                    "rmp_rating": 4.0, "trace_rating": 4.0, "avg_rating": 4.0, "difficulty": 3.0,
                    "would_take_again_pct": 90.0, "total_reviews": 10, "avg_hours": 5.0}
        if "rmp_reviews" in sql or "trace_comments" in sql:
            return {"cnt": 3}
        return None
    def lee_prof_search(term, limit=1):
        return [{"slug": "lee-prof", "name": "J. Lee", "name_key": "j lee"}]
    r_lee = retrieve("is professor Lee good", "Lee", lee_query, lee_query_one, lee_prof_search, limit=8)
    check("unique professor surname wins over course-by-name substring hit",
          r_lee.get("professor_slug") == "lee-prof")
    check("professor-wins path is not a course disambiguation", r_lee.get("kind") != "course_disambiguation")

    # resolve_course_by_name itself: "lee" must NOT match "Sleep and Cognition" (word-anchored,
    # not a bare substring) — the professor guard above is belt-and-suspenders with this fix.
    def sleep_query(sql, params):
        if "course_catalog" in sql:
            return [{"code": "PSYC2500", "name": "Sleep and Cognition", "department": "Psychology"}]
        return []
    check("resolve_course_by_name anchors to word boundaries (no 'lee' inside 'sleep')",
          resolve_course_by_name("lee", sleep_query) == [])
    # a genuine word-anchored hit still matches
    def law_query(sql, params):
        if "course_catalog" in sql:
            return [{"code": "LAW1000", "name": "Law and Society", "department": "Law"}]
        return []
    check("resolve_course_by_name still matches a real word-anchored hint",
          [m["code"] for m in resolve_course_by_name("law", law_query)] == ["LAW1000"])

    # ── superlative / ranking detection ──
    s1 = parse_course_superlative("Which CS course has the highest rating?")
    check("superlative: highest rating -> CS/rating/desc",
          s1 == {"subject": "CS", "metric": "rating", "direction": "desc"})
    s2 = parse_course_superlative("what's the easiest math course")
    check("superlative: easiest -> MATH/difficulty/asc",
          s2 == {"subject": "MATH", "metric": "difficulty", "direction": "asc"})
    s3 = parse_course_superlative("hardest cs class")
    check("superlative: hardest -> CS/difficulty/desc",
          s3 == {"subject": "CS", "metric": "difficulty", "direction": "desc"})
    s4 = parse_course_superlative("which DS course has the most work")
    check("superlative: most work -> DS/hours/desc",
          s4 == {"subject": "DS", "metric": "hours", "direction": "desc"})
    # misses: a code-vs-code compare, a professor question, a plain topic listing
    check("superlative: rejects code compare", parse_course_superlative("is CS 3500 harder than CS 3000") is None)
    check("superlative: rejects professor q", parse_course_superlative("is Guha hard") is None)
    check("superlative: rejects topic listing", parse_course_superlative("what database courses are there") is None)

    # rank_courses_by_metric: SQL shape + threshold filter + direction
    rank_calls = {}
    def rank_query(sql, params):
        rank_calls["sql"] = sql; rank_calls["params"] = list(params)
        return [
            {"code": "CS7870", "name": "Seminar", "department": "CS", "m_w": 10.0, "m_r": 2.0},   # below threshold
            {"code": "CS3100", "name": "PDI 2", "department": "CS", "m_w": 445.0, "m_r": 100.0},  # 4.45
            {"code": "CS2000", "name": "Intro", "department": "CS", "m_w": 880.0, "m_r": 200.0},  # 4.40
        ]
    ranked = rank_courses_by_metric("CS", "rating", "desc", rank_query, limit=5, min_responses=30)
    check("ranking uses sargable LIKE prefix", "tc.course_code LIKE %s" in rank_calls["sql"])
    check("ranking anchors with a regex to drop false prefixes", "tc.course_code ~ %s" in rank_calls["sql"])
    check("regexp_replace no longer in the WHERE clause", "regexp_replace" not in rank_calls["sql"])
    check("ranking passes the metric LIKE term", "%overall%" in rank_calls["params"])
    check("ranking passes the subject LIKE param", "CS%" in rank_calls["params"])
    check("ranking passes the subject regex param", "^CS[0-9]" in rank_calls["params"])
    check("ranking drops below-threshold courses (CS7870 n=2)", all(c["code"] != "CS7870" for c in ranked))
    check("ranking sorts desc by value", [c["code"] for c in ranked] == ["CS3100", "CS2000"])
    check("ranking carries computed value", ranked[0]["value"] == 4.45 and ranked[0]["responses"] == 100)
    # asc direction (easiest)
    ranked_asc = rank_courses_by_metric("CS", "difficulty", "asc", rank_query, limit=5, min_responses=30)
    check("ranking asc sorts low first", [c["code"] for c in ranked_asc] == ["CS2000", "CS3100"])

    # retrieve() returns a course_ranking block on a superlative query (even with a junk hint)
    def sup_retrieve_query(sql, params):
        if "trace_scores" in sql and "tc.course_code LIKE" in sql:
            return [{"code": "CS3100", "name": "PDI 2", "department": "CS", "m_w": 445.0, "m_r": 100.0}]
        return []
    rr = retrieve("Which CS course has the highest rating?", "CS course",
                  sup_retrieve_query, lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("retrieve returns course_ranking kind", rr.get("kind") == "course_ranking")
    check("retrieve course_ranking carries ranked courses", rr["courses"][0]["code"] == "CS3100")
    check("retrieve course_ranking metric tagged", rr.get("metric") == "rating")

    # unknown/empty subject ranking -> no block (falls through)
    rr0 = retrieve("Which ZZ course has the highest rating?", "ZZ course",
                   lambda sql, params: [], lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("empty ranking falls through (no course_ranking)", rr0.get("kind") != "course_ranking")

    # a REAL professor hint must win over the ranking branch (don't drop the named professor)
    def prof_hit_query(sql, params):
        if "trace_scores" in sql and "tc.course_code LIKE" in sql:
            return [{"code": "CS3100", "name": "PDI 2", "department": "CS", "m_w": 445.0, "m_r": 100.0}]
        return query_fn(sql, params)  # reuse the prof-facts fakes defined earlier in selftest
    rrp = retrieve("which CS course did Guha call hardest", "Guha",
                   prof_hit_query, query_one_fn, prof_search_fn, limit=8)
    check("professor hint suppresses ranking branch", rrp.get("kind") != "course_ranking")
    check("professor hint resolves the professor", rrp.get("professor_slug") == "guha-prof")

    # ── RRF fusion: same id in both lists ranks above id in one list ──
    fused = _rrf_fuse([("a", 1), ("b", 2)], [("b", 1), ("c", 2)], k=60)
    order = [i for i, _ in fused]
    check("rrf: id in both lists ranks first", order[0] == "b")
    check("rrf: includes all unique ids", set(order) == {"a", "b", "c"})

    # ── per-source floor: TRACE-heavy candidate list still yields reddit+rmp slots ──
    cand = (
        [{"id": f"t{i}", "source": "trace"} for i in range(8)]
        + [{"id": "r1", "source": "reddit"}, {"id": "r2", "source": "reddit"}, {"id": "r3", "source": "reddit"}]
        + [{"id": "m1", "source": "rmp"}]
    )  # already in fused-rank order
    picked = _apply_source_floor(cand, limit=8, reddit_floor=2, rmp_floor=2)
    srcs = [p["source"] for p in picked]
    check("floor: <=8 picked", len(picked) == 8)
    check("floor: at least 2 reddit when available", srcs.count("reddit") >= 2)
    check("floor: rmp present (only 1 exists) ", "rmp" in srcs)
    check("floor: rest filled by trace", srcs.count("trace") >= 4)

    # ── floor with a missing source: its slots roll into fused fill, no filler ──
    cand2 = [{"id": f"t{i}", "source": "trace"} for i in range(10)]  # only trace exists
    picked2 = _apply_source_floor(cand2, limit=8, reddit_floor=2, rmp_floor=2)
    check("floor: missing sources don't pad; 8 trace returned", len(picked2) == 8 and all(p["source"] == "trace" for p in picked2))

    # ── fetch_evidence: issues lexical (plainto_tsquery) + vector, fuses, tags source ──
    captured = {"sql": []}
    def ev_query(sql, params=None):
        captured["sql"].append(sql)
        if "plainto_tsquery" in sql:  # lexical
            return [{"id": "r1"}, {"id": "t1"}]
        if "embedding" in sql:        # vector
            return [{"id": "t1"}, {"id": "m1"}]
        if "FROM evidence" in sql and "WHERE id IN" in sql:  # hydrate
            return [{"id": "r1", "source": "reddit", "body": "office hours great", "subreddit": "NEU",
                     "reddit_score": 5, "permalink": "/r/x", "created_utc": None, "sentiment": "positive"},
                    {"id": "t1", "source": "trace", "body": "clear lectures", "subreddit": None,
                     "reddit_score": None, "permalink": None, "created_utc": None, "sentiment": None},
                    {"id": "m1", "source": "rmp", "body": "tough but fair", "subreddit": None,
                     "reddit_score": None, "permalink": None, "created_utc": None, "sentiment": None}]
        return []
    def fake_embed(q): return [0.1] * 384
    res = fetch_evidence("guha-prof", None, "office hours", fake_embed, ev_query, limit=8)
    check("fetch_evidence lexical uses plainto_tsquery not websearch",
          any("plainto_tsquery" in s for s in captured["sql"]) and not any("websearch_to_tsquery" in s for s in captured["sql"]))
    check("fetch_evidence issued a vector query", any("embedding" in s for s in captured["sql"]))
    check("fetch_evidence tags source on every row", all("source" in r for r in res))
    check("fetch_evidence tolerates NULL sentiment", any(r["sentiment"] is None for r in res))

    # ── embed-fn failure → lexical-only, no crash ──
    res_lex = fetch_evidence("guha-prof", None, "office hours", lambda q: None, ev_query, limit=8)
    check("embed None -> lexical-only still returns rows", len(res_lex) >= 1 and all("source" in r for r in res_lex))

    # ── Issue 27: entity filter is split slug-only / code-only, no OR-with-NULL-param ──
    slug_where, slug_params = _entity_filter("guha-prof", None)
    check("slug-only filter has no OR", "OR" not in slug_where)
    check("slug-only filter params carry only the slug", slug_params == ("guha-prof",))
    code_where, code_params = _entity_filter(None, "CS3500")
    check("code-only filter keeps the Issue-1 reddit OR-leg", "OR e." in code_where)
    check("code-only filter carries code + two ILIKE patterns", code_params == ("CS3500", "%CS3500%", "%CS 3500%"))

    # ── Issue 1: course questions admit Reddit evidence via the code-only ILIKE leg ──
    captured_code = {"sql": []}
    def code_ev_query(sql, params=None):
        captured_code["sql"].append(sql)
        if "plainto_tsquery" in sql:
            return [{"id": "r1"}]
        if "FROM evidence" in sql and "WHERE id IN" in sql:
            return [{"id": "r1", "source": "reddit", "body": "CS3500 is rough", "subreddit": "NEU",
                     "reddit_score": 5, "permalink": "/r/x", "created_utc": None, "sentiment": None}]
        return []
    res_code = fetch_evidence(None, "CS3500", "is it hard", None, code_ev_query, limit=8)
    check("code-only lexical SQL carries the reddit ILIKE leg",
          any("e.source = 'reddit'" in s and "ILIKE" in s for s in captured_code["sql"]))
    check("code-only lexical SQL has no OR on the entity equality (Issue-1 leg excepted)",
          all(s.split("AND e.flagged")[0].count("e.course_code = %s OR") == 1 for s in captured_code["sql"] if "plainto_tsquery" in s))
    check("code path still returns the reddit row", len(res_code) == 1 and res_code[0]["source"] == "reddit")

    # professor path (slug set) must NOT gain the reddit ILIKE leg
    captured_slug = {"sql": []}
    def slug_ev_query(sql, params=None):
        captured_slug["sql"].append(sql)
        if "plainto_tsquery" in sql:
            return [{"id": "r1"}]
        if "FROM evidence" in sql and "WHERE id IN" in sql:
            return [{"id": "r1", "source": "reddit", "body": "great office hours", "subreddit": "NEU",
                     "reddit_score": 5, "permalink": "/r/x", "created_utc": None, "sentiment": None}]
        return []
    fetch_evidence("guha-prof", None, "office hours", None, slug_ev_query, limit=8)
    check("professor path SQL is unchanged (no reddit ILIKE leg)",
          all("ILIKE" not in s for s in captured_slug["sql"]))

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Question-path retrieval (structured-first).")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
