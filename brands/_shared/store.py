"""Temporary hosting for finished videos, so Metricool can import them.

Metricool copies a post's media to its own storage the moment the post is scheduled, so a
file only has to be reachable for a few minutes. Files go to the owner's Cloudflare R2
bucket under this brand's own folder, latienditabad/, and are deleted once the posts that
use them are scheduled. Nothing outside latienditabad/ is ever written, listed for deletion,
or deleted.

The access keys come from the cloud environment (the *_R2_ACCESS_KEY_ID / *_R2_SECRET_ACCESS_KEY
pair); the bucket and account id from TB_R2_BUCKET and TB_R2_ACCOUNT_ID (or, without them, from the owner's BrandPros checkout on this machine).

    python3 brands/_shared/store.py put FILE KEY        # upload, print a signed link (valid 6 h)
    python3 brands/_shared/store.py link KEY            # fresh signed link for an uploaded file
    python3 brands/_shared/store.py rm KEY              # delete one file
    python3 brands/_shared/store.py ls [PREFIX]         # list files under latienditabad/
    python3 brands/_shared/store.py sweep [HOURS]       # delete latienditabad/ files older than HOURS (default 48)

KEY is relative to latienditabad/, e.g. shop-2026-11-10/video.mp4.
"""
import datetime
import os
import sys

import boto3
from botocore.config import Config

ROOT = "latienditabad/"
TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".json": "application/json"}


def _env(suffix):
    for k, v in os.environ.items():
        if k.endswith(suffix) and v:
            return v
    return None


def _sibling():
    """Without TB_R2_*, reuse the bucket settings of the owner's BrandPros checkout if one is on this machine."""
    import re
    path = os.path.join(os.environ.get("BRANDPROS_DIR", "/home/user/fwagtx/brandpros"), "brand", "_shared", "store.py")
    try:
        txt = open(path).read()
        return re.search(r'"bucket": "([^"]+)"', txt).group(1), re.search(r'"account_id": "([^"]+)"', txt).group(1)
    except Exception:
        return None, None


def _cfg():
    bucket, account = os.environ.get("TB_R2_BUCKET"), os.environ.get("TB_R2_ACCOUNT_ID")
    if not (bucket and account):
        bucket, account = _sibling()
    kid, sec = _env("R2_ACCESS_KEY_ID"), _env("R2_SECRET_ACCESS_KEY")
    if not (bucket and account and kid and sec):
        raise SystemExit("STOP: R2 hosting is not configured in this environment (TB_R2_BUCKET, TB_R2_ACCOUNT_ID and the R2 key pair).")
    return bucket, account, kid, sec


def _client():
    bucket, account, kid, sec = _cfg()
    return boto3.client("s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
                        aws_access_key_id=kid, aws_secret_access_key=sec, region_name="auto",
                        config=Config(signature_version="s3v4", retries={"max_attempts": 5}))


def _bucket():
    return _cfg()[0]


def _key(key):
    key = key.lstrip("/")
    if ".." in key.split("/"):
        raise SystemExit("Bad key: " + key)
    return key if key.startswith(ROOT) else ROOT + key


def link(key, seconds=6 * 3600):
    return _client().generate_presigned_url("get_object", Params={"Bucket": _bucket(), "Key": _key(key)}, ExpiresIn=seconds)


def put(path, key):
    ext = os.path.splitext(path)[1].lower()
    _client().upload_file(path, _bucket(), _key(key), ExtraArgs={"ContentType": TYPES.get(ext, "application/octet-stream")})
    return link(key)


def ls(prefix=""):
    c, out, token = _client(), [], None
    while True:
        kw = {"Bucket": _bucket(), "Prefix": _key(prefix)}
        if token:
            kw["ContinuationToken"] = token
        r = c.list_objects_v2(**kw)
        out += r.get("Contents", [])
        if not r.get("IsTruncated"):
            return out
        token = r["NextContinuationToken"]


def rm(key):
    _client().delete_object(Bucket=_bucket(), Key=_key(key))


def sweep(hours=48):
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    old = [o["Key"] for o in ls() if o["LastModified"] < cut and o["Key"].startswith(ROOT)]
    for k in old:
        _client().delete_object(Bucket=_bucket(), Key=k)
    return old


def main(a):
    if not a:
        raise SystemExit(__doc__)
    cmd = a[0]
    if cmd == "put":
        print(put(a[1], a[2]))
    elif cmd == "link":
        print(link(a[1]))
    elif cmd == "rm":
        rm(a[1]); print("deleted", _key(a[1]))
    elif cmd == "ls":
        for o in ls(a[1] if len(a) > 1 else ""):
            print(o["LastModified"].strftime("%Y-%m-%d %H:%M"), o["Size"], o["Key"])
    elif cmd == "sweep":
        old = sweep(float(a[1]) if len(a) > 1 else 48)
        print(len(old), "old files deleted")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
