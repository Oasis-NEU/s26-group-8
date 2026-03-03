"""
RateMyProfessor.com Web Scraper

Phase 1: Uses RMP's GraphQL API (via browser fetch) to collect all professors.
         Returns up to 1000 professors per request with cursor pagination.
         ~5-15 seconds for 3000+ professors.

Phase 2: Uses RMP's GraphQL API (via browser fetch) to collect individual
         reviews for each professor. No page navigation — just API calls.
         ~0.3s per professor vs ~5s with Selenium page loads.

Only one Chrome instance is opened (headless) to establish auth cookies.
All data flows through GraphQL — no XPath scraping needed.

Press 'q' at any time during scraping to save progress and exit cleanly.

Usage:
    python fetch.py -s <SCHOOL_ID>                  # full scrape with reviews
    python fetch.py -s <SCHOOL_ID> --no-reviews     # summary only (instant)
    python fetch.py -s <SCHOOL_ID> --json           # also export JSON
"""

__author__ = "Benjamin"
__version__ = "3.1.0"

# Standard library imports
import base64
import csv
import json
import os
import time
import argparse
import logging
import threading
from typing import List, Dict, Optional, Any, Tuple

# Progress bar
from tqdm import tqdm

# Selenium imports (only used for browser-context GraphQL calls)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.remote.webdriver import WebDriver

# Local imports
from models import Professor, Review

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RMP_GRAPHQL_URL: str = "https://www.ratemyprofessors.com/graphql"
RMP_BASE_URL: str = "https://www.ratemyprofessors.com"

# How many professors to request per GraphQL page (max 1000)
GRAPHQL_PAGE_SIZE: int = 1000

# Maximum reviews to fetch per professor (None = all)
MAX_REVIEWS_PER_PROFESSOR: Optional[int] = None

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger: logging.Logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quit listener — runs in a background thread, sets a flag on 'q'
# ---------------------------------------------------------------------------

class QuitListener:
    """Listens for 'q' keypress in a background thread.
    Uses msvcrt on Windows for instant detection, stdin on Unix."""

    def __init__(self) -> None:
        self.quit_requested: bool = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start listening for 'q' in a daemon thread."""
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        print("  (Press 'q' at any time to save progress and exit)\n")

    def _listen(self) -> None:
        """Block on stdin waiting for 'q'."""
        try:
            import msvcrt  # Windows only
            while not self.quit_requested:
                if msvcrt.kbhit():
                    key: bytes = msvcrt.getch()
                    if key in (b"q", b"Q"):
                        self.quit_requested = True
                        return
                time.sleep(0.1)
        except ImportError:
            # Unix/Mac fallback — requires pressing Enter after 'q'
            try:
                while not self.quit_requested:
                    line: str = input()
                    if line.strip().lower() == "q":
                        self.quit_requested = True
                        return
            except EOFError:
                pass

    @property
    def should_quit(self) -> bool:
        return self.quit_requested


# Global quit listener
_quit: QuitListener = QuitListener()


# ---------------------------------------------------------------------------
# GraphQL query templates
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
                    school {
                        id
                        name
                    }
                    avgRating
                    numRatings
                    avgDifficulty
                    wouldTakeAgainPercent
                }
            }
            pageInfo {
                hasNextPage
                endCursor
            }
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
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
    }
}
"""


# ===========================================================================
# RMPSchool — orchestrates the full scrape
# ===========================================================================

class RMPSchool:
    """Represents a school on RateMyProfessor.com.

    Supports graceful shutdown: press 'q' to save progress and exit.
    """

    def __init__(self, school_id: int, scrape_reviews: bool = True) -> None:
        self.school_id: int = school_id
        self.school_name: str = "Unknown School"
        self.professors_list: List[Professor] = []
        self.driver: Optional[WebDriver] = None
        self._interrupted: bool = False

        school_id_str: str = f"School-{school_id}"
        self._graphql_school_id: str = base64.b64encode(
            school_id_str.encode()
        ).decode()

        self._init_driver()

        assert self.driver is not None
        self.driver.get(f"{RMP_BASE_URL}/school/{school_id}")
        time.sleep(3)

        self._collect_professors_via_graphql()

        print(f"\n{'='*60}")
        print(f"  RMP Scraper — {self.school_name}")
        print(f"  Professors found: {len(self.professors_list)}")
        print(f"{'='*60}\n")

        if scrape_reviews and self.professors_list and not _quit.should_quit:
            self._scrape_all_reviews()

    # ------------------------------------------------------------------
    # Phase 1: GraphQL professor collection
    # ------------------------------------------------------------------

    def _graphql_request(
        self, cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        assert self.driver is not None

        variables: Dict[str, Any] = {
            "count": GRAPHQL_PAGE_SIZE,
            "cursor": cursor or "",
            "query": {
                "text": "",
                "schoolID": self._graphql_school_id,
                "fallback": True,
            },
        }

        payload: Dict[str, Any] = {
            "query": TEACHER_SEARCH_QUERY,
            "variables": variables,
        }

        js_script: str = """
            var callback = arguments[arguments.length - 1];
            var payload = arguments[0];
            var url = arguments[1];
            fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            .then(function(resp) { return resp.json(); })
            .then(function(data) { callback(JSON.stringify(data)); })
            .catch(function(err) { callback(JSON.stringify({"error": err.toString()})); });
        """

        self.driver.set_script_timeout(30)
        raw_result: str = self.driver.execute_async_script(
            js_script, payload, RMP_GRAPHQL_URL
        )
        result: Dict[str, Any] = json.loads(raw_result)

        if "error" in result:
            raise RuntimeError(f"Browser GraphQL fetch failed: {result['error']}")

        return result

    def _collect_professors_via_graphql(self) -> None:
        cursor: Optional[str] = None
        has_next: bool = True
        page_num: int = 0

        pbar: tqdm = tqdm(
            desc="Fetching professors",
            unit=" profs",
            ncols=120,
        )

        while has_next:
            if _quit.should_quit:
                self._interrupted = True
                break

            page_num += 1
            try:
                data: Dict[str, Any] = self._graphql_request(cursor)
            except Exception as e:
                logger.error(f"Request failed on page {page_num}: {e}")
                print(f"\n  ✗ Request failed: {e}")
                break

            search_data: Optional[Dict[str, Any]] = (
                data.get("data", {})
                .get("search", {})
                .get("teachers", {})
            )

            if not search_data:
                break

            edges: List[Dict[str, Any]] = search_data.get("edges", [])
            page_info: Dict[str, Any] = search_data.get("pageInfo", {})

            for edge in edges:
                node: Dict[str, Any] = edge.get("node", {})

                if self.school_name == "Unknown School":
                    school_info: Optional[Dict[str, str]] = node.get("school")
                    if school_info:
                        self.school_name = school_info.get("name", "Unknown School")

                legacy_id: Optional[int] = node.get("legacyId")
                prof_url: str = (
                    f"{RMP_BASE_URL}/professor/{legacy_id}" if legacy_id else ""
                )

                wta_raw: Optional[float] = node.get("wouldTakeAgainPercent")
                wta_str: Optional[str] = None
                if wta_raw is not None and wta_raw >= 0:
                    wta_str = f"{wta_raw:.0f}%"

                avg_rating: Optional[float] = node.get("avgRating")
                avg_diff: Optional[float] = node.get("avgDifficulty")
                first_name: str = node.get("firstName", "")
                last_name: str = node.get("lastName", "")

                prof: Professor = Professor(
                    name=f"{first_name} {last_name}".strip(),
                    department=node.get("department"),
                    rating=str(avg_rating) if avg_rating is not None else None,
                    num_ratings=str(node.get("numRatings", "N/A")),
                    would_take_again_pct=wta_str,
                    level_of_difficulty=(
                        str(avg_diff) if avg_diff is not None else None
                    ),
                    professor_url=prof_url,
                    graphql_id=node.get("id"),
                )
                self.professors_list.append(prof)

            pbar.update(len(edges))
            has_next = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")
            if not edges:
                break

        pbar.close()
        if self._interrupted:
            print(f"  ⚠ Interrupted — collected {len(self.professors_list)} professors so far")
        else:
            print(f"  ✓ Collected {len(self.professors_list)} professors")

    # ------------------------------------------------------------------
    # Driver setup
    # ------------------------------------------------------------------

    def _init_driver(self) -> None:
        options: ChromeOptions = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--ignore-ssl-errors")
        options.add_argument("log-level=3")
        options.add_argument("start-maximized")
        options.add_argument("--allow-running-insecure-content")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-features=PreloadMediaEngagementData,MediaEngagementBypassAutoplayPolicies")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-background-networking")

        blocked_domains: str = (
            "MAP doubleclick.net 127.0.0.1, "
            "MAP googlesyndication.com 127.0.0.1, "
            "MAP googleadservices.com 127.0.0.1, "
            "MAP google-analytics.com 127.0.0.1, "
            "MAP googletagmanager.com 127.0.0.1, "
            "MAP facebook.net 127.0.0.1, "
            "MAP amazon-adsystem.com 127.0.0.1, "
            "MAP ads.pubmatic.com 127.0.0.1, "
            "MAP cdn.taboola.com 127.0.0.1, "
            "MAP tpc.googlesyndication.com 127.0.0.1"
        )
        options.add_argument(f"--host-resolver-rules={blocked_domains}")

        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        self.driver = webdriver.Chrome(options=options)

    # ------------------------------------------------------------------
    # Phase 2: GraphQL review fetching
    # ------------------------------------------------------------------

    def _parse_ratings_response(
        self, data: Dict[str, Any]
    ) -> Tuple[List[Review], bool, Optional[str]]:
        reviews: List[Review] = []

        teacher_node: Optional[Dict[str, Any]] = (
            data.get("data", {}).get("node")
        )
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
            tb_str: Optional[str] = None
            if tb_val is True:
                tb_str = "Yes"
            elif tb_val is False:
                tb_str = "No"

            att_val: Optional[str] = r.get("attendanceMandatory")
            att_str: Optional[str] = None
            if att_val == "mandatory":
                att_str = "Mandatory"
            elif att_val == "non mandatory":
                att_str = "Not Mandatory"
            elif att_val:
                att_str = att_val

            quality_val: Optional[int] = r.get("qualityRating")
            quality_str: Optional[str] = str(quality_val) if quality_val is not None else None

            tags_raw: Optional[str] = r.get("ratingTags")
            if tags_raw:
                tags_raw = " ".join(tags_raw.split())

            raw_comment: Optional[str] = r.get("comment")
            if raw_comment:
                raw_comment = " ".join(raw_comment.split())

            online_val: Optional[bool] = r.get("isForOnlineClass")
            online_str: Optional[str] = None
            if online_val is True:
                online_str = "Yes"
            elif online_val is False:
                online_str = "No"

            review: Review = Review(
                course=r.get("class"),
                quality=quality_str,
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

        has_next: bool = page_info.get("hasNextPage", False)
        end_cursor: Optional[str] = page_info.get("endCursor")
        return reviews, has_next, end_cursor

    def _batch_fetch_ratings(
        self, requests_list: List[Dict[str, Any]]
    ) -> List[Optional[Dict[str, Any]]]:
        assert self.driver is not None

        js_script: str = """
            var callback = arguments[arguments.length - 1];
            var payloads = arguments[0];
            var url = arguments[1];
            var promises = payloads.map(function(payload) {
                return fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                })
                .then(function(resp) { return resp.json(); })
                .catch(function(err) { return {"error": err.toString()}; });
            });
            Promise.all(promises)
                .then(function(results) { callback(JSON.stringify(results)); })
                .catch(function(err) { callback(JSON.stringify({"error": err.toString()})); });
        """

        self.driver.set_script_timeout(60)
        raw: str = self.driver.execute_async_script(
            js_script, requests_list, RMP_GRAPHQL_URL
        )
        parsed: Any = json.loads(raw)

        if isinstance(parsed, dict) and "error" in parsed:
            return [None] * len(requests_list)

        results: List[Optional[Dict[str, Any]]] = []
        for item in parsed:
            if isinstance(item, dict):
                if "error" in item and "data" not in item:
                    results.append(None)
                else:
                    results.append(item)
            else:
                results.append(None)
        return results

    def _scrape_all_reviews(self) -> None:
        if self.driver is None:
            return

        batch_size: int = 5
        page_size: int = 100
        max_reviews: Optional[int] = MAX_REVIEWS_PER_PROFESSOR

        profs_with_ratings: List[Professor] = [
            p for p in self.professors_list
            if p.graphql_id and p.num_ratings not in (None, "0", "N/A")
        ]
        skipped: int = len(self.professors_list) - len(profs_with_ratings)
        if skipped > 0:
            print(f"  Skipping {skipped} professors with 0 ratings")

        total: int = len(profs_with_ratings)
        total_reviews: int = 0
        failed_profs: List[Professor] = []

        pbar: tqdm = tqdm(
            total=total,
            desc="Fetching reviews",
            unit=" prof",
            ncols=120,
        )

        for batch_start in range(0, total, batch_size):
            # --- Check for quit ---
            if _quit.should_quit:
                self._interrupted = True
                pbar.close()
                profs_done: int = sum(1 for p in profs_with_ratings if p.reviews)
                print(f"\n  ⚠ Quit requested — saving progress...")
                print(f"  ⚠ Scraped {total_reviews} reviews from {profs_done}/{total} professors")
                return

            batch: List[Professor] = profs_with_ratings[batch_start : batch_start + batch_size]

            payloads: List[Dict[str, Any]] = []
            for prof in batch:
                payloads.append({
                    "query": TEACHER_RATINGS_QUERY,
                    "variables": {
                        "id": prof.graphql_id,
                        "count": page_size,
                        "cursor": "",
                    },
                })

            try:
                results: List[Optional[Dict[str, Any]]] = self._batch_fetch_ratings(payloads)
            except Exception:
                failed_profs.extend(batch)
                pbar.update(len(batch))
                continue

            for prof, result in zip(batch, results):
                if result is None:
                    failed_profs.append(prof)
                    pbar.update(1)
                    continue

                try:
                    reviews, has_next, cursor = self._parse_ratings_response(result)

                    while has_next:
                        if _quit.should_quit:
                            break
                        if max_reviews and len(reviews) >= max_reviews:
                            break
                        extra_payload: Dict[str, Any] = {
                            "query": TEACHER_RATINGS_QUERY,
                            "variables": {
                                "id": prof.graphql_id,
                                "count": page_size,
                                "cursor": cursor or "",
                            },
                        }
                        extra_results: List[Optional[Dict[str, Any]]] = self._batch_fetch_ratings([extra_payload])
                        if not extra_results or extra_results[0] is None:
                            break
                        more_reviews, has_next, cursor = self._parse_ratings_response(extra_results[0])
                        reviews.extend(more_reviews)
                        if not more_reviews:
                            break

                    if max_reviews:
                        reviews = reviews[:max_reviews]

                    prof.reviews = reviews
                    total_reviews += len(reviews)
                except Exception:
                    failed_profs.append(prof)

                pbar.update(1)

            time.sleep(0.175)

        pbar.close()

        # --- Retry failed professors ---
        if failed_profs and not _quit.should_quit:
            print(f"  Retrying {len(failed_profs)} failed professors...")
            retry_pbar: tqdm = tqdm(failed_profs, desc="Retrying", unit=" prof")
            retry_failed: int = 0
            failure_reasons: Dict[str, str] = {}

            for prof in retry_pbar:
                if _quit.should_quit:
                    self._interrupted = True
                    break

                try:
                    payload: Dict[str, Any] = {
                        "query": TEACHER_RATINGS_QUERY,
                        "variables": {"id": prof.graphql_id, "count": page_size, "cursor": ""},
                    }
                    results = self._batch_fetch_ratings([payload])
                    if not results or results[0] is None:
                        retry_failed += 1
                        failure_reasons[f"{prof.name} ({prof.department})"] = "No response"
                        continue

                    raw_result: Dict[str, Any] = results[0]
                    if "errors" in raw_result:
                        errs: List[Dict[str, Any]] = raw_result["errors"]
                        msg: str = errs[0].get("message", "Unknown error") if errs else "Unknown"
                        if "data" not in raw_result or raw_result["data"] is None:
                            retry_failed += 1
                            failure_reasons[f"{prof.name} ({prof.department})"] = msg
                            continue

                    reviews, has_next, cursor = self._parse_ratings_response(raw_result)
                    while has_next:
                        if _quit.should_quit or (max_reviews and len(reviews) >= max_reviews):
                            break
                        extra_payload = {
                            "query": TEACHER_RATINGS_QUERY,
                            "variables": {"id": prof.graphql_id, "count": page_size, "cursor": cursor or ""},
                        }
                        extra_results = self._batch_fetch_ratings([extra_payload])
                        if not extra_results or extra_results[0] is None:
                            break
                        more_reviews, has_next, cursor = self._parse_ratings_response(extra_results[0])
                        reviews.extend(more_reviews)
                        if not more_reviews:
                            break

                    if max_reviews:
                        reviews = reviews[:max_reviews]
                    prof.reviews = reviews
                    total_reviews += len(reviews)
                except Exception as e:
                    retry_failed += 1
                    failure_reasons[f"{prof.name} ({prof.department})"] = str(e)

                time.sleep(0.2)

            retry_pbar.close()
            recovered: int = len(failed_profs) - retry_failed
            print(f"  ✓ Recovered {recovered}/{len(failed_profs)} on retry")
            final_failed: int = retry_failed

            if failure_reasons:
                print(f"\n  Failed professors ({len(failure_reasons)}):")
                for prof in failed_profs:
                    key: str = f"{prof.name} ({prof.department})"
                    if key in failure_reasons:
                        print(f"    - {prof.name} ({prof.department})")
                        print(f"      URL: {prof.professor_url}")
                        print(f"      Error: {failure_reasons[key]}")
        else:
            final_failed = 0

        profs_with_reviews: int = sum(1 for p in profs_with_ratings if len(p.reviews) > 0)

        print(
            f"  ✓ Fetched {total_reviews} reviews from "
            f"{profs_with_reviews}/{total} professors"
            + (f" ({final_failed} failed)" if final_failed else "")
        )

    # ------------------------------------------------------------------
    # Export methods
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
        print(f"  ✓ Professor summary CSV saved to: {file_path}")

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
        print(f"  ✓ Full JSON dump saved to: {file_path}")

    def close(self) -> None:
        """Shut down the browser and kill any lingering Chrome processes."""
        if self.driver:
            pid: Optional[int] = None
            try:
                pid = self.driver.service.process.pid
            except Exception:
                pass
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            if pid is not None:
                import platform
                if platform.system() == "Windows":
                    try:
                        import subprocess
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass


# ===========================================================================
# CLI Entry Point
# ===========================================================================

def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Scrape professor ratings & reviews from RateMyProfessor.com"
    )
    parser.add_argument("-s", "--sid", help="RateMyProfessor school ID", type=int)
    parser.add_argument("-f", "--file_path", help="Custom file path for the output CSV", type=str)
    parser.add_argument("--json", help="Also export full data (with reviews) to JSON", action="store_true")
    parser.add_argument("--no-reviews", help="Skip review scraping (summary only)", action="store_true")

    args: argparse.Namespace = parser.parse_args()

    if args.sid is None:
        parser.error("A school ID is required. Use -s <SCHOOL_ID>.")

    # Start quit listener
    _quit.start()

    scrape_reviews: bool = not args.no_reviews
    school: RMPSchool = RMPSchool(args.sid, scrape_reviews=scrape_reviews)

    school_name_fp: str = (
        school.school_name.replace(" ", "").replace("-", "_").lower()
    )
    script_dir: str = os.path.dirname(os.path.abspath(__file__))

    if args.file_path:
        professors_csv_path: str = args.file_path
    else:
        professors_csv_path = os.path.join(
            script_dir, "output_data", f"{school_name_fp}_professors.csv"
        )

    # Always save whatever we have — even partial data on quit
    school.dump_professors_to_csv(professors_csv_path)

    if scrape_reviews:
        reviews_csv_path: str = professors_csv_path.replace("_professors.csv", "_reviews.csv")
        school.dump_reviews_to_csv(reviews_csv_path)

    if args.json:
        json_path: str = professors_csv_path.replace("_professors.csv", "_full.json")
        school.dump_to_json(json_path)

    school.close()

    if school._interrupted or _quit.should_quit:
        print("\n  ⚠ Partial save complete — run again for full data.\n")
    else:
        print("\n  Done!\n")


if __name__ == "__main__":
    main()
