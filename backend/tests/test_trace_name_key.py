"""Tests for the RMP->TRACE name resolution and its persistence.

Background: RMP and TRACE spell the same professor differently, so precompute
falls back to a fuzzy surname match. The old rule accepted a single shared
initial and took the first candidate it saw, and — worse — never recorded which
TRACE name it picked. The read path joins on name_key, so those professors
displayed a TRACE rating with zero courses behind it (99 of them in prod).

Two halves are tested here:
  * fuzzy_trace_match  — the match must be strict and must refuse ambiguity.
  * trace_key + read path — the resolved name must actually be used, while RMP
    lookups keep using the RMP name.
"""

import pytest

from precompute import absorbed_trace_key, fuzzy_trace_match
from professor_full import build_full, trace_key


# ── the match rule ──────────────────────────────────────────────────────────

DEPTS = {
    "jonathan smith": "Computer Science",
    "julio smith": "Chemistry",
    "julian smith": "Computer Science",
    "margaret heckman": "Journalism",
    "prasanth george": "Mathematics",
}


def _by_last(*names):
    out = {}
    for n in names:
        out.setdefault(n.split()[-1], []).append(n)
    return out


def test_shared_prefix_of_three_matches():
    # "jon" -> "jonathan" is the case the fallback exists for.
    assert fuzzy_trace_match("jon smith", _by_last("jonathan smith")) == "jonathan smith"


def test_exact_first_name_matches():
    assert fuzzy_trace_match("jonathan smith", _by_last("jonathan smith")) == "jonathan smith"


def test_single_initial_is_rejected():
    # The old rule accepted this via startswith, pulling in a stranger.
    assert fuzzy_trace_match("j smith", _by_last("jonathan smith")) is None


def test_two_character_prefix_is_rejected():
    assert fuzzy_trace_match("jo smith", _by_last("jonathan smith")) is None


def test_ambiguous_candidates_are_rejected():
    # Old behaviour: break on the first hit, so this silently returned whichever
    # name happened to be first in the list.
    got = fuzzy_trace_match("jon smith", _by_last("jonathan smith", "jonquil smith"))
    assert got is None


def test_nickname_without_shared_prefix_is_dropped():
    # meg/margaret share no prefix. The old rule matched neither, yet Meg
    # Heckman still carried a 4.81 — it came from a different Heckman.
    assert fuzzy_trace_match("meg heckman", _by_last("margaret heckman")) is None


def test_different_surname_never_matches():
    assert fuzzy_trace_match("jonathan jones", _by_last("jonathan smith")) is None


def test_single_word_name_is_rejected():
    assert fuzzy_trace_match("smith", _by_last("jonathan smith")) is None


def test_college_mismatch_is_rejected():
    # Computer Science -> Khoury, Chemistry -> Science. The second candidate is
    # there only so the surname isn't unique — a unique surname is itself a
    # corroboration and would override the college veto.
    got = fuzzy_trace_match("jul smith", _by_last("julio smith", "bob smith"),
                            rmp_dept="Computer Science", trace_dept_lookup=DEPTS)
    assert got is None


def test_same_college_is_accepted():
    got = fuzzy_trace_match("jon smith", _by_last("jonathan smith"),
                            rmp_dept="Computer Science", trace_dept_lookup=DEPTS)
    assert got == "jonathan smith"


def test_unknown_department_does_not_block_a_match():
    # get_college returns "Other" for unmapped departments; that must not be
    # treated as a mismatch or we would drop every professor in a new dept.
    got = fuzzy_trace_match("jon smith", _by_last("jonathan smith"),
                            rmp_dept="Basket Weaving", trace_dept_lookup=DEPTS)
    assert got == "jonathan smith"


def test_same_college_corroborates_a_common_surname():
    # Surname not unique, no course data — the matching college is what carries it.
    got = fuzzy_trace_match("jul smith", _by_last("julio smith", "bob smith"),
                            rmp_dept="Chemistry", trace_dept_lookup=DEPTS)
    assert got == "julio smith"


def test_two_plausible_first_names_are_ambiguous_even_with_a_college_hint():
    # Both "julio" and "julian" are plausible expansions of "jul"; a college
    # hint must not be used to pick one of two equally plausible people.
    got = fuzzy_trace_match("jul smith", _by_last("julio smith", "julian smith"),
                            rmp_dept="Chemistry", trace_dept_lookup=DEPTS)
    assert got is None


def test_punctuation_in_first_name_is_ignored():
    # "j. timothy sage" and "j timothy sage" are the same person.
    assert fuzzy_trace_match("j. timothy sage", _by_last("j timothy sage")) == "j timothy sage"


def test_hyphenated_trace_first_name_matches_on_prefix():
    # "kai" / "kai-tak" -> normalises to "kaitak", 3-char prefix holds.
    assert fuzzy_trace_match("kai wan", _by_last("kai-tak wan")) == "kai-tak wan"


def test_exact_first_name_shorter_than_prefix_minimum_still_matches():
    # "or" is 2 chars, below FUZZY_MIN_PREFIX, but an exact match needs no prefix rule.
    assert fuzzy_trace_match("or aharon", _by_last("or beit aharon")) == "or beit aharon"


def test_exact_first_name_survives_college_disagreement():
    # Middle name in TRACE, departments disagree — still obviously the same person.
    depts = {"alina ionica lungeanu": "Art and Design"}
    got = fuzzy_trace_match("alina lungeanu", _by_last("alina ionica lungeanu"),
                            rmp_dept="Business Administration", trace_dept_lookup=depts)
    assert got == "alina ionica lungeanu"


def test_prefix_match_blocked_by_college_disagreement():
    # "dan"/"daniel" is weak evidence, so a college disagreement sinks it.
    depts = {"daniel koloski": "Mechanical Engineering"}
    got = fuzzy_trace_match("dan koloski", _by_last("daniel koloski", "wanda koloski"),
                            rmp_dept="Business Administration", trace_dept_lookup=depts)
    assert got is None


def test_unique_surname_corroborates_a_prefix_match():
    # A rare surname plus a matching prefix is near-certain: this is what brings
    # "chai mutsalklisana" -> "chaiyaporn mutsalklisana" back.
    got = fuzzy_trace_match("chai mutsalklisana", _by_last("chaiyaporn mutsalklisana"))
    assert got == "chaiyaporn mutsalklisana"


def test_exact_and_prefix_candidate_together_are_ambiguous():
    # "kai" fits both "kai yee wan" (exact) and "kai-tak wan" (prefix). Calling
    # one of them exact does not make the choice unambiguous, so refuse.
    got = fuzzy_trace_match("kai wan", _by_last("kai yee wan", "kai-tak wan"))
    assert got is None


# ── course subjects: the strongest disambiguator ────────────────────────────

def test_conflicting_course_subjects_veto_a_prefix_match():
    # The real false positive: "yan li" teaches HIST, "yaning li" teaches ME.
    got = fuzzy_trace_match("yan li", _by_last("yaning li"),
                            rmp_subjects={"yan li": {"HIST", "ASNS"}},
                            trace_subjects={"yaning li": {"ME"}})
    assert got is None


def test_conflicting_course_subjects_veto_even_an_exact_first_name():
    got = fuzzy_trace_match("rob james", _by_last("rob james jr"),
                            rmp_subjects={"rob james": {"FIN"}},
                            trace_subjects={"rob james jr": {"MUSC"}})
    assert got is None


def test_shared_subject_corroborates_across_a_college_disagreement():
    depts = {"donald king": "Chemistry"}
    got = fuzzy_trace_match("don king", _by_last("donald king", "wanda king"),
                            rmp_dept="Computer Science", trace_dept_lookup=depts,
                            rmp_subjects={"don king": {"MATH", "CALC"}},
                            trace_subjects={"donald king": {"MATH"}})
    assert got == "donald king"


def test_subject_abbreviation_mismatch_is_not_a_conflict():
    # BIO/BIOL and HIS/HIST are the same subject spelled two ways.
    got = fuzzy_trace_match("celine esch", _by_last("celine de esch", "bob esch"),
                            rmp_subjects={"celine esch": {"BIO"}},
                            trace_subjects={"celine de esch": {"BIOL"}})
    assert got == "celine de esch"


def test_missing_subject_data_does_not_veto():
    # Most RMP professors have no parseable course codes; absence must not block.
    got = fuzzy_trace_match("greg aloupis", _by_last("gregory aloupis"),
                            rmp_subjects={}, trace_subjects={"gregory aloupis": {"CS"}})
    assert got == "gregory aloupis"


def test_two_exact_candidates_are_still_ambiguous():
    # Same normalised first name twice (punctuation differs) -> refuse.
    assert fuzzy_trace_match("j. smith", _by_last("j smith", "j. smith")) is None


# ── trace_key resolution ────────────────────────────────────────────────────

def test_trace_key_prefers_the_recorded_trace_name():
    assert trace_key({"name_key": "meg heckman",
                      "trace_name_key": "margaret heckman"}) == "margaret heckman"


def test_trace_key_falls_back_when_null():
    assert trace_key({"name_key": "olin guha", "trace_name_key": None}) == "olin guha"


def test_trace_key_falls_back_when_column_absent():
    # Catalog built before the column existed — must not raise.
    assert trace_key({"name_key": "olin guha"}) == "olin guha"


# ── no duplicate catalog row per fuzzy match ────────────────────────────────

def test_exactly_matched_trace_name_gets_no_second_row():
    assert absorbed_trace_key("olin guha", {"olin guha"}, set()) is True


def test_fuzzy_matched_trace_name_gets_no_second_row():
    # The duplicate: "meg heckman" (RMP row) already carries margaret's TRACE
    # scores, so a "margaret heckman" TRACE-only row would double her on the
    # leaderboard with the same reviews behind both entries.
    assert absorbed_trace_key("margaret heckman", {"meg heckman"},
                              {"margaret heckman"}) is True


def test_genuinely_trace_only_professor_still_gets_a_row():
    assert absorbed_trace_key("prasanth george", {"meg heckman"},
                              {"margaret heckman"}) is False


def test_no_fuzzy_matches_leaves_behaviour_unchanged():
    # Empty fuzzy set must reduce to the original rmp_name_keys check.
    assert absorbed_trace_key("margaret heckman", {"meg heckman"}, set()) is False


# ── the read path actually uses it ──────────────────────────────────────────

class KeyRecordingQuery:
    """Records (sql, params) so tests can assert which name_key each table
    was queried with."""

    def __init__(self, name_key, trace_name_key):
        self.calls = []
        self._nk = name_key
        self._tnk = trace_name_key

    def _rows_for(self, sql):
        s = sql.lower()
        if "from professors_catalog" in s:
            return [{"name": "Meg Heckman", "slug": "meg-heckman",
                     "name_key": self._nk, "trace_name_key": self._tnk,
                     "department": "Journalism", "rmp_rating": 5.0,
                     "trace_rating": 4.81, "avg_rating": 4.91, "difficulty": None,
                     "would_take_again_pct": None, "total_reviews": 112,
                     "professor_url": None, "image_url": None, "avg_hours": None}]
        if "from rmp_reviews" in s:
            return [{"course": "JRNL1150", "quality": 5, "difficulty": 2,
                     "date": "2024", "tags": "", "attendance": "", "grade": "A",
                     "textbook": "", "online_class": "", "comment": "Great."}]
        if "from trace_comments" in s:
            return [{"tc_term_id": 901, "tc_course_id": 1, "question": "Comments",
                     "comment": "Excellent."}]
        if "from trace_scores" in s:
            return [{"course_id": 1, "term_id": 901, "display_name": "JRNL1150: News",
                     "question": "Overall rating", "mean": 4.81, "count_1": 0,
                     "count_2": 0, "count_3": 1, "count_4": 3, "count_5": 8,
                     "completed": 12}]
        if "from trace_courses" in s:
            return [{"course_id": 1, "term_id": 901, "term_title": "Fall 2024",
                     "department_name": "Journalism", "display_name": "JRNL1150: News",
                     "section": "1", "enrollment": 30, "instructor_id": 7}]
        return []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        return self._rows_for(sql)

    def query_one(self, sql, params=None):
        self.calls.append((sql, params))
        rows = self._rows_for(sql)
        return rows[0] if rows else None

    def keys_used_against(self, table):
        """Every param value passed to a query touching `table`."""
        out = []
        for sql, params in self.calls:
            if table.lower() in sql.lower() and params:
                out.extend(p for p in params if isinstance(p, str))
        return out


def _build(name_key="meg heckman", trace_name_key="margaret heckman"):
    rq = KeyRecordingQuery(name_key, trace_name_key)
    data = build_full("meg-heckman", rq.query, rq.query_one, sanitize=lambda t: t,
                      fetch_reddit_mentions=lambda slug, q: [], is_authed=False)
    return data, rq


def test_trace_courses_queried_with_the_trace_name():
    _, rq = _build()
    assert "margaret heckman" in rq.keys_used_against("from trace_courses")
    assert "meg heckman" not in rq.keys_used_against("from trace_courses")


def test_trace_scores_queried_with_the_trace_name():
    _, rq = _build()
    assert "margaret heckman" in rq.keys_used_against("from trace_scores")


def test_rmp_reviews_still_queried_with_the_rmp_name():
    # The whole point of keeping two keys: RMP data is not under the TRACE name.
    _, rq = _build()
    assert rq.keys_used_against("from rmp_reviews") == ["meg heckman"]


def test_fuzzy_matched_professor_now_has_courses():
    # The regression itself: rating shown, nothing behind it.
    data, _ = _build()
    assert data["traceRating"] == 4.81
    assert len(data["traceCourses"]) == 1, "TRACE rating with no courses is the bug"
    assert data["traceRatingCounts"], "rating distribution must resolve too"


def test_exact_match_professor_is_unaffected():
    # trace_name_key NULL -> everything keeps using name_key, as before.
    _, rq = _build(name_key="olin guha", trace_name_key=None)
    assert rq.keys_used_against("from trace_courses") == ["olin guha"]
    assert rq.keys_used_against("from rmp_reviews") == ["olin guha"]


# ── the Ask pipeline uses it too ─────────────────────────────────────────────

class FactsRecorder:
    """Records which name_key each table in fetch_facts was queried with."""

    def __init__(self, trace_name_key):
        self.calls = []
        self._tnk = trace_name_key

    def query_one(self, sql, params=None):
        self.calls.append((sql, params))
        if "from professors_catalog" in sql.lower():
            return {"slug": "meg-heckman", "name_key": "meg heckman",
                    "trace_name_key": self._tnk, "name": "Meg Heckman",
                    "department": "Journalism", "rmp_rating": 5.0,
                    "trace_rating": 4.81, "avg_rating": 4.91, "difficulty": None,
                    "would_take_again_pct": None, "total_reviews": 112,
                    "avg_hours": None}
        return {"cnt": 7}

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        return [{"display_name": "JRNL1150:01 (Journalism 1)"}]

    def keys_used_against(self, table):
        out = []
        for sql, params in self.calls:
            if table.lower() in sql.lower() and params:
                out.extend(p for p in params if isinstance(p, str))
        return out


def _facts(trace_name_key="margaret heckman"):
    from rag.chat_retrieve import fetch_facts
    rec = FactsRecorder(trace_name_key)
    facts = fetch_facts("meg-heckman", rec.query_one, rec.query)
    return facts, rec


def test_ask_course_list_uses_the_trace_name():
    # Otherwise Ask answers "no courses on record" for a professor whose profile
    # page lists them correctly.
    _, rec = _facts()
    assert rec.keys_used_against("from trace_courses") == ["margaret heckman"]


def test_ask_comment_count_splits_the_two_keys():
    _, rec = _facts()
    keys = rec.keys_used_against("from rmp_reviews")
    assert "meg heckman" in keys, "RMP comments live under the RMP name"
    assert "margaret heckman" in keys, "TRACE comments live under the TRACE name"


def test_ask_falls_back_for_exact_matches():
    _, rec = _facts(trace_name_key=None)
    assert rec.keys_used_against("from trace_courses") == ["meg heckman"]


def test_ask_selects_the_column_it_needs():
    # A column list that omits trace_name_key silently reintroduces the bug.
    _, rec = _facts()
    catalog_sql = next(s for s, _ in rec.calls if "professors_catalog" in s)
    assert "trace_name_key" in catalog_sql


# ── pre-flight guard: refuse before anything is dropped ─────────────────────

from precompute import (  # noqa: E402
    CATALOG_COLUMNS,
    CATALOG_INSERT_SQL,
    catalog_row,
    unreachable_trace_rows,
)


def _row(name_key, trace_rating, trace_name_key):
    """A catalog row built by name, so this helper cannot drift out of step with
    the guard the way two copies of `row[7]` silently would."""
    return catalog_row(name_key=name_key, trace_rating=trace_rating,
                       trace_name_key=trace_name_key)


# ── the schema the guard reads by position ──────────────────────────────────

def test_the_insert_names_exactly_the_columns_a_row_carries():
    """The guard indexes rows positionally, so an INSERT that disagrees with
    CATALOG_COLUMNS would have it checking the wrong field with nothing failing.
    One list, used by both, is what makes the positions trustworthy."""
    named = CATALOG_INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    assert [c.strip() for c in named.split(",")] == list(CATALOG_COLUMNS)


def test_a_row_has_one_slot_per_column():
    assert len(catalog_row()) == len(CATALOG_COLUMNS)


def test_unspecified_fields_are_null():
    assert set(catalog_row(slug="x")) == {"x", None}


def test_an_unknown_field_is_rejected():
    # A typo'd keyword must not silently vanish into a NULL column.
    with pytest.raises(KeyError):
        catalog_row(trace_namekey="oops")


def test_reachable_via_trace_name_key_is_accepted():
    rows = [_row("meg heckman", 4.81, "margaret heckman")]
    assert unreachable_trace_rows(rows, {"margaret heckman"}) == []


def test_reachable_via_own_name_key_is_accepted():
    rows = [_row("olin guha", 4.5, None)]
    assert unreachable_trace_rows(rows, {"olin guha"}) == []


def test_ghost_rating_is_reported():
    # The 99-professor prod regression: a rating pointing at a TRACE name that
    # doesn't exist.
    rows = [_row("meg heckman", 4.81, "nonexistent heckman")]
    assert unreachable_trace_rows(rows, {"margaret heckman"}) == \
        [("meg heckman", "nonexistent heckman")]


def test_ghost_via_missing_fallback_is_reported():
    rows = [_row("ghost prof", 4.2, None)]
    assert unreachable_trace_rows(rows, {"someone else"}) == [("ghost prof", "ghost prof")]


def test_professors_without_a_trace_rating_are_ignored():
    # RMP-only professors have no TRACE key and must not trip the guard.
    rows = [_row("rmp only", None, None)]
    assert unreachable_trace_rows(rows, set()) == []


def test_all_bad_rows_are_reported_not_just_the_first():
    rows = [_row("a", 4.0, "ghost a"), _row("b", 4.0, "ghost b"),
            _row("c", 4.0, "real c")]
    assert len(unreachable_trace_rows(rows, {"real c"})) == 2
