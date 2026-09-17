"""Build the active NFL contracts payload for the site.

nflverse publishes OverTheCap's contract table daily as parquet. Reading
parquet needs pyarrow and a few hundred megabytes, which the web server
does not have to spare, so this runs in GitHub Actions
(.github/workflows/sync-contracts.yml): it reads the file and writes the
active rows, gzipped, to the path given as its one argument. The
workflow then posts that file to /api/sync-contracts with curl, the
same way every other sync job talks to the site.
"""
import gzip
import io
import json
import sys
import urllib.request

import pyarrow.parquet as pq

PARQUET_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
               "contracts/historical_contracts.parquet")
COLUMNS = ["player", "position", "team", "is_active", "year_signed", "years", "value",
           "apy", "guaranteed", "apy_cap_pct", "gsis_id", "otc_id", "player_page", "height",
           "weight", "college", "draft_year", "draft_round", "draft_overall", "draft_team",
           "date_of_birth", "season_history", "contract_history"]


def main():
    if len(sys.argv) != 2:
        print("usage: sync_contracts.py OUT_FILE", file=sys.stderr)
        return 2
    out_path = sys.argv[1]
    print("downloading", PARQUET_URL)
    data = urllib.request.urlopen(PARQUET_URL, timeout=180).read()
    table = pq.read_table(io.BytesIO(data), columns=COLUMNS)
    meta = table.schema.metadata or {}
    stamp = (meta.get(b"nflverse_timestamp") or b"").decode("utf-8", "replace") or None
    rows = [r for r in table.to_pylist() if r.get("is_active")]
    print(f"{table.num_rows} rows in the file, {len(rows)} active; stamp {stamp}")
    if len(rows) < 500:
        print("too few active rows to trust the file; not writing", file=sys.stderr)
        return 1
    body = gzip.compress(json.dumps({"stamp": stamp, "rows": rows}, default=str).encode("utf-8"))
    with open(out_path, "wb") as f:
        f.write(body)
    print(f"wrote {len(body)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
