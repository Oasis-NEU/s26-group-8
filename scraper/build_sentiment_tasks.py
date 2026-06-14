"""
Sentiment work-file builder for resolved Reddit professor mentions.

There are two independent scoring tiers, selected with --tier (default
``highconf``). Each tier has its own task/score files so a completed tier
stays frozen and verify-clean while another tier is still being scored:

    highconf      resolved AND confidence >= 0.7  (v1, the original pass)
                  -> sentiment_tasks.csv / sentiment_scores.csv
    conv_context  resolved AND method == conv_context AND confidence == 0.55
                  -> sentiment_tasks_cc.csv / sentiment_scores_cc.csv

The two tiers cover disjoint mentions, share the same score schema, and join
on (source_type, source_id, professor_slug), so their score files can simply
be unioned downstream.

Usage
-----
    python build_sentiment_tasks.py                        # build highconf tasks
    python build_sentiment_tasks.py --tier conv_context    # build conv_context tasks
    python build_sentiment_tasks.py --progress [--tier ..] # scored / total + resume point
    python build_sentiment_tasks.py --verify   [--tier ..] # audit scores vs tasks
    python build_sentiment_tasks.py --selftest             # offline checks, then exit
"""

__author__ = "RateMyHusky"
__version__ = "1.1.0"

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REDDIT_DIR = os.path.join(SCRIPT_DIR, "reddit_data")
MENTIONS_CSV = os.path.join(REDDIT_DIR, "reddit_mentions.csv")
POSTS_CSV = os.path.join(REDDIT_DIR, "reddit_neu_posts.csv")
COMMENTS_CSV = os.path.join(REDDIT_DIR, "reddit_neu_comments.csv")

CONFIDENCE_THRESHOLD = 0.7
CONV_CONTEXT_CONFIDENCE = 0.55


def _qualifies_highconf(row: dict) -> bool:
    """In scope for v1: resolved AND confidence >= 0.7 (any method)."""
    if row.get("status") != "resolved":
        return False
    try:
        return float(row.get("confidence", "")) >= CONFIDENCE_THRESHOLD
    except (TypeError, ValueError):
        return False


def _qualifies_conv_context(row: dict) -> bool:
    """In scope for the deferred tier: resolved conv_context at confidence 0.55."""
    if row.get("status") != "resolved" or row.get("method") != "conv_context":
        return False
    try:
        return abs(float(row.get("confidence", "")) - CONV_CONTEXT_CONFIDENCE) < 1e-9
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Tier:
    """A scoring tier: its scope predicate and its dedicated task/score files."""
    name: str
    qualifies: Callable[[dict], bool]
    tasks_csv: str
    scores_csv: str


TIERS = {
    "highconf": Tier(
        name="highconf",
        qualifies=_qualifies_highconf,
        tasks_csv=os.path.join(REDDIT_DIR, "sentiment_tasks.csv"),
        scores_csv=os.path.join(REDDIT_DIR, "sentiment_scores.csv"),
    ),
    "conv_context": Tier(
        name="conv_context",
        qualifies=_qualifies_conv_context,
        tasks_csv=os.path.join(REDDIT_DIR, "sentiment_tasks_cc.csv"),
        scores_csv=os.path.join(REDDIT_DIR, "sentiment_scores_cc.csv"),
    ),
}

TASK_FIELDS = [
    "source_type", "source_id", "professor_slug",
    "professor_name", "matched_token", "method", "text",
]
SCORE_FIELDS = [
    "source_type", "source_id", "professor_slug",
    "sentiment", "score", "on_topic", "sarcasm", "hyperbole", "rationale",
]

csv.field_size_limit(10 * 1024 * 1024)


def clean_text(s: str) -> str:
    """Collapse all whitespace runs (incl. newlines/tabs) to single spaces, strip."""
    return " ".join((s or "").split())


def _join_post_text(row: dict) -> str:
    return clean_text(f"{row.get('title', '')} {row.get('selftext', '')}")


def load_source_text(path: str, needed_ids: set, text_fn: Callable[[dict], str]) -> dict:
    """Return {id: cleaned_text} for ids in `needed_ids`. `text_fn(row)->str`."""
    out: dict = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rid = row.get("id")
            if rid in needed_ids:
                out[rid] = text_fn(row)
    return out


def build_task_rows(mentions, text_by_key, qualifies) -> list:
    """Filter mentions to scope, attach source text, return sorted task dicts.

    `text_by_key` maps (source_type, source_id) -> cleaned text.
    `qualifies(row)->bool` is the tier's scope predicate.
    Mentions whose text is missing are skipped (cannot score empty text).
    """
    rows = []
    for m in mentions:
        if not qualifies(m):
            continue
        key = (m["source_type"], m["source_id"])
        text = text_by_key.get(key, "")
        if not text:
            continue
        rows.append({
            "source_type": m["source_type"],
            "source_id": m["source_id"],
            "professor_slug": m["professor_slug"],
            "professor_name": m["professor_name"],
            "matched_token": m["matched_token"],
            "method": m["method"],
            "text": text,
        })
    rows.sort(key=lambda r: (r["source_id"], r["professor_slug"], r["method"]))
    return rows


def run_build(tier: Tier) -> None:
    with open(MENTIONS_CSV, encoding="utf-8") as f:
        mentions = [m for m in csv.DictReader(f) if tier.qualifies(m)]
    print(f"tier {tier.name}: qualifying mentions: {len(mentions)}")

    post_ids = {m["source_id"] for m in mentions if m["source_type"] == "post"}
    comment_ids = {m["source_id"] for m in mentions if m["source_type"] == "comment"}

    text_by_key = {}
    for rid, txt in load_source_text(POSTS_CSV, post_ids, _join_post_text).items():
        text_by_key[("post", rid)] = txt
    for rid, txt in load_source_text(
        COMMENTS_CSV, comment_ids, lambda r: clean_text(r.get("body", ""))
    ).items():
        text_by_key[("comment", rid)] = txt

    missing = sum(
        1 for m in mentions
        if (m["source_type"], m["source_id"]) not in text_by_key
    )
    if missing:
        print(f"warning: {missing} mentions had no source text and were skipped")
    rows = build_task_rows(mentions, text_by_key, tier.qualifies)
    with open(tier.tasks_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TASK_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} task rows -> {tier.tasks_csv}")


def score_key(row: dict) -> tuple:
    return (row["source_type"], row["source_id"], row["professor_slug"])


def pending_keys(all_keys, done_keys) -> list:
    """Task keys, in order, not yet present in done_keys."""
    done = set(done_keys)
    return [k for k in all_keys if k not in done]


def _read_keys(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [score_key(r) for r in csv.DictReader(f)]


def run_progress(tier: Tier) -> None:
    task_keys = _read_keys(tier.tasks_csv)
    done_keys = _read_keys(tier.scores_csv)
    pend = pending_keys(task_keys, done_keys)
    total, scored = len(task_keys), len(task_keys) - len(pend)
    print(f"tier {tier.name}: scored {scored} / {total}  ({len(pend)} remaining)")
    if pend:
        nxt = pend[0]
        print(f"resume at: source_id={nxt[1]} slug={nxt[2]}")
    else:
        print("all task rows scored")


def _score_is_zero(val) -> bool:
    """True iff val parses to ~0.0. A non-numeric/unparseable val is NOT zero."""
    try:
        return abs(float(val or 0)) < 1e-9
    except (TypeError, ValueError):
        return False


def audit_scores(task_keys, score_rows) -> dict:
    """Cross-check score rows against task keys.

    Returns dict with: missing (task keys with no score), orphans (score keys
    with no task), duplicates (score keys appearing >1), rule_violations
    (on_topic=false but sentiment != neutral or score != 0).
    """
    task_set = set(task_keys)
    seen = {}
    rule_violations = []
    for r in score_rows:
        k = score_key(r)
        seen[k] = seen.get(k, 0) + 1
        if str(r.get("on_topic", "")).lower() == "false":
            neutral = r.get("sentiment") == "neutral"
            zero = _score_is_zero(r.get("score"))
            if not (neutral and zero):
                rule_violations.append(k)
    score_set = set(seen)
    return {
        "missing": [k for k in task_keys if k not in score_set],
        "orphans": sorted(score_set - task_set),
        "duplicates": sorted(k for k, c in seen.items() if c > 1),
        "rule_violations": sorted(set(rule_violations)),
    }


def run_verify(tier: Tier) -> int:
    task_keys = _read_keys(tier.tasks_csv)
    if not os.path.exists(tier.scores_csv):
        print(f"no {os.path.basename(tier.scores_csv)} yet — nothing to verify")
        return 1
    with open(tier.scores_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing_cols = [c for c in SCORE_FIELDS if c not in (reader.fieldnames or [])]
        if missing_cols:
            print(f"error: {tier.scores_csv} missing columns: {missing_cols}")
            return 1
        score_rows = list(reader)

    a = audit_scores(task_keys, score_rows)
    print(f"tier {tier.name}  tasks: {len(task_keys)}  scores: {len(score_rows)}")
    print(f"missing: {len(a['missing'])}  orphans: {len(a['orphans'])}  "
          f"duplicates: {len(a['duplicates'])}  rule_violations: {len(a['rule_violations'])}")

    dist = {}
    flags = {"sarcasm": 0, "hyperbole": 0, "off_topic": 0}
    for r in score_rows:
        dist[r["sentiment"]] = dist.get(r["sentiment"], 0) + 1
        if str(r.get("sarcasm", "")).lower() == "true":
            flags["sarcasm"] += 1
        if str(r.get("hyperbole", "")).lower() == "true":
            flags["hyperbole"] += 1
        if str(r.get("on_topic", "")).lower() == "false":
            flags["off_topic"] += 1
    print("sentiment distribution:", dict(sorted(dist.items())))
    print("flag counts:", flags)

    problems = a["missing"] or a["orphans"] or a["duplicates"] or a["rule_violations"]
    if problems:
        for label in ("missing", "orphans", "duplicates", "rule_violations"):
            if a[label]:
                print(f"  {label} (first 5): {a[label][:5]}")
        return 1
    print("verify OK")
    return 0


def selftest() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    hc = _qualifies_highconf
    cc = _qualifies_conv_context
    check("threshold is 0.7", CONFIDENCE_THRESHOLD == 0.7)
    check("conv_context confidence is 0.55", CONV_CONTEXT_CONFIDENCE == 0.55)
    check("task fields have text", "text" in TASK_FIELDS)
    check("score fields have rationale", "rationale" in SCORE_FIELDS)
    check("highconf resolved 1.0", hc({"status": "resolved", "confidence": "1.0"}))
    check("highconf resolved 0.7", hc({"status": "resolved", "confidence": "0.7"}))
    check("highconf rejects 0.55", not hc({"status": "resolved", "confidence": "0.55"}))
    check("highconf rejects ambiguous", not hc({"status": "ambiguous", "confidence": "0.97"}))
    check("highconf rejects bad confidence", not hc({"status": "resolved", "confidence": ""}))
    check("conv_context accepts resolved cc 0.55",
          cc({"status": "resolved", "method": "conv_context", "confidence": "0.55"}))
    check("conv_context rejects ambiguous cc 0.55",
          not cc({"status": "ambiguous", "method": "conv_context", "confidence": "0.55"}))
    check("conv_context rejects non-cc method",
          not cc({"status": "resolved", "method": "lastname", "confidence": "0.55"}))
    check("conv_context rejects cc at other confidence",
          not cc({"status": "resolved", "method": "conv_context", "confidence": "0.9"}))
    check("tiers have distinct task files",
          TIERS["highconf"].tasks_csv != TIERS["conv_context"].tasks_csv)
    check("tiers have distinct score files",
          TIERS["highconf"].scores_csv != TIERS["conv_context"].scores_csv)
    check("clean_text collapses newlines", clean_text("a\n\nb\tc") == "a b c")
    check("clean_text strips", clean_text("  hi  ") == "hi")
    check("join post text", _join_post_text({"title": "T", "selftext": "S"}) == "T S")
    check("join post empty selftext", _join_post_text({"title": "T", "selftext": ""}) == "T")

    sample_mentions = [
        {"status": "resolved", "confidence": "1.0", "source_type": "comment",
         "source_id": "c1", "professor_slug": "jane-kim", "professor_name": "Jane Kim",
         "matched_token": "jane kim", "method": "exact_full"},
        {"status": "ambiguous", "confidence": "0.97", "source_type": "comment",
         "source_id": "c2", "professor_slug": "", "professor_name": "",
         "matched_token": "kim", "method": "lastname"},
    ]
    text_by_key = {("comment", "c1"): "Jane Kim is hard but fair"}
    tasks = build_task_rows(sample_mentions, text_by_key, _qualifies_highconf)
    check("build keeps only qualifying", len(tasks) == 1)
    check("build attaches text", tasks[0]["text"] == "Jane Kim is hard but fair")
    check("build sorts stable", tasks == sorted(tasks, key=lambda r: (r["source_id"], r["professor_slug"], r["method"])))

    cc_mentions = [
        {"status": "resolved", "confidence": "1.0", "source_type": "comment",
         "source_id": "c1", "professor_slug": "jane-kim", "professor_name": "Jane Kim",
         "matched_token": "jane kim", "method": "exact_full"},
        {"status": "resolved", "confidence": "0.55", "source_type": "comment",
         "source_id": "c3", "professor_slug": "joe-lee", "professor_name": "Joe Lee",
         "matched_token": "", "method": "conv_context"},
    ]
    cc_text = {("comment", "c1"): "x", ("comment", "c3"): "thread reply about the prof"}
    cc_tasks = build_task_rows(cc_mentions, cc_text, _qualifies_conv_context)
    check("conv_context build keeps only cc 0.55", [t["source_id"] for t in cc_tasks] == ["c3"])

    done = {("comment", "c1", "jane-kim")}
    all_keys = [("comment", "c1", "jane-kim"), ("comment", "c2", "joe-lee")]
    pend = pending_keys(all_keys, done)
    check("pending excludes done", pend == [("comment", "c2", "joe-lee")])
    check("scored key parsing", score_key({"source_type": "comment", "source_id": "c1", "professor_slug": "jane-kim"}) == ("comment", "c1", "jane-kim"))

    audit = audit_scores(
        task_keys=[("comment", "c1", "a"), ("comment", "c2", "b")],
        score_rows=[
            {"source_type": "comment", "source_id": "c1", "professor_slug": "a",
             "sentiment": "positive", "score": "0.8", "on_topic": "true",
             "sarcasm": "false", "hyperbole": "false", "rationale": "praise"},
        ],
    )
    check("audit counts missing", audit["missing"] == [("comment", "c2", "b")])
    check("audit no dups", audit["duplicates"] == [])
    check("audit no orphans", audit["orphans"] == [])
    check("audit bad rule flags on_topic", audit["rule_violations"] == [])
    bad = audit_scores(
        task_keys=[("comment", "c1", "a")],
        score_rows=[
            {"source_type": "comment", "source_id": "c1", "professor_slug": "a",
             "sentiment": "negative", "score": "-0.5", "on_topic": "false",
             "sarcasm": "false", "hyperbole": "false", "rationale": "off topic"},
        ],
    )
    check("audit catches off-topic non-neutral", len(bad["rule_violations"]) == 1)
    malformed = audit_scores(
        task_keys=[("comment", "c3", "x")],
        score_rows=[
            {"source_type": "comment", "source_id": "c3", "professor_slug": "x",
             "sentiment": "neutral", "score": "abc", "on_topic": "false",
             "sarcasm": "false", "hyperbole": "false", "rationale": "bad score"},
        ],
    )
    check("audit catches malformed score", len(malformed["rule_violations"]) == 1)
    dup = audit_scores(
        task_keys=[("comment", "c1", "a")],
        score_rows=[
            {"source_type": "comment", "source_id": "c1", "professor_slug": "a",
             "sentiment": "positive", "score": "0.5", "on_topic": "true",
             "sarcasm": "false", "hyperbole": "false", "rationale": "one"},
            {"source_type": "comment", "source_id": "c1", "professor_slug": "a",
             "sentiment": "positive", "score": "0.5", "on_topic": "true",
             "sarcasm": "false", "hyperbole": "false", "rationale": "two"},
        ],
    )
    check("audit detects duplicate", dup["duplicates"] == [("comment", "c1", "a")])

    print(f"\n{failures} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Build/track the sentiment work file.")
    p.add_argument("--tier", choices=sorted(TIERS), default="highconf",
                   help="Scoring tier (default: highconf). conv_context = deferred 0.55 pass.")
    p.add_argument("--progress", action="store_true", help="Report scored/total and resume point")
    p.add_argument("--verify", action="store_true", help="Audit scores against tasks")
    p.add_argument("--selftest", action="store_true", help="Run offline unit checks and exit")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())

    tier = TIERS[args.tier]
    if args.progress:
        run_progress(tier)
    elif args.verify:
        sys.exit(run_verify(tier))
    else:
        run_build(tier)


if __name__ == "__main__":
    main()
