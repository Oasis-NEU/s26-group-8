"""Static HTML snapshots of professor/course pages for AI/search crawlers.

Pure HTML builders here have no Flask or DB dependencies so they can be unit
tested directly. The blueprint routes (added in a later task) wire these to
the existing API data functions.
"""

import json
import re
from datetime import date
from html import escape as _html_escape
from flask import Blueprint  # noqa: F401  (used by the route task)

MAX_SNAPSHOT_REVIEWS = 15
SITE = "https://ratemyhusky.com"

# Search engines (incl. Bing Webmaster) flag meta descriptions outside the
# ~120–160 char band. Dynamic summaries grow with names/courses, so clip at a
# word boundary to stay safely under the ceiling.
MAX_DESCRIPTION = 155


def _clip_description(text: str) -> str:
    if len(text) <= MAX_DESCRIPTION:
        return text
    cut = text[:MAX_DESCRIPTION].rsplit(" ", 1)[0].rstrip(" .,;:—-")
    return cut + "…"


def _esc(value) -> str:
    """HTML-escape a value; None -> ''. Quotes escaped for attribute safety."""
    if value is None:
        return ""
    return _html_escape(str(value), quote=True)


def _jsonld_script(obj) -> str:
    # Escape '<' so a value can't break out of the <script> tag.
    payload = json.dumps(obj).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{payload}</script>'


def _page(title: str, description: str, canonical: str, body: str,
          jsonld: list, image: str | None = None, noindex: bool = False,
          og_type: str = "website", image_alt: str | None = None) -> str:
    robots = '<meta name="robots" content="noindex">' if noindex else \
             '<meta name="robots" content="index, follow">'
    # A real, content-bearing image gives social platforms a large card; the
    # bare logo is only a last-resort fallback.
    has_real_image = bool(image)
    img = image or f"{SITE}/logo.jpg"
    alt = image_alt or title
    twitter_card = "summary_large_image" if has_real_image else "summary"
    description = _clip_description(description)
    scripts = "\n".join(_jsonld_script(b) for b in jsonld)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(description)}">
<link rel="canonical" href="{_esc(canonical)}">
{robots}
<meta property="og:site_name" content="RateMyHusky">
<meta property="og:type" content="{_esc(og_type)}">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(description)}">
<meta property="og:url" content="{_esc(canonical)}">
<meta property="og:image" content="{_esc(img)}">
<meta property="og:image:alt" content="{_esc(alt)}">
<meta name="twitter:card" content="{twitter_card}">
<meta name="twitter:title" content="{_esc(title)}">
<meta name="twitter:description" content="{_esc(description)}">
<meta name="twitter:image" content="{_esc(img)}">
<meta name="twitter:image:alt" content="{_esc(alt)}">
{scripts}
</head>
<body>
{body}
</body>
</html>
"""


def _stat_rows(pairs) -> str:
    rows = []
    for label, value in pairs:
        if value is None or value == "":
            continue
        rows.append(f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>")
    return "<dl>" + "".join(rows) + "</dl>" if rows else ""


def _rating_suffix(avg) -> str:
    return f" ({_esc(avg)}/5)" if avg is not None else ""


def _article(noun: str) -> str:
    return "an" if noun[:1].lower() in "aeiou" else "a"


def _breadcrumb_list(section_name: str, section_url: str, page_name: str, page_url: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": section_name, "item": section_url},
            {"@type": "ListItem", "position": 3, "name": page_name, "item": page_url},
        ],
    }


def _month_year(today: date) -> str:
    return today.strftime("%B %Y")


def _course_code(display_name: str) -> str:
    """Extract the base course code (e.g. 'EECE2150') from a trace_courses
    display_name like 'EECE2150:02 (Circuits) - X'. Same rule as the
    professors_catalog / React course-code extraction elsewhere."""
    dn = (display_name or "").strip()
    if not dn:
        return ""
    m = re.match(r"^([A-Za-z]+\d+)", dn)
    return m.group(1).upper() if m else ""


def _dedupe_courses(courses: list) -> list:
    """Unique base course codes from traceCourses entries, first-seen order.
    Entries with no extractable code are skipped."""
    seen = set()
    out = []
    for c in courses or []:
        code = _course_code(c.get("displayName"))
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _select_reviews(reviews: list, limit: int) -> list:
    """Prefer recent + longest + spread across courses: group by course (no-
    course group last), order courses by their most recent review date desc,
    sort within a course by (date desc, comment length desc), then
    round-robin across courses until `limit` reviews are chosen."""
    groups = {}
    order = []
    for r in reviews:
        course = (r.get("course") or "").strip()
        if course not in groups:
            groups[course] = []
            order.append(course)
        groups[course].append(r)

    for course in order:
        groups[course].sort(
            key=lambda r: (r.get("date") or "", len((r.get("comment") or "").strip())),
            reverse=True,
        )

    def _most_recent_date(course):
        return max((r.get("date") or "" for r in groups[course]), default="")

    named = sorted((c for c in order if c), key=_most_recent_date, reverse=True)
    courses_in_order = named + ([""] if "" in groups else [])

    selected = []
    indices = {c: 0 for c in courses_in_order}
    while len(selected) < limit:
        progressed = False
        for course in courses_in_order:
            i = indices[course]
            if i < len(groups[course]):
                selected.append(groups[course][i])
                indices[course] = i + 1
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
    return selected


def professor_html(profile: dict, reviews: list, canonical: str,
                   trace_count: int = 0) -> str:
    name = profile.get("name") or ""
    dept = profile.get("department") or ""
    # Falsy, not just None, covers both the NULL the catalog holds for an
    # unrated professor and the 0.0 this field used to be coalesced to. 0 is
    # outside the 1-5 scale either way, so there is no reading of it as a score:
    # "rated 0/5 by 0 students" is the sentence Google would have indexed for
    # the ~2,083 unrated professors who still carry a course list.
    avg = profile.get("avgRating") or None
    total = profile.get("totalRatings") or 0
    wta = profile.get("wouldTakeAgainPct")
    diff = profile.get("difficulty")
    rmp_count = len(reviews)

    title = f"{name} Reviews & Ratings — Northeastern {dept}"
    wta_txt = f" ({wta}% would take again)" if wta is not None else ""
    summary = (
        f"{name} professor reviews and ratings: {avg}/5 from {total} student "
        f"reviews at Northeastern{wta_txt}. TRACE + RateMyProfessor + Reddit."
        if avg is not None else
        f"{name}, Northeastern {dept} professor: no student ratings yet. "
        "TRACE + RateMyProfessor + Reddit."
    )
    month_year = _month_year(date.today())

    diff_clause = f", with {diff}/5 difficulty" if diff is not None else ""
    wta_clause = f" and {wta}% who would take them again" if wta is not None else ""
    verdict = (
        f"{name} is {_article(dept)} {dept} professor at Northeastern University rated "
        f"{avg}/5 by {total} students{diff_clause}{wta_clause} "
        f"(TRACE + RateMyProfessors + Reddit, updated {month_year})."
        if avg is not None else
        f"{name} is {_article(dept)} {dept} professor at Northeastern University "
        f"with no student ratings yet "
        f"(TRACE + RateMyProfessors + Reddit, updated {month_year})."
    )

    stats = _stat_rows([
        ("Average rating", f"{avg}/5" if avg is not None else None),
        ("Total ratings", total),
        ("Would take again", f"{wta}%" if wta is not None else None),
        ("Difficulty", f"{diff}/5" if diff is not None else None),
        ("RateMyProfessor rating", profile.get("rmpRating")),
        ("TRACE rating", profile.get("traceRating")),
        # Count of TRACE evaluations only — the comment text stays gated.
        ("TRACE reviews", trace_count if trace_count else None),
        ("RateMyProfessor reviews", rmp_count if rmp_count else None),
    ])

    courses = profile.get("traceCourses") or []
    course_codes = _dedupe_courses(courses)
    course_items = "".join(
        f'<li><a href="{SITE}/courses/{_esc(code)}">{_esc(code)}</a></li>' for code in course_codes
    )
    courses_block = f"<h2>Courses taught</h2><ul>{course_items}</ul>" if course_items else ""

    colleagues = profile.get("colleagues") or []
    colleague_items = "".join(
        f'<li><a href="{SITE}/professors/{_esc(c.get("slug"))}">{_esc(c.get("name"))}</a>'
        f'{_rating_suffix(c.get("avgRating"))}</li>'
        for c in colleagues if c.get("slug")
    )
    colleagues_block = (
        f"<h2>More {_esc(dept)} professors at Northeastern</h2><ul>{colleague_items}</ul>"
        if colleague_items else ""
    )

    selected_reviews = _select_reviews(reviews, MAX_SNAPSHOT_REVIEWS)
    review_items = []
    for r in selected_reviews:
        comment = (r.get("comment") or "").strip()
        if not comment:
            continue
        meta = " ".join(x for x in [_esc(r.get("course")), _esc(r.get("date"))] if x)
        review_items.append(f"<blockquote>{_esc(comment)}<cite>{meta}</cite></blockquote>")
    reviews_block = ("<h2>Student reviews</h2>" + "".join(review_items)) if review_items else ""

    # ── FAQ: plain HTML only, no FAQPage JSON-LD (Google restricted that rich
    # result; the extractable text is the value for LLM answer engines). ──
    faq_items = []
    if total:
        wta_faq = f" {wta}% of students said they would take them again." if wta is not None else ""
        faq_items.append((
            f"Is {name} a good professor?",
            f"{name} has an average rating of {avg}/5 from {total} student reviews.{wta_faq}",
        ))
    if diff is not None:
        faq_items.append((
            f"How hard are {name}'s classes?",
            f"{name}'s classes have a difficulty rating of {diff}/5 based on student reviews.",
        ))
    if course_codes:
        faq_items.append((
            f"What courses does {name} teach at Northeastern?",
            f"{name} has taught {', '.join(course_codes)} at Northeastern.",
        ))
    faq_html = "".join(
        f"<h3>{_esc(q)}</h3><p>{_esc(a)}</p>" for q, a in faq_items
    )
    faq_block = f"<h2>Frequently asked questions</h2>{faq_html}" if faq_html else ""

    freshness = f"<p>Data updated {_esc(month_year)}.</p>"

    # Thin content (no ratings, no RMP review text, no TRACE evaluations)
    # is excluded from the sitemap; keep search engines from indexing it too.
    is_zero_content = not total and not review_items and not trace_count

    body = (
        f"<h1>{_esc(name)} — Ratings & Reviews (Northeastern University)</h1>"
        f"<p>{_esc(verdict)}</p>"
        f"<p>{_esc(summary)}</p>"
        f"{stats}{courses_block}{colleagues_block}{reviews_block}{faq_block}"
        f"{freshness}"
        f'<p><a href="{_esc(canonical)}">View on RateMyHusky</a></p>'
    )

    person = {
        "@type": "Person",
        "name": name,
        "jobTitle": "Professor",
        "worksFor": {
            "@type": "CollegeOrUniversity",
            "name": "Northeastern University",
            "sameAs": "https://www.northeastern.edu",
        },
        "knowsAbout": dept,
        "url": canonical,
    }
    if profile.get("imageUrl"):
        person["image"] = profile["imageUrl"]
    if profile.get("professorUrl"):
        person["sameAs"] = [profile["professorUrl"]]
    # NB: schema.org Person does not support aggregateRating, and Google rejects
    # it ("Invalid object type" in Rich Results), so we do not emit one here.
    profilepage = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "dateModified": date.today().isoformat(),
        "mainEntity": person,
    }
    breadcrumb = _breadcrumb_list("Professors", f"{SITE}/professors", name, canonical)

    return _page(title, summary, canonical, body, [profilepage, breadcrumb],
                 image=profile.get("imageUrl"), og_type="profile",
                 image_alt=f"{name}, professor of {dept} at Northeastern University",
                 noindex=is_zero_content)


def course_html(detail: dict, canonical: str) -> str:
    s = detail.get("summary") or {}
    code = s.get("code") or ""
    cname = s.get("name") or ""
    avg = s.get("avgRating")
    last = s.get("latestTermTitle") or ""

    title = f"{code} Reviews — {cname} at Northeastern"
    avg_txt = f"Average rating {avg}/5. " if avg is not None else ""
    last_txt = f"Last taught {last}. " if last else ""
    summary = (
        f"{code} ({cname}) course reviews and ratings at Northeastern (NEU). "
        f"{avg_txt}{last_txt}"
        f"Compare instructors with TRACE + RateMyProfessor reviews."
    )

    stats = _stat_rows([
        ("Average rating", f"{avg}/5" if avg is not None else None),
        ("Average enrollment", s.get("avgEnrollment")),
        ("Last taught", last),
    ])

    instructors = detail.get("instructors") or []
    inst_items = "".join(
        f'<li><a href="{SITE}/professors/{_esc(i.get("slug"))}">{_esc(i.get("name"))}</a></li>'
        for i in instructors if i.get("slug")
    )
    inst_block = f"<h2>Instructors</h2><ul>{inst_items}</ul>" if inst_items else ""
    freshness = f"<p>Data updated {_esc(_month_year(date.today()))}.</p>"

    body = (
        f"<h1>{_esc(code)} — {_esc(cname)}: Reviews & Ratings</h1>"
        f"<p>{_esc(summary)}</p>"
        f"{stats}{inst_block}{freshness}"
        f'<p><a href="{_esc(canonical)}">View on RateMyHusky</a></p>'
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": f"{code} — {cname}",
        "courseCode": code,
        "provider": {"@type": "CollegeOrUniversity", "name": "Northeastern University"},
    }
    rating_count = s.get("ratingCount")
    if avg is not None and rating_count:
        jsonld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": avg,
            "ratingCount": rating_count,
            "bestRating": 5,
        }
    breadcrumb = _breadcrumb_list("Courses", f"{SITE}/courses", code, canonical)
    return _page(title, summary, canonical, body, [jsonld, breadcrumb])


def home_html(stats: list, top_professors: list, canonical: str) -> str:
    title = "RateMyHusky — Northeastern University Professor Reviews & Ratings"
    summary = (
        "RateMyHusky combines TRACE evaluations and RateMyProfessor reviews for "
        "Northeastern professors and courses. Compare ratings, difficulty, and "
        "reviews — free."
    )

    stat_rows = _stat_rows([(s.get("label"), s.get("value")) for s in (stats or [])])

    prof_li = []
    for p in (top_professors or []):
        if not p.get("slug"):
            continue
        rating = p.get("avgRating")
        suffix = _rating_suffix(rating)
        prof_li.append(
            f'<li><a href="{SITE}/professors/{_esc(p.get("slug"))}">{_esc(p.get("name"))}</a>'
            f' — {_esc(p.get("department"))}{suffix}</li>'
        )
    prof_items = "".join(prof_li)
    profs_block = (
        f"<h2>Top-rated professors</h2><ul>{prof_items}</ul>" if prof_items else ""
    )

    body = (
        f"<h1>{_esc(title)}</h1>"
        f"<p>{_esc(summary)}</p>"
        f"{stat_rows}{profs_block}"
        f'<p>Browse all <a href="{SITE}/professors">professors</a>, '
        f'<a href="{SITE}/courses">courses</a>, and '
        f'<a href="{SITE}/departments">departments</a>.</p>'
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "RateMyHusky",
        "url": SITE,
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{SITE}/professors?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }
    return _page(title, summary, canonical, body, [jsonld])


LISTING_CAP = 20


def professors_listing_html(entries: list, total: int, canonical: str) -> str:
    title = "Northeastern Professor Ratings & Reviews | RateMyHusky"
    total_txt = str(total) if total else "thousands of"
    summary = (
        f"Browse {total_txt} Northeastern University (NEU) professor ratings and "
        f"reviews. Compare ratings, difficulty, and would-take-again from TRACE "
        f"evaluations and RateMyProfessor reviews."
    )

    shown = [e for e in (entries or []) if e.get("slug")][:LISTING_CAP]
    items = "".join(
        f'<li><a href="{SITE}/professors/{_esc(e.get("slug"))}">{_esc(e.get("name"))}</a>'
        f' — {_esc(e.get("department"))}'
        f'{_rating_suffix(e.get("avgRating"))}</li>'
        for e in shown
    )
    body = (
        f"<h1>Northeastern University Professor Ratings & Reviews</h1>"
        f"<p>{_esc(summary)}</p>"
        f"<ul>{items}</ul>"
        f'<p>Browse professors by <a href="{SITE}/departments">department</a>.</p>'
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": e.get("name"),
                "url": f"{SITE}/professors/{e.get('slug')}",
            }
            for i, e in enumerate(shown)
        ],
    }
    return _page(title, summary, canonical, body, [jsonld])


def courses_listing_html(entries: list, total: int, canonical: str) -> str:
    title = "Northeastern Course Reviews & Ratings | RateMyHusky"
    total_txt = str(total) if total else "thousands of"
    summary = (
        f"Browse {total_txt} Northeastern University (NEU) course reviews and "
        f"ratings. Compare instructors, average ratings, and enrollment from "
        f"TRACE evaluations."
    )

    shown = [e for e in (entries or []) if e.get("code")][:LISTING_CAP]
    items = "".join(
        f'<li><a href="{SITE}/courses/{_esc(e.get("code"))}">'
        f'{_esc(e.get("code"))} — {_esc(e.get("name"))}</a>'
        f'{_rating_suffix(e.get("avgRating"))}</li>'
        for e in shown
    )
    body = (
        f"<h1>Northeastern University Course Reviews & Ratings</h1>"
        f"<p>{_esc(summary)}</p>"
        f"<ul>{items}</ul>"
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": f"{e.get('code')} — {e.get('name')}",
                "url": f"{SITE}/courses/{e.get('code')}",
            }
            for i, e in enumerate(shown)
        ],
    }
    return _page(title, summary, canonical, body, [jsonld])


def department_html(detail: dict, canonical: str) -> str:
    name = detail.get("name") or ""
    n = detail.get("professorCount") or 0
    avg = detail.get("avgRating")
    professors = [p for p in (detail.get("professors") or []) if p.get("slug")]

    title = f"{name} Professors at Northeastern — Ratings & Reviews"
    month_year = _month_year(date.today())

    top = professors[0] if professors else None
    summary_sentences = [
        f"The {name} department at Northeastern University has {n} rated "
        f"professors averaging {avg}/5." if avg is not None else
        f"The {name} department at Northeastern University has {n} rated professors."
    ]
    if top:
        summary_sentences.append(
            f"The highest-rated is {top.get('name')} ({top.get('avgRating')}/5 "
            f"from {top.get('totalRatings')} reviews)."
        )
    wta_values = [p["wouldTakeAgainPct"] for p in professors if p.get("wouldTakeAgainPct") is not None]
    if wta_values:
        avg_wta = round(sum(wta_values) / len(wta_values), 1)
        summary_sentences.append(
            f"On average, {avg_wta}% of students would take these professors again."
        )
    summary = " ".join(summary_sentences)

    rows = "".join(
        f"<tr><td><a href=\"{SITE}/professors/{_esc(p.get('slug'))}\">{_esc(p.get('name'))}</a></td>"
        f"<td>{_esc(p.get('avgRating'))}</td>"
        f"<td>{_esc(p.get('difficulty'))}</td>"
        f"<td>{_esc(p.get('wouldTakeAgainPct'))}{'%' if p.get('wouldTakeAgainPct') is not None else ''}</td>"
        f"<td>{_esc(p.get('totalRatings'))}</td></tr>"
        for p in professors
    )
    table = (
        "<table><thead><tr><th>Name</th><th>Rating</th><th>Difficulty</th>"
        "<th>Would take again</th><th>Reviews</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )

    freshness = f"<p>Data updated {_esc(month_year)}.</p>"

    body = (
        f"<h1>Northeastern {_esc(name)} — Professor Ratings & Reviews</h1>"
        f"<p>{_esc(summary)}</p>"
        f"{table}{freshness}"
        f'<p><a href="{_esc(canonical)}">View on RateMyHusky</a></p>'
    )

    itemlist = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": p.get("name"),
                "url": f"{SITE}/professors/{p.get('slug')}",
            }
            for i, p in enumerate(professors)
        ],
    }
    breadcrumb = _breadcrumb_list("Departments", f"{SITE}/departments", name, canonical)

    return _page(title, summary, canonical, body, [itemlist, breadcrumb])


def departments_listing_html(entries: list, total: int, canonical: str) -> str:
    title = "Northeastern Departments — Professor Ratings & Reviews | RateMyHusky"
    total_txt = str(total) if total else "dozens of"
    summary = (
        f"Browse {total_txt} Northeastern University (NEU) academic departments. "
        f"Compare professor ratings, difficulty, and would-take-again by department."
    )

    shown = [e for e in (entries or []) if e.get("slug")]
    items = "".join(
        f'<li><a href="{SITE}/departments/{_esc(e.get("slug"))}">{_esc(e.get("name"))}</a>'
        f' — {_esc(e.get("professorCount"))} professors'
        f'{_rating_suffix(e.get("avgRating"))}</li>'
        for e in shown
    )
    body = (
        f"<h1>Northeastern University Departments — Professor Ratings by Department</h1>"
        f"<p>{_esc(summary)}</p>"
        f"<ul>{items}</ul>"
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": e.get("name"),
                "url": f"{SITE}/departments/{e.get('slug')}",
            }
            for i, e in enumerate(shown)
        ],
    }
    return _page(title, summary, canonical, body, [jsonld])


def not_found_html(kind: str) -> str:
    body = f"<h1>{_esc(kind.title())} not found</h1>"
    return _page("Not found | RateMyHusky", "Not found", f"{SITE}/", body, [],
                 noindex=True)


render_bp = Blueprint("render", __name__)


# Lazy accessors so tests can monkeypatch and routes avoid circular imports.
def _get_profile_view():
    from server import professor_profile
    return professor_profile


def _get_reviews_view():
    from server import professor_reviews
    return professor_reviews


def _get_course_view():
    from server import course_profile  # the /api/courses/<code> view (server.py:1651)
    return course_profile


def _json_or_404(resp):
    """Return (data, None) on success or (None, status) when the view returned
    an error tuple."""
    if isinstance(resp, tuple):
        return None, resp[1]
    return resp.get_json(), None


@render_bp.route("/render/professors/<slug>")
def render_professor(slug):
    from flask import Response
    profile_resp = _get_profile_view()(slug)
    data, err = _json_or_404(profile_resp)
    if err:
        return Response(not_found_html("professor"), status=404, mimetype="text/html")

    reviews_resp = _get_reviews_view()(slug)
    rdata, rerr = _json_or_404(reviews_resp)
    reviews = (rdata or {}).get("reviews", []) if not rerr else []
    # TRACE evaluation count (comment text stays gated; we expose only the number).
    trace_count = len((rdata or {}).get("traceComments", [])) if not rerr else 0

    canonical = f"{SITE}/professors/{slug}"
    html = professor_html(data, reviews, canonical, trace_count=trace_count)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return resp


@render_bp.route("/render/courses/<code>")
def render_course(code):
    from flask import Response
    detail_resp = _get_course_view()(code)
    data, err = _json_or_404(detail_resp)
    if err:
        return Response(not_found_html("course"), status=404, mimetype="text/html")

    canonical = f"{SITE}/courses/{code}"
    html = course_html(data, canonical)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return resp


def _get_stats_view():
    from server import stats
    return stats


def _get_professors_catalog_view():
    from server import professors_catalog
    return professors_catalog


def _get_courses_catalog_view():
    from server import courses_catalog
    return courses_catalog


def _cache_headers(resp):
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return resp


@render_bp.route("/render/home")
def render_home():
    from flask import Response
    stats_data, serr = _json_or_404(_get_stats_view()())
    stats_list = stats_data if (not serr and isinstance(stats_data, list)) else []

    cat_data, cerr = _json_or_404(_get_professors_catalog_view()())
    top = (cat_data or {}).get("professors", [])[:10] if not cerr else []

    html = home_html(stats_list, top, f"{SITE}/")
    return _cache_headers(Response(html, mimetype="text/html"))


@render_bp.route("/render/professors")
def render_professors_listing():
    from flask import Response
    data, err = _json_or_404(_get_professors_catalog_view()())
    entries = (data or {}).get("professors", []) if not err else []
    total = (data or {}).get("total", 0) if not err else 0
    html = professors_listing_html(entries, total, f"{SITE}/professors")
    return _cache_headers(Response(html, mimetype="text/html"))


@render_bp.route("/render/courses")
def render_courses_listing():
    from flask import Response
    data, err = _json_or_404(_get_courses_catalog_view()())
    entries = (data or {}).get("courses", []) if not err else []
    total = (data or {}).get("total", 0) if not err else 0
    html = courses_listing_html(entries, total, f"{SITE}/courses")
    return _cache_headers(Response(html, mimetype="text/html"))


def _get_departments_hub_view():
    from server import departments_hub
    return departments_hub


def _get_department_hub_detail_view():
    from server import department_hub_detail
    return department_hub_detail


@render_bp.route("/render/departments")
def render_departments_listing():
    from flask import Response
    data, err = _json_or_404(_get_departments_hub_view()())
    entries = (data or {}).get("departments", []) if not err else []
    total = (data or {}).get("total", 0) if not err else 0
    html = departments_listing_html(entries, total, f"{SITE}/departments")
    return _cache_headers(Response(html, mimetype="text/html"))


@render_bp.route("/render/departments/<slug>")
def render_department(slug):
    from flask import Response
    detail_resp = _get_department_hub_detail_view()(slug)
    data, err = _json_or_404(detail_resp)
    if err:
        return Response(not_found_html("department"), status=404, mimetype="text/html")

    canonical = f"{SITE}/departments/{slug}"
    html = department_html(data, canonical)
    return _cache_headers(Response(html, mimetype="text/html"))
