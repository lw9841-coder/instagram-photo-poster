#!/usr/bin/env python3
"""Report new comments on recent Instagram posts.

Replying to comments is the cheapest way to grow an account, but only if you
see them. This checks recent
posts and reports what is new since the last run.

    python3 comments.py            # new comments only
    python3 comments.py --all      # everything on recent posts, ignore history
    python3 comments.py --posts 5  # how far back to look, default 10

Exit codes: 0 nothing new, 10 there are new comments, 1 something went wrong.
The distinct code lets a scheduled run stay quiet when there is nothing to say.
"""
import argparse
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
GRAPH = "https://graph.instagram.com/v23.0"
SEEN = HERE / "comments_seen.json"


def load_env():
    env = {}
    f = HERE / ".env"
    if not f.exists():
        sys.exit("No .env file. Run setup first.")
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("IG_ACCESS_TOKEN", "IG_USER_ID"):
        if not env.get(k):
            sys.exit(f"{k} missing from .env")
    return env


def api(path, params, token):
    params = dict(params, access_token=token)
    url = f"{GRAPH}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:300]
        sys.exit(f"Instagram API error ({e.code}) on {path}:\n  {msg}")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.gaierror) or "nodename nor servname" in str(reason):
            sys.exit("Cannot resolve graph.instagram.com.\n"
                     "  Almost always ProtonVPN - it does not resolve this one\n"
                     "  hostname. Disconnect the VPN and run this again.")
        sys.exit(f"Could not reach Instagram: {reason}")


def ago(ts):
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return ts
    mins = (datetime.now(timezone.utc) - t).total_seconds() / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    if mins < 1440:
        return f"{int(mins // 60)}h ago"
    return f"{int(mins // 1440)}d ago"


def main():
    ap = argparse.ArgumentParser(description="New comments on recent posts.")
    ap.add_argument("--all", action="store_true",
                    help="show every comment, not just unseen ones")
    ap.add_argument("--posts", type=int, default=10,
                    help="how many recent posts to check, default 10")
    args = ap.parse_args()

    env = load_env()
    seen = set()
    if SEEN.exists() and not args.all:
        try:
            seen = set(json.loads(SEEN.read_text()))
        except Exception:
            pass

    media = api(f"/{env['IG_USER_ID']}/media",
                {"fields": "id,caption,permalink,timestamp,comments_count",
                 "limit": args.posts}, env["IG_ACCESS_TOKEN"]).get("data", [])

    all_ids, new_by_post, total = set(), [], 0
    for m in media:
        if not m.get("comments_count"):
            continue
        cs = api(f"/{m['id']}/comments",
                 {"fields": "id,text,username,timestamp,like_count",
                  "limit": 50}, env["IG_ACCESS_TOKEN"]).get("data", [])
        fresh = []
        for c in cs:
            all_ids.add(c["id"])
            if c["id"] not in seen:
                fresh.append(c)
        if fresh:
            cap = (m.get("caption") or "").split("\n")[0][:44]
            new_by_post.append((cap, m["permalink"], fresh))
            total += len(fresh)

    # Remember everything seen this run, including comments on posts that had
    # nothing new, so the file does not grow stale.
    SEEN.write_text(json.dumps(sorted(all_ids | seen)))

    if not total:
        print("No new comments.")
        return 0

    label = "comment" if total == 1 else "comments"
    print(f"{total} new {label}\n")
    for cap, link, fresh in new_by_post:
        print(f"  on “{cap}”")
        print(f"  {link}")
        for c in fresh:
            likes = f"  ({c['like_count']} likes)" if c.get("like_count") else ""
            print(f"    @{c['username']}  {ago(c.get('timestamp',''))}{likes}")
            for line in (c.get("text") or "").splitlines():
                print(f"      {line}")
        print()
    return 10


if __name__ == "__main__":
    sys.exit(main())
