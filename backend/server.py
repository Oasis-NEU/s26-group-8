"""
Backend API server for NEU Professor Ratings.
Place this file in: backend/server.py

Install deps:  pip install flask flask-cors pandas
Run:           python backend/server.py
"""

import os, math
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
# ──────────────────────────────────────────────
COLLEGE_MAP = {
    # Khoury College of Computer Sciences
    "Computer Science":           "Khoury",
    "Information Systems":        "Khoury",
    "Cybersecurity":              "Khoury",
    "Data Science":               "Khoury",
    "Computer Information Systm": "Khoury",
    "Information Science":        "Khoury",
    # College of Engineering
    "Electrical Engineering":     "Engineering",
    "Electrical & Computer Engr": "Engineering",
    "Mechanical Engineering":     "Engineering",
    "Mechanical & Industrial Eng":"Engineering",
    "Civil Engineering":          "Engineering",
    "Civil & Environmental Eng":  "Engineering",
    "Chemical Engineering":       "Engineering",
    "Bioengineering":             "Engineering",
    "Engineering":                "Engineering",
    "Industrial Engineering":     "Engineering",
    # D'Amore-McKim School of Business
    "Finance":                    "Business",
    "Finance & Insurance":        "Business",
    "Accounting":                 "Business",
    "Accounting & Finance":       "Business",
    "Marketing":                  "Business",
    "Management":                 "Business",
    "International Business":     "Business",
    "Business":                   "Business",
    "Business Administration":    "Business",
    "Entrepreneurship":           "Business",
    "Supply Chain Management":    "Business",
    # College of Science
    "Physics":                    "Science",
    "Chemistry":                  "Science",
    "Biology":                    "Science",
    "Mathematics":                "Science",
    "Math":                       "Science",
    "Marine Biology":             "Science",
    "Biochemistry":               "Science",
    "Environmental Science":      "Science",
    "Behavioral Neuroscience":    "Science",
    # College of Arts, Media and Design (CAMD)
    "Art":                        "CAMD",
    "Music":                      "CAMD",
    "Communications":             "CAMD",
    "Communication Studies":      "CAMD",
    "Journalism":                 "CAMD",
    "Media":                      "CAMD",
    "Graphic Design":             "CAMD",
    "Architecture":               "CAMD",
    "Theater":                    "CAMD",
    "Game Design":                "CAMD",
    # Bouvé College of Health Sciences
    "Nursing":                    "Health Sciences",
    "Pharmacy":                   "Health Sciences",
    "Health Sciences":            "Health Sciences",
    "Physical Therapy":           "Health Sciences",
    "Speech Language Pathology":  "Health Sciences",
    "Counseling Psychology":      "Health Sciences",
    "Applied Psychology":         "Health Sciences",
    # College of Social Sciences and Humanities (CSSH)
    "Political Science":          "CSSH",
    "Economics":                  "CSSH",
    "History":                    "CSSH",
    "Psychology":                 "CSSH",
    "Sociology":                  "CSSH",
    "Philosophy":                 "CSSH",
    "English":                    "CSSH",
    "Languages":                  "CSSH",
    "Anthropology":               "CSSH",
    "Linguistics":                "CSSH",
    "Criminal Justice":           "CSSH",
    "World Languages Center":     "CSSH",
    "Human Services":             "CSSH",
    "Religious Studies":          "CSSH",
    # College of Professional Studies
    "Professional Studies":       "Professional Studies",
    "Education":                  "Professional Studies",
    # Law
    "Law":                        "Law",
}


def get_college(dept: str) -> str:
    if not isinstance(dept, str):
        return "Other"
    if dept in COLLEGE_MAP:
        return COLLEGE_MAP[dept]
    dept_lower = dept.lower()
    for key, college in COLLEGE_MAP.items():
        if key.lower() in dept_lower or dept_lower in key.lower():
            return college
    return "Other"


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

# Filter trace_scores to only rows whose question mentions "overall"
trace_scores["question"] = trace_scores["question"].astype(str)
overall_scores = trace_scores[
    trace_scores["question"].str.lower().str.contains("overall", na=False)
].copy()
overall_scores["mean"] = pd.to_numeric(overall_scores["mean"], errors="coerce")
overall_scores["completed"] = pd.to_numeric(overall_scores["completed"], errors="coerce")
overall_scores.dropna(subset=["mean", "completed"], inplace=True)

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
#   weight = completed (number of students who responded)
#   value  = mean (the score for that section)
def weighted_avg(group):
    w = group["completed"]
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
#  Attach TRACE overall + blended rating + combined reviews to rmp_profs
# ──────────────────────────────────────────────
#  RMP rating is on a 1-5 scale.  TRACE mean is also 1-5.
#  Blended = average of both when TRACE is available,
#  otherwise fall back to just RMP.
#  Total reviews = RMP num_ratings + TRACE completed responses
# ──────────────────────────────────────────────
rmp_profs["_name_key"] = rmp_profs["name"].astype(str).str.strip().str.lower()
rmp_profs["trace_overall"] = rmp_profs["_name_key"].map(trace_lookup)
rmp_profs["trace_reviews"] = rmp_profs["_name_key"].map(trace_reviews_lookup).fillna(0).astype(int)
rmp_profs["total_reviews"] = rmp_profs["num_ratings"].astype(int) + rmp_profs["trace_reviews"]

rmp_profs["blended_rating"] = rmp_profs.apply(
    lambda r: round((r["rating"] + r["trace_overall"]) / 2, 2)
              if pd.notna(r["trace_overall"])
              else round(r["rating"], 2),
    axis=1,
)

has_trace = rmp_profs["trace_overall"].notna().sum()
print(f"[startup] {has_trace}/{len(rmp_profs)} RMP professors matched to TRACE data")

# ──────────────────────────────────────────────
#  Precompute stats
# ──────────────────────────────────────────────
#  Professors  = unique full names from trace_courses
#  Courses     = unique courseId in trace_courses (each section has its own courseId)
#  Comments    = rmp_reviews with non-empty comments + trace_comments rows
#  Departments = unique departmentName in trace_courses
# ──────────────────────────────────────────────
stat_professor_count = trace_courses["_full"].nunique()
stat_course_count    = trace_courses["courseId"].nunique()

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
    subset = subset[
        (subset["num_ratings"] >= min_reviews) &
        (subset["trace_reviews"] >= min_reviews) &
        (subset["trace_overall"].notna())
    ]

    # Sort by blended_rating desc, then total_reviews desc as tiebreak
    subset = subset.sort_values(
        ["blended_rating", "total_reviews"], ascending=[False, False]
    )
    top = subset.head(limit)

    result = []
    for _, row in top.iterrows():
        has_trace_data = pd.notna(row["trace_overall"])
        result.append({
            "name":           row["name"],
            "dept":           row["department"],
            "rmpRating":      round(float(row["rating"]), 2),
            "traceRating":    round(float(row["trace_overall"]), 2) if has_trace_data else None,
            "blendedRating":  round(float(row["blended_rating"]), 2),
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
    has_trace_data = pd.notna(row["trace_overall"])
    return jsonify({
        "name":          row["name"],
        "dept":          row["department"],
        "rmpRating":     round(float(row["rating"]), 2),
        "traceRating":   round(float(row["trace_overall"]), 2) if has_trace_data else None,
        "blendedRating": round(float(row["blended_rating"]), 2),
        "rmpReviews":    int(row["num_ratings"]),
        "traceReviews":  int(row["trace_reviews"]),
        "totalReviews":  int(row["total_reviews"]),
        "url":           row.get("professor_url", ""),
        "college":       row["college"],
    })


if __name__ == "__main__":
    print(f"Loaded {len(rmp_profs)} RMP professors, {len(rmp_reviews)} RMP reviews")
    print(f"Stats → {stat_professor_count} professors, {stat_course_count} courses, "
          f"{stat_total_comments} comments, {stat_department_count} departments")
    app.run(debug=True, port=5001)