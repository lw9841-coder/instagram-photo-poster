#!/usr/bin/env python3
"""
The caption step.

Deliberately does NOT try to write captions on its own. A caption written from
metadata alone reads like a stranger describing your photo - the thing that
makes one land is knowing who's in it and what actually happened, and only
the person who took it knows that.

So this does three jobs:
  brief  - lay out everything known about a photo, and name what isn't known
  save   - store an approved caption next to its photo
  queue  - show what's approved and ready to post
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
CANDIDATES = HERE / "candidates.json"
QUEUE = HERE / "queue.json"


def load_candidates():
    if not CANDIDATES.exists():
        sys.exit("No candidates.json. Run scan.py first.")
    return json.loads(CANDIDATES.read_text())


def load_queue():
    return json.loads(QUEUE.read_text()) if QUEUE.exists() else []


def save_queue(q):
    QUEUE.write_text(json.dumps(q, indent=2))


def find(cands, rank):
    for c in cands:
        if c["rank"] == rank:
            return c
    sys.exit(f"No candidate ranked {rank}. Ranks: {[c['rank'] for c in cands]}")


def cmd_brief(args):
    cands = load_candidates()
    picks = [find(cands, r) for r in args.ranks] if args.ranks else cands[: args.top]

    for c in picks:
        print("=" * 68)
        print(f"CANDIDATE #{c['rank']}   score {c['score']}")
        print("=" * 68)
        print(f"file        {c['file']}")
        print(f"taken       {c['date'][:16].replace('T', ' ')}")
        print(f"size        {c['width']}x{c['height']}"
              + ("   TOO SMALL - will look soft on Instagram" if c.get("too_small_to_post") else ""))

        known, unknown = [], []
        if c.get("place"):
            known.append(f"location: {c['place']}")
        else:
            unknown.append("where this was taken")

        if c.get("persons"):
            known.append(f"people (tagged in Photos): {', '.join(c['persons'])}")
        elif c.get("face_count"):
            unknown.append(f"who the {c['face_count']} people in this photo are")

        if c.get("apple_caption"):
            known.append(f"Apple's own description: \"{c['apple_caption']}\"")
        if c.get("labels"):
            known.append(f"objects detected: {', '.join(c['labels'])}")
        if c.get("favorite"):
            known.append("you marked this a favorite")
        if c.get("shots_in_moment", 1) > 1:
            known.append(f"one of {c['shots_in_moment']} shots from this moment")

        print("\nWHAT'S KNOWN")
        for k in known:
            print(f"  - {k}")
        if not known:
            print("  - nothing beyond the pixels")

        print("\nWHAT I'D NEED FROM YOU")
        unknown.append("what happened - the one detail that makes it worth posting")
        for u in unknown:
            print(f"  - {u}")
        print()


def cmd_save(args):
    cands = load_candidates()
    c = find(cands, args.rank)
    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text().strip()
    if not text:
        sys.exit("Give me caption text with --text or --text-file.")
    if len(text) > 2200:
        sys.exit(f"That's {len(text)} characters. Instagram's limit is 2200.")

    q = [x for x in load_queue() if x["uuid"] != c["uuid"]]
    q.append({
        "uuid": c["uuid"], "rank": c["rank"], "file": c["file"],
        "caption": text, "approved_at": datetime.now().isoformat(timespec="seconds"),
        "posted": False,
    })
    save_queue(q)
    print(f"Approved #{c['rank']} ({len(text)} chars). {len(q)} in the queue.")
    print(f"\nPost it with:\n  {HERE}/.venv/bin/python {HERE}/post.py \\\n"
          f"    '{c['file']}' --caption-file <(python3 -c \"import json;print(json.load(open('{QUEUE}'))[-1]['caption'])\")")


def cmd_queue(args):
    q = load_queue()
    pending = [x for x in q if not x["posted"]]
    if not pending:
        print("Nothing approved yet.")
        return
    print(f"{len(pending)} ready to post:\n")
    for x in pending:
        print(f"  #{x['rank']}  {Path(x['file']).name}   approved {x['approved_at'][:16]}")
        for line in x["caption"].splitlines():
            print(f"      {line}")
        print()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("brief", help="everything known about a photo, plus what isn't")
    b.add_argument("ranks", nargs="*", type=int)
    b.add_argument("--top", type=int, default=3)
    b.set_defaults(fn=cmd_brief)

    s = sub.add_parser("save", help="store an approved caption")
    s.add_argument("rank", type=int)
    s.add_argument("--text"); s.add_argument("--text-file")
    s.set_defaults(fn=cmd_save)

    q = sub.add_parser("queue", help="what's approved and ready")
    q.set_defaults(fn=cmd_queue)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
