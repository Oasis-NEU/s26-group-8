"""course_catalog is built from one string, so its parser decides what a course is.

trace_courses.display_name comes in three shapes across 2015-2025 (counts from
the 105,376-row export):

    "ENGW3302:09 (Advanced Writing in Tech Prof) - Laurie Nardone"   94,970
    "NRSG2001:B01 (Foundations Prof Nursing Prac) - Christy Leyland"  6,469
    "ACCT1201 (FINANCL ACCOUNTING  REPORTING) - ANDREW TROTMAN"       3,937

The old parser matched only the first: it required ":<digits>". Everything else
fell out of the catalog (186 courses had TRACE sections but no page), and the
SQL twin — SPLIT_PART(display_name, ':', 1) — turned the third shape into
"ACCT1201FINANCLACCOUNTINGREPORTINGANDREWTROTMAN", a course_code that matches
nothing the course page looks up.

Two more things this pins down:

  * Which title wins. 615 codes carry more than one title and 123 more than one
    department string; the old drop_duplicates() pick was decided by frame order.
    Most recent term wins now, which also lands on the better string 214 times
    because newer TRACE exports keep the "&" older ones dropped.

  * Topics codes. 49 codes run under several titles inside a single term
    (HONR3310 as "Election 2024" and "Language and Power" at once). Averaging
    unrelated classes into one course rating is meaningless, so they are flagged.

No database: build_course_rows is pure.
"""

import pytest

from precompute import (
    build_course_rows,
    course_code_from_display_name,
    parse_display_name,
    titles_are_variants,
)


def rows(records):
    """{code: row} for easy assertions."""
    return {r[0]: r for r in build_course_rows(records)}


NAME, DEPT, SEARCH, TOPICS = 1, 2, 3, 4


# ── the three display_name shapes ───────────────────────────────────────────

def test_numeric_section_is_parsed():
    assert parse_display_name(
        "ENGW3302:09 (Advanced Writing in Tech Prof) - Laurie Nardone"
    ) == ("ENGW3302", "Advanced Writing in Tech Prof")


def test_alphanumeric_section_is_parsed():
    # 6,469 rows use letters in the section (V30, B01, N01, G01). The old
    # ":\d+" parser dropped every one of them.
    assert parse_display_name(
        "NRSG2001:B01 (Foundations Prof Nursing Prac) - Christy Leyland"
    ) == ("NRSG2001", "Foundations Prof Nursing Prac")


def test_missing_section_is_parsed():
    # 3,937 rows (Fall 2015 + the Law terms) carry no section at all.
    assert parse_display_name(
        "ACCT1201 (FINANCL ACCOUNTING  REPORTING) - ANDREW TROTMAN"
    ) == ("ACCT1201", "FINANCL ACCOUNTING  REPORTING")


def test_unparseable_display_name_is_skipped_not_guessed():
    assert parse_display_name("no code here at all") == (None, None)
    assert rows([("no code here at all", "Fall 2024", "English")]) == {}


@pytest.mark.parametrize("display_name,expected", [
    ("ENGW3302:09 (Advanced Writing) - X", "ENGW3302"),
    ("NRSG2001:B01 (Foundations) - X", "NRSG2001"),
    ("ACCT1201 (FINANCL ACCOUNTING  REPORTING) - ANDREW TROTMAN", "ACCT1201"),
    ("engw3302:09 (lowercase source) - X", "ENGW3302"),
    ("", ""),
    (None, ""),
])
def test_course_code_extraction(display_name, expected):
    assert course_code_from_display_name(display_name) == expected


def test_course_code_never_absorbs_the_title_or_instructor():
    """The bug that hid 3,937 sections: no-section rows produced a course_code
    with the title and instructor mashed into it."""
    code = course_code_from_display_name(
        "ACCT1201 (FINANCL ACCOUNTING  REPORTING) - ANDREW TROTMAN"
    )
    assert code == "ACCT1201"
    assert "ACCOUNTING" not in code and "TROTMAN" not in code


# ── which title wins ────────────────────────────────────────────────────────

def test_most_recent_term_supplies_the_title():
    # ME2350 ran as "Engineering Mechanics & Design" through Summer 2019, then
    # as "Statics" from Fall 2019 on.
    built = rows([
        ("ME2350:01 (Engineering Mechanics & Design) - A", "Fall 2018", "Mechanical Engineering"),
        ("ME2350:01 (Statics) - B", "Spring 2025", "Mechanical Engineering"),
    ])
    assert built["ME2350"][NAME] == "Statics"


def test_stripped_ampersand_loses_to_the_spelled_out_variant():
    # Older exports drop "&", leaving a double space. The newer row wins, which
    # is what fixes 214 titles.
    built = rows([
        ("IE6200:01 (Engineering Probs  Stats) - A", "Fall 2016", "MIE"),
        ("IE6200:01 (Engineering Probs & Stats) - A", "Fall 2024", "MIE"),
    ])
    assert built["IE6200"][NAME] == "Engineering Probs & Stats"


def test_title_pick_does_not_depend_on_record_order():
    forward = [
        ("HIST2211:01 (The World Since 1945) - A", "Fall 2017", "History"),
        ("HIST2211:01 (World History since 1945) - B", "Spring 2025", "History"),
    ]
    assert rows(forward)["HIST2211"][NAME] == rows(list(reversed(forward)))["HIST2211"][NAME]


def test_ties_within_a_term_break_alphabetically_for_stability():
    same_term = [
        ("XX1000:01 (Beta) - A", "Fall 2024", "Dept"),
        ("XX1000:02 (Alpha) - B", "Fall 2024", "Dept"),
    ]
    assert rows(same_term)["XX1000"][NAME] == "Alpha"
    assert rows(list(reversed(same_term)))["XX1000"][NAME] == "Alpha"


def test_department_also_comes_from_the_most_recent_term():
    built = rows([
        ("XX1000:01 (Thing) - A", "Fall 2016", "Old Department"),
        ("XX1000:01 (Thing) - A", "Spring 2025", "New Department"),
    ])
    assert built["XX1000"][DEPT] == "New Department"


def test_missing_department_yields_empty_string_not_nan():
    built = rows([("XX1000:01 (Thing) - A", "Fall 2024", None)])
    assert built["XX1000"][DEPT] == ""


# ── topics codes ────────────────────────────────────────────────────────────

def test_two_titles_in_one_term_is_a_topics_code():
    built = rows([
        ("HONR3310:01 (Election 2024) - A", "Fall 2024", "Honors"),
        ("HONR3310:02 (Language and Power) - B", "Fall 2024", "Honors"),
    ])
    assert built["HONR3310"][TOPICS] is True


def test_a_rename_across_terms_is_not_a_topics_code():
    """The distinction the flag turns on: sequential titles are one course
    renamed; simultaneous titles are several courses sharing a code."""
    built = rows([
        ("ME2350:01 (Engineering Mechanics & Design) - A", "Fall 2018", "MIE"),
        ("ME2350:01 (Statics) - B", "Spring 2025", "MIE"),
    ])
    assert built["ME2350"][TOPICS] is False


def test_punctuation_variants_in_one_term_are_not_topics():
    # "Mergers  Acquisitions" and "Mergers and Acquisitions" are the same title
    # written two ways, not two classes.
    built = rows([
        ("FINA6214:01 (Mergers  Acquisitions) - A", "Fall 2024", "Finance"),
        ("FINA6214:02 (Mergers and Acquisitions) - B", "Fall 2024", "Finance"),
    ])
    assert built["FINA6214"][TOPICS] is False


def test_abbreviated_title_in_one_term_is_not_topics():
    """The flag's expensive direction. A false negative leaves a mediocre average
    on display; a false positive *deletes* a real course rating. Comparing
    normalised strings could only see punctuation variants, so one term spelling
    a title out and another abbreviating it read as two courses."""
    built = rows([
        ("PSYC1101:01 (Intro to Psych) - A", "Fall 2024", "Psychology"),
        ("PSYC1101:02 (Introduction to Psychology) - B", "Fall 2024", "Psychology"),
    ])
    assert built["PSYC1101"][TOPICS] is False


def test_an_abbreviation_does_not_make_unrelated_titles_agree():
    # The guard on the guard: matching by prefix must not collapse the real
    # topics codes it exists alongside.
    built = rows([
        ("HONR3310:01 (Election 2024) - A", "Fall 2024", "Honors"),
        ("HONR3310:02 (Language and Power) - B", "Fall 2024", "Honors"),
    ])
    assert built["HONR3310"][TOPICS] is True


@pytest.mark.parametrize("a, b", [
    ("Intro to Psych", "Introduction to Psychology"),
    ("Advanced Writing in Tech Prof", "Advanced Writing in Technical Professions"),
    ("Mergers  Acquisitions", "Mergers and Acquisitions"),
    ("Org Behavior", "Organizational Behavior"),
    ("Race/Ethnicity in America", "Race and Ethnicity in America"),
])
def test_titles_that_are_one_title_written_two_ways(a, b):
    assert titles_are_variants(a, b)


@pytest.mark.parametrize("a, b", [
    ("Election 2024", "Language and Power"),
    ("Special Topics", "Special Topics in AI"),   # a container plus an offering
    ("Organic Chemistry 1", "Organic Chemistry 2"),
    ("Statics", "Dynamics"),
])
def test_titles_that_are_genuinely_different_courses(a, b):
    assert not titles_are_variants(a, b)


def test_variant_matching_is_symmetric():
    assert (titles_are_variants("Intro to Psych", "Introduction to Psychology")
            is titles_are_variants("Introduction to Psychology", "Intro to Psych"))


def test_a_one_letter_stub_is_not_an_abbreviation_of_anything():
    # "P" would prefix-match half the catalog; an abbreviation has to carry
    # enough of the word to identify it.
    assert not titles_are_variants("P Chemistry", "Physical Chemistry")


def test_same_title_many_sections_is_not_topics():
    built = rows([
        (f"ENGW3302:{i:02d} (Advanced Writing in Tech Prof) - Prof {i}", "Fall 2024", "English")
        for i in range(1, 12)
    ])
    assert built["ENGW3302"][TOPICS] is False


def test_law_and_regular_terms_of_the_same_year_are_distinct_terms():
    """"Fall 2024" and "Law Semester - Fall 2024" are separate offerings, so one
    title in each is not evidence of a topics code."""
    built = rows([
        ("LAW7651:01 (Human Rights) - A", "Fall 2024", "Law"),
        ("LAW7651:01 (Comparative Rights) - B", "Law Semester - Fall 2024", "Law"),
    ])
    assert built["LAW7651"][TOPICS] is False


# ── search text ─────────────────────────────────────────────────────────────

def test_search_text_keeps_every_historical_title():
    # Someone who took ME2350 in 2018 knows it as "Engineering Mechanics &
    # Design" and will search for that, not for its current title.
    built = rows([
        ("ME2350:01 (Engineering Mechanics & Design) - A", "Fall 2018", "MIE"),
        ("ME2350:01 (Statics) - B", "Spring 2025", "MIE"),
    ])
    search_text = built["ME2350"][SEARCH]
    assert "engineering mechanics & design" in search_text
    assert "statics" in search_text
    assert search_text.startswith("me2350 ")


def test_search_text_is_lowercase_and_deduplicated():
    built = rows([
        ("XX1000:01 (Thing) - A", "Fall 2023", "Dept"),
        ("XX1000:02 (Thing) - B", "Fall 2024", "Dept"),
    ])
    assert built["XX1000"][SEARCH] == "xx1000 thing"


# ── shape ───────────────────────────────────────────────────────────────────

def test_one_row_per_code_sorted_for_a_stable_insert():
    built = build_course_rows([
        ("ZZ9000:01 (Later) - A", "Fall 2024", "Dept"),
        ("AA1000:01 (Earlier) - B", "Fall 2024", "Dept"),
        ("AA1000:02 (Earlier) - C", "Fall 2024", "Dept"),
    ])
    assert [r[0] for r in built] == ["AA1000", "ZZ9000"]
    assert all(len(r) == 5 for r in built)
