#!/usr/bin/env python3
"""Put an image on a public URL long enough for Meta to fetch it, then remove it.

Meta will not accept a file upload for photos. Their words: "We cURL your image
using the passed in URL so it must be on a public server." The original design
used a temporary Cloudflare tunnel, which stopped working on 2026-08-29 -
cloudflared still issues a hostname but no DNS is ever published for it, so the
URL resolves nowhere, on any resolver. This replaces it with Cloudflare R2.

The bucket stays private. Each post gets a presigned URL - a signed link that
works without credentials and expires on its own - so nothing is ever world
readable by default, and the object is deleted as soon as Instagram has it.
"""
import sys
import uuid
from pathlib import Path

REQUIRED = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET")

# How long Meta gets to fetch the image. Their download is usually instant;
# this is generous so a slow fetch never fails a post.
LINK_TTL_SECONDS = 900


def _client(env):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        sys.exit("boto3 is missing. Run: .venv/bin/pip install boto3")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        region_name="auto",
    )


def check(env):
    """Return a list of missing settings, empty if configured."""
    return [k for k in REQUIRED if not env.get(k)]


def upload(env, path):
    """Upload one file. Returns (public_url, key)."""
    missing = check(env)
    if missing:
        sys.exit("R2 is not configured. Missing in .env: " + ", ".join(missing))

    path = Path(path)
    # Random key, so a filename never reveals anything and two posts of the
    # same photo cannot collide.
    key = f"pending/{uuid.uuid4().hex}{path.suffix.lower()}"
    ctype = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"

    c = _client(env)
    c.upload_file(str(path), env["R2_BUCKET"], key,
                  ExtraArgs={"ContentType": ctype})
    url = c.generate_presigned_url(
        "get_object",
        Params={"Bucket": env["R2_BUCKET"], "Key": key},
        ExpiresIn=LINK_TTL_SECONDS,
    )
    return url, key


def delete(env, key):
    """Remove the object. Never fatal - a leftover file is not worth a crash."""
    try:
        _client(env).delete_object(Bucket=env["R2_BUCKET"], Key=key)
        return True
    except Exception as e:
        print(f"  note: could not delete {key} from R2 ({e}). Remove it by hand.",
              file=sys.stderr)
        return False


def sweep(env, older_than_hours=24):
    """Delete anything left behind by a crashed run."""
    from datetime import datetime, timedelta, timezone
    c = _client(env)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    removed = 0
    paginator = c.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=env["R2_BUCKET"], Prefix="pending/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                c.delete_object(Bucket=env["R2_BUCKET"], Key=obj["Key"])
                removed += 1
    return removed
