"""
RateMyProfessor.com Web Scraper — Lightweight Edition

No Selenium, no Chrome, no browser. Pure HTTP requests.
Runs on any server with ~20MB RAM.

Usage:
    python fetch_lite.py -s 696
    python fetch_lite.py -s 696 --no-reviews
    python fetch_lite.py -s 696 --json
"""

import base64
import csv
import json
import os
import sys
import time
import argparse
import logging
import threading
from typing import List, Dict, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, Future

import requests
from tqdm import tqdm

from models import Professor, Review

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RMP_GRAPHQL_URL: str = "https://www.ratemyprofessors.com/graphql"
RMP_BASE_URL: str = "https://www.ratemyprofessors.com"
GRAPHQL_PAGE_SIZE: int = 1000
MAX_REVIEWS_PER_PROFESSOR: Optional[int] = None
MAX_SEARCH_PAGES: int = 10   # safety limit: stop after 10 search pages (~10k professors)
MAX_REVIEW_PAGES: int = 50   # safety limit: stop after 50 review pages (~5k reviews per prof)

logging.basicConfig(level=logging.WARNING)
logger: logging.Logger = logging.getLogger(__name__)

RATE_LIMIT_REQ_PER_SEC: float = 0.0  # 0 = disabled (no rate limiting from residential IP; probe showed zero 429s at 5,555 req/min)
RATE_LIMIT_BURST: int = 0

# How many professors may fail their review fetch before the run is called bad.
# Exiting non-zero on the first failure meant one unlucky request cost every
# professor a week of freshness, since the weekly workflow aborts before the DB
# load and the data-store push. Above the threshold it is no longer flakiness —
# a block, an API change — and aborting is right.
MAX_TOLERATED_REVIEW_FAILURES_PCT: float = 1.0
MIN_TOLERATED_REVIEW_FAILURES: int = 5   # floor, so a small school isn't stricter


def failure_tolerance(attempted: int) -> int:
    """How many failed review fetches a run may carry and still be trusted."""
    return max(MIN_TOLERATED_REVIEW_FAILURES,
               int(attempted * MAX_TOLERATED_REVIEW_FAILURES_PCT / 100))

# ---------------------------------------------------------------------------
# Token bucket rate limiter
# ---------------------------------------------------------------------------


class TokenBucket:
    """Thread-safe token bucket rate limiter. Disabled when rate <= 0."""
    def __init__(self, rate: float, capacity: int):
        self._rate = rate
        self._disabled = rate <= 0
        # When enabled, capacity must be >= 1 or acquire() can block forever.
        self._capacity = max(1, capacity) if not self._disabled else capacity
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> None:
        if self._disabled:
            return
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                sleep_for = (tokens - self._tokens) / self._rate
            time.sleep(sleep_for)
    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now


_bucket: TokenBucket = TokenBucket(RATE_LIMIT_REQ_PER_SEC, RATE_LIMIT_BURST)


def load_previous_reviews(file_path: str) -> List[Dict[str, str]]:
    """Last run's review rows, or [] on the first run. Must be called before
    dump_reviews_to_csv truncates the file it is reading."""
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def carried_forward_rows(
    previous_rows: List[Dict[str, str]],
    fresh_names: set,
    failed_names: set,
) -> List[Dict[str, str]]:
    """Previous rows to re-emit for professors whose review fetch failed.

    Tolerating a failure is only safe if the professor's reviews survive it.
    precompute counts num_ratings from the rows present in this CSV, and
    catalog_row emits rmp_rating=None at num_ratings 0 — so a tolerated
    professor with no rows loses their rating on the site rather than merely
    going stale.

    Names, not ids, because the CSV has no id column. That is safe here only
    because a name is skipped entirely once any professor carrying it produced
    fresh rows: 49 scraped professors share a name with a different profile
    page, and carrying one namesake's old rows alongside the other's fresh ones
    would duplicate reviews. Conservative in the right direction — the worst
    case is a namesake pair going stale together for a week.
    """
    resurrect = failed_names - fresh_names
    if not resurrect:
        return []
    return [r for r in previous_rows if r.get("professor_name") in resurrect]


class ProfessorFetchError(Exception):
    """The teacher search stopped before RMP ran out of pages.

    The phase-1 twin of ReviewFetchError, and the more destructive of the two.
    precompute drops and rebuilds professors_catalog from the professor CSV, so
    a short search truncates the live site, and the workflow then force-pushes
    that CSV over the data store as an orphan commit — there is no history to
    roll back to.

    The old loop broke out of pagination on a failed request and returned what
    it had, leaving only the workflow's row-count floor as a guard. That floor
    did not cover the case it most needed to: 3,892 professors is four pages of
    1,000, and losing the last (892 rows) left exactly 3,000 against a `< 3000`
    check. Raising means a truncated search can never reach the CSV at all.
    """


class ReviewFetchError(Exception):
    """A ratings request failed, as opposed to returning zero ratings.

    The distinction matters: RMP serves zero rating nodes for some professors
    whose summary counters still claim numRatings >= 1 (a rating was deleted and
    the aggregate was never recalculated — its own website says "doesn't have any
    ratings yet" on the same page it prints an average). Those professors are
    complete, not failed. A request that errors or gets rate-limited is a real
    gap and must be retried and reported.
    """

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------
TEACHER_SEARCH_QUERY: str = """
query TeacherSearchPaginationQuery(
    $count: Int!,
    $cursor: String,
    $query: TeacherSearchQuery!
) {
    search: newSearch {
        teachers(query: $query, first: $count, after: $cursor) {
            didFallback
            edges {
                cursor
                node {
                    id
                    legacyId
                    firstName
                    lastName
                    department
                    school { id name }
                    avgRating
                    numRatings
                    avgDifficulty
                    wouldTakeAgainPercent
                }
            }
            pageInfo { hasNextPage endCursor }
        }
    }
}
"""

TEACHER_RATINGS_QUERY: str = """
query TeacherRatingsPageQuery(
    $id: ID!,
    $count: Int!,
    $cursor: String
) {
    node(id: $id) {
        ... on Teacher {
            ratings(first: $count, after: $cursor) {
                edges {
                    node {
                        comment
                        class
                        date
                        qualityRating
                        difficultyRatingRounded
                        ratingTags
                        grade
                        isForOnlineClass
                        attendanceMandatory
                        textbookIsUsed
                    }
                }
                pageInfo { hasNextPage endCursor }
            }
        }
    }
}
"""


# ===========================================================================
# RMPSchool — lightweight, no browser
# ===========================================================================

class RMPSchool:
    def __init__(self, school_id: int, scrape_reviews: bool = True) -> None:
        self.school_id: int = school_id
        self.school_name: str = "Unknown School"
        self.professors_list: List[Professor] = []
        self._interrupted: bool = False
        # graphql_id -> error, for professors whose reviews could not be fetched
        # at all. Keyed by id rather than name because names are not unique.
        self.failed_review_fetches: Dict[str, str] = {}
        # Professors whose reviews we tried to fetch — the denominator the
        # failure tolerance is measured against.
        self.review_fetch_attempts: int = 0

        self._graphql_school_id: str = base64.b64encode(
            f"School-{school_id}".encode()
        ).decode()

        # Set up HTTP session with browser-like headers
        self._session: requests.Session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "Referer": "https://www.ratemyprofessors.com/",
            "Origin": "https://www.ratemyprofessors.com",
            "Content-Type": "application/json",
            "Authorization": "Basic dGVzdDp0ZXN0",
        })

        # Grab cookies from a real page first
        print("  Establishing session...")
        try:
            cookie_resp: requests.Response = self._session.get(
                f"{RMP_BASE_URL}/school/{school_id}",
                timeout=15,
            )
            cookie_resp.raise_for_status()
            print(f"  ✓ Session ready ({len(self._session.cookies)} cookies)")
        except Exception as e:
            print(f"  ⚠ Cookie fetch failed: {e} — trying without cookies")

        # Phase 1
        self._collect_professors()

        print(f"\n{'='*60}")
        print(f"  RMP Scraper (Lite) — {self.school_name}")
        print(f"  Professors found: {len(self.professors_list)}")
        print(f"{'='*60}\n")

        # Phase 2
        if scrape_reviews and self.professors_list:
            self._scrape_all_reviews()

    # ------------------------------------------------------------------
    # GraphQL request
    # ------------------------------------------------------------------

    def _graphql_post(
        self, payload: Dict[str, Any], retries: int = 2
    ) -> Optional[Dict[str, Any]]:
        """Make a GraphQL POST request with rate limiting and fail-fast retries."""
        for attempt in range(retries):
            _bucket.acquire()
            try:
                resp: requests.Response = self._session.post(
                    RMP_GRAPHQL_URL,
                    json=payload,
                    timeout=30,
                )
                if resp.status_code == 403:
                    if attempt == 0:
                        print(f"  ⚠ Got 403 — retrying with different headers...")
                    self._session.headers.pop("Authorization", None)
                    time.sleep(2)
                    continue
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    if attempt < retries - 1:
                        time.sleep(wait)
                        continue
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                logger.error(f"GraphQL request failed: {e}")
                return None
        return None

    # ------------------------------------------------------------------
    # Phase 1: Collect professors
    # ------------------------------------------------------------------

    def _collect_professors(self) -> None:
        """Page through the teacher search until RMP says there is no next page.

        Raises ProfessorFetchError rather than returning a short list — see the
        exception's docstring for why a truncated search is worse than a
        truncated review fetch.
        """
        cursor: Optional[str] = None
        has_next: bool = True
        page_count: int = 0

        pbar: tqdm = tqdm(desc="Fetching professors", unit=" profs")

        while has_next:
            page_count += 1
            if page_count > MAX_SEARCH_PAGES:
                pbar.close()
                raise ProfessorFetchError(
                    f"hit the {MAX_SEARCH_PAGES}-page search limit with more pages "
                    f"pending ({len(self.professors_list)} professors so far)"
                )
            payload: Dict[str, Any] = {
                "query": TEACHER_SEARCH_QUERY,
                "variables": {
                    "count": GRAPHQL_PAGE_SIZE,
                    "cursor": cursor or "",
                    "query": {
                        "text": "",
                        "schoolID": self._graphql_school_id,
                        "fallback": True,
                    },
                },
            }

            data: Optional[Dict[str, Any]] = self._graphql_post(payload)
            if not data:
                pbar.close()
                raise ProfessorFetchError(
                    f"teacher search failed on page {page_count} "
                    f"({len(self.professors_list)} professors collected before the failure)"
                )

            search_data: Optional[Dict[str, Any]] = (
                data.get("data", {}).get("search", {}).get("teachers", {})
            )
            if not search_data:
                pbar.close()
                raise ProfessorFetchError(
                    f"unexpected response on page {page_count} "
                    f"({len(self.professors_list)} professors collected): "
                    f"{json.dumps(data)[:200]}"
                )

            edges: List[Dict[str, Any]] = search_data.get("edges", [])
            page_info: Dict[str, Any] = search_data.get("pageInfo", {})

            for edge in edges:
                node: Dict[str, Any] = edge.get("node", {})

                if self.school_name == "Unknown School":
                    school_info: Optional[Dict[str, str]] = node.get("school")
                    if school_info:
                        self.school_name = school_info.get("name", "Unknown School")

                legacy_id: Optional[int] = node.get("legacyId")
                wta_raw: Optional[float] = node.get("wouldTakeAgainPercent")
                wta_str: Optional[str] = None
                if wta_raw is not None and wta_raw >= 0:
                    wta_str = f"{wta_raw:.0f}%"

                avg_rating: Optional[float] = node.get("avgRating")
                avg_diff: Optional[float] = node.get("avgDifficulty")

                prof: Professor = Professor(
                    name=f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
                    department=node.get("department"),
                    rating=str(avg_rating) if avg_rating is not None else None,
                    num_ratings=str(node.get("numRatings", "N/A")),
                    would_take_again_pct=wta_str,
                    level_of_difficulty=str(avg_diff) if avg_diff is not None else None,
                    professor_url=f"{RMP_BASE_URL}/professor/{legacy_id}" if legacy_id else "",
                    graphql_id=node.get("id"),
                )
                self.professors_list.append(prof)

            pbar.update(len(edges))
            has_next = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")
            # An empty page is a complete answer only if RMP agrees it is the
            # last one. Rows missing while a next page is promised means the
            # cursor walk broke, which is data loss, not an empty school.
            if not edges and has_next:
                pbar.close()
                raise ProfessorFetchError(
                    f"page {page_count} returned no professors but RMP reports "
                    f"another page ({len(self.professors_list)} professors collected)"
                )

        pbar.close()
        print(f"  ✓ Collected {len(self.professors_list)} professors")

    # ------------------------------------------------------------------
    # Phase 2: Collect reviews
    # ------------------------------------------------------------------

    def _parse_ratings(
        self, data: Dict[str, Any]
    ) -> Tuple[List[Review], bool, Optional[str]]:
        reviews: List[Review] = []
        teacher_node: Optional[Dict[str, Any]] = data.get("data", {}).get("node")
        if not teacher_node:
            return reviews, False, None

        ratings_conn: Optional[Dict[str, Any]] = teacher_node.get("ratings")
        if not ratings_conn:
            return reviews, False, None

        edges: List[Dict[str, Any]] = ratings_conn.get("edges", [])
        page_info: Dict[str, Any] = ratings_conn.get("pageInfo", {})

        for edge in edges:
            r: Dict[str, Any] = edge.get("node", {})

            tb_val: Optional[bool] = r.get("textbookIsUsed")
            tb_str: Optional[str] = "Yes" if tb_val is True else ("No" if tb_val is False else None)

            att_val: Optional[str] = r.get("attendanceMandatory")
            att_str: Optional[str] = None
            if att_val == "mandatory":
                att_str = "Mandatory"
            elif att_val == "non mandatory":
                att_str = "Not Mandatory"
            elif att_val:
                att_str = att_val

            quality_val: Optional[int] = r.get("qualityRating")
            tags_raw: Optional[str] = r.get("ratingTags")
            if tags_raw:
                tags_raw = " ".join(tags_raw.split())

            raw_comment: Optional[str] = r.get("comment")
            if raw_comment:
                raw_comment = " ".join(raw_comment.split())

            online_val: Optional[bool] = r.get("isForOnlineClass")
            online_str: Optional[str] = "Yes" if online_val is True else ("No" if online_val is False else None)

            review: Review = Review(
                course=r.get("class"),
                quality=str(quality_val) if quality_val is not None else None,
                difficulty=str(r.get("difficultyRatingRounded")) if r.get("difficultyRatingRounded") is not None else None,
                date=r.get("date"),
                tags=tags_raw,
                attendance=att_str,
                grade=r.get("grade"),
                textbook=tb_str,
                online_class=online_str,
                comment=raw_comment,
            )
            reviews.append(review)

        return reviews, page_info.get("hasNextPage", False), page_info.get("endCursor")

    def _fetch_reviews_for_professor(self, prof: Professor) -> List[Review]:
        """Fetch all reviews for a single professor. Thread-safe.

        Raises ReviewFetchError if a request fails, rather than returning what it
        managed to collect. A silent partial return is indistinguishable from "this
        professor has no reviews", which is how a rate-limited page turns into
        permanently missing data: the row count still looks plausible, so nothing
        downstream notices. Raising lets the retry pass do its job and keeps the
        final tally honest.
        """
        reviews: List[Review] = []
        cursor: Optional[str] = None
        has_next: bool = True
        page_count: int = 0

        while has_next:
            page_count += 1
            if page_count > MAX_REVIEW_PAGES:
                raise ReviewFetchError(
                    f"{prof.name}: hit the {MAX_REVIEW_PAGES}-page limit with more "
                    f"pages pending ({len(reviews)} reviews so far)"
                )
            payload: Dict[str, Any] = {
                "query": TEACHER_RATINGS_QUERY,
                "variables": {
                    "id": prof.graphql_id,
                    "count": 100,
                    "cursor": cursor or "",
                },
            }
            data: Optional[Dict[str, Any]] = self._graphql_post(payload)
            if not data:
                raise ReviewFetchError(
                    f"{prof.name}: ratings request failed on page {page_count} "
                    f"({len(reviews)} reviews collected before the failure)"
                )

            new_reviews, has_next, cursor = self._parse_ratings(data)
            reviews.extend(new_reviews)
            if not new_reviews:
                break

            if MAX_REVIEWS_PER_PROFESSOR and len(reviews) >= MAX_REVIEWS_PER_PROFESSOR:
                reviews = reviews[:MAX_REVIEWS_PER_PROFESSOR]
                break

        return reviews

    def _scrape_all_reviews(self) -> None:
        profs_with_ratings: List[Professor] = [
            p for p in self.professors_list
            if p.graphql_id and p.num_ratings not in (None, "0", "N/A")
        ]
        skipped: int = len(self.professors_list) - len(profs_with_ratings)
        if skipped > 0:
            print(f"  Skipping {skipped} professors with 0 ratings")

        total: int = len(profs_with_ratings)
        self.review_fetch_attempts = total
        total_reviews: int = 0

        # Outcome per professor: absent = fetched fine (possibly zero ratings),
        # present = the last attempt raised. Only the latter is missing data.
        #
        # Keyed by graphql_id, not name. 49 of the 3,892 scraped professors share
        # a name with a different RMP profile page (Rick Arrowood has three), and
        # name keys let namesakes overwrite each other: one's success popped the
        # other's genuine failure, which then got misreported as a phantom and
        # swallowed by the exit check, while a retry wiped reviews that had
        # already been fetched. graphql_id is safe as a key because
        # profs_with_ratings only keeps professors that have one.
        errors: Dict[str, str] = {}

        def run_pass(profs: List[Professor], workers: int, desc: str) -> None:
            nonlocal total_reviews
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures: Dict[Future, Professor] = {
                    ex.submit(self._fetch_reviews_for_professor, p): p for p in profs
                }
                for future in tqdm(as_completed(futures), total=len(profs),
                                   desc=desc, unit=" prof"):
                    prof: Professor = futures[future]
                    try:
                        reviews: List[Review] = future.result()
                    except Exception as e:
                        errors[prof.graphql_id] = str(e)
                        continue
                    errors.pop(prof.graphql_id, None)
                    prof.reviews = reviews
                    total_reviews += len(reviews)

        run_pass(profs_with_ratings, 10, "Fetching reviews")

        # Retry only genuine failures. The old code retried every professor who
        # came back with zero reviews, which meant re-fetching hundreds of
        # legitimately-empty professors every run and reporting them as failures.
        for attempt, workers in ((2, 5), (3, 1)):
            if not errors:
                break
            retry = [p for p in profs_with_ratings if p.graphql_id in errors]
            print(f"  Retry pass {attempt}: {len(retry)} professors whose requests failed")
            total_reviews -= sum(len(p.reviews) for p in retry)  # avoid double count
            for p in retry:
                p.reviews = []
            run_pass(retry, workers, f"Retrying (pass {attempt})")

        # RMP claims a rating exists but serves no rating nodes — verified against
        # its own website, which prints "doesn't have any ratings yet" alongside an
        # average. Nothing to fetch, so these are complete, not missing.
        phantom = [p for p in profs_with_ratings
                   if not p.reviews and p.graphql_id not in errors]
        fetched = sum(1 for p in profs_with_ratings if p.reviews)

        print(f"  ✓ Fetched {total_reviews} reviews from {fetched}/{total} professors")
        if phantom:
            print(f"  ℹ {len(phantom)} professors have a rating count but no ratings on "
                  f"RMP (deleted reviews, stale counters) — nothing to fetch:")
            for p in phantom[:10]:
                print(f"      {p.name} (RMP claims {p.num_ratings})")
            if len(phantom) > 10:
                print(f"      ... and {len(phantom) - 10} more")
        if errors:
            print(f"  ✗ {len(errors)} professors could NOT be fetched after 3 passes — "
                  f"this IS missing data:")
            for err in list(errors.values())[:10]:
                print(f"      {err}")   # already prefixed with the professor's name
            if len(errors) > 10:
                print(f"      ... and {len(errors) - 10} more")
        self.failed_review_fetches = errors

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def dump_professors_to_csv(self, file_path: str) -> None:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        fieldnames: List[str] = [
            "name", "department", "rating", "num_ratings",
            "would_take_again_pct", "level_of_difficulty", "professor_url",
        ]
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer: csv.DictWriter = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for prof in self.professors_list:
                writer.writerow(prof.flat_csv_row())
        print(f"  ✓ Professor CSV saved to: {file_path}")

    def dump_reviews_to_csv(
        self, file_path: str, extra_rows: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Write this run's reviews, plus any rows carried forward for
        professors whose fetch failed (see carried_forward_rows)."""
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        fieldnames: List[str] = [
            "professor_name", "department", "overall_rating", "course",
            "quality", "difficulty", "date", "tags", "attendance",
            "grade", "textbook", "online_class", "comment",
        ]
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer: csv.DictWriter = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for prof in self.professors_list:
                for row in prof.review_csv_rows():
                    writer.writerow(row)
            for row in extra_rows or []:
                writer.writerow(row)
        print(f"  ✓ Reviews CSV saved to: {file_path}")

    def dump_to_json(self, file_path: str) -> None:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        data: Dict[str, Any] = {
            "school_id": self.school_id,
            "school_name": self.school_name,
            "num_professors": len(self.professors_list),
            "professors": [p.to_dict() for p in self.professors_list],
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  ✓ JSON saved to: {file_path}")

    def close(self) -> None:
        self._session.close()


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="RMP Scraper (Lightweight — no browser needed)"
    )
    parser.add_argument("-s", "--sid", help="School ID", type=int, default=696)
    parser.add_argument("-f", "--file_path", help="Output CSV path", type=str)
    parser.add_argument("--json", help="Also export JSON", action="store_true")
    parser.add_argument("--no-reviews", help="Skip reviews", action="store_true")

    args: argparse.Namespace = parser.parse_args()

    # A truncated search must not reach the CSV. Exit non-zero WITHOUT writing,
    # so the previous data store snapshot survives untouched.
    try:
        school: RMPSchool = RMPSchool(args.sid, scrape_reviews=not args.no_reviews)
    except ProfessorFetchError as e:
        sys.exit(
            f"✗ Scrape failed: {e}. RMP reported more professors than were "
            "collected, so the result is incomplete. Aborting without writing "
            "output to preserve existing data."
        )

    # Fail loud instead of writing an empty CSV. For a fixed, populated school
    # an empty result never happens legitimately — it means RMP blocked the
    # requests (403/429) or changed its API. Exit non-zero WITHOUT writing so
    # the failure surfaces here and existing output is left untouched.
    if not school.professors_list:
        school.close()
        sys.exit(
            f"✗ Scrape failed: 0 professors collected for school {args.sid}. "
            "Likely a 403/429 block or an RMP API change (see logs above). "
            "Aborting without writing output to preserve existing data."
        )

    school_name_fp: str = school.school_name.replace(" ", "").replace("-", "_").lower()
    script_dir: str = os.path.dirname(os.path.abspath(__file__))

    professors_csv: str = args.file_path or os.path.join(
        script_dir, "output_data", f"{school_name_fp}_professors.csv"
    )

    school.dump_professors_to_csv(professors_csv)

    failures: Dict[str, str] = school.failed_review_fetches

    if not args.no_reviews:
        reviews_csv: str = professors_csv.replace("_professors.csv", "_reviews.csv")
        # Read the old file before the write below truncates it, and keep the
        # rows of any professor we failed to re-fetch. Without this, tolerating
        # a failure costs that professor their rating rather than a week of
        # freshness — precompute reads num_ratings off these rows.
        carried: List[Dict[str, Any]] = []
        if failures:
            failed_names = {p.name for p in school.professors_list
                            if p.graphql_id in failures}
            fresh_names = {p.name for p in school.professors_list if p.reviews}
            carried = carried_forward_rows(
                load_previous_reviews(reviews_csv), fresh_names, failed_names)
            if carried:
                print(f"  ℹ Carried forward {len(carried)} previous reviews for "
                      f"{len(failed_names - fresh_names)} professors whose fetch failed")
        school.dump_reviews_to_csv(reviews_csv, extra_rows=carried)

    if args.json:
        school.dump_to_json(professors_csv.replace("_professors.csv", "_full.json"))

    attempted: int = school.review_fetch_attempts
    school.close()
    print("\n  Done!\n")

    # Exit non-zero *after* writing, so the good rows are preserved. A handful of
    # failed professors is flakiness and is now tolerated — they keep last run's
    # reviews and the refresh proceeds for everyone else. Past the threshold it
    # is a block or an API change, and stopping before the DB load is right.
    if failures:
        tolerance: int = failure_tolerance(attempted)
        if len(failures) > tolerance:
            sys.exit(
                f"✗ {len(failures)} of {attempted} professors' reviews could not be "
                f"fetched after 3 passes, over the tolerance of {tolerance}. That is "
                "a block or an API change rather than flakiness. CSVs were written, "
                "but nothing downstream should trust them."
            )
        print(
            f"  ⚠ {len(failures)} of {attempted} professors' reviews could not be "
            f"fetched (tolerance {tolerance}). They keep their previous reviews; "
            "the next run should pick them up."
        )


if __name__ == "__main__":
    main()