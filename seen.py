#!/usr/bin/env python3
"""
Has this photo already been posted?

Compares candidates against fingerprints of what's already on the account.
Instagram re-encodes and crops what you upload, so an exact match never
happens - this uses a perceptual hash and compares several crops, because
Instagram trims a 3:4 photo down to 4:5 and that moves the framing.
"""
import json, sys
from pathlib import Path
from PIL import Image

HERE = Path(__file__).parent
POSTED = HERE / "posted_hashes.json"


def dhash_bytes(img):
    """64-bit difference hash, as 8 ints - same construction the browser used."""
    g = img.convert("L").resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    out = []
    for y in range(8):
        b = 0
        for x in range(8):
            b = (b << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
        out.append(b)
    return out


def crops(img):
    """The original, plus how Instagram would have trimmed it."""
    w, h = img.size
    yield "full", img
    for name, target in (("4:5", 0.8), ("1:1", 1.0)):
        if w / h < target:                      # too tall - trim top and bottom
            nh = int(w / target)
            top = (h - nh) // 2
            yield name, img.crop((0, top, w, top + nh))
        elif w / h > target:                    # too wide - trim the sides
            nw = int(h * target)
            left = (w - nw) // 2
            yield name, img.crop((left, 0, left + nw, h))


def hamming(a, b):
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


def best_match(path, posted):
    img = Image.open(path)
    best = (999, None, None)
    for cname, c in crops(img):
        h = dhash_bytes(c)
        for p in posted:
            d = hamming(h, p["b"])
            if d < best[0]:
                best = (d, p, cname)
    return best


def main():
    if not POSTED.exists():
        sys.exit("No posted_hashes.json - fetch the account's post fingerprints first.")
    posted = json.loads(POSTED.read_text())
    threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 14

    cands = json.loads((HERE / "candidates.json").read_text())
    print(f"Comparing {len(cands)} candidates against {len(posted)} posted images")
    print(f"(distance 0 = identical, 64 = opposite; flagging at <= {threshold})\n")

    for c in cands:
        d, p, cname = best_match(c["file"], posted)
        flag = "  ALREADY POSTED" if d <= threshold else ""
        via = f" via {cname} crop" if cname != "full" else ""
        print(f"  #{c['rank']:2d} {c['date'][:10]}  closest distance {d:2d}"
              f"  -> post {p['code']} ({p['d']}){via}{flag}")


if __name__ == "__main__":
    main()
