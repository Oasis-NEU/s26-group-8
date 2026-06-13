#!/usr/bin/env python3
"""
Toggle RateMyHusky maintenance mode and set the estimated downtime.

Usage:
  python maintenance.py -on          # enable (no countdown)
  python maintenance.py -on -10      # enable, ~10 minutes
  python maintenance.py -on -45m     # enable, ~45 minutes
  python maintenance.py -on -2h      # enable, ~2 hours
  python maintenance.py -off         # disable

While maintenance is on, every request without the dev-bypass cookie
(including /api/*) is 307-redirected to /maintenance.html. Devs keep full
access by opening the bypass URL printed by `-on` once per browser.

Changes take effect on the next Vercel deploy: commit + push
frontend/vercel.json and frontend/public/maintenance.html to apply.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
VERCEL_JSON = os.path.join(ROOT, "frontend", "vercel.json")
MAINTENANCE_HTML = os.path.join(ROOT, "frontend", "public", "maintenance.html")

# Keeps regular visitors out during maintenance; not a security boundary.
BYPASS_COOKIE = "rmh_dev"
BYPASS_SECRET = "afc2b681c0431c28c6768222"
BYPASS_URL = f"https://ratemyhusky.com/maintenance.html?bypass={BYPASS_SECRET}"

MAINT_DEST = "/maintenance.html"
# Catch everything except the maintenance page itself, the assets it needs,
# and robots.txt/ads.txt (crawlers + AdSense verification).
MAINT_SOURCE = (
    "/((?!maintenance\\.html|logo\\.jpg|neu-husky-icon\\.png"
    "|robots\\.txt|ads\\.txt).*)"
)

ENDS_AT_PATTERN = r"(var MAINTENANCE_ENDS_AT\s*=\s*)(?:null|\d+)(;)"


def maintenance_rule():
    return {
        "source": MAINT_SOURCE,
        "destination": MAINT_DEST,
        "permanent": False,
        "missing": [
            {"type": "cookie", "key": BYPASS_COOKIE, "value": BYPASS_SECRET}
        ],
    }


def render_vercel_json(on: bool) -> str:
    with open(VERCEL_JSON) as f:
        data = json.load(f)

    redirects = data.get("redirects", [])
    if not isinstance(redirects, list):
        raise RuntimeError(
            "Invalid frontend/vercel.json: 'redirects' must be a list. "
            "No files were changed."
        )

    # Drop any previous maintenance rule, then re-add if enabling, so
    # running -on twice never duplicates it.
    redirects = [r for r in redirects if r.get("destination") != MAINT_DEST]
    if on:
        redirects.insert(0, maintenance_rule())

    if redirects:
        data["redirects"] = redirects
    else:
        data.pop("redirects", None)

    return json.dumps(data, indent=2) + "\n"


def render_maintenance_html(ends_at_ms) -> str:
    with open(MAINTENANCE_HTML) as f:
        content = f.read()

    if len(re.findall(ENDS_AT_PATTERN, content)) != 1:
        raise RuntimeError(
            "Invalid frontend/public/maintenance.html: expected exactly one "
            "MAINTENANCE_ENDS_AT assignment. No files were changed."
        )

    value = "null" if ends_at_ms is None else str(ends_at_ms)
    return re.sub(
        ENDS_AT_PATTERN,
        lambda m: f"{m.group(1)}{value}{m.group(2)}",
        content,
        count=1,
    )


def parse_minutes(arg: str) -> int:
    arg = arg.lstrip("-").strip().lower()
    if re.fullmatch(r"\d+h", arg):
        return int(arg[:-1]) * 60
    if re.fullmatch(r"\d+m?", arg):
        return int(arg.rstrip("m"))
    raise SystemExit(
        f"Can't parse ETA '{arg}': use minutes (-10, -45m) or hours (-2h). "
        "The countdown needs a number."
    )


def usage():
    print(__doc__)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("-on", "-off"):
        usage()

    on = args[0] == "-on"
    ends_at = None
    if on and len(args) > 1:
        minutes = parse_minutes(args[1])
        ends_at = int(time.time() * 1000) + minutes * 60_000

    # Render both files before writing either, so a validation error in one
    # never leaves the other half-updated.
    new_vercel = render_vercel_json(on)
    new_html = render_maintenance_html(ends_at)
    with open(VERCEL_JSON, "w") as f:
        f.write(new_vercel)
    with open(MAINTENANCE_HTML, "w") as f:
        f.write(new_html)

    if on:
        if ends_at is not None:
            eta = datetime.fromtimestamp(ends_at / 1000).strftime("%I:%M %p")
            print(f"[maintenance] ON  (countdown until ~{eta.lstrip('0')})")
        else:
            print("[maintenance] ON  (no ETA countdown)")
        print(f"Dev bypass (open once per browser): {BYPASS_URL}")
    else:
        print("[maintenance] OFF")

    print(
        "Commit + push frontend/vercel.json and "
        "frontend/public/maintenance.html to apply."
    )


if __name__ == "__main__":
    main()
