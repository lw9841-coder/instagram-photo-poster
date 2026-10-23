#!/usr/bin/env python3
"""
Publish photos to Instagram as stories, one story per photo.

Without --publish this only builds the 9:16 frames into story-previews/ so
they can be looked at first. Nothing reaches Instagram until --publish.

A story sent through the API is a bare image: no caption, no mention
stickers, no links, no location. When there is someone to tag, post that one
from the phone instead.

This is its own file on purpose. post.py publishes the feed and is left alone.
"""
import argparse, json, shutil, sys, tempfile, time, urllib.request
from pathlib import Path

import r2
from post import HERE, api, load_env

W, H = 1080, 1920
PREVIEWS = HERE / "story-previews"


def prep_story(src, workdir):
    """Build a 1080x1920 JPEG: the photo, whole, centered on a blurred and
    darkened copy of itself. This is what the Instagram app does with a photo
    that is not 9:16, and it works for any shape of source.

    EXIF rotation is baked into the pixels and the flag dropped, for the same
    reason as prep_image in post.py: Meta honors the tag inconsistently.
    """
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    img = ImageOps.exif_transpose(Image.open(src))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size

    # Background: scale to cover the frame, crop the overflow, blur, darken.
    s = max(W / w, H / h)
    bg = img.resize((max(W, round(w * s)), max(H, round(h * s))), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H))
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)

    # Foreground: scale to fit, never enlarge. A small photo blown up to
    # full width looks worse than the same photo sitting smaller and sharp.
    s = min(W / w, H / h, 1.0)
    fg = img.resize((round(w * s), round(h * s)), Image.LANCZOS) if s < 1 else img
    if s == 1.0 and w < W * 0.75:
        print(f"  warning: {Path(src).name} is only {w}x{h}, it will sit small in the frame")
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))

    out = Path(workdir) / (Path(src).stem + "_story.jpg")
    bg.save(out, "JPEG", quality=92, optimize=True)
    return out


def wait_fetchable(url, seconds=60):
    # Same rule as post.py: never hand Meta a URL we have not fetched first.
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def publish_one(env, frame):
    """Upload, publish, clean up. Returns the media id. Exits on failure."""
    url, key = r2.upload(env, frame)
    try:
        if not wait_fetchable(url):
            sys.exit("The presigned R2 link never became fetchable.")
        uid, token = env["IG_USER_ID"], env["IG_ACCESS_TOKEN"]
        cid = api("POST", f"/{uid}/media",
                  {"image_url": url, "media_type": "STORIES"}, token)["id"]
        for _ in range(30):
            st = api("GET", f"/{cid}", {"fields": "status_code,status"}, token)
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                sys.exit(f"Instagram rejected the image: {st.get('status')}")
            time.sleep(2)
        else:
            sys.exit("Instagram never finished fetching the image.")
        return api("POST", f"/{uid}/media_publish", {"creation_id": cid}, token)["id"]
    finally:
        r2.delete(env, key)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="+", help="one story is posted per photo, in order")
    ap.add_argument("--publish", action="store_true",
                    help="actually post. Without this, only previews are built.")
    args = ap.parse_args()

    for src in args.image:
        if not Path(src).is_file():
            sys.exit(f"No such file: {src}")

    if not args.publish:
        PREVIEWS.mkdir(exist_ok=True)
        for src in args.image:
            print(f"Preview  {prep_story(src, PREVIEWS)}")
        print("\nNothing was posted. Rerun with --publish to post these.")
        return

    env = load_env()
    workdir = tempfile.mkdtemp(prefix="igstory-")
    done = []
    try:
        for n, src in enumerate(args.image, start=1):
            print(f"[{n}/{len(args.image)}] {Path(src).name}")
            try:
                media_id = publish_one(env, prep_story(src, workdir))
            except SystemExit as e:
                # A published story cannot be taken back from here, so say
                # exactly where the batch stands instead of just dying.
                print(f"\nFAILED on {Path(src).name}: {e}")
                print(f"  posted     {', '.join(done) or 'none'}")
                rest = [Path(s).name for s in args.image[n - 1:]]
                print(f"  not posted {', '.join(rest)}")
                sys.exit(1)
            done.append(Path(src).name)
            print(f"  posted. Media ID {media_id}")
            with open(HERE / "posted.log", "a") as f:
                f.write(json.dumps({"type": "story", "media_id": media_id,
                                    "image": str(src)}) + "\n")
        print(f"\nAll {len(done)} posted. Stories expire after 24 hours.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
