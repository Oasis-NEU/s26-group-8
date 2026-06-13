"""
Reddit subreddit scraper for r/NEU

So this scraper avoids Reddit's own servers entirely and pulls from Arctic
Shift (https://arctic-shift.photon-reddit.com), the public Pushshift
successor: full historical posts AND comments by subreddit, date-windowed
pagination, ~1000 requests/window, and an archive that is kept close to
real-time. It is a separate archive service, so bulk pulling it places zero
load on Reddit and carries no ban risk against your Reddit account/IP.

Anti-ban / anti-stuck design
-----------------------------
  * Descriptive User-Agent with a contact (Arctic Shift asks for this).
  * Honors X-Ratelimit-Remaining / X-Ratelimit-Reset and Retry-After.
  * Exponential backoff + jitter on 429 / 422-timeout / 5xx.
  * Conservative inter-request delay (default ~2 req/s).
  * Checkpoint file: resumes from the last seen timestamp so an interruption or
    a transient block never loses work and never re-fetches the whole archive.

Usage
-----
    python reddit_scrape.py                        # posts + comments, full history
    python reddit_scrape.py --subreddits NEU foo   # override subreddit list
    python reddit_scrape.py --no-comments          # posts only
    python reddit_scrape.py --since 2024-01-01     # only archive newer than a date
    python reddit_scrape.py --selftest             # run offline unit checks and exit
"""

__author__ = "RateMyHusky"
__version__ = "1.0.0"

import argparse
import csv
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import requests

# Windows consoles default to cp1252 and can't encode the status glyphs below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ARCTIC_BASE: str = "https://arctic-shift.photon-reddit.com"
DEFAULT_SUBREDDITS: List[str] = ["NEU"]
PAGE_SIZE: int = 100  # Arctic Shift max per request
CONTACT: str = "reddit@ratemyhusky.com"
USER_AGENT: str = f"ratemyhusky-reddit-scraper/{__version__} (contact {CONTACT})"

SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR: str = os.path.join(SCRIPT_DIR, "reddit_data")
CHECKPOINT_FILE: str = os.path.join(OUTPUT_DIR, "reddit_checkpoint.json")

POST_FIELDS: List[str] = [
    "id", "created_utc", "author", "title", "selftext", "score", "upvote_ratio",
    "num_comments", "link_flair_text", "permalink", "url", "over_18",
    "removed_by_category", "subreddit",
]
COMMENT_FIELDS: List[str] = [
    "id", "created_utc", "author", "body", "score", "link_id", "parent_id",
    "permalink", "subreddit",
]

def parse_since(value: Optional[str]) -> int:
    """Parse a --since value into an epoch-second lower bound (inclusive).

    Accepts a YYYY-MM-DD date or a raw epoch integer. Returns 0 when unset.
    """
    if not value:
        return 0
    value = value.strip()
    if value.isdigit():
        return int(value)
    dt: datetime = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def next_after(current_after: int, page: List[Dict[str, Any]]) -> int:
    """Compute the `after` value for the next page given the current page.

    Advances to the newest created_utc on the page. If the whole page shares the
    same timestamp as the current cursor (a same-second cluster larger than the
    page size), bump by one second so pagination always makes forward progress.
    Dedup of the resulting boundary row(s) is handled by the caller via seen-ids.
    """
    if not page:
        return current_after
    newest: int = max(int(row.get("created_utc", 0)) for row in page)
    if newest <= current_after:
        return current_after + 1
    return newest


def build_search_params(subreddit: str, cursor: int) -> Dict[str, Any]:
    """Build Arctic Shift search params for one page.

    The API 400s on near-zero epochs ('after' must be a valid date), so when
    there is no lower bound the param is omitted and ascending sort starts at
    the oldest archived row.
    """
    params: Dict[str, Any] = {"subreddit": subreddit, "sort": "asc", "limit": PAGE_SIZE}
    if cursor > 0:
        params["after"] = cursor
    return params


def is_retryable_status(status: int) -> bool:
    """True for transient statuses worth a backoff-and-retry.

    Besides 429 and 5xx, Arctic Shift returns 422 ("Timeout. Maybe slow down
    a bit") when its backend query times out under load — also transient.
    """
    return status == 429 or status == 422 or status >= 500


def should_pause_for_ratelimit(remaining: Optional[str], threshold: float = 2.0) -> bool:
    """True when the rate-limit budget is low enough that we should wait."""
    if remaining is None:
        return False
    try:
        return float(remaining) <= threshold
    except ValueError:
        return False


def trim(row: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """Keep only the configured fields; normalize whitespace in text bodies."""
    out: Dict[str, Any] = {}
    for k in fields:
        v = row.get(k)
        if isinstance(v, str):
            v = " ".join(v.split())
        out[k] = v
    return out


# ===========================================================================
# Checkpoint
# ===========================================================================

@dataclass
class Checkpoint:
    """Per-(subreddit, kind) progress so runs resume instead of restarting."""

    data: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "Checkpoint":
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return cls(data=json.load(f))
            except (json.JSONDecodeError, OSError):
                print("  ⚠ checkpoint unreadable — starting fresh")
        return cls()

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def get_after(self, subreddit: str, kind: str, default: int) -> int:
        return self.data.get(f"{subreddit}:{kind}", {}).get("last_created_utc", default)

    def set_after(self, subreddit: str, kind: str, value: int, count: int) -> None:
        self.data[f"{subreddit}:{kind}"] = {"last_created_utc": value, "rows": count}


# ===========================================================================
# Arctic Shift client
# ===========================================================================

class ArcticShift:
    """Rate-limit-aware client for the Arctic Shift archive API."""

    def __init__(self, base_delay: float = 0.5, max_retries: int = 5) -> None:
        self.base_delay: float = base_delay
        self.max_retries: int = max_retries
        self.session: requests.Session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _get(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """GET one page, retrying with backoff; returns the `data` list."""
        for attempt in range(self.max_retries):
            try:
                resp: requests.Response = self.session.get(
                    ARCTIC_BASE + path, params=params, timeout=60
                )
            except requests.RequestException as e:
                wait = self._backoff(attempt)
                print(f"  ⚠ network error ({e}); retry in {wait:.1f}s")
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                self._respect_ratelimit(resp)
                payload = resp.json()
                return payload.get("data") or []

            if is_retryable_status(resp.status_code):
                wait = self._retry_wait(resp, attempt)
                print(f"  ⚠ HTTP {resp.status_code}; backing off {wait:.1f}s")
                time.sleep(wait)
                continue

            # 4xx other than 429 — a bad query, not transient. Surface it.
            raise RuntimeError(f"Arctic Shift {resp.status_code}: {resp.text[:200]}")

        raise RuntimeError(f"Arctic Shift: exhausted {self.max_retries} retries for {path}")

    def _respect_ratelimit(self, resp: requests.Response) -> None:
        remaining = resp.headers.get("X-Ratelimit-Remaining")
        reset = resp.headers.get("X-Ratelimit-Reset")
        if should_pause_for_ratelimit(remaining):
            wait = float(reset) + 1 if reset else 5.0
            print(f"  · rate-limit low (remaining={remaining}); pausing {wait:.0f}s")
            time.sleep(wait)
        else:
            time.sleep(self.base_delay)

    def _retry_wait(self, resp: requests.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("X-Ratelimit-Reset")
        if retry_after:
            try:
                return float(retry_after) + 1
            except ValueError:
                pass
        return self._backoff(attempt)

    def _backoff(self, attempt: int) -> float:
        return min(60.0, (2 ** attempt)) + random.uniform(0, 1.0)

    def iter_items(
        self, kind: str, subreddit: str, after: int, since: int
    ) -> Iterator[Dict[str, Any]]:
        """Yield every post/comment for a subreddit newer than `after`.

        `kind` is "posts" or "comments". Walks forward in time using the
        date-windowed `after` cursor, deduping the same-second boundary.
        """
        path = f"/api/{kind}/search"
        cursor = max(after, since)
        seen_at_boundary: set = set()

        while True:
            page = self._get(path, build_search_params(subreddit, cursor))
            if not page:
                return

            new_in_page = 0
            for row in page:
                rid = row.get("id")
                if rid in seen_at_boundary:
                    continue
                new_in_page += 1
                yield row

            advanced = next_after(cursor, page)
            # Rebuild the boundary dedup set for the rows sitting exactly on the
            # new cursor second, so the next page doesn't re-emit them.
            seen_at_boundary = {
                row.get("id") for row in page
                if int(row.get("created_utc", 0)) == advanced
            }
            if advanced == cursor and new_in_page == 0:
                return  # no progress possible
            cursor = advanced
            if len(page) < PAGE_SIZE:
                return


# ===========================================================================
# CSV sink
# ===========================================================================

class CsvWriter:
    """Append-only CSV writer that writes a header once per file."""

    def __init__(self, path: str, fields: List[str]) -> None:
        self.path = path
        self.fields = fields
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=fields, extrasaction="ignore")
        if self._new:
            self._writer.writeheader()

    def write(self, row: Dict[str, Any]) -> None:
        self._writer.writerow(row)

    def close(self) -> None:
        self._fh.close()


# ===========================================================================
# Orchestration
# ===========================================================================

def scrape_subreddit(
    client: ArcticShift, ckpt: Checkpoint, subreddit: str, kind: str,
    fields: List[str], since: int, limit: Optional[int],
) -> int:
    csv_path = os.path.join(OUTPUT_DIR, f"reddit_{subreddit.lower()}_{kind}.csv")
    writer = CsvWriter(csv_path, fields)
    after = ckpt.get_after(subreddit, kind, default=since)
    count = 0
    last_ts = after
    print(f"\n→ r/{subreddit} {kind}: resuming after epoch {after} "
          f"({datetime.fromtimestamp(after, timezone.utc).date() if after else 'beginning'})")
    try:
        for row in client.iter_items(kind, subreddit, after=after, since=since):
            writer.write(trim(row, fields))
            count += 1
            last_ts = max(last_ts, int(row.get("created_utc", 0)))
            if count % 500 == 0:
                ckpt.set_after(subreddit, kind, last_ts, count)
                ckpt.save(CHECKPOINT_FILE)
                print(f"  · {count} {kind} so far "
                      f"(through {datetime.fromtimestamp(last_ts, timezone.utc).date()})")
            if limit and count >= limit:
                print(f"  · reached --limit {limit}")
                break
    finally:
        writer.close()
        ckpt.set_after(subreddit, kind, last_ts, count)
        ckpt.save(CHECKPOINT_FILE)
    print(f"  ✓ wrote {count} {kind} → {csv_path}")
    return count


def run(args: argparse.Namespace) -> None:
    since = parse_since(args.since)
    ckpt = Checkpoint.load(CHECKPOINT_FILE)
    client = ArcticShift(base_delay=args.delay)
    kinds: List[str] = []
    if not args.no_posts:
        kinds.append("posts")
    if not args.no_comments:
        kinds.append("comments")

    for sub in args.subreddits:
        for kind in kinds:
            fields = POST_FIELDS if kind == "posts" else COMMENT_FIELDS
            scrape_subreddit(client, ckpt, sub, kind, fields, since, args.limit)

    print("\n  Done.")


# ===========================================================================
# Offline self-test
# ===========================================================================

def selftest() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(f"  {'✓' if cond else '✗'} {name}")
        if not cond:
            failures += 1

    check("parse_since empty -> 0", parse_since(None) == 0)
    check("parse_since epoch", parse_since("1700000000") == 1700000000)
    check("parse_since date", parse_since("2024-01-01") == 1704067200)

    page = [{"id": "a", "created_utc": 100}, {"id": "b", "created_utc": 150}]
    check("next_after advances to newest", next_after(50, page) == 150)
    check("next_after bumps on stall", next_after(150, [{"id": "x", "created_utc": 150}]) == 151)
    check("next_after empty page keeps cursor", next_after(50, []) == 50)

    check("ratelimit pause when low", should_pause_for_ratelimit("1") is True)
    check("ratelimit no pause when high", should_pause_for_ratelimit("999") is False)
    check("ratelimit no pause when absent", should_pause_for_ratelimit(None) is False)

    row = {"id": "z", "title": "hello   world\n\nfoo", "score": 5, "extra": "drop"}
    trimmed = trim(row, ["id", "title", "score"])
    check("trim keeps fields", set(trimmed) == {"id", "title", "score"})
    check("trim normalizes whitespace", trimmed["title"] == "hello world foo")

    # Arctic Shift rejects near-zero epochs for `after` (HTTP 400), so a
    # no-lower-bound search must omit the param entirely.
    fresh = build_search_params("NEU", 0)
    check("search params omit after when no cursor", "after" not in fresh)
    check("search params keep subreddit/sort/limit",
          (fresh["subreddit"], fresh["sort"], fresh["limit"]) == ("NEU", "asc", PAGE_SIZE))
    check("search params include positive cursor", build_search_params("NEU", 123)["after"] == 123)

    # Arctic Shift signals transient overload with 429, 5xx, and also
    # 422 "Timeout. Maybe slow down a bit" — all must be retried, not raised.
    check("retryable 429", is_retryable_status(429) is True)
    check("retryable 422 timeout", is_retryable_status(422) is True)
    check("retryable 500", is_retryable_status(500) is True)
    check("retryable 503", is_retryable_status(503) is True)
    check("not retryable 400", is_retryable_status(400) is False)
    check("not retryable 404", is_retryable_status(404) is False)

    print(f"\n  {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    p = argparse.ArgumentParser(description="Scrape r/NEU via the Arctic Shift archive (no Reddit API).")
    p.add_argument("--subreddits", nargs="+", default=DEFAULT_SUBREDDITS)
    p.add_argument("--since", help="Lower bound: YYYY-MM-DD or epoch seconds")
    p.add_argument("--limit", type=int, help="Max items per (subreddit, kind) — useful for testing")
    p.add_argument("--delay", type=float, default=0.5, help="Base seconds between requests (~2 req/s default)")
    p.add_argument("--no-posts", action="store_true")
    p.add_argument("--no-comments", action="store_true")
    p.add_argument("--selftest", action="store_true", help="Run offline unit checks and exit")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())
    run(args)


if __name__ == "__main__":
    main()
