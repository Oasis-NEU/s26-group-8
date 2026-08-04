"""A professor's slug changes when an ALIAS_MAP entry renames them, so old
slugs have to keep working.

precompute builds the catalog primary key as name_to_slug(_name_key), and
_name_key is the *aliased* name. So adding `"dan koloski": "daniel koloski"`
silently turns /professors/dan-koloski into /professors/daniel-koloski. The
name_key fallback in the read path cannot recover that (it derives "dan koloski",
but the stored name_key is now "daniel koloski"), so the old URL 404'd and the
bookmark — which stores the bare slug and skips rows that no longer resolve —
vanished without a trace. 76 professors were renamed in one go.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

import bookmarks  # noqa: E402
from prof_aliases import ALIAS_MAP, canonical_slug, retired_slugs  # noqa: E402
from professor_full import resolve_professor  # noqa: E402


# ── the map itself ──────────────────────────────────────────────────────────

def test_renamed_professor_maps_old_slug_to_new():
    assert canonical_slug("dan-koloski") == "daniel-koloski"
    assert canonical_slug("ben-wormwood") == "benjamin-wormwood"


def test_unknown_and_empty_slugs_map_to_nothing():
    assert canonical_slug("someone-who-was-never-aliased") is None
    assert canonical_slug("") is None
    assert canonical_slug(None) is None


def test_every_alias_that_changes_the_slug_is_covered():
    # The bug was that 76/76 new aliases renamed a slug with no redirect. Assert
    # the map is derived from ALIAS_MAP rather than hand-maintained, so the next
    # alias addition cannot reintroduce the gap.
    def slug(name):
        import re
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    renamed = {slug(k): slug(v) for k, v in ALIAS_MAP.items() if slug(k) != slug(v)}
    assert renamed, "sanity: ALIAS_MAP should contain slug-changing entries"
    for old, new in renamed.items():
        assert canonical_slug(old) == new


def test_aliases_that_do_not_change_the_slug_are_not_redirects():
    # e.g. "j. timothy sage" -> "j timothy sage" slugs identically; a self-
    # redirect would be a pointless extra lookup on every miss.
    from prof_aliases import SLUG_ALIASES
    assert all(old != new for old, new in SLUG_ALIASES.items())
    assert canonical_slug("j-timothy-sage") is None


def test_retired_slugs_is_the_inverse_of_the_forward_map():
    assert "dan-koloski" in retired_slugs("daniel-koloski")
    assert retired_slugs("daniel-koloski") == retired_slugs("Daniel-Koloski")
    assert retired_slugs("never-renamed") == []


# ── resolution order: a live slug must never be shadowed ────────────────────

class _Catalog:
    """query_one over a fake professors_catalog, recording lookups."""

    def __init__(self, rows):
        self.rows = rows            # list of dicts
        self.queries = []

    def query_one(self, sql, params):
        self.queries.append((sql, params))
        col = "slug" if "WHERE slug" in sql else "name_key"
        for row in self.rows:
            if row.get(col) == params[0]:
                return row
        return None


def test_retired_slug_resolves_to_the_renamed_professor():
    cat = _Catalog([{"slug": "daniel-koloski", "name_key": "daniel koloski"}])
    prof = resolve_professor("dan-koloski", cat.query_one)
    assert prof is not None and prof["slug"] == "daniel-koloski"


def test_live_slug_wins_over_a_retired_one_with_the_same_name():
    # If some other professor legitimately owns "dan-koloski", the direct hit
    # must win — the alias map is only consulted after a miss.
    cat = _Catalog([
        {"slug": "dan-koloski", "name_key": "dan koloski", "who": "live"},
        {"slug": "daniel-koloski", "name_key": "daniel koloski", "who": "renamed"},
    ])
    prof = resolve_professor("dan-koloski", cat.query_one)
    assert prof["who"] == "live"
    assert len(cat.queries) == 1, "a direct hit must not trigger the fallbacks"


def test_name_key_fallback_still_works():
    cat = _Catalog([{"slug": "j-timothy-sage", "name_key": "j timothy sage"}])
    assert resolve_professor("j-timothy-sage", cat.query_one) is not None


def test_unknown_slug_still_returns_none():
    cat = _Catalog([])
    assert resolve_professor("nobody-at-all", cat.query_one) is None


# ── bookmarks survive the rename ────────────────────────────────────────────

class _FakeDB:
    def __init__(self, bookmark_rows, catalog_rows):
        self.bookmark_rows = bookmark_rows
        self.catalog_rows = catalog_rows
        self.writes = []

    def query(self, sql, params):
        if "FROM bookmarks" in sql:
            return self.bookmark_rows
        if "professors_catalog" in sql:
            wanted = set(params[0])
            return [r for r in self.catalog_rows if r["slug"] in wanted]
        return []

    def write(self, sql, params):
        self.writes.append((sql, params))


def _catalog_row(slug):
    return {
        "slug": slug, "name": "Daniel Koloski", "department": "Analytics",
        "college": "Professional Studies", "avg_rating": 4.5, "rmp_rating": 4.4,
        "trace_rating": 4.6, "total_reviews": 30, "total_comments": 12,
        "would_take_again_pct": 90.0, "image_url": None,
        "focus_x": 50.0, "focus_y": 30.0,
    }


class _Stamp:
    def __init__(self, s):
        self.s = s

    def isoformat(self):
        return self.s


def test_bookmark_on_a_retired_slug_still_lists_the_professor():
    db = _FakeDB(
        [{"item_type": "professor", "item_key": "dan-koloski",
          "created_at": _Stamp("2026-01-01T00:00:00")}],
        [_catalog_row("daniel-koloski")],
    )
    out = bookmarks.list_bookmarks("user-1", db.query)
    assert len(out["professors"]) == 1, "a renamed professor must not vanish"
    assert out["professors"][0]["slug"] == "daniel-koloski", "reports the current slug"
    assert out["professors"][0]["bookmarkedAt"] == "2026-01-01T00:00:00"


def test_bookmark_on_a_live_slug_is_unaffected():
    db = _FakeDB(
        [{"item_type": "professor", "item_key": "jane-doe",
          "created_at": _Stamp("2026-01-02T00:00:00")}],
        [_catalog_row("jane-doe")],
    )
    out = bookmarks.list_bookmarks("user-1", db.query)
    assert [p["slug"] for p in out["professors"]] == ["jane-doe"]


def test_a_genuinely_deleted_professor_is_still_skipped():
    db = _FakeDB(
        [{"item_type": "professor", "item_key": "gone-forever",
          "created_at": _Stamp("2026-01-03T00:00:00")}],
        [],
    )
    assert bookmarks.list_bookmarks("user-1", db.query)["professors"] == []


def test_removing_a_renamed_bookmark_matches_the_stored_retired_slug():
    # list_bookmarks reports the current slug, so that is what comes back to be
    # unbookmarked, while the stored row still holds the retired one.
    db = _FakeDB([], [])
    bookmarks.remove_bookmark("user-1", "professor", "daniel-koloski", db.write)
    _, params = db.writes[0]
    assert "dan-koloski" in params[2] and "daniel-koloski" in params[2]


def test_removing_a_course_bookmark_is_unchanged():
    db = _FakeDB([], [])
    bookmarks.remove_bookmark("user-1", "course", "CS2500", db.write)
    _, params = db.writes[0]
    assert params[2] == ["CS2500"]


def _inserted_key(writes):
    """The key an add actually stored. Adding a renamed professor also emits a
    DELETE that collapses any row left under their retired slug, so "the first
    write" is not the insert."""
    return next(params[2] for sql, params in writes if "INSERT" in sql)


def test_adding_a_bookmark_from_a_stale_page_stores_the_current_slug():
    cat = _Catalog([{"slug": "daniel-koloski", "name_key": "daniel koloski"}])
    writes = []
    result = bookmarks.add_bookmark(
        "user-1", "professor", "dan-koloski", cat.query_one,
        lambda sql, params: writes.append((sql, params)))
    assert result == "ok"
    assert _inserted_key(writes) == "daniel-koloski", "must not store an already-dead slug"


def test_adding_a_bookmark_for_a_real_professor_still_works():
    cat = _Catalog([{"slug": "jane-doe", "name_key": "jane doe"}])
    writes = []
    assert bookmarks.add_bookmark(
        "user-1", "professor", "jane-doe", cat.query_one,
        lambda sql, params: writes.append((sql, params))) == "ok"
    assert _inserted_key(writes) == "jane-doe"


# ── the invariant that keeps a retired slug from also being a live one ───────
# `silvio amir` was an ALIAS_MAP key that TRACE still used as a live instructor
# name. TRACE name_keys were the one side precompute never aliased, so that name
# got its own catalog row — making `silvio-amir` simultaneously a live slug and a
# retired one. resolve_professor survives that (tested above: direct slug first),
# but the bookmark paths expand retired_slugs() unconditionally, so bookmarking
# or unbookmarking `silvio-amir-alves-moreira` deleted the user's bookmark of the
# *other* professor, and one stored row listed as two professors.
#
# precompute now aliases the TRACE side too, which is what makes the collision
# unrepresentable rather than merely unlikely. These tests guard the two
# properties that fix depends on.

def test_alias_map_has_no_chains_so_one_pass_is_enough():
    """No alias target is itself an alias key.

    precompute applies ALIAS_MAP with a single .replace() pass on both the RMP and
    the TRACE side, and SLUG_ALIASES is likewise single-hop. A chain (a -> b, b ->
    c) would leave `b` behind as a canonical name that is also a retired slug —
    exactly the collision above — so the single-pass design is only sound while
    this holds. Adding a chained entry must fail here rather than silently
    resurrect the bookmark bug.
    """
    chains = sorted(set(ALIAS_MAP.values()) & set(ALIAS_MAP.keys()))
    assert chains == [], f"chained aliases need a second pass or flattening: {chains}"


def test_aliasing_a_corpus_leaves_no_name_that_is_a_retired_slug():
    """The property precompute's TRACE-side .replace(ALIAS_MAP) buys.

    Mirrors the derivation at the "TRACE name keys" step: whatever spellings the
    corpus contains, once aliased none of them can slug to a retired slug, so no
    catalog row can occupy one.
    """
    import re

    from prof_aliases import SLUG_ALIASES

    def slug(name):
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    # A corpus deliberately seeded with every retired spelling there is.
    corpus = set(ALIAS_MAP.keys()) | {"someone unaliased", "silvio amir"}
    aliased = {ALIAS_MAP.get(n, n) for n in corpus}

    live_and_retired = sorted(n for n in aliased if slug(n) in SLUG_ALIASES)
    assert live_and_retired == [], (
        "these names would be live catalog rows under a retired slug, so the "
        f"bookmark paths would cross-delete: {live_and_retired}")
    # And the merge actually happened rather than the assert passing vacuously.
    assert "silvio amir" not in aliased
    assert "silvio amir alves moreira" in aliased


def test_precompute_aliases_the_trace_side_and_repairs_stored_keys():
    """The two source lines the property above depends on.

    The property test applies ALIAS_MAP itself, so it would keep passing if
    precompute stopped doing so. These pin the actual derivation:

      1. the TRACE name_key must be aliased, or an ALIAS_MAP key that TRACE uses
         as a live name gets its own catalog row again;
      2. the trace_courses backfill must rewrite disagreeing keys, not just NULL
         ones. Aliasing changes the key for professors whose rows are already
         populated; backfilling only NULLs would leave their sections under the
         retired spelling with no catalog row pointing at them, because the read
         path joins on COALESCE(trace_name_key, name_key).
    """
    import inspect

    import precompute

    src = inspect.getsource(precompute.main)

    derivation = next(
        line for line in src.splitlines() if 'tc["name_key"] =' in line)
    assert ".replace(ALIAS_MAP)" in derivation, (
        f"TRACE name_key must be aliased like the RMP side: {derivation.strip()}")

    backfill = src.split("UPDATE trace_courses tc SET name_key")[1].split('"""')[0]
    assert "IS DISTINCT FROM" in backfill, (
        "the name_key backfill must repair rows that already hold a stale key, "
        f"not only NULLs: {backfill.strip()}")
    assert "tc.name_key IS NULL" not in backfill
