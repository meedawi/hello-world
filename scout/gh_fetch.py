"""Collect GitHub repos that stopped receiving pushes but still have a meaningful audience.

Uses the search API, splitting star ranges until each query returns < 1000 results
(the search API's hard cap). Writes data/gh_repos.jsonl.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parent.parent / "data"
MIN_STARS = int(os.environ.get("MIN_STARS", 300))
MAX_STARS = 200_000
STALE_BEFORE = (datetime.now(timezone.utc) - timedelta(days=540)).date().isoformat()
S = requests.Session()
S.headers.update({
    "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})


def search(q, page=1):
    while True:
        r = S.get("https://api.github.com/search/repositories",
                  params={"q": q, "per_page": 100, "page": page, "sort": "stars"}, timeout=60)
        if r.status_code in (403, 429):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            time.sleep(max(reset - time.time(), 5) + 1)
            continue
        r.raise_for_status()
        time.sleep(2.1)  # stay under 30 searches/minute
        return r.json()


def query(lo, hi):
    return f"stars:{lo}..{hi} pushed:<{STALE_BEFORE} archived:false fork:false"


def ranges(lo, hi):
    total = search(query(lo, hi))["total_count"]
    if total <= 1000 or lo == hi:
        return [(lo, hi, total)]
    mid = (lo + hi) // 2
    return ranges(lo, mid) + ranges(mid + 1, hi)


def main():
    out = DATA / "gh_repos.jsonl"
    seen = {}
    for lo, hi, total in ranges(MIN_STARS, MAX_STARS):
        print(f"stars {lo}..{hi}: {total}")
        for page in range(1, min(10, (total + 99) // 100) + 1):
            for it in search(query(lo, hi), page)["items"]:
                seen[it["full_name"]] = {
                    k: it.get(k) for k in (
                        "full_name", "html_url", "description", "language", "stargazers_count",
                        "forks_count", "open_issues_count", "pushed_at", "created_at", "topics",
                        "has_issues", "homepage",
                    )
                } | {"license": (it.get("license") or {}).get("spdx_id")}
    with out.open("w") as f:
        for rec in seen.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(seen)} repos to {out}")


if __name__ == "__main__":
    main()
