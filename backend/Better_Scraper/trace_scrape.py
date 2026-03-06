"""
Northeastern TRACE Course Evaluation Scraper

Dumps all evaluated courses from TRACE to CSV.
Requires your session cookies from a logged-in browser.

How to get cookies:
    1. Log into https://www.applyweb.com/eval/new/reportbrowser
    2. Open DevTools (F12) → Network tab
    3. Click any action on the page
    4. Find any request to applyweb.com → click it → Headers tab
    5. Copy the entire Cookie header value
    6. Paste it into COOKIE below or pass via --cookie flag

Usage:
    python trace_scrape.py
    python trace_scrape.py --cookie "ASP.NET_SessionId=abc; .ASPXAUTH=xyz"
    python trace_scrape.py --rpp 200   # smaller pages if 500 times out
"""

__author__ = "Benjamin"
__version__ = "1.0.0"

import csv
import os
import argparse
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone

import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Paste your cookie here (or use --cookie flag)
# ---------------------------------------------------------------------------
COOKIE: str = ""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TRACE_URL: str = "https://www.applyweb.com/eval/new/reportbrowser/evaluatedCourses"
FIELDS: List[str] = [
    "courseId",
    "schoolCode",
    "termId",
    "termTitle",
    "instructorId",
    "termEndDate",
    "instructorFirstName",
    "instructorLastName",
    "departmentName",
    "enrollment",
    "displayName",
    "section",
]


def scrape_trace(cookie: str, rpp: int = 500) -> List[Dict[str, Any]]:
    """Scrape all evaluated courses from TRACE.

    Args:
        cookie: Session cookie string from browser.
        rpp: Results per page (max 500).

    Returns:
        List of course dicts with selected fields.
    """
    session: requests.Session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "Referer": "https://www.applyweb.com/eval/new/reportbrowser",
        "Origin": "https://www.applyweb.com",
    })

    # First request to get total count
    print("  Connecting to TRACE...")
    payload: Dict[str, Any] = {
        "page": 1,
        "rpp": 1,
        "search": "",
        "excludeTA": False,
        "sort": None,
    }

    resp: requests.Response = session.post(TRACE_URL, json=payload, timeout=30)
    if resp.status_code == 401:
        print("  ✗ Authentication failed — your cookies have expired.")
        print("    Re-login to TRACE and copy fresh cookies.")
        return []
    if resp.status_code != 200:
        print(f"  ✗ Request failed with status {resp.status_code}")
        return []

    data: Dict[str, Any] = resp.json()
    total: int = data.get("total", 0)
    print(f"  ✓ Connected — {total:,} courses found")

    if total == 0:
        return []

    # Paginate through all courses
    total_pages: int = (total + rpp - 1) // rpp
    all_courses: List[Dict[str, Any]] = []
    from concurrent.futures import ThreadPoolExecutor, as_completed, Future

    pbar: tqdm = tqdm(total=total, desc="Fetching courses", unit=" courses")

    def fetch_page(page_num: int) -> List[Dict[str, Any]]:
        p: Dict[str, Any] = {
            "page": page_num,
            "rpp": rpp,
            "search": "",
            "excludeTA": False,
            "sort": None,
        }
        r: requests.Response = session.post(TRACE_URL, json=p, timeout=60)
        if r.status_code == 401:
            return []
        r.raise_for_status()
        return r.json().get("data", [])

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures: Dict[Future, int] = {
            executor.submit(fetch_page, page): page
            for page in range(1, total_pages + 1)
        }

        for future in as_completed(futures):
            try:
                courses: List[Dict[str, Any]] = future.result()
                for course in courses:
                    end_date_ms: Optional[int] = course.get("termEndDate")
                    end_date_str: Optional[str] = None
                    if end_date_ms is not None:
                        end_date_str = datetime.fromtimestamp(
                            end_date_ms / 1000, tz=timezone.utc
                        ).strftime("%Y-%m-%d")

                    row: Dict[str, Any] = {}
                    for f in FIELDS:
                        if f == "termEndDate":
                            row[f] = end_date_str
                        else:
                            row[f] = course.get(f)
                    all_courses.append(row)
                pbar.update(len(courses))
            except Exception as e:
                page_num: int = futures[future]
                print(f"\n  ✗ Page {page_num} failed: {e}")

    pbar.close()
    session.close()
    return all_courses


def save_csv(courses: List[Dict[str, Any]], file_path: str) -> None:
    """Save courses to CSV."""
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer: csv.DictWriter = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for course in courses:
            writer.writerow(course)
    print(f"  ✓ Saved {len(courses):,} courses to: {file_path}")


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Scrape Northeastern TRACE evaluations to CSV"
    )
    parser.add_argument(
        "--cookie", type=str, default=COOKIE,
        help="Session cookie string from browser DevTools",
    )
    parser.add_argument(
        "--rpp", type=int, default=500,
        help="Results per page (default 500, lower if timeouts)",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output CSV path",
    )

    args: argparse.Namespace = parser.parse_args()

    cookie: str = args.cookie.strip()
    if not cookie:
        print("\n  No cookie provided. Paste your TRACE session cookie:")
        print("  (DevTools → Network → any request → Headers → Cookie)\n")
        cookie = input("  Cookie: ").strip()

    if not cookie:
        print("  ✗ Cannot proceed without cookies.")
        return

    courses: List[Dict[str, Any]] = scrape_trace(cookie, rpp=args.rpp)

    if not courses:
        print("  ✗ No courses scraped.")
        return

    script_dir: str = os.path.dirname(os.path.abspath(__file__))
    output_path: str = args.output or os.path.join(
        script_dir, "output_data", "trace_courses.csv"
    )

    save_csv(courses, output_path)
    print(f"\n  Done! ({len(courses):,} courses)\n")


if __name__ == "__main__":
    main()