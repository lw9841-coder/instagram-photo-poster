#!/usr/bin/env python3
"""
Publish a photo to Instagram.

Meta won't take a file upload - it insists on fetching the image from a public
URL. Each photo goes to a private Cloudflare R2 bucket behind a short-lived
signed link (see r2.py) and is deleted as soon as Instagram has it. Nothing of
yours is left sitting on a public server.
"""
import argparse, json, shutil, socket, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

import r2
from pathlib import Path

HERE = Path(__file__).parent
GRAPH = "https://graph.instagram.com/v23.0"


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
    missing = [k for k in ("IG_ACCESS_TOKEN", "IG_USER_ID") if not env.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    return env


def prep_image(src, workdir):
    """Instagram wants JPEG, at most 1440px wide, aspect between 4:5 and 1.91:1.

    Critically: bake EXIF rotation into the pixels and strip the flag. Many
    iPhone photos are stored sideways with an orientation tag, and Meta honors
    that tag inconsistently - which is how a portrait photo ends up posted on
    its side. Do not "simplify" this back to a plain sips convert.
    """
    from PIL import Image, ImageOps

    out = Path(workdir) / "post.jpg"
    img = Image.open(src)
    raw_orientation = img.getexif().get(274)
    img = ImageOps.exif_transpose(img)          # rotate pixels, drop the flag
    if img.mode != "RGB":
        img = img.convert("RGB")
    if raw_orientation not in (None, 1):
        print(f"  rotated upright (EXIF orientation was {raw_orientation})")

    w, h = img.size
    if w > 1440:
        h = int(h * 1440 / w); w = 1440
        img = img.resize((w, h), Image.LANCZOS)

    ratio = w / h
    if ratio < 0.8:
        print(f"  note: {w}x{h} is taller than Instagram's 4:5 limit - it will crop top and bottom")
    if ratio > 1.91:
        print(f"  note: {w}x{h} is wider than Instagram's 1.91:1 limit - it will crop the sides")
    if max(w, h) < 1080:
        print(f"  warning: only {w}x{h}. Instagram displays at 1080 wide, so this will look soft.")

    img.save(out, "JPEG", quality=92, optimize=True)
    return out, w, h


def api(method, path, params, token):
    params = dict(params, access_token=token)
    if method == "GET":
        req = urllib.request.Request(f"{GRAPH}{path}?{urllib.parse.urlencode(params)}")
    else:
        req = urllib.request.Request(f"{GRAPH}{path}",
                                     data=urllib.parse.urlencode(params).encode(),
                                     method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:400]
        sys.exit(f"Instagram API error ({e.code}) on {path}:\n  {msg}")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.gaierror) or "nodename nor servname" in str(reason):
            sys.exit("Cannot resolve graph.instagram.com.\n"
            "\n"
            "  This is almost always a VPN. ProtonVPN's DNS does not resolve this\n"
            "  one hostname, even though instagram.com and graph.facebook.com both\n"
            "  work fine - so the connection looks healthy and only this breaks.\n"
            "\n"
            "  Disconnect the VPN and run this again.")
        sys.exit(f"Could not reach Instagram: {reason}")


def whoami(env):
    me = api("GET", f"/{env['IG_USER_ID']}",
             {"fields": "id,username,account_type,media_count"}, env["IG_ACCESS_TOKEN"])
    return me


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="*", help="path to the photo; give more "
                                             "than one to post a carousel")
    ap.add_argument("--caption", default="")
    ap.add_argument("--caption-file")
    ap.add_argument("--tag", action="append", default=[], metavar="USERNAME[:N]",
                    help="tag an account; repeatable. Append :N to put the tag "
                         "on photo N of a carousel, e.g. example_user:3. "
                         "Without :N the tag goes on --tag-image.")
    ap.add_argument("--tag-image", type=int, default=1, metavar="N",
                    help="default photo for tags that don't specify one. "
                         "1-based. Default 1.")
    ap.add_argument("--check", action="store_true",
                    help="just verify the token works, post nothing")
    ap.add_argument("--stage", action="store_true",
                    help="build the containers so Instagram validates the "
                         "images, caption and every tagged username, then "
                         "stop without publishing. Unpublished containers "
                         "expire on their own after 24 hours.")
    ap.add_argument("--dry-run", action="store_true",
                    help="prepare and expose the image but do not publish")
    args = ap.parse_args()

    env = load_env()

    if args.check:
        me = whoami(env)
        print("Token works.")
        print(f"  username     {me.get('username')}")
        print(f"  account type {me.get('account_type')}")
        print(f"  posts        {me.get('media_count')}")
        return

    if not args.image:
        sys.exit("Give me an image path, or use --check.")
    if len(args.image) > 10:
        sys.exit(f"{len(args.image)} images. Instagram allows 10 in a carousel.")
    # Group tags by the photo they belong to: "name:3" -> photo 3.
    tags_by_image = {}
    for raw in args.tag:
        name, _, where = raw.partition(":")
        try:
            n = int(where) if where else args.tag_image
        except ValueError:
            sys.exit(f"--tag {raw}: after the colon give a photo number.")
        if n < 1 or n > max(1, len(args.image)):
            sys.exit(f"--tag {raw} points at photo {n}, but there are "
                     f"{len(args.image)} photos.")
        tags_by_image.setdefault(n, []).append(name.lstrip("@"))
    caption = args.caption
    if args.caption_file:
        caption = Path(args.caption_file).read_text().strip()
    if len(caption) > 2200:
        sys.exit(f"Caption is {len(caption)} characters. Instagram's limit is 2200.")

    workdir = tempfile.mkdtemp(prefix="igpost-")
    r2_key = None
    r2_keys = []
    try:
        images = []
        for n, src in enumerate(args.image):
            sub = Path(workdir) / f"i{n}"
            sub.mkdir()
            im, w, h = prep_image(src, sub)
            print(f"Prepared {Path(src).name} at {w}x{h}")
            images.append(im)
        img = images[0]

        # Meta insists on fetching the photo from a public URL. See r2.py.
        uploaded = [r2.upload(env, i) for i in images]
        r2_keys = [k for _, k in uploaded]
        r2_key = r2_keys[0]
        image_url = uploaded[0][0]
        print(f"Uploaded {len(uploaded)} image(s) to R2, waiting to be fetchable...")

        # Do not hand Meta a URL we have not fetched ourselves first; a 404 on
        # their side fails the whole post with an unhelpful error.
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(image_url, timeout=8) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                pass
            time.sleep(2)
        if not ready:
            sys.exit("The presigned R2 link never became fetchable.\n"
                     "  Check the API token has Object Read & Write on this bucket.")
        print(f"  fetchable after {60 - int(deadline - time.time())}s")

        if args.dry_run:
            print(f"Dry run. Image is reachable at {image_url}")
            print("Publishing skipped. Press Ctrl-C when done looking.")
            time.sleep(120)
            return

        print("Handing the image to Instagram...")
        # Instagram wants a position per tag, 0-1 from the top left. Spread
        # the tags on a given photo evenly so they never stack on each other.
        def tags_for(n):
            names = tags_by_image.get(n)
            if not names:
                return None
            k = len(names)
            return json.dumps([{"username": u,
                                "x": round((i + 1) / (k + 1), 3),
                                "y": 0.5}
                               for i, u in enumerate(names)])

        for n in sorted(tags_by_image):
            where = f" on photo {n}" if len(uploaded) > 1 else ""
            print(f"Tagging{where}: "
                  f"{', '.join('@' + u for u in tags_by_image[n])}")

        if len(uploaded) > 1:
            # A carousel is built in two rounds: one container per photo marked
            # is_carousel_item, then a parent container listing their ids. Tags
            # belong to a child, not the parent, so they go on the photo the
            # people are actually in.
            children = []
            for n, (url, _) in enumerate(uploaded, start=1):
                p = {"image_url": url, "is_carousel_item": "true"}
                t = tags_for(n)
                if t:
                    p["user_tags"] = t
                c = api("POST", f"/{env['IG_USER_ID']}/media", p,
                        env["IG_ACCESS_TOKEN"])
                children.append(c["id"])
            print(f"Built {len(children)} carousel items")
            params = {"media_type": "CAROUSEL",
                      "children": ",".join(children),
                      "caption": caption}
        else:
            params = {"image_url": image_url, "caption": caption}
            t = tags_for(1)
            if t:
                params["user_tags"] = t

        container = api("POST", f"/{env['IG_USER_ID']}/media",
                        params, env["IG_ACCESS_TOKEN"])
        cid = container["id"]

        # Instagram downloads asynchronously; wait for it to finish
        for _ in range(30):
            st = api("GET", f"/{cid}", {"fields": "status_code,status"},
                     env["IG_ACCESS_TOKEN"])
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code == "ERROR":
                sys.exit(f"Instagram rejected the image: {st.get('status')}")
            time.sleep(2)
        else:
            sys.exit("Instagram never finished fetching the image.")

        if args.stage:
            # Everything above is what Instagram validates: it fetched each
            # image, accepted the caption, and accepted every tagged username.
            # A bad handle fails before this point, with nothing published.
            print("\nStaged. Instagram accepted all of it and published nothing.")
            print(f"  container   {cid}")
            print(f"  photos      {len(uploaded)}")
            print(f"  caption     {len(caption)} chars, accepted")
            if tags_by_image:
                for n in sorted(tags_by_image):
                    print(f"  tags photo {n}  "
                          f"{', '.join('@' + u for u in tags_by_image[n])}  accepted")
            else:
                print("  tags        none")
            print("\nRerun without --stage to publish. The container expires in 24h.")
            return

        res = api("POST", f"/{env['IG_USER_ID']}/media_publish",
                  {"creation_id": cid}, env["IG_ACCESS_TOKEN"])
        media_id = res["id"]
        print(f"\nPosted. Media ID {media_id}")

        for k in r2_keys:
            r2.delete(env, k)
        r2_keys, r2_key = [], None

        link = api("GET", f"/{media_id}", {"fields": "permalink"}, env["IG_ACCESS_TOKEN"])
        if link.get("permalink"):
            print(f"  {link['permalink']}")

        with open(HERE / "posted.log", "a") as f:
            f.write(json.dumps({"media_id": media_id, "image": str(args.image),
                                "caption": caption,
                                "permalink": link.get("permalink")}) + "\n")
    finally:
        # If we uploaded but never published - crash, rejection, Ctrl-C - the
        # object is still sitting in a public bucket. Always clean it up.
        for k in r2_keys:
            r2.delete(env, k)
        shutil.rmtree(workdir, ignore_errors=True)
        print("Temp files removed.")


if __name__ == "__main__":
    main()
