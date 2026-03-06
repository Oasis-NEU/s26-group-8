"""
Backend API server for NEU Professor Ratings.
Place this file in: backend/server.py

Install deps:  pip install flask flask-cors pandas
Run:           python backend/server.py
"""

import os, re
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ──────────────────────────────────────────────
#  Load CSVs once at startup
# ──────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), "Better_Scraper", "output_data")

rmp_profs    = pd.read_csv(os.path.join(DATA_DIR, "rmp_professors.csv"))
rmp_reviews  = pd.read_csv(os.path.join(DATA_DIR, "rmp_reviews.csv"))
trace_courses = pd.read_csv(os.path.join(DATA_DIR, "trace_courses.csv"))
trace_scores  = pd.read_csv(os.path.join(DATA_DIR, "trace_scores.csv"))
trace_comments = pd.read_csv(os.path.join(DATA_DIR, "trace_comments.csv"))

# Clean RMP data
rmp_profs["rating"]      = pd.to_numeric(rmp_profs["rating"], errors="coerce")
rmp_profs["num_ratings"] = pd.to_numeric(rmp_profs["num_ratings"], errors="coerce")
rmp_profs.dropna(subset=["rating", "num_ratings"], inplace=True)


# ──────────────────────────────────────────────
#  Friendly stat formatting:  round down then "+"
#    3863 → "3,800+",  42677 → "42,600+",  1234 → "1,200+"
# ──────────────────────────────────────────────
def friendly_count(n: int) -> str:
    if n < 100:
        return str(n)
    # Round down to nearest 100
    rounded = (n // 100) * 100
    return f"{rounded:,}+"


# ──────────────────────────────────────────────
#  Department → College mapping (NEU)
#  Exact match only — no substring matching
# ──────────────────────────────────────────────
COLLEGE_MAP = {
    # Khoury College of Computer Sciences
    "Computer Science":                   "Khoury",
    "Information Science":                "Khoury",
    "Information Systems":                "Khoury",
    "Computer & Informational Tech.":     "Khoury",
    "Computer amp Informational Tech.":   "Khoury",
    "Computer  Informational Tech.":      "Khoury",
    "Computer Engineering":               "Khoury",
    "Cybersecurity":                      "Khoury",
    "Data Science":                       "Khoury",
    "Computer Information Systm":         "Khoury",
    # College of Engineering
    "Engineering":                        "Engineering",
    "Electrical Engineering":             "Engineering",
    "Mechanical Engineering":             "Engineering",
    "Civil Engineering":                  "Engineering",
    "Chemical Engineering":               "Engineering",
    "Industrial Engineering":             "Engineering",
    "Materials Engineering":              "Engineering",
    "Engineering Technology":             "Engineering",
    "Electronics":                        "Engineering",
    "Electrical & Computer Engr":         "Engineering",
    "Mechanical & Industrial Eng":        "Engineering",
    "Civil & Environmental Eng":          "Engineering",
    "Bioengineering":                     "Engineering",
    "Industrial Technology":              "Engineering",
    # D'Amore-McKim School of Business
    "Business":                           "Business",
    "Business Administration":            "Business",
    "Finance":                            "Business",
    "Finance & Insurance":                "Business",
    "Accounting":                         "Business",
    "Accounting & Finance":               "Business",
    "Marketing":                          "Business",
    "Management":                         "Business",
    "Entrepreneurship":                   "Business",
    "International Business":             "Business",
    "Supply Chain Management":            "Business",
    "Operations Management":              "Business",
    "Managerial Science":                 "Business",
    "Organizational Behavior":            "Business",
    "Organizational Leadership":          "Business",
    "Human Resources Management":         "Business",
    "Leadership":                         "Business",
    # College of Science
    "Mathematics":                        "Science",
    "Physics":                            "Science",
    "Chemistry":                          "Science",
    "Biology":                            "Science",
    "Biochemistry":                       "Science",
    "Environmental Science":              "Science",
    "Environmental Studies":              "Science",
    "Marine Sciences":                    "Science",
    "Marine Biology":                     "Science",
    "Microbiology":                       "Science",
    "Biotechnology":                      "Science",
    "Geology":                            "Science",
    "Earth Science":                      "Science",
    "Biomedical":                         "Science",
    "Science":                            "Science",
    "Math":                               "Science",
    "Behavioral Neuroscience":            "Science",
    # College of Arts, Media and Design (CAMD)
    "Art":                                "CAMD",
    "Art History":                        "CAMD",
    "Architecture":                       "CAMD",
    "Communication Studies":              "CAMD",
    "Communication":                      "CAMD",
    "Communications":                     "CAMD",
    "Journalism":                         "CAMD",
    "Media":                              "CAMD",
    "Media Studies":                      "CAMD",
    "Graphic Design":                     "CAMD",
    "Design":                             "CAMD",
    "Music":                              "CAMD",
    "Music Technology":                   "CAMD",
    "Music Business":                     "CAMD",
    "Theater":                            "CAMD",
    "Game Design":                        "CAMD",
    "Fine Arts":                          "CAMD",
    "Visual Arts":                        "CAMD",
    "Cinema":                             "CAMD",
    "Photography":                        "CAMD",
    "Multimedia":                         "CAMD",
    "Creative Studies":                   "CAMD",
    # Bouvé College of Health Sciences
    "Health Science":                     "Health Sciences",
    "Health Sciences":                    "Health Sciences",
    "Nursing":                            "Health Sciences",
    "Pharmacy":                           "Health Sciences",
    "Physical Therapy":                   "Health Sciences",
    "Speech & Hearing Sciences":          "Health Sciences",
    "Speech Language Pathology":          "Health Sciences",
    "Health Management":                  "Health Sciences",
    "Health  Physical Education":         "Health Sciences",
    "Medicine":                           "Health Sciences",
    "Regulatory Affairs":                 "Health Sciences",
    "Counseling Psychology":              "Health Sciences",
    "Applied Psychology":                 "Health Sciences",
    # College of Social Sciences and Humanities (CSSH)
    "Political Science":                  "CSSH",
    "Economics":                          "CSSH",
    "History":                            "CSSH",
    "Psychology":                         "CSSH",
    "Sociology":                          "CSSH",
    "Philosophy":                         "CSSH",
    "English":                            "CSSH",
    "Writing":                            "CSSH",
    "Literature":                         "CSSH",
    "Linguistics":                        "CSSH",
    "Languages":                          "CSSH",
    "Modern Languages":                   "CSSH",
    "Spanish":                            "CSSH",
    "French":                             "CSSH",
    "Arabic":                             "CSSH",
    "Sign Language":                      "CSSH",
    "World Languages Center":             "CSSH",
    "Criminal Justice":                   "CSSH",
    "Anthropology":                       "CSSH",
    "Human Services":                     "CSSH",
    "Religious Studies":                  "CSSH",
    "Judaic Studies":                     "CSSH",
    "International Studies":              "CSSH",
    "International Affairs":              "CSSH",
    "International Politics":             "CSSH",
    "East Asian Studies":                 "CSSH",
    "Latin American Studies":             "CSSH",
    "African-American Studies":           "CSSH",
    "Women's Studies":                    "CSSH",
    "Women":                              "CSSH",
    "Social Science":                     "CSSH",
    "Public Policy":                      "CSSH",
    "Public Administration":              "CSSH",
    "Urban Studies":                      "CSSH",
    "Humanities":                         "CSSH",
    # College of Professional Studies
    "Education":                          "Professional Studies",
    "Professional Studies":               "Professional Studies",
    "Counseling & Educational Psych":     "Professional Studies",
    "Counseling amp Educational Psych":   "Professional Studies",
    "Counseling  Educational Psych":      "Professional Studies",
    # Law
    "Law":                                "Law",
}


def get_college(dept: str) -> str:
    if not isinstance(dept, str):
        return "Other"
    return COLLEGE_MAP.get(dept, "Other")


rmp_profs["college"] = rmp_profs["department"].apply(get_college)

# ──────────────────────────────────────────────
#  Build TRACE "overall" rating per instructor
# ──────────────────────────────────────────────
#  1. Build a lowercase full-name key in trace_courses
#  2. Get all courseIds + instructorIds per name
#  3. Filter trace_scores to "overall" questions
#  4. Compute weighted avg of mean scores (weighted by completed responses)
# ──────────────────────────────────────────────

# Normalise names for matching
trace_courses["_first"] = trace_courses["instructorFirstName"].astype(str).str.strip().str.lower()
trace_courses["_last"]  = trace_courses["instructorLastName"].astype(str).str.strip().str.lower()
trace_courses["_full"]  = trace_courses["_first"] + " " + trace_courses["_last"]

# Build TRACE department lookup: use the most recent department per instructor
# (highest termId = most recent term)
trace_courses["termId"] = pd.to_numeric(trace_courses["termId"], errors="coerce")
_dept_sorted = trace_courses.sort_values("termId", ascending=False).drop_duplicates(subset=["_full"])
trace_dept_lookup = dict(zip(_dept_sorted["_full"], _dept_sorted["departmentName"]))

# Filter trace_scores to only rows whose question mentions "overall"
trace_scores["question"] = trace_scores["question"].astype(str)
overall_scores = trace_scores[
    trace_scores["question"].str.lower().str.contains("overall", na=False)
].copy()

# The stored `mean` column is WRONG in the CSV — compute it from count_1..count_5
for col in ["count_1", "count_2", "count_3", "count_4", "count_5", "completed"]:
    overall_scores[col] = pd.to_numeric(overall_scores[col], errors="coerce").fillna(0).astype(int)

overall_scores["_total_responses"] = (
    overall_scores["count_1"] + overall_scores["count_2"] +
    overall_scores["count_3"] + overall_scores["count_4"] +
    overall_scores["count_5"]
)
overall_scores["_weighted_sum"] = (
    1 * overall_scores["count_1"] + 2 * overall_scores["count_2"] +
    3 * overall_scores["count_3"] + 4 * overall_scores["count_4"] +
    5 * overall_scores["count_5"]
)
# Avoid division by zero
overall_scores["mean"] = overall_scores.apply(
    lambda r: r["_weighted_sum"] / r["_total_responses"]
              if r["_total_responses"] > 0 else np.nan,
    axis=1,
)
overall_scores.dropna(subset=["mean"], inplace=True)

# Map courseId+instructorId → overall mean scores
# First get the set of (courseId, instructorId) per full name from trace_courses
instructor_courses = (
    trace_courses[["courseId", "instructorId", "_full"]]
    .drop_duplicates()
)

# Merge overall_scores with instructor_courses on courseId + instructorId
merged = overall_scores.merge(
    instructor_courses,
    on=["courseId", "instructorId"],
    how="inner",
)

# Weighted average per instructor name:
#   weight = _total_responses (actual number who answered the question)
#   value  = mean (computed from count_1..count_5)
def weighted_avg(group):
    w = group["_total_responses"]
    v = group["mean"]
    total_w = w.sum()
    if total_w == 0:
        return np.nan
    return (v * w).sum() / total_w

trace_avg_by_name = (
    merged
    .groupby("_full")
    .apply(weighted_avg, include_groups=False)
    .reset_index()
    .rename(columns={0: "trace_overall"})
)

# Build a lookup dict: lowercase full name → trace overall (1-5 scale)
trace_lookup = dict(zip(trace_avg_by_name["_full"], trace_avg_by_name["trace_overall"]))
print(f"[startup] Matched {len(trace_lookup)} instructors to TRACE overall scores")

# ──────────────────────────────────────────────
#  Build TRACE review count per instructor
# ──────────────────────────────────────────────
#  1. Get unique (courseId, instructorId, termId) combos from trace_courses per name
#  2. Match into trace_scores on (courseId, instructorId, termId)
#  3. Deduplicate: each (courseId, instructorId, termId) has many rows (one per question)
#     but `completed` is the same across all questions — take one row per combo
#  4. Sum `completed` across all combos for that instructor
# ──────────────────────────────────────────────

# Get unique course sections per instructor name
instructor_sections = (
    trace_courses[["courseId", "instructorId", "termId", "_full"]]
    .drop_duplicates(subset=["courseId", "instructorId", "termId"])
)

# Ensure trace_scores has numeric completed
trace_scores["completed"] = pd.to_numeric(trace_scores["completed"], errors="coerce")

# Deduplicate trace_scores to one row per (courseId, instructorId, termId)
# — all questions for the same section share the same `completed` count
scores_deduped = (
    trace_scores
    .drop_duplicates(subset=["courseId", "instructorId", "termId"])
    [["courseId", "instructorId", "termId", "completed"]]
)

# Merge with instructor names
trace_reviews_merged = scores_deduped.merge(
    instructor_sections,
    on=["courseId", "instructorId", "termId"],
    how="inner",
)

# Sum completed per instructor name
trace_review_counts = (
    trace_reviews_merged
    .groupby("_full")["completed"]
    .sum()
    .reset_index()
    .rename(columns={"completed": "trace_reviews"})
)

trace_reviews_lookup = dict(zip(trace_review_counts["_full"], trace_review_counts["trace_reviews"]))
print(f"[startup] Computed TRACE review counts for {len(trace_reviews_lookup)} instructors")

# ──────────────────────────────────────────────
#  Attach TRACE overall + avg rating + combined reviews to rmp_profs
# ──────────────────────────────────────────────
#  RMP rating is on a 1-5 scale.  TRACE mean is also 1-5.
#  Blended = average of both when TRACE is available,
#  otherwise fall back to just RMP.
#  Total reviews = RMP num_ratings + TRACE completed responses
# ──────────────────────────────────────────────
rmp_profs["_name_key"] = rmp_profs["name"].astype(str).str.strip().str.lower()
rmp_profs["trace_overall"] = rmp_profs["_name_key"].map(trace_lookup)
rmp_profs["trace_reviews"] = rmp_profs["_name_key"].map(trace_reviews_lookup).fillna(0).astype(int)
rmp_profs["trace_dept"] = rmp_profs["_name_key"].map(trace_dept_lookup)
rmp_profs["total_reviews"] = rmp_profs["num_ratings"].astype(int) + rmp_profs["trace_reviews"]

def compute_avg_rating(r):
    has_rmp = r["num_ratings"] > 0 and r["rating"] > 0
    has_trace = pd.notna(r["trace_overall"]) and r["trace_reviews"] > 0

    if has_rmp and has_trace:
        return round((r["rating"] + r["trace_overall"]) / 2, 2)
    elif has_trace:
        return round(float(r["trace_overall"]), 2)
    elif has_rmp:
        return round(float(r["rating"]), 2)
    else:
        return 0.0

rmp_profs["avg_rating"] = rmp_profs.apply(compute_avg_rating, axis=1)

has_trace = rmp_profs["trace_overall"].notna().sum()
print(f"[startup] {has_trace}/{len(rmp_profs)} RMP professors matched to TRACE data")

# ──────────────────────────────────────────────
#  Precompute stats
# ──────────────────────────────────────────────
#  Professors  = unique full names from trace_courses
#  Courses     = unique course codes from displayName (e.g. ACCT6228, not per-section)
#  Comments    = rmp_reviews with non-empty comments + trace_comments rows (1 row = 1 comment)
#  Departments = unique departmentName in trace_courses
# ──────────────────────────────────────────────
stat_professor_count = trace_courses["_full"].nunique()

# Extract course code before the colon: "ACCT6228:02 (Name) - Prof" → "ACCT6228"
trace_courses["_course_code"] = trace_courses["displayName"].astype(str).str.split(":").str[0]
stat_course_count = trace_courses["_course_code"].nunique()

# Comments: RMP reviews with a non-empty comment + all trace_comments rows
rmp_comment_count = int(rmp_reviews["comment"].dropna().astype(str).str.strip().ne("").sum())
stat_total_comments = rmp_comment_count + len(trace_comments)

stat_department_count = trace_courses["departmentName"].nunique()

print(f"[stats] {stat_professor_count} professors, {stat_course_count} courses, "
      f"{stat_total_comments} comments ({rmp_comment_count} RMP + {len(trace_comments)} TRACE), "
      f"{stat_department_count} departments")


# ──────────────────────────────────────────────
#  API Routes
# ──────────────────────────────────────────────
@app.route("/api/stats")
def stats():
    return jsonify([
        {"label": "Professors",  "value": friendly_count(stat_professor_count)},
        {"label": "Courses",     "value": friendly_count(stat_course_count)},
        {"label": "Comments",    "value": friendly_count(stat_total_comments)},
        {"label": "Departments", "value": friendly_count(stat_department_count)},
    ])


@app.route("/api/colleges")
def colleges():
    counts = rmp_profs["college"].value_counts()
    college_list = sorted([c for c, n in counts.items() if n >= 5 and c != "Other"])
    return jsonify(college_list)


@app.route("/api/goat-professors")
def goat_professors():
    college     = request.args.get("college", "Khoury")
    limit       = int(request.args.get("limit", "10"))
    min_reviews = int(request.args.get("min_reviews", "10"))

    # Small colleges get no minimum review requirement
    NO_MIN_COLLEGES = {"Law", "Professional Studies"}
    if college in NO_MIN_COLLEGES:
        min_reviews = 0

    subset = rmp_profs[rmp_profs["college"] == college].copy()
    if college in NO_MIN_COLLEGES:
        # Require at least 3 total reviews across both sources
        subset = subset[subset["total_reviews"] >= 3]
    else:
        subset = subset[
            (subset["num_ratings"] >= min_reviews) &
            (subset["trace_reviews"] >= min_reviews) &
            (subset["trace_overall"].notna())
        ]

    # Sort by avg_rating desc, then total_reviews desc as tiebreak
    subset = subset.sort_values(
        ["avg_rating", "total_reviews"], ascending=[False, False]
    )
    top = subset.head(limit)

    result = []
    for _, row in top.iterrows():
        has_rmp   = int(row["num_ratings"]) > 0 and row["rating"] > 0
        has_trace = pd.notna(row["trace_overall"]) and int(row["trace_reviews"]) > 0
        result.append({
            "name":           row["name"],
            "dept":           row["trace_dept"] if pd.notna(row["trace_dept"]) else row["department"],
            "rmpRating":      round(float(row["rating"]), 2) if has_rmp else None,
            "traceRating":    round(float(row["trace_overall"]), 2) if has_trace else None,
            "avgRating":  round(float(row["avg_rating"]), 2),
            "rmpReviews":     int(row["num_ratings"]),
            "traceReviews":   int(row["trace_reviews"]),
            "totalReviews":   int(row["total_reviews"]),
            "url":            row.get("professor_url", ""),
        })

    return jsonify(result)


@app.route("/api/random-professor")
def random_professor():
    pool = rmp_profs[rmp_profs["num_ratings"] >= 3]
    row = pool.sample(1).iloc[0]
    has_rmp   = int(row["num_ratings"]) > 0 and row["rating"] > 0
    has_trace = pd.notna(row["trace_overall"]) and int(row["trace_reviews"]) > 0
    return jsonify({
        "name":          row["name"],
        "dept":          row["trace_dept"] if pd.notna(row["trace_dept"]) else row["department"],
        "rmpRating":     round(float(row["rating"]), 2) if has_rmp else None,
        "traceRating":   round(float(row["trace_overall"]), 2) if has_trace else None,
        "avgRating": round(float(row["avg_rating"]), 2),
        "rmpReviews":    int(row["num_ratings"]),
        "traceReviews":  int(row["trace_reviews"]),
        "totalReviews":  int(row["total_reviews"]),
        "url":           row.get("professor_url", ""),
        "college":       row["college"],
    })


# ──────────────────────────────────────────────
#  Precompute search indexes for autocomplete
# ──────────────────────────────────────────────
# Professors: merge RMP + TRACE so all instructors are searchable

# RMP professors (already have avg_rating, trace_dept, etc.)
rmp_for_search = rmp_profs[["name", "trace_dept", "avg_rating", "total_reviews"]].copy()
rmp_for_search["_name_lower"] = rmp_for_search["name"].astype(str).str.strip().str.lower()
rmp_for_search["dept_display"] = rmp_for_search["trace_dept"]  # only TRACE dept

# TRACE-only professors (not in RMP)
trace_unique = trace_courses[["_full", "departmentName"]].drop_duplicates(subset=["_full"])
trace_unique = trace_unique.rename(columns={"_full": "_name_lower", "departmentName": "dept_display"})
# Exclude those already in RMP
rmp_names = set(rmp_for_search["_name_lower"])
trace_only = trace_unique[~trace_unique["_name_lower"].isin(rmp_names)].copy()

# Build proper name (title case) from the lowercase key
trace_only["name"] = trace_only["_name_lower"].str.title()
trace_only["avg_rating"] = 0.0  # no rating data
trace_only["total_reviews"] = 0

# Combine
prof_search = pd.concat([
    rmp_for_search[["name", "_name_lower", "dept_display", "avg_rating", "total_reviews"]],
    trace_only[["name", "_name_lower", "dept_display", "avg_rating", "total_reviews"]],
], ignore_index=True)

# Drop any without a TRACE department
prof_search = prof_search[prof_search["dept_display"].notna()]

# Split into individual name parts for whole-word matching
prof_search["_name_parts"] = prof_search["_name_lower"].str.split()
prof_search = prof_search.drop_duplicates(subset=["_name_lower"])

print(f"[search] Indexed {len(prof_search)} professors ({len(rmp_for_search)} RMP + {len(trace_only)} TRACE-only)")

# Courses: unique course codes with their name
# displayName format: "ACCT6228:02 (Contmp Issues Accountng Theory) - Michael Rezuke"

def parse_course(display_name):
    m = re.match(r"^([A-Z]+\d+):\d+\s+\((.+?)\)", str(display_name))
    if m:
        return m.group(1), m.group(2)
    return None, None

trace_courses["_parsed"] = trace_courses["displayName"].apply(parse_course)
trace_courses["_code"] = trace_courses["_parsed"].apply(lambda x: x[0])
trace_courses["_cname"] = trace_courses["_parsed"].apply(lambda x: x[1])

course_search = (
    trace_courses[trace_courses["_code"].notna()]
    [["_code", "_cname", "departmentName"]]
    .drop_duplicates(subset=["_code"])
    .copy()
)
course_search["_search_lower"] = (
    course_search["_code"].str.lower() + " " + course_search["_cname"].astype(str).str.lower()
)

print(f"[search] Indexed {len(prof_search)} professors, {len(course_search)} courses")


@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip().lower()
    search_type = request.args.get("type", "Professor")
    limit = int(request.args.get("limit", "3"))

    if len(q) < 2:
        return jsonify([])

    if search_type == "Professor":
        # Tier 1: query matches a whole first or last name exactly
        exact_word = prof_search[prof_search["_name_parts"].apply(
            lambda parts: q in parts if isinstance(parts, list) else False
        )]
        # Tier 2: query matches the start of any name part (but not exact)
        starts_word = prof_search[
            prof_search["_name_parts"].apply(
                lambda parts: any(p.startswith(q) for p in parts) if isinstance(parts, list) else False
            ) &
            ~prof_search.index.isin(exact_word.index)
        ]
        # Tier 3: substring match anywhere in full name
        contains = prof_search[
            prof_search["_name_lower"].str.contains(q, na=False) &
            ~prof_search.index.isin(exact_word.index) &
            ~prof_search.index.isin(starts_word.index)
        ]
        matches = pd.concat([
            exact_word.sort_values("total_reviews", ascending=False),
            starts_word.sort_values("total_reviews", ascending=False),
            contains.sort_values("total_reviews", ascending=False),
        ]).head(limit)

        results = []
        for _, r in matches.iterrows():
            results.append({
                "type":   "professor",
                "name":   r["name"],
                "dept":   r["dept_display"],
                "rating": round(float(r["avg_rating"]), 2) if r["avg_rating"] > 0 else None,
            })
        return jsonify(results)

    else:
        # Match courses: prioritize code prefix, then code contains, then name contains
        code_starts = course_search[course_search["_code"].str.lower().str.startswith(q, na=False)]
        code_contains = course_search[
            course_search["_code"].str.lower().str.contains(q, na=False) &
            ~course_search["_code"].str.lower().str.startswith(q, na=False)
        ]
        name_contains = course_search[
            course_search["_cname"].astype(str).str.lower().str.contains(q, na=False) &
            ~course_search["_code"].str.lower().str.contains(q, na=False)
        ]
        matches = pd.concat([code_starts, code_contains, name_contains]).head(limit)

        results = []
        for _, r in matches.iterrows():
            results.append({
                "type": "course",
                "code": r["_code"],
                "name": r["_cname"],
                "dept": r["departmentName"],
            })
        return jsonify(results)


if __name__ == "__main__":
    print(f"Loaded {len(rmp_profs)} RMP professors, {len(rmp_reviews)} RMP reviews")
    print(f"Stats → {stat_professor_count} professors, {stat_course_count} courses, "
          f"{stat_total_comments} comments, {stat_department_count} departments")
    app.run(debug=True, port=5001)