#!/usr/bin/env python3
"""Print like/comment counts for the most recent post in posted.log (or a given media id)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from post import load_env, api
env = load_env()
if len(sys.argv) > 1:
    mid = sys.argv[1]
else:
    lines = [l for l in Path(__file__).with_name("posted.log").read_text().splitlines() if l.strip()]
    if not lines: sys.exit("posted.log is empty")
    mid = json.loads(lines[-1])["media_id"]
m = api("GET", f"/{mid}", {"fields": "like_count,comments_count,permalink,timestamp"}, env["IG_ACCESS_TOKEN"])
print(f"{m.get('like_count',0)} likes, {m.get('comments_count',0)} comments\n{m.get('permalink','')}")
