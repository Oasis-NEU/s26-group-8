#!/usr/bin/env python3
"""
Toggle RateMyHusky maintenance mode, set the downtime estimate, and mint
signed dev-bypass invite links.

Usage:
  python maintenance.py -on            # enable (no countdown)
  python maintenance.py -on -10        # enable, ~10 minutes
  python maintenance.py -on -45m       # enable, ~45 minutes
  python maintenance.py -on -2h        # enable, ~2 hours
  python maintenance.py -off           # disable
  python maintenance.py -invite ben    # mint a 7-day bypass link for "ben"
  python maintenance.py -invite ben 30 # ... valid 30 days
  python maintenance.py -genkey        # create .env with fresh secrets

Maintenance gating runs in frontend/middleware.ts. Bypass links carry an
HMAC-signed, expiring token verified at the edge; the signing key lives in
.env locally and in the MAINT_SIGNING_KEY env var on Vercel — never in git.

Changes take effect on the next Vercel deploy: commit + push
frontend/maintenance.config.json and frontend/public/maintenance.html.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT, ".env")
MAINT_CONFIG = os.path.join(ROOT, "frontend", "maintenance.config.json")
MAINTENANCE_HTML = os.path.join(ROOT, "frontend", "public", "maintenance.html")

ENDS_AT_PATTERN = r"(var MAINTENANCE_ENDS_AT\s*=\s*)(?:null|\d+)(;)"


def load_env_key(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{name}="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value
    raise SystemExit(
        f"{name} not found in environment or .env. "
        "Run `python maintenance.py -genkey` first."
    )


def render_config(on: bool) -> str:
    return json.dumps({"on": on}, indent=2) + "\n"


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


def mint_token(name: str, days: int) -> str:
    if not re.fullmatch(r"[a-z0-9-]{1,32}", name):
        raise SystemExit("Invite name must be lowercase letters/digits/dashes.")
    key = load_env_key("MAINT_SIGNING_KEY")
    expiry = int(time.time()) + days * 86400
    payload = f"{name}.{expiry}"
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def genkey():
    existing = ""
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            existing = f.read()

    lines = []
    for name in ("MAINT_SIGNING_KEY", "PROXY_SECRET"):
        if f"{name}=" in existing:
            print(f"{name} already in .env, keeping it.")
        else:
            lines.append(f"{name}={secrets.token_hex(32)}")

    if lines:
        with open(ENV_FILE, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(lines) + "\n")
        print(f"Wrote {len(lines)} secret(s) to .env (gitignored).")

    print("\nNow copy the values from .env into:")
    print("  Vercel  (project env vars): MAINT_SIGNING_KEY and PROXY_SECRET")
    print("  Railway (service env vars): PROXY_SECRET")
    print("Vercel env changes apply on the next deploy; Railway restarts "
          "the service. Set Vercel first, deploy, then Railway.")


def toggle(on: bool, ends_at):
    # Render both files before writing either, so a validation error in one
    # never leaves the other half-updated.
    new_config = render_config(on)
    new_html = render_maintenance_html(ends_at)
    with open(MAINT_CONFIG, "w") as f:
        f.write(new_config)
    with open(MAINTENANCE_HTML, "w") as f:
        f.write(new_html)

    if on:
        if ends_at is not None:
            eta = datetime.fromtimestamp(ends_at / 1000).strftime("%I:%M %p")
            print(f"[maintenance] ON  (countdown until ~{eta.lstrip('0')})")
        else:
            print("[maintenance] ON  (no ETA countdown)")
        print("Need access during maintenance? "
              "python maintenance.py -invite <name>")
    else:
        print("[maintenance] OFF")

    print(
        "Commit + push frontend/maintenance.config.json and "
        "frontend/public/maintenance.html to apply."
    )


def usage():
    print(__doc__)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        usage()

    if args[0] == "-on":
        ends_at = None
        if len(args) > 1:
            minutes = parse_minutes(args[1])
            ends_at = int(time.time() * 1000) + minutes * 60_000
        toggle(True, ends_at)

    elif args[0] == "-off":
        toggle(False, None)

    elif args[0] == "-invite":
        if len(args) < 2:
            usage()
        days = int(args[2]) if len(args) > 2 else 7
        token = mint_token(args[1].lower(), days)
        print(f"Bypass link for '{args[1]}' (valid {days} days):")
        print(f"  https://ratemyhusky.com/maintenance.html?bypass={token}")

    elif args[0] == "-genkey":
        genkey()

    else:
        usage()


if __name__ == "__main__":
    main()
