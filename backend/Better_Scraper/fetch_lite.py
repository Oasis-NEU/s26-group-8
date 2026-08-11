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

# 0 = disabled (probe showed zero 429s at 5,555 req/min from a residential IP).
# GitHub runners use datacenter IPs that RMP may throttle — set RMP_RATE_LIMIT_RPS
# (e.g. "5") to enable the bucket without a code change.
RATE_LIMIT_REQ_PER_SEC: float = float(os.getenv("RMP_RATE_LIMIT_RPS", "0"))
RATE_LIMIT_BURST: int = int(os.getenv("RMP_RATE_LIMIT_BURST", "10"))

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
        drop_auth: bool = False
        for attempt in range(retries):
            _bucket.acquire()
            try:
                resp: requests.Response = self._session.post(
                    RMP_GRAPHQL_URL,
                    json=payload,
                    timeout=30,
                    # requests removes a header whose per-request value is None
                    headers={"Authorization": None} if drop_auth else None,
                )
                if resp.status_code == 403:
                    if attempt == 0:
                        print(f"  ⚠ Got 403 — retrying this request without auth header...")
                    # Never mutate the shared session: popping the header would
                    # permanently un-auth all 15 worker threads for the whole run.
                    drop_auth = True
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
        cursor: Optional[str] = None
        has_next: bool = True
        page_count: int = 0

        pbar: tqdm = tqdm(desc="Fetching professors", unit=" profs")

        while has_next:
            page_count += 1
            if page_count > MAX_SEARCH_PAGES:
                print(f"\n  ⚠ Hit {MAX_SEARCH_PAGES}-page search limit — stopping pagination")
                break
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
                print("\n  ✗ Failed to fetch professors")
                break

            search_data: Optional[Dict[str, Any]] = (
                ((data.get("data") or {}).get("search") or {}).get("teachers") or {}
            )
            if not search_data:
                print(f"\n  ✗ Unexpected response: {json.dumps(data)[:200]}")
                break

            edges: List[Dict[str, Any]] = search_data.get("edges", [])
            page_info: Dict[str, Any] = search_data.get("pageInfo", {})

            if search_data.get("didFallback"):
                pbar.close()
                sys.exit(
                    "✗ Scrape aborted: RMP search fell back to a cross-school query "
                    "(didFallback=true) — results are not school-filtered. "
                    "Aborting without writing output."
                )

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
            if not edges:
                break

        pbar.close()
        print(f"  ✓ Collected {len(self.professors_list)} professors")

    # ------------------------------------------------------------------
    # Phase 2: Collect reviews
    # ------------------------------------------------------------------

    def _parse_ratings(
        self, data: Dict[str, Any]
    ) -> Tuple[List[Review], bool, Optional[str]]:
        reviews: List[Review] = []
        teacher_node: Optional[Dict[str, Any]] = (data.get("data") or {}).get("node")
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
        """Fetch all reviews for a single professor. Thread-safe."""
        reviews: List[Review] = []
        cursor: Optional[str] = None
        has_next: bool = True
        page_count: int = 0

        while has_next:
            page_count += 1
            if page_count > MAX_REVIEW_PAGES:
                break
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
                break

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
        total_reviews: int = 0

        pbar: tqdm = tqdm(total=total, desc="Fetching reviews", unit=" prof")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures: Dict[Future, Professor] = {
                executor.submit(self._fetch_reviews_for_professor, prof): prof
                for prof in profs_with_ratings
            }

            for future in as_completed(futures):
                prof: Professor = futures[future]
                try:
                    reviews: List[Review] = future.result()
                    prof.reviews = reviews
                    total_reviews += len(reviews)
                except Exception:
                    pass
                pbar.update(1)

        pbar.close()

        # Second pass: retry failed professors with a small thread pool
        failed_profs = [p for p in profs_with_ratings if not p.reviews]
        if failed_profs:
            print(f"  Retrying {len(failed_profs)} failed professors...")
            with ThreadPoolExecutor(max_workers=5) as retry_executor:
                retry_futures: Dict[Future, Professor] = {
                    retry_executor.submit(self._fetch_reviews_for_professor, prof): prof
                    for prof in failed_profs
                }
                for future in tqdm(as_completed(retry_futures), total=len(failed_profs), desc="Retrying", unit=" prof"):
                    prof: Professor = retry_futures[future]
                    try:
                        reviews: List[Review] = future.result()
                        prof.reviews = reviews
                        total_reviews += len(reviews)
                    except Exception:
                        pass
            recovered = sum(1 for p in failed_profs if p.reviews)
            print(f"  Retry recovered {recovered}/{len(failed_profs)} professors")

        profs_done: int = sum(1 for p in profs_with_ratings if p.reviews)
        print(
            f"  ✓ Fetched {total_reviews} reviews from "
            f"{profs_done}/{total} professors"
            + (f" ({total - profs_done} still failed)" if profs_done < total else "")
        )

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

    def dump_reviews_to_csv(self, file_path: str) -> None:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        fieldnames: List[str] = [
            "professor_name", "department", "overall_rating", "course",
            "quality", "difficulty", "date", "tags", "attendance",
            "grade", "textbook", "online_class", "comment",
        ]
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer: csv.DictWriter = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for prof in self.professors_list:
                for row in prof.review_csv_rows():
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
    parser.add_argument(
        "--expect_school",
        help='Abort if the scraped school name differs (default "Northeastern University"; pass "" to disable)',
        type=str,
        default="Northeastern University",
    )

    args: argparse.Namespace = parser.parse_args()

    school: RMPSchool = RMPSchool(args.sid, scrape_reviews=not args.no_reviews)

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

    if args.expect_school and school.school_name != args.expect_school:
        school.close()
        sys.exit(
            f"✗ Scrape failed: school name {school.school_name!r} != expected "
            f"{args.expect_school!r} (wrong-school fallback or RMP API change). "
            "Aborting without writing output."
        )

    school_name_fp: str = school.school_name.replace(" ", "").replace("-", "_").lower()
    script_dir: str = os.path.dirname(os.path.abspath(__file__))

    professors_csv: str = args.file_path or os.path.join(
        script_dir, "output_data", f"{school_name_fp}_professors.csv"
    )

    school.dump_professors_to_csv(professors_csv)

    if not args.no_reviews:
        school.dump_reviews_to_csv(professors_csv.replace("_professors.csv", "_reviews.csv"))

    if args.json:
        school.dump_to_json(professors_csv.replace("_professors.csv", "_full.json"))

    school.close()
    print("\n  Done!\n")


if __name__ == "__main__":
    main()