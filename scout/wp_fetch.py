"""Fetch the full WordPress.org plugin directory metadata into data/wp_plugins.jsonl."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

API = "https://api.wordpress.org/plugins/info/1.2/"
PER_PAGE = 250
FIELDS = [
    "active_installs", "last_updated", "added", "downloaded", "tested", "requires_php",
    "rating", "ratings", "num_ratings", "support_threads", "support_threads_resolved",
    "tags", "short_description", "homepage", "donate_link", "version", "author",
]
NO_FIELDS = ["description", "sections", "icons", "banners", "screenshots", "versions", "contributors"]
OUT = Path(__file__).resolve().parent.parent / "data" / "wp_plugins.jsonl"


def params(page):
    p = {"action": "query_plugins", "request[page]": page, "request[per_page]": PER_PAGE,
         "request[browse]": "updated"}  # stable ordering; default ordering drifts between pages
    for f in FIELDS:
        p[f"request[fields][{f}]"] = 1
    for f in NO_FIELDS:
        p[f"request[fields][{f}]"] = 0
    return p


def fetch(page):
    for attempt in range(5):
        try:
            r = requests.get(API, params=params(page), timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            time.sleep(2 ** attempt)
            err = e
    print(f"page {page} failed: {err}", file=sys.stderr)
    return {"plugins": []}


def main():
    first = fetch(1)
    pages = first["info"]["pages"]
    print(f"{first['info']['results']} plugins, {pages} pages")
    plugins = list(first["plugins"])
    with ThreadPoolExecutor(8) as ex:
        for i, res in enumerate(ex.map(fetch, range(2, pages + 1)), start=2):
            plugins.extend(res["plugins"])
            if i % 25 == 0:
                print(f"  page {i}/{pages}")
    seen = {}
    for p in plugins:
        seen[p["slug"]] = p
    with OUT.open("w") as f:
        for p in seen.values():
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"wrote {len(seen)} plugins to {OUT}")


if __name__ == "__main__":
    main()
