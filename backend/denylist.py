"""Professors who have asked for their data not to be published.

A data-deletion request has to survive the pipeline, and nothing else here does:
precompute DROPs and rebuilds professors_catalog from the CSVs on every run, and
migrate_to_crdb re-inserts trace_courses/trace_comments from the store. Deleting
someone's rows by hand removes them until the next refresh puts them back. So the
list lives in the repo, is read by every loader, and is applied before a row is
ever written.

Three enforcement points, because the data arrives by three routes:

  migrate_to_crdb   drops their rows at CSV load (raw tables)
  precompute        drops them from the catalog build (their page)
  load_evidence     keeps them out of the RAG corpus (chat cannot quote them)

and purge_denied.py removes what is already loaded, which the filters cannot —
a filter only governs the next write.

## Scope: the database, not the data store

Deliberate, and worth stating because it is the surprising half. The filters run
at *load* time, so the scraped CSVs in RateMyHusky-data still contain a denied
professor's rows — the scrapers write everyone, and the loaders drop them on the
way in. Nothing the site or the chat can reach holds their data; the private
store does.

Chosen on 2026-08-12 over scrubbing at write time. The store is private, and its
push step force-pushes an orphan commit each run, so there is no accumulating
history — a professor's rows leave the store as soon as they leave the source
CSVs rather than living in git forever. For RMP that is automatic once RMP
removes them. For TRACE it is not: those exports carry historical terms, so the
rows persist in the store until someone edits the file.

If a request has to mean "retained nowhere", this is the piece to extend: filter
in fetch_lite's CSV dumps and add a scrub pass before the store push. Until then,
do not tell a requester their data is deleted everywhere — it is removed from
everything published, which is a different sentence.

## Why the entries are hashed

This repo is public. A file listing "people who asked to be forgotten" by name,
in a privacy mechanism, published on GitHub and indexed forever, would leak the
thing it exists to protect — and would tell anyone reading that this named person
was the subject of a removal request, which is more than the original data said.

The hash is sha256 over the normalized name key. Be clear about what that buys:
against someone holding the NEU professor list (public, ~3,900 names) it is not
protection at all — they can hash the list and match in a second. It defends
against casual reading, repo search and search-engine indexing, not against a
motivated party. It is the least-bad option; a private list would be better and
would stop being version-controlled, which is the property that makes this work.

Use the CLI rather than editing by hand:

    python denylist.py add "Julia Garrett" --note "2026-08-11 request"
    python denylist.py check "Julia Garrett"
    python denylist.py list
"""

import argparse
import hashlib
import os
import re
import sys
import unicodedata

from prof_aliases import ALIAS_MAP

DENYLIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "denylist.txt")


def normalize_name(name):
    """Same rule as precompute.normalize_name, duplicated to stay import-light.

    precompute imports pandas and numpy; migrate_to_crdb and
    scraper/load_evidence_to_crdb.py do not, and this module is imported by all
    three. test_denylist pins the two implementations together.
    """
    s = str(name).strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def name_key(name):
    """Normalized, alias-resolved key — the form every table joins on.

    ALIAS_MAP matters here: a professor RMP spells "sakib miazi" and TRACE spells
    "md nazmus sakib miazi" is one person, and a denylist that caught only the
    spelling they happened to write in their request would leave the other half
    of their data published.
    """
    key = normalize_name(name)
    return ALIAS_MAP.get(key, key)


def entry_hash(name):
    """The stored form of one name. See the module docstring on what this buys."""
    return hashlib.sha256(name_key(name).encode("utf-8")).hexdigest()


def _read_entries(path=None):
    """(hash, note) per line. Blank lines and # comments ignored."""
    path = path or DENYLIST_PATH
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, _, note = line.partition("#")
            digest = digest.strip()
            if digest:
                entries.append((digest, note.strip()))
    return entries


_cache = {}


def denied_hashes(path=None, refresh=False):
    """The hash set, read once per process.

    Cached because migrate_to_crdb consults it per CSV row — 1.7M times for
    trace_comments — and re-reading the file there would dominate the load.
    """
    path = path or DENYLIST_PATH
    if refresh or path not in _cache:
        _cache[path] = frozenset(h for h, _ in _read_entries(path))
    return _cache[path]


def is_denied(name, path=None):
    """Is this professor on the list? Accepts any spelling ALIAS_MAP resolves."""
    if name is None:
        return False
    text = str(name).strip()
    if not text:
        return False
    return entry_hash(text) in denied_hashes(path)


def is_denied_key(key, path=None):
    """As is_denied, for a name_key that is already normalized and alias-resolved.

    The hot path: precompute and the loaders have already computed the key, and
    re-normalizing an alias-resolved key is both wasted work and wrong if
    ALIAS_MAP ever maps a target to a further target.
    """
    if not key:
        return False
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest() in denied_hashes(path)


def denied_full_name(first, last, path=None):
    """TRACE stores names split; this joins them the way precompute does."""
    return is_denied(f"{normalize_name(first)} {normalize_name(last)}", path)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cmd_add(args):
    digest = entry_hash(args.name)
    if digest in denied_hashes(refresh=True):
        print(f"Already on the list ({digest[:12]}...).")
        return 0
    note = args.note or "no note"
    exists = os.path.exists(DENYLIST_PATH)
    with open(DENYLIST_PATH, "a", encoding="utf-8") as fh:
        if not exists:
            fh.write(_HEADER)
        fh.write(f"{digest}  # {note}\n")
    print(f"Added {digest[:12]}...  ({note})")
    print("\nThe filters govern the NEXT write only. To remove rows already "
          "loaded, run:\n    python purge_denied.py --dry-run\n"
          "then re-run without --dry-run.")
    return 0


def _cmd_check(args):
    denied_hashes(refresh=True)   # the CLI must not read a cache from an earlier add
    hit = is_denied(args.name)
    print(f"{args.name!r} -> key {name_key(args.name)!r} -> "
          f"{'DENIED' if hit else 'not on the list'}")
    return 0 if hit else 1


def _cmd_list(args):
    entries = _read_entries()
    print(f"{len(entries)} entries in {DENYLIST_PATH}")
    for digest, note in entries:
        print(f"  {digest[:12]}...  {note}")
    return 0


_HEADER = """\
# Professors who have asked that their data not be published.
#
# sha256 of the normalized, alias-resolved name key. Hashed because this repo is
# public and a plaintext list would publish the very association it exists to
# suppress. See denylist.py for what that does and does not protect.
#
# Managed with:  python denylist.py add "<name>" --note "<date> <ref>"
# Do not edit by hand; the hash must match name_key() exactly or it will not fire.
#
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a professor to the list")
    p_add.add_argument("name")
    p_add.add_argument("--note", help="Date and request reference, no PII")
    p_add.set_defaults(func=_cmd_add)

    p_check = sub.add_parser("check", help="Is this name on the list?")
    p_check.add_argument("name")
    p_check.set_defaults(func=_cmd_check)

    p_list = sub.add_parser("list", help="Show entries (hashes and notes)")
    p_list.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
