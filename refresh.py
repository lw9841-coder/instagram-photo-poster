#!/usr/bin/env python3
"""
Keep the Instagram access token alive.

Instagram long-lived tokens last 60 days and die silently - the first sign is a
failed post. This extends the token before that happens and records the new
expiry in .env.

Two things worth knowing about Meta's rules, because they shape this script:

  - A long-lived token cannot be refreshed until it is at least 24 hours old.
    Refreshing a brand new one fails, so this no-ops in that window.
  - Refreshing an already-expired token is impossible. There is no recovery
    path except generating a new one by hand in the app dashboard. That is why
    the default mode runs early rather than at the last minute.

Usage:
    python3 refresh.py              # refresh if inside the renewal window
    python3 refresh.py --status     # print days remaining, touch nothing
    python3 refresh.py --force      # refresh now regardless of days remaining
    python3 refresh.py --exchange   # one time: turn a short-lived token long-lived

Exit codes: 0 fine (refreshed or nothing needed), 1 needs a human, 2 broken.
"""
import argparse
import json
import shutil
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
ENV = HERE / ".env"
GRAPH = "https://graph.instagram.com"

# Refresh once the token has less than this long to live. Meta allows a refresh
# at any point in a valid token's life, so going early costs nothing and buys
# a wide margin for a laptop that was closed for two weeks.
RENEW_WHEN_DAYS_LEFT = 20

# Meta refuses to refresh a token younger than this.
MIN_TOKEN_AGE = timedelta(hours=24)


def load_env():
    if not ENV.exists():
        sys.exit("No .env file next to refresh.py.")
    env = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def save_env(updates):
    """Rewrite .env in place, preserving comments and key order."""
    backup = ENV.parent / ".env.bak"
    shutil.copy2(ENV, backup)
    backup.chmod(0o600)
    lines = ENV.read_text().splitlines()
    out, seen = [], set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV.write_text("\n".join(out) + "\n")
    ENV.chmod(0o600)


def get(path, params):
    url = f"{GRAPH}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)["error"]
            msg = err.get("message", body[:300])
        except Exception:
            msg = body[:300]
        print(f"Instagram API error ({e.code}) on {path}:\n  {msg}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.gaierror) or "nodename nor servname" in str(reason):
            print("Cannot resolve graph.instagram.com.\n"
            "\n"
            "  This is almost always a VPN. ProtonVPN's DNS does not resolve this\n"
            "  one hostname, even though instagram.com and graph.facebook.com both\n"
            "  work fine - so the connection looks healthy and only this breaks.\n"
            "\n"
            "  Disconnect the VPN and run this again.", file=sys.stderr)
        else:
            print(f"Could not reach Instagram: {reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Network problem calling {path}: {e}", file=sys.stderr)
        return None


def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def record(token, expires_in, issued=None):
    now = datetime.now(timezone.utc)
    updates = {
        "IG_ACCESS_TOKEN": token,
        "IG_TOKEN_EXPIRES": (now + timedelta(seconds=int(expires_in))).isoformat(timespec="seconds"),
        "IG_TOKEN_ISSUED": (issued or now).isoformat(timespec="seconds"),
    }
    save_env(updates)
    days = int(expires_in) // 86400
    print(f"Token updated. Valid {days} days, until "
          f"{(now + timedelta(seconds=int(expires_in))).date()}. Value not displayed.")


def cmd_status(env):
    token = env.get("IG_ACCESS_TOKEN")
    if not token:
        print("No token in .env. Generate one in the Meta app dashboard, then:")
        print("  python3 setcreds.py IG_ACCESS_TOKEN")
        return 1
    exp = parse_dt(env.get("IG_TOKEN_EXPIRES"))
    issued = parse_dt(env.get("IG_TOKEN_ISSUED"))
    now = datetime.now(timezone.utc)

    print(f"Token present ({len(token)} chars).")
    if issued:
        print(f"  issued  {issued.date()} ({(now - issued).days}d ago)")
    if not exp:
        print("  expiry  UNKNOWN - no IG_TOKEN_EXPIRES recorded.")
        print("          Run: python3 refresh.py --force")
        return 1
    left = (exp - now).days
    print(f"  expires {exp.date()} ({left} days left)")
    if left < 0:
        print("  EXPIRED. No refresh is possible. Generate a new token by hand in")
        print("  the Meta app dashboard, then: python3 setcreds.py IG_ACCESS_TOKEN")
        return 1
    if left <= RENEW_WHEN_DAYS_LEFT:
        print(f"  Inside the renewal window. Run: python3 refresh.py")
    return 0


def cmd_exchange(env):
    """Short-lived -> long-lived. Only needed if the dashboard gave a 1-hour token."""
    secret = env.get("META_APP_SECRET")
    if not secret:
        print("META_APP_SECRET is not set. Run: python3 setcreds.py META_APP_SECRET",
              file=sys.stderr)
        return 1
    data = get("/access_token", {
        "grant_type": "ig_exchange_token",
        "client_secret": secret,
        "access_token": env["IG_ACCESS_TOKEN"],
    })
    if not data or "access_token" not in data:
        print("Exchange failed. If the token is already long-lived this is expected -"
              " use --force to refresh instead.", file=sys.stderr)
        return 1
    record(data["access_token"], data.get("expires_in", 60 * 86400))
    return 0


def cmd_refresh(env, force):
    token = env.get("IG_ACCESS_TOKEN")
    if not token:
        print("No token to refresh. Generate one first.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    exp = parse_dt(env.get("IG_TOKEN_EXPIRES"))
    issued = parse_dt(env.get("IG_TOKEN_ISSUED"))

    if exp and not force:
        left = (exp - now).days
        if left < 0:
            print("Token already expired. Refresh is impossible - generate a new one "
                  "by hand in the Meta app dashboard.", file=sys.stderr)
            return 1
        if left > RENEW_WHEN_DAYS_LEFT:
            print(f"{left} days left, no refresh needed "
                  f"(renews at {RENEW_WHEN_DAYS_LEFT} days).")
            return 0

    if issued and (now - issued) < MIN_TOKEN_AGE and not force:
        wait = MIN_TOKEN_AGE - (now - issued)
        print(f"Token is under 24h old; Meta will refuse. Try again in "
              f"{int(wait.total_seconds() // 3600) + 1}h.")
        return 0

    data = get("/refresh_access_token", {
        "grant_type": "ig_refresh_token",
        "access_token": token,
    })
    if not data or "access_token" not in data:
        print("Refresh failed. Common causes: the token is under 24 hours old, "
              "already expired, or was revoked. If it is expired, generate a new "
              "one by hand in the Meta app dashboard.", file=sys.stderr)
        return 1

    # A refresh returns a NEW token, and the issue date resets to now.
    record(data["access_token"], data.get("expires_in", 60 * 86400))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Keep the Instagram token alive.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="days remaining, no changes")
    g.add_argument("--force", action="store_true", help="refresh regardless of days left")
    g.add_argument("--exchange", action="store_true",
                   help="one time: short-lived token -> long-lived")
    args = ap.parse_args()

    env = load_env()
    if args.status:
        return cmd_status(env)
    if args.exchange:
        return cmd_exchange(env)
    return cmd_refresh(env, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
