import json
import re
from datetime import date
from render import (
    professor_html, course_html, not_found_html, home_html, _esc,
    _clip_description, MAX_DESCRIPTION,
    MAX_SNAPSHOT_REVIEWS, professors_listing_html, courses_listing_html,
    department_html, departments_listing_html,
)


def _extract_jsonld(html):
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    return [json.loads(b) for b in blocks]


def _meta_description(html):
    m = re.search(r'<meta name="description" content="(.*?)">', html)
    return m.group(1) if m else None


def test_esc_escapes_html_and_handles_none():
    assert _esc(None) == ""
    assert _esc("<b>&'\"") == "&lt;b&gt;&amp;&#x27;&quot;"
    assert _esc(4.25) == "4.25"


def test_professor_html_has_title_canonical_and_h1():
    profile = {
        "name": "Francis Georges", "department": "Economics",
        "avgRating": 4.25, "totalRatings": 2686, "wouldTakeAgainPct": 83,
        "difficulty": 2.9, "rmpRating": 4.3, "traceRating": 4.2,
        "imageUrl": "https://img/x.jpg", "professorUrl": None,
        "traceCourses": [{"displayName": "ECON1115: Macro"}],
    }
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/francis-georges")
    assert "<!doctype html>" in html.lower()
    assert "<title>Francis Georges Reviews &amp; Ratings — Northeastern Economics</title>" in html
    assert '<link rel="canonical" href="https://ratemyhusky.com/professors/francis-georges"' in html
    assert "<h1>Francis Georges — Ratings & Reviews (Northeastern University)</h1>" in html
    # summary paragraph mentions the key numbers
    assert "4.25" in html and "2686" in html and "83%" in html


def test_professor_meta_description_leads_with_reviews_and_ratings_phrase():
    profile = _base_profile()
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    desc = _meta_description(html)
    assert desc.startswith(
        "Francis Georges professor reviews and ratings: 4.25/5 from 2686 student "
        "reviews at Northeastern (83% would take again). TRACE + RateMyProfessor + Reddit."
    )


def test_professor_meta_description_omits_would_take_again_clause_when_absent():
    profile = _base_profile(wouldTakeAgainPct=None)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    desc = _meta_description(html)
    assert "would take again" not in desc
    assert desc.startswith(
        "Francis Georges professor reviews and ratings: 4.25/5 from 2686 student "
        "reviews at Northeastern. TRACE + RateMyProfessor + Reddit."
    )


def test_professor_html_jsonld_person_never_has_aggregate_rating():
    # schema.org Person does not support aggregateRating and Google rejects it
    # as an invalid object type in Rich Results, so it must be omitted even
    # when ratings exist (the numbers still appear in the human-readable summary).
    profile = {
        "name": "Francis Georges", "department": "Economics",
        "avgRating": 4.25, "totalRatings": 2686, "wouldTakeAgainPct": 83,
        "difficulty": 2.9, "rmpRating": None, "traceRating": None,
        "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/francis-georges")
    blocks = _extract_jsonld(html)
    person = blocks[0]["mainEntity"]
    assert person["@type"] == "Person"
    assert person["name"] == "Francis Georges"
    assert "aggregateRating" not in person


def test_professor_html_says_no_ratings_rather_than_rating_them_zero():
    """The crawler-facing page must not claim an unrated professor scored 0/5.

    Same defect as the React card's "0.00", and worse, because this is the copy
    Google indexes: "rated 0/5 by 0 students" reads as a real, terrible rating
    of a professor nobody has reviewed. The stats row goes too — _stat_rows
    already drops a None value, which is how difficulty and would-take-again
    have always handled being absent.
    """
    profile = _base_profile(avgRating=None, totalRatings=0, wouldTakeAgainPct=None,
                            difficulty=None, rmpRating=None, traceRating=None)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "0/5" not in html
    assert "Average rating" not in html
    desc = _meta_description(html)
    assert "no student ratings yet" in desc.lower()


def test_professor_html_omits_aggregate_rating_when_no_ratings():
    profile = {
        "name": "New Prof", "department": "Music",
        "avgRating": 0.0, "totalRatings": 0, "wouldTakeAgainPct": None,
        "difficulty": None, "rmpRating": None, "traceRating": None,
        "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/new-prof")
    person = _extract_jsonld(html)[0]["mainEntity"]
    assert "aggregateRating" not in person


# ── ProfilePage wrapper (P1-1) ──

def test_professor_html_jsonld_wrapped_in_profilepage():
    profile = _base_profile(imageUrl="https://img/x.jpg", professorUrl="https://www.ratemyprofessors.com/professor/12345")
    canonical = "https://ratemyhusky.com/professors/francis-georges"
    html = professor_html(profile, [], canonical)
    block = _extract_jsonld(html)[0]
    assert block["@context"] == "https://schema.org"
    assert block["@type"] == "ProfilePage"
    assert block["dateModified"] == date.today().isoformat()
    person = block["mainEntity"]
    assert person["@type"] == "Person"
    assert person["name"] == "Francis Georges"
    assert person["jobTitle"] == "Professor"
    assert person["knowsAbout"] == "Economics"
    assert person["image"] == "https://img/x.jpg"
    assert person["url"] == canonical
    assert person["sameAs"] == ["https://www.ratemyprofessors.com/professor/12345"]
    worksfor = person["worksFor"]
    assert worksfor == {
        "@type": "CollegeOrUniversity",
        "name": "Northeastern University",
        "sameAs": "https://www.northeastern.edu",
    }


def test_professor_html_jsonld_omits_person_sameas_when_no_rmp_url():
    profile = _base_profile(professorUrl=None)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/francis-georges")
    person = _extract_jsonld(html)[0]["mainEntity"]
    assert "sameAs" not in person
    # worksFor still carries the university sameAs
    assert person["worksFor"]["sameAs"] == "https://www.northeastern.edu"


def test_professor_html_jsonld_omits_image_when_absent():
    profile = _base_profile(imageUrl=None)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/francis-georges")
    person = _extract_jsonld(html)[0]["mainEntity"]
    assert "image" not in person


# ── BreadcrumbList (P1-3) ──

def test_professor_html_has_breadcrumb_list():
    canonical = "https://ratemyhusky.com/professors/francis-georges"
    html = professor_html(_base_profile(), [], canonical)
    blocks = _extract_jsonld(html)
    breadcrumb = next(b for b in blocks if b.get("@type") == "BreadcrumbList")
    items = breadcrumb["itemListElement"]
    assert items[0] == {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://ratemyhusky.com/"}
    assert items[1] == {"@type": "ListItem", "position": 2, "name": "Professors", "item": "https://ratemyhusky.com/professors"}
    assert items[2]["position"] == 3
    assert items[2]["name"] == "Francis Georges"
    assert items[2]["item"] == canonical


def test_course_html_has_breadcrumb_list():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [],
    }
    canonical = "https://ratemyhusky.com/courses/econ1115"
    html = course_html(detail, canonical)
    blocks = _extract_jsonld(html)
    breadcrumb = next(b for b in blocks if b.get("@type") == "BreadcrumbList")
    items = breadcrumb["itemListElement"]
    assert items[0] == {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://ratemyhusky.com/"}
    assert items[1] == {"@type": "ListItem", "position": 2, "name": "Courses", "item": "https://ratemyhusky.com/courses"}
    assert items[2]["position"] == 3
    assert items[2]["name"] == "ECON1115"
    assert items[2]["item"] == canonical


# ── Course AggregateRating (P1-2) ──

def test_course_html_includes_aggregate_rating_when_avg_and_count_present():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025",
                    "ratingCount": 342},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    course_block = next(b for b in _extract_jsonld(html) if b.get("@type") == "Course")
    assert course_block["aggregateRating"] == {
        "@type": "AggregateRating",
        "ratingValue": 4.1,
        "ratingCount": 342,
        "bestRating": 5,
    }


def test_course_html_omits_aggregate_rating_when_no_rating_count():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025",
                    "ratingCount": None},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    course_block = next(b for b in _extract_jsonld(html) if b.get("@type") == "Course")
    assert "aggregateRating" not in course_block


def test_course_html_omits_aggregate_rating_when_no_avg_rating():
    detail = {
        "summary": {"code": "NEW1000", "name": "Brand New Course",
                    "department": "TBD", "avgRating": None,
                    "avgEnrollment": None, "latestTermTitle": "",
                    "ratingCount": 5},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/new1000")
    course_block = next(b for b in _extract_jsonld(html) if b.get("@type") == "Course")
    assert "aggregateRating" not in course_block


def test_course_html_omits_aggregate_rating_when_count_missing_key():
    # No "ratingCount" key at all in summary (older payload shape) — must not fabricate.
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    course_block = next(b for b in _extract_jsonld(html) if b.get("@type") == "Course")
    assert "aggregateRating" not in course_block


def test_professor_html_escapes_review_comment():
    profile = {
        "name": "X Y", "department": "CS", "avgRating": 3.0, "totalRatings": 1,
        "wouldTakeAgainPct": None, "difficulty": None, "rmpRating": None,
        "traceRating": None, "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    reviews = [{"course": "CS1", "quality": 3, "difficulty": 3, "date": "2024",
                "comment": "<script>alert(1)</script> great"}]
    html = professor_html(profile, reviews, "https://ratemyhusky.com/professors/x-y")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_professor_html_caps_reviews():
    profile = {
        "name": "X Y", "department": "CS", "avgRating": 3.0, "totalRatings": 50,
        "wouldTakeAgainPct": None, "difficulty": None, "rmpRating": None,
        "traceRating": None, "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    reviews = [{"course": "CS1", "quality": 3, "difficulty": 3, "date": "2024",
                "comment": f"comment number {i}"} for i in range(50)]
    html = professor_html(profile, reviews, "https://ratemyhusky.com/professors/x-y")
    assert html.count("<blockquote>") == MAX_SNAPSHOT_REVIEWS


def test_course_html_has_title_h1_and_jsonld():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [{"name": "Francis Georges", "slug": "francis-georges"}],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    assert "<title>ECON1115 Reviews — Macroeconomics at Northeastern</title>" in html
    assert "<h1>ECON1115 — Macroeconomics: Reviews & Ratings</h1>" in html
    block = _extract_jsonld(html)[0]
    assert block["@type"] == "Course"
    assert block["courseCode"] == "ECON1115"


def test_course_meta_description_leads_with_reviews_and_ratings_phrase():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": None},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    desc = _meta_description(html)
    assert desc == (
        "ECON1115 (Macroeconomics) course reviews and ratings at Northeastern (NEU). "
        "Average rating 4.1/5. "
        "Compare instructors with TRACE + RateMyProfessor reviews."
    )


def test_not_found_html_is_noindex():
    html = not_found_html("professor")
    assert '<meta name="robots" content="noindex">' in html


def test_render_professor_route_returns_html(render_client):
    resp = render_client.get("/render/professors/francis-georges")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "<h1>Francis Georges — Ratings & Reviews (Northeastern University)</h1>" in body
    assert "Excellent lecturer." in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_professor_missing_is_404_noindex(render_client):
    resp = render_client.get("/render/professors/missing")
    assert resp.status_code == 404
    assert '<meta name="robots" content="noindex">' in resp.get_data(as_text=True)


def test_render_course_route_returns_html(render_client):
    resp = render_client.get("/render/courses/econ1115")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1>ECON1115 — Macroeconomics: Reviews & Ratings</h1>" in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


# ── TRACE review count (number only, never the comment text) ──

def _base_profile(**over):
    p = {
        "name": "Francis Georges", "department": "Economics", "avgRating": 4.25,
        "totalRatings": 2686, "wouldTakeAgainPct": 83, "difficulty": 2.9,
        "rmpRating": None, "traceRating": None, "imageUrl": None,
        "professorUrl": None, "traceCourses": [],
    }
    p.update(over)
    return p


def test_professor_html_shows_trace_review_count():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=5662)
    assert "<dt>TRACE reviews</dt><dd>5662</dd>" in html


def test_professor_html_omits_trace_count_when_zero():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=0)
    assert "TRACE reviews" not in html


def test_professor_html_never_shows_gated_trace_comment_text():
    # Even if a gated (empty-text) trace entry were somehow passed as a review,
    # only RMP review blockquotes are rendered; the trace count is a number only.
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=42)
    # The count appears, but no blockquote section exists (no RMP review text given).
    assert "<dd>42</dd>" in html
    assert "<blockquote>" not in html


def test_professor_html_shows_rmp_review_count():
    reviews = [{"course": "ECON1115", "quality": 5, "difficulty": 3,
                "date": "2024", "comment": "Great."}]
    html = professor_html(_base_profile(), reviews, "https://ratemyhusky.com/professors/x",
                          trace_count=10)
    assert "<dt>RateMyProfessor reviews</dt><dd>1</dd>" in html


# ── noindex for zero-content professor pages (thin-content SEO) ──

def test_professor_html_is_noindex_when_no_ratings_no_reviews_no_trace():
    profile = _base_profile(totalRatings=0)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x",
                          trace_count=0)
    assert '<meta name="robots" content="noindex">' in html


def test_professor_html_is_indexed_when_ratings_exist():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=0)
    assert '<meta name="robots" content="index, follow">' in html


# ── Social link-preview meta tags ──

def test_professor_html_has_twitter_card_large_image_when_photo():
    html = professor_html(_base_profile(imageUrl="https://img/x.jpg"), [],
                          "https://ratemyhusky.com/professors/x")
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:title" content="Francis Georges Reviews &amp; Ratings — Northeastern Economics">' in html
    assert '<meta name="twitter:image" content="https://img/x.jpg">' in html
    assert '<meta property="og:image:alt"' in html


def test_professor_html_twitter_summary_when_no_photo():
    html = professor_html(_base_profile(imageUrl=None), [],
                          "https://ratemyhusky.com/professors/x")
    # Falls back to the bare logo → small summary card, not large image.
    assert '<meta name="twitter:card" content="summary">' in html


def test_course_html_has_twitter_tags():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    assert '<meta name="twitter:card"' in html
    assert '<meta name="twitter:title" content="ECON1115 Reviews — Macroeconomics at Northeastern">' in html


def test_render_professor_route_exposes_trace_count(render_client):
    # The conftest stub returns traceComments with entries; the route should
    # surface their count without rendering any gated comment text.
    resp = render_client.get("/render/professors/francis-georges")
    body = resp.get_data(as_text=True)
    assert "TRACE reviews" in body


def test_home_html_title_canonical_h1_and_summary():
    stats = [
        {"label": "Professors", "value": "9.3K"},
        {"label": "Courses", "value": "5K"},
        {"label": "Comments", "value": "120K"},
        {"label": "Departments", "value": "180"},
    ]
    top = [{"name": "Francis Georges", "slug": "francis-georges",
            "department": "Economics", "avgRating": 4.25}]
    html = home_html(stats, top, "https://ratemyhusky.com/")
    assert "<!doctype html>" in html.lower()
    assert "<title>RateMyHusky — Northeastern University Professor Reviews &amp; Ratings</title>" in html
    assert '<link rel="canonical" href="https://ratemyhusky.com/"' in html
    assert "<h1>RateMyHusky — Northeastern University Professor Reviews &amp; Ratings</h1>" in html
    # stat values rendered as-is
    assert "9.3K" in html and "Professors" in html
    # top professor linked into its detail page
    assert '<a href="https://ratemyhusky.com/professors/francis-georges">' in html
    # description must carry both target keywords
    desc = _meta_description(html)
    assert "ratings" in desc and "reviews" in desc


def test_home_html_jsonld_website_with_searchaction():
    html = home_html([], [], "https://ratemyhusky.com/")
    block = _extract_jsonld(html)[0]
    assert block["@type"] == "WebSite"
    assert block["url"] == "https://ratemyhusky.com"
    action = block["potentialAction"]
    assert action["@type"] == "SearchAction"
    assert "{search_term_string}" in action["target"]
    assert action["query-input"] == "required name=search_term_string"


def test_home_html_renders_without_top_professors():
    # Graceful: empty top list still produces a valid page, no <a> prof links.
    html = home_html([{"label": "Professors", "value": "9.3K"}], [], "https://ratemyhusky.com/")
    assert "<h1>" in html
    assert "/professors/" not in html.split("<body>")[1].split("Browse")[0]


def test_professors_listing_title_total_and_entry_links():
    entries = [{"name": "Francis Georges", "slug": "francis-georges",
                "department": "Economics", "avgRating": 4.25}]
    html = professors_listing_html(entries, 9329, "https://ratemyhusky.com/professors")
    assert "<title>Northeastern Professor Ratings &amp; Reviews | RateMyHusky</title>" in html
    assert "<h1>Northeastern University Professor Ratings & Reviews</h1>" in html
    assert "9329" in html  # total mentioned in summary
    assert '<a href="https://ratemyhusky.com/professors/francis-georges">Francis Georges</a>' in html
    desc = _meta_description(html)
    assert desc.startswith("Browse 9329 Northeastern University (NEU) professor ratings and reviews.")


def test_professors_listing_jsonld_itemlist():
    entries = [
        {"name": "A", "slug": "a", "department": "CS", "avgRating": 4.0},
        {"name": "B", "slug": "b", "department": "CS", "avgRating": 3.0},
    ]
    block = _extract_jsonld(
        professors_listing_html(entries, 2, "https://ratemyhusky.com/professors"))[0]
    assert block["@type"] == "ItemList"
    assert len(block["itemListElement"]) == 2
    assert block["itemListElement"][0]["position"] == 1
    assert block["itemListElement"][0]["url"] == "https://ratemyhusky.com/professors/a"


def test_professors_listing_caps_at_20_and_escapes_name():
    entries = [{"name": f"<b>P{i}</b>", "slug": f"p{i}",
                "department": "X", "avgRating": 1.0} for i in range(40)]
    html = professors_listing_html(entries, 40, "https://ratemyhusky.com/professors")
    assert html.count("<li>") == 20
    assert "<b>P0</b>" not in html
    assert "&lt;b&gt;P0&lt;/b&gt;" in html


def test_courses_listing_title_total_and_entry_links():
    entries = [{"code": "ECON1115", "name": "Macroeconomics",
                "department": "Economics", "avgRating": 4.1}]
    html = courses_listing_html(entries, 5013, "https://ratemyhusky.com/courses")
    assert "<title>Northeastern Course Reviews &amp; Ratings | RateMyHusky</title>" in html
    assert "<h1>Northeastern University Course Reviews & Ratings</h1>" in html
    assert "5013" in html
    assert '<a href="https://ratemyhusky.com/courses/ECON1115">ECON1115 — Macroeconomics</a>' in html
    desc = _meta_description(html)
    assert desc.startswith("Browse 5013 Northeastern University (NEU) course reviews and ratings.")


def test_courses_listing_jsonld_itemlist():
    entries = [{"code": "CS1", "name": "Intro", "department": "CS", "avgRating": 4.0}]
    block = _extract_jsonld(
        courses_listing_html(entries, 1, "https://ratemyhusky.com/courses"))[0]
    assert block["@type"] == "ItemList"
    assert block["itemListElement"][0]["url"] == "https://ratemyhusky.com/courses/CS1"


def test_render_home_route(render_client):
    resp = render_client.get("/render/home")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "<h1>RateMyHusky — Northeastern University Professor Reviews &amp; Ratings</h1>" in body
    assert "francis-georges" in body  # top professor linked
    assert "Professors" in body and "9.3K" in body  # stats rendered
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_professors_listing_route(render_client):
    resp = render_client.get("/render/professors")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1>Northeastern University Professor Ratings & Reviews</h1>" in body
    assert "9329" in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_courses_listing_route(render_client):
    resp = render_client.get("/render/courses")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1>Northeastern University Course Reviews & Ratings</h1>" in body
    assert "ECON1115" in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_home_degrades_when_top_professors_fail(render_client, monkeypatch):
    import render
    monkeypatch.setattr(render, "_get_professors_catalog_view",
                        lambda: (lambda: ({"error": "boom"}, 500)), raising=False)
    resp = render_client.get("/render/home")
    assert resp.status_code == 200  # still renders, top-prof section just omitted
    body = resp.get_data(as_text=True)
    assert "<h1>RateMyHusky" in body


# ── Meta description length (Bing flags outside ~120–160) ──

def test_clip_description_leaves_short_text_unchanged():
    short = "A short description."
    assert _clip_description(short) == short


def test_clip_description_truncates_at_word_boundary_with_ellipsis():
    long = "word " * 60  # 300 chars, well over the cap
    clipped = _clip_description(long)
    assert len(clipped) <= MAX_DESCRIPTION + 1  # +1 for the ellipsis char
    assert clipped.endswith("…")
    assert "word…" in clipped and clipped[:-1].strip().split()[-1] == "word"


def test_professor_meta_description_within_limit_even_with_long_name():
    profile = _base_profile(
        name="Maximilian Alexander Bartholomew Featherstonehaugh III",
        department="Interdisciplinary Engineering and Public Policy Studies",
        wouldTakeAgainPct=88,
    )
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    desc = _meta_description(html)
    assert len(desc) <= MAX_DESCRIPTION + 1


def test_course_meta_description_within_limit_even_with_long_name():
    detail = {
        "summary": {
            "code": "INPC9999",
            "name": "Advanced Topics in Interdisciplinary Engineering and Public Policy",
            "department": "Interdisciplinary Engineering and Public Policy",
            "avgRating": 4.1, "avgEnrollment": 120, "latestTermTitle": "Fall 2025",
        },
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/x")
    desc = _meta_description(html)
    assert len(desc) <= MAX_DESCRIPTION + 1


def test_home_meta_description_within_limit():
    html = home_html([], [], "https://ratemyhusky.com/")
    desc = _meta_description(html)
    assert len(desc) <= MAX_DESCRIPTION + 1


# ── Courses-taught links, deduped by base course code (P1-4) ──

def test_professor_html_courses_taught_deduped_and_linked():
    profile = _base_profile(traceCourses=[
        {"displayName": "EECE2150:02 (Circuits) - X"},
        {"displayName": "EECE2150:03 (Circuits) - X"},
        {"displayName": "EECE2150:04 (Circuits) - X"},
        {"displayName": "CS3500:01 (OOD) - X"},
    ])
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert html.count('href="https://ratemyhusky.com/courses/EECE2150"') == 1
    assert 'href="https://ratemyhusky.com/courses/CS3500"' in html


def test_professor_html_courses_taught_skips_entries_with_no_extractable_code():
    profile = _base_profile(traceCourses=[{"displayName": "(Special Topics) - X"}])
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "/courses/" not in html.split("<h2>Courses taught</h2>")[1].split("</ul>")[0] \
        if "<h2>Courses taught</h2>" in html else True


def test_professor_html_omits_courses_block_when_no_courses():
    html = professor_html(_base_profile(traceCourses=[]), [], "https://ratemyhusky.com/professors/x")
    assert "<h2>Courses taught</h2>" not in html


# ── "More professors in <department>" block (P1-4) ──

def test_professor_html_colleagues_block_rendered():
    profile = _base_profile(colleagues=[
        {"name": "Alice Smith", "slug": "alice-smith", "avgRating": 4.5, "totalRatings": 30},
        {"name": "Bob Jones", "slug": "bob-jones", "avgRating": None, "totalRatings": 2},
    ])
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "<h2>More Economics professors at Northeastern</h2>" in html
    assert '<a href="https://ratemyhusky.com/professors/alice-smith">Alice Smith</a> (4.5/5)' in html
    assert '<a href="https://ratemyhusky.com/professors/bob-jones">Bob Jones</a></li>' in html


def test_professor_html_omits_colleagues_block_when_empty():
    html = professor_html(_base_profile(colleagues=[]), [], "https://ratemyhusky.com/professors/x")
    assert "More Economics professors" not in html


def test_professor_html_omits_colleagues_block_when_absent():
    # Older callers/tests that don't pass colleagues at all must still work.
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x")
    assert "More Economics professors" not in html


# ── One-line verdict under the H1 (P1-5) ──

def test_professor_html_verdict_sentence_all_clauses():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x")
    month_year = date.today().strftime("%B %Y")
    expected = (
        "Francis Georges is an Economics professor at Northeastern University "
        "rated 4.25/5 by 2686 students, with 2.9/5 difficulty and 83% who would "
        f"take them again (TRACE + RateMyProfessors + Reddit, updated {month_year})."
    )
    assert expected in html


def test_professor_html_verdict_sentence_omits_missing_clauses():
    profile = _base_profile(wouldTakeAgainPct=None, difficulty=None)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    month_year = date.today().strftime("%B %Y")
    expected = (
        "Francis Georges is an Economics professor at Northeastern University "
        f"rated 4.25/5 by 2686 students (TRACE + RateMyProfessors + Reddit, updated {month_year})."
    )
    assert expected in html


def test_professor_html_verdict_appears_before_summary_paragraph():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x")
    body = html.split("<body>")[1]
    h1_pos = body.index("<h1>")
    verdict_pos = body.index("is an Economics professor at Northeastern University")
    summary_pos = body.index("professor reviews and ratings:")
    assert h1_pos < verdict_pos < summary_pos


def test_professor_html_verdict_uses_an_for_vowel_department():
    profile = _base_profile(department="Electrical Engineering")
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "is an Electrical Engineering professor" in html


def test_professor_html_verdict_uses_a_for_consonant_department():
    profile = _base_profile(department="Computer Science")
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "is a Computer Science professor" in html


# ── Freshness line (P1-4) ──

def test_professor_html_has_freshness_line():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x")
    month_year = date.today().strftime("%B %Y")
    assert f"<p>Data updated {month_year}.</p>" in html


def test_course_html_has_freshness_line():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    month_year = date.today().strftime("%B %Y")
    assert f"<p>Data updated {month_year}.</p>" in html


# ── FAQ block on professor pages, plain HTML, no FAQPage schema (P1-4) ──

def test_professor_html_faq_present_with_all_stats():
    profile = _base_profile(traceCourses=[{"displayName": "CS3500:01 (OOD) - X"}])
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "<h2>Frequently asked questions</h2>" in html
    assert "<h3>Is Francis Georges a good professor?</h3>" in html
    assert "<h3>How hard are Francis Georges&#x27;s classes?</h3>" in html
    assert "<h3>What courses does Francis Georges teach at Northeastern?</h3>" in html
    assert "CS3500" in html.split("What courses does Francis Georges teach")[1]


def test_professor_html_faq_has_no_faqpage_jsonld():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x")
    blocks = _extract_jsonld(html)
    assert all(b.get("@type") != "FAQPage" for b in blocks)


def test_professor_html_faq_omits_difficulty_qa_when_absent():
    profile = _base_profile(difficulty=None)
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "<h3>How hard are Francis Georges&#x27;s classes?</h3>" not in html


def test_professor_html_faq_omits_courses_qa_when_no_courses():
    html = professor_html(_base_profile(traceCourses=[]), [], "https://ratemyhusky.com/professors/x")
    assert "<h3>What courses does Francis Georges teach at Northeastern?</h3>" not in html


def test_professor_html_faq_omits_entirely_when_no_ratings():
    profile = _base_profile(totalRatings=0, avgRating=0.0, wouldTakeAgainPct=None,
                            difficulty=None, traceCourses=[])
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/x")
    assert "<h2>Frequently asked questions</h2>" not in html


# ── Review selection: recent + longest + spread across courses (P1-5) ──

def test_professor_html_review_selection_round_robins_across_courses_by_recency():
    # Course A's most recent review is newer than course B's -> A goes first
    # in the round-robin order, then alternates.
    reviews = [
        {"course": "A", "date": "2024-01-01", "comment": "a-old " * 3},
        {"course": "A", "date": "2024-06-01", "comment": "a-new " * 3},
        {"course": "B", "date": "2023-01-01", "comment": "b-old " * 3},
        {"course": "B", "date": "2023-06-01", "comment": "b-new " * 3},
    ]
    profile = _base_profile()
    html = professor_html(profile, reviews, "https://ratemyhusky.com/professors/x")
    body = html.split("<h2>Student reviews</h2>")[1]
    # Order: A's newest, B's newest, A's oldest, B's oldest (round robin,
    # each course internally sorted by date desc).
    a_new = body.index("a-new")
    b_new = body.index("b-new")
    a_old = body.index("a-old")
    b_old = body.index("b-old")
    assert a_new < b_new < a_old < b_old


def test_professor_html_review_selection_sorts_by_length_within_same_date():
    reviews = [
        {"course": "A", "date": "2024-01-01", "comment": "short"},
        {"course": "A", "date": "2024-01-01", "comment": "a much longer comment here"},
    ]
    html = professor_html(_base_profile(), reviews, "https://ratemyhusky.com/professors/x")
    body = html.split("<h2>Student reviews</h2>")[1]
    assert body.index("a much longer comment here") < body.index(">short<")


def test_professor_html_review_selection_puts_no_course_group_last():
    reviews = [
        {"course": "", "date": "2024-12-01", "comment": "no-course-newest " * 3},
        {"course": "A", "date": "2020-01-01", "comment": "a-oldest " * 3},
    ]
    html = professor_html(_base_profile(), reviews, "https://ratemyhusky.com/professors/x")
    body = html.split("<h2>Student reviews</h2>")[1]
    # Even though the no-course review is more recent, its course-group is
    # ordered last, so course A's review is selected/rendered first.
    assert body.index("a-oldest") < body.index("no-course-newest")


def test_professor_html_review_selection_still_caps_at_max_snapshot_reviews():
    reviews = [{"course": f"C{i}", "date": "2024-01-01", "comment": f"comment {i}"}
               for i in range(50)]
    html = professor_html(_base_profile(), reviews, "https://ratemyhusky.com/professors/x")
    assert html.count("<blockquote>") == MAX_SNAPSHOT_REVIEWS


# ── Department hub pages (P2-1a) ──

def _dept_detail(**over):
    d = {
        "name": "Computer Science", "slug": "computer-science",
        "professorCount": 3, "avgRating": 3.87,
        "professors": [
            {"name": "Alice Smith", "slug": "alice-smith", "avgRating": 4.5,
             "difficulty": 2.1, "wouldTakeAgainPct": 90, "totalRatings": 120},
            {"name": "Bob Jones", "slug": "bob-jones", "avgRating": 3.8,
             "difficulty": 3.0, "wouldTakeAgainPct": 70, "totalRatings": 45},
            {"name": "Carol Lee", "slug": "carol-lee", "avgRating": 3.3,
             "difficulty": 4.2, "wouldTakeAgainPct": None, "totalRatings": 10},
        ],
    }
    d.update(over)
    return d


def test_department_html_title_h1_and_canonical():
    canonical = "https://ratemyhusky.com/departments/computer-science"
    html = department_html(_dept_detail(), canonical)
    assert "<!doctype html>" in html.lower()
    assert "<title>Computer Science Professors at Northeastern — Ratings &amp; Reviews</title>" in html
    assert '<link rel="canonical" href="https://ratemyhusky.com/departments/computer-science"' in html
    assert "<h1>Northeastern Computer Science — Professor Ratings & Reviews</h1>" in html


def test_department_html_summary_mentions_count_avg_and_top_professor():
    # The full generated summary (chatbot-quotable text) lives in the body;
    # the <meta name="description"> is separately clipped for Bing's ~155
    # char limit (same pattern as professor_html's verdict vs. summary).
    html = department_html(_dept_detail(), "https://ratemyhusky.com/departments/computer-science")
    body = html.split("<body>")[1]
    assert (
        "The Computer Science department at Northeastern University has 3 rated "
        "professors averaging 3.87/5. The highest-rated is Alice Smith (4.5/5 from 120 reviews)."
    ) in body


def test_department_html_summary_omits_would_take_again_when_no_data():
    # The table's "Would take again" column header is unrelated; only the
    # generated summary sentence must skip the would-take-again clause.
    detail = _dept_detail(professors=[
        {"name": "Alice Smith", "slug": "alice-smith", "avgRating": 4.5,
         "difficulty": 2.1, "wouldTakeAgainPct": None, "totalRatings": 120},
    ])
    html = department_html(detail, "https://ratemyhusky.com/departments/computer-science")
    summary_para = html.split("</h1>")[1].split("</p>")[0]
    assert "would take" not in summary_para.lower()


def test_department_html_summary_includes_would_take_again_when_data_present():
    html = department_html(_dept_detail(), "https://ratemyhusky.com/departments/computer-science")
    body = html.split("<body>")[1]
    assert "would take these professors again" in body


def test_department_html_table_has_all_professors_with_correct_columns():
    html = department_html(_dept_detail(), "https://ratemyhusky.com/departments/computer-science")
    assert html.count("<tr>") == 4  # header + 3 professor rows
    assert "<th>Name</th>" in html and "<th>Rating</th>" in html
    assert "<th>Difficulty</th>" in html and "<th>Would take again</th>" in html
    assert "<th>Reviews</th>" in html
    assert '<a href="https://ratemyhusky.com/professors/alice-smith">Alice Smith</a>' in html
    assert '<a href="https://ratemyhusky.com/professors/bob-jones">Bob Jones</a>' in html
    assert '<a href="https://ratemyhusky.com/professors/carol-lee">Carol Lee</a>' in html
    assert "<td>90%</td>" in html
    assert "<td>4.5</td>" in html
    assert "<td>2.1</td>" in html
    assert "<td>120</td>" in html


def test_department_html_table_no_truncation_with_many_professors():
    profs = [{"name": f"Prof {i}", "slug": f"prof-{i}", "avgRating": 3.0,
              "difficulty": 3.0, "wouldTakeAgainPct": 50, "totalRatings": 5}
             for i in range(60)]
    detail = _dept_detail(professors=profs, professorCount=60)
    html = department_html(detail, "https://ratemyhusky.com/departments/computer-science")
    assert html.count("<tr>") == 61  # header + all 60, no cap
    assert '<a href="https://ratemyhusky.com/professors/prof-59">Prof 59</a>' in html


def test_department_html_jsonld_itemlist_of_professors():
    html = department_html(_dept_detail(), "https://ratemyhusky.com/departments/computer-science")
    blocks = _extract_jsonld(html)
    itemlist = next(b for b in blocks if b.get("@type") == "ItemList")
    items = itemlist["itemListElement"]
    assert len(items) == 3
    assert items[0] == {"@type": "ListItem", "position": 1, "name": "Alice Smith",
                        "url": "https://ratemyhusky.com/professors/alice-smith"}


def test_department_html_jsonld_breadcrumb_list():
    canonical = "https://ratemyhusky.com/departments/computer-science"
    html = department_html(_dept_detail(), canonical)
    blocks = _extract_jsonld(html)
    breadcrumb = next(b for b in blocks if b.get("@type") == "BreadcrumbList")
    items = breadcrumb["itemListElement"]
    assert items[0] == {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://ratemyhusky.com/"}
    assert items[1] == {"@type": "ListItem", "position": 2, "name": "Departments", "item": "https://ratemyhusky.com/departments"}
    assert items[2] == {"@type": "ListItem", "position": 3, "name": "Computer Science", "item": canonical}


def test_department_html_has_freshness_line():
    html = department_html(_dept_detail(), "https://ratemyhusky.com/departments/computer-science")
    month_year = date.today().strftime("%B %Y")
    assert f"<p>Data updated {month_year}.</p>" in html


def test_department_html_escapes_professor_names():
    detail = _dept_detail(professors=[
        {"name": "<b>Evil</b>", "slug": "evil", "avgRating": 3.0,
         "difficulty": 3.0, "wouldTakeAgainPct": 50, "totalRatings": 5},
    ])
    html = department_html(detail, "https://ratemyhusky.com/departments/computer-science")
    assert "<b>Evil</b>" not in html
    assert "&lt;b&gt;Evil&lt;/b&gt;" in html


def test_department_html_skips_professors_without_slug():
    detail = _dept_detail(professors=[
        {"name": "No Slug", "slug": None, "avgRating": 3.0,
         "difficulty": 3.0, "wouldTakeAgainPct": 50, "totalRatings": 5},
        {"name": "Alice Smith", "slug": "alice-smith", "avgRating": 4.5,
         "difficulty": 2.1, "wouldTakeAgainPct": 90, "totalRatings": 120},
    ])
    html = department_html(detail, "https://ratemyhusky.com/departments/computer-science")
    assert "No Slug" not in html
    assert html.count("<tr>") == 2  # header + the one professor with a slug


def test_departments_listing_title_h1_and_entries():
    entries = [
        {"slug": "computer-science", "name": "Computer Science", "professorCount": 214, "avgRating": 3.9},
        {"slug": "economics", "name": "Economics", "professorCount": 40, "avgRating": 4.1},
    ]
    html = departments_listing_html(entries, 80, "https://ratemyhusky.com/departments")
    assert "<title>Northeastern Departments — Professor Ratings &amp; Reviews | RateMyHusky</title>" in html
    assert "<h1>Northeastern University Departments — Professor Ratings by Department</h1>" in html
    assert "80" in html
    assert '<a href="https://ratemyhusky.com/departments/computer-science">Computer Science</a>' in html
    assert "214 professors" in html


def test_departments_listing_lists_every_department_no_cap():
    entries = [{"slug": f"dept-{i}", "name": f"Dept {i}", "professorCount": 5, "avgRating": 3.5}
               for i in range(80)]
    html = departments_listing_html(entries, 80, "https://ratemyhusky.com/departments")
    assert html.count("<li>") == 80


def test_departments_listing_jsonld_itemlist():
    entries = [{"slug": "computer-science", "name": "Computer Science", "professorCount": 214, "avgRating": 3.9}]
    block = _extract_jsonld(
        departments_listing_html(entries, 1, "https://ratemyhusky.com/departments"))[0]
    assert block["@type"] == "ItemList"
    assert block["itemListElement"][0] == {
        "@type": "ListItem", "position": 1, "name": "Computer Science",
        "url": "https://ratemyhusky.com/departments/computer-science",
    }


def test_render_departments_listing_route(render_client):
    resp = render_client.get("/render/departments")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1>Northeastern University Departments — Professor Ratings by Department</h1>" in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_department_route_returns_html(render_client):
    resp = render_client.get("/render/departments/computer-science")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1>Northeastern Computer Science — Professor Ratings & Reviews</h1>" in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_department_missing_is_404_noindex(render_client):
    resp = render_client.get("/render/departments/missing")
    assert resp.status_code == 404
    assert '<meta name="robots" content="noindex">' in resp.get_data(as_text=True)


def test_department_html_meta_description_within_limit_even_with_long_name():
    detail = _dept_detail(
        name="Interdisciplinary Engineering and Public Policy Studies and Advanced Robotics",
    )
    html = department_html(detail, "https://ratemyhusky.com/departments/x")
    desc = _meta_description(html)
    assert len(desc) <= MAX_DESCRIPTION + 1


def test_department_html_handles_zero_professors_without_crashing():
    detail = _dept_detail(professors=[], professorCount=0, avgRating=None)
    html = department_html(detail, "https://ratemyhusky.com/departments/computer-science")
    assert "<h1>" in html
    assert html.count("<tr>") == 1  # header row only, no professor rows
    body = html.split("</h1>")[1].split("</p>")[0]
    assert "highest-rated" not in body
