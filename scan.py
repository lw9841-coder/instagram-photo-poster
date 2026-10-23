#!/usr/bin/env python3
"""
Stage 1: Scan the Photos library and shortlist post-worthy candidates.

Cheap, local, no API calls. Uses Apple's own on-device photo scores
(27 aesthetic dimensions) to throw out the obvious junk and rank what's
left, so only a handful of photos ever need real review.
"""
import argparse, datetime, json, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent
OSXPHOTOS = HERE / ".venv" / "bin" / "osxphotos"
STAGING = HERE / "candidates"

# Content Apple's Vision framework labels that is never a social post.
JUNK_LABELS = {
    "document", "text", "receipt", "screenshot", "menu", "paper",
    "envelope", "business card", "poster", "monitor", "screen",
    "computer", "webpage", "spreadsheet", "calendar", "id card",
}

# Aesthetic dimensions that make a photo good TO POST, with weights.
# Positive = we want it. Negative = it hurts.
WEIGHTS = {
    "overall":                  3.0,   # Apple's own aggregate judgment
    "curation":                 2.0,   # "would Memories pick this"
    "interesting_subject":      1.5,
    "well_framed_subject":      1.2,
    "sharply_focused_subject":  1.2,
    "pleasant_composition":     1.0,
    "well_timed_shot":          1.0,
    "well_chosen_subject":      1.0,
    "pleasant_lighting":        0.7,
    "harmonious_color":         0.5,
    "lively_color":             0.3,
    "immersiveness":            0.3,
    "intrusive_object_presence": -1.5,
    "failure":                  -3.0,
    "noise":                    -0.8,
    "low_light":                -0.8,
}

FACE_BONUS = 1.2      # photos with people outperform scenery, consistently
NAMED_FACE_BONUS = 0.8  # a *recognized* person means it's someone you know
FAVORITE_BONUS = 2.5  # you already flagged it yourself - trust that
PLACE_BONUS = 0.4     # a location gives the caption something to stand on


def load_photos(days_back, from_date=None, to_date=None):
    if from_date:
        start = from_date
    else:
        start = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    cmd = [str(OSXPHOTOS), "query", "--from-date", start, "--only-photos", "--json"]
    if to_date:
        cmd += ["--to-date", to_date]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"osxphotos failed:\n{out.stderr[:800]}")
    # osxphotos prints progress on stdout before the JSON; find the array
    txt = out.stdout
    i = txt.find("[")
    return json.loads(txt[i:]) if i >= 0 else []


def load_saved_library(path):
    """Read photo records from a saved `osxphotos query --json` file instead of
    the live Photos library. Relative image paths resolve next to the file, so
    the sample library in examples/ runs on any Mac."""
    path = Path(path)
    photos = json.loads(path.read_text())
    for p in photos:
        p["path_derivatives"] = [str((path.parent / x).resolve())
                                 for x in (p.get("path_derivatives") or [])]
    return photos


# Apps whose images are NOT your own photography. A picture someone texted
# you, or one an app generated, must never go out under your name.
def not_his_photo(p):
    imported_by = [str(x).lower() for x in (p.get("imported_by") or [])]
    from_camera = any("camera" in x for x in imported_by)
    exif = p.get("exif_info") or {}
    has_camera_exif = bool(exif.get("camera_make"))
    if from_camera or has_camera_exif:
        return None
    # name the culprit app when we can, so the filter is auditable
    app = next((x for x in imported_by if "." in x), None) or "another app"
    pretty = {
        "com.openai.chat": "ChatGPT",
        "com.apple.mobilesms.compose": "Messages",
        "com.burbn.instagram": "Instagram",
        "com.apple.mobilesafari": "Safari",
        "com.facebook.Messenger": "Messenger",
    }.get(app, app)
    return f"not your photo (saved from {pretty})"


def is_junk(p):
    """Hard exclusions - things that are never a post, no judgment needed."""
    if p.get("intrash") or p.get("hidden"):
        return "trashed/hidden"
    if p.get("ismovie") or not p.get("isphoto"):
        return "not a photo"
    # Burst shots: only consider the one Apple/you picked
    if p.get("burst") and not p.get("burst_selected") and not p.get("burst_default_pick"):
        return "unselected burst frame"
    labels = {l.lower() for l in (p.get("labels_normalized") or p.get("labels") or [])}
    hits = labels & JUNK_LABELS
    if hits:
        return f"junk content ({', '.join(sorted(hits))})"
    fn = (p.get("original_filename") or "").lower()
    if fn.startswith(("screenshot", "screen shot", "simulator")):
        return "screenshot"
    foreign = not_his_photo(p)
    if foreign:
        return foreign
    sc = p.get("score") or {}
    if not sc:
        return "no score data"
    if sc.get("failure", 0) > 0.55:
        return "Apple flagged as a bad shot"
    return None


def postability(p):
    sc = p.get("score") or {}
    total = sum(w * sc.get(k, 0.0) for k, w in WEIGHTS.items())
    persons = [x for x in (p.get("persons") or []) if x and x != "_UNKNOWN_"]
    faces = p.get("face_info") or []
    if faces or persons:
        total += FACE_BONUS
    if persons:
        total += NAMED_FACE_BONUS
    if p.get("favorite"):
        total += FAVORITE_BONUS
    if p.get("place") or p.get("latitude"):
        total += PLACE_BONUS
    return total


def fetch_originals(photos, outdir):
    """Pull full-resolution originals down from iCloud for the finalists only.

    Most of the library lives in iCloud under "Optimize Mac Storage", so the
    only thing on this Mac is a preview - often 768px, which Instagram shows
    soft. This downloads the real file for the handful we actually care about.

    Uses the AppleScript/Photos.app route deliberately. The PhotoKit route
    (--use-photokit) fails with an authorization error even though Claude has
    Photos access granted, because the venv python has no bundle identity.
    """
    outdir.mkdir(exist_ok=True)
    uuids = []
    for p in photos:
        uuids += ["--uuid", p["uuid"]]
    if not uuids:
        return {}
    cmd = [str(OSXPHOTOS), "export", str(outdir), "--update", "--download-missing",
           "--convert-to-jpeg", "--jpeg-quality", "1.0", "--skip-original-if-edited",
           "--report", str(outdir / "export.json"), "--force-update"] + uuids
    subprocess.run(cmd, capture_output=True, text=True)
    # map uuid -> the largest jpeg the export produced for it
    got = {}
    try:
        rows = json.loads((outdir / "export.json").read_text())
        for r in rows:
            u, f = r.get("uuid"), r.get("filename") or r.get("dest")
            if not u or not f:
                continue
            fp = Path(f)
            if fp.suffix.lower() in (".jpeg", ".jpg") and fp.exists():
                prev = got.get(u)
                if not prev or fp.stat().st_size > Path(prev).stat().st_size:
                    got[u] = str(fp)
    except Exception:
        pass
    return got


def best_preview(p):
    """Largest locally-available JPEG. Works even when the original is
    still up in iCloud and not downloaded to this Mac."""
    paths = [x for x in (p.get("path_derivatives") or []) if x and os.path.exists(x)]
    if not paths:
        orig = p.get("path")
        return orig if orig and os.path.exists(orig) else None
    return max(paths, key=lambda f: os.path.getsize(f))


def drop_already_posted(photos, threshold=14):
    """Filter out photos already on the account. Without this the same good
    photo keeps resurfacing every week, which is how you end up posting a
    duplicate."""
    try:
        from PIL import Image
        import seen
    except ImportError:
        return photos, 0
    hashes_file = HERE / "posted_hashes.json"
    if not hashes_file.exists():
        return photos, 0
    posted = json.loads(hashes_file.read_text())
    kept, dropped = [], 0
    for p in photos:
        src = best_preview(p)
        if not src:
            kept.append(p); continue
        try:
            d, _, _ = seen.best_match(src, posted)
        except Exception:
            kept.append(p); continue
        if d <= threshold:
            dropped += 1
        else:
            kept.append(p)
    return kept, dropped


def dedupe_moments(photos, window_sec=120, per_moment=1):
    """Ten shots of the same handshake is one moment, not ten candidates.
    Group by time proximity, keep only the best of each cluster."""
    def ts(p):
        try:
            return datetime.datetime.fromisoformat(p["date"]).timestamp()
        except Exception:
            return 0.0
    ordered = sorted(photos, key=ts)
    clusters, cur = [], []
    for p in ordered:
        if cur and ts(p) - ts(cur[-1]) > window_sec:
            clusters.append(cur); cur = []
        cur.append(p)
    if cur:
        clusters.append(cur)
    kept = []
    for c in clusters:
        c.sort(key=lambda x: x["_score"], reverse=True)
        for p in c[:per_moment]:
            p["_moment_size"] = len(c)
            kept.append(p)
    return kept


def spread_picks(photos, top, max_per_day=3, max_per_place=4):
    """Pick the best photos, but don't let one good night eat the whole
    shortlist. Caps how many finalists can come from a single day or place
    so the list reflects the whole stretch of time."""
    from collections import defaultdict
    day_n, place_n = defaultdict(int), defaultdict(int)
    picked, overflow = [], []
    for p in photos:
        day = (p.get("date") or "")[:10]
        pl = (place_str(p) or "nowhere")
        if day_n[day] >= max_per_day or place_n[pl] >= max_per_place:
            overflow.append(p); continue
        day_n[day] += 1; place_n[pl] += 1
        picked.append(p)
        if len(picked) >= top:
            return picked
    # if caps left us short, backfill with the best of what we skipped
    picked.extend(overflow[: top - len(picked)])
    return picked


def place_str(p):
    pl = p.get("place")
    if isinstance(pl, dict):
        for k in ("name", "address_str"):
            if pl.get(k):
                return pl[k]
        n = pl.get("names") or {}
        for k in ("city", "sub_administrative_area", "state_province", "area_of_interest"):
            v = n.get(k)
            if v:
                return v[0] if isinstance(v, list) else v
    return str(pl) if pl else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="how far back to look")
    ap.add_argument("--from-date"); ap.add_argument("--to-date")
    ap.add_argument("--top", type=int, default=12, help="how many finalists to stage")
    ap.add_argument("--per-moment", type=int, default=1)
    ap.add_argument("--max-per-day", type=int, default=3,
                    help="cap finalists from any single day")
    ap.add_argument("--max-per-place", type=int, default=4,
                    help="cap finalists from any single location")
    ap.add_argument("--no-originals", action="store_true",
                    help="skip the iCloud download and use local previews only")
    ap.add_argument("--include-posted", action="store_true",
                    help="do not filter out photos already on the account")
    ap.add_argument("--library-json", metavar="FILE",
                    help="read photos from a saved osxphotos JSON file instead "
                         "of the Photos library, e.g. examples/sample-library.json")
    args = ap.parse_args()

    if args.library_json:
        photos = load_saved_library(args.library_json)
        args.no_originals = True   # nothing to pull from iCloud
    else:
        photos = load_photos(args.days, args.from_date, args.to_date)
    print(f"Found {len(photos)} photos in range.")

    kept, drops = [], {}
    for p in photos:
        why = is_junk(p)
        if why:
            drops[why] = drops.get(why, 0) + 1
            continue
        kept.append(p)

    print("\nFiltered out:")
    for why, n in sorted(drops.items(), key=lambda x: -x[1]):
        print(f"  {n:5d}  {why}")
    print(f"\n{len(kept)} photos survived the filter.")

    for p in kept:
        p["_score"] = postability(p)
    kept = dedupe_moments(kept, per_moment=args.per_moment)
    print(f"{len(kept)} remain after collapsing duplicate moments.")

    kept.sort(key=lambda p: p["_score"], reverse=True)
    if not args.include_posted:
        # only check the plausible ones - hashing is the slow part
        head, tail = kept[: args.top * 4], kept[args.top * 4:]
        head, n_posted = drop_already_posted(head)
        kept = head + tail
        if n_posted:
            print(f"{n_posted} dropped as already posted to Instagram.")
    finalists = spread_picks(kept, args.top, args.max_per_day, args.max_per_place)

    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    originals = {}
    if not args.no_originals:
        print("\nPulling full-resolution originals from iCloud for the finalists...")
        originals = fetch_originals(finalists, HERE / "originals")
        print(f"  got {len(originals)} of {len(finalists)}")

    manifest = []
    for rank, p in enumerate(finalists, 1):
        src = originals.get(p["uuid"]) or best_preview(p)
        if not src:
            continue
        dst = STAGING / f"{rank:02d}_{p['uuid'][:8]}.jpeg"
        shutil.copy2(src, dst)
        dim = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(dst)],
                             capture_output=True, text=True).stdout
        w = h = 0
        for line in dim.splitlines():
            if "pixelWidth" in line: w = int(line.split(":")[1])
            if "pixelHeight" in line: h = int(line.split(":")[1])
        manifest.append({
            "rank": rank,
            "uuid": p["uuid"],
            "file": str(dst),
            "score": round(p["_score"], 2),
            "date": p.get("date"),
            "place": place_str(p),
            "persons": [x for x in (p.get("persons") or []) if x != "_UNKNOWN_"],
            "face_count": len(p.get("face_info") or []),
            "favorite": bool(p.get("favorite")),
            "labels": (p.get("labels") or [])[:6],
            "apple_caption": p.get("ai_caption"),
            "shots_in_moment": p.get("_moment_size", 1),
            "width": w, "height": h,
            "original_on_mac": not p.get("ismissing"),
            "too_small_to_post": max(w, h) < 1080,
            "full_res": p["uuid"] in originals,
        })

    (HERE / "candidates.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nStaged {len(manifest)} finalists in {STAGING.name}/")
    print("Manifest: candidates.json\n")
    for m in manifest:
        who = ", ".join(m["persons"]) or (f"{m['face_count']} unnamed face(s)" if m["face_count"] else "no people")
        flag = "  LOW-RES" if m["too_small_to_post"] else ""
        print(f"  #{m['rank']:2d} score {m['score']:6.2f}  {m['date'][:10]}  {m['width']}x{m['height']:<5} "
              f"{(m['place'] or 'no location')[:30]:32s} {who}{flag}")


if __name__ == "__main__":
    main()
