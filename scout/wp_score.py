"""Shortlist stale WordPress plugins that people still download.

Core signal: a plugin with no release for a long time ships no updates, so its recent
downloads are mostly fresh installs -> live demand that nobody is serving.
Writes data/wp_candidates.csv sorted by score.
"""
import csv
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parent.parent / "data"
STATS = "https://api.wordpress.org/stats/plugin/1.0/downloads.php"
NOW = datetime.utcnow()
MIN_STALE_DAYS = 540  # ~18 months without a release
MIN_INSTALLS = 1000


def last_updated(p):
    try:
        return datetime.strptime(p["last_updated"][:10], "%Y-%m-%d")
    except (KeyError, ValueError, TypeError):
        return None


def downloads_90d(slug):
    for attempt in range(4):
        try:
            r = requests.get(STATS, params={"slug": slug, "limit": 90}, timeout=30)
            r.raise_for_status()
            days = r.json() or {}
            vals = [int(v) for v in days.values()]
            last30 = sum(vals[-30:])
            first30 = sum(vals[:30])
            return sum(vals), last30, first30
        except Exception:  # noqa: BLE001
            time.sleep(2 ** attempt)
    return None, None, None


def main():
    plugins = [json.loads(line) for line in (DATA / "wp_plugins.jsonl").open()]
    pool = []
    for p in plugins:
        lu = last_updated(p)
        if lu and (NOW - lu).days >= MIN_STALE_DAYS and p.get("active_installs", 0) >= MIN_INSTALLS:
            p["_stale_days"] = (NOW - lu).days
            pool.append(p)
    print(f"{len(pool)} stale plugins with >= {MIN_INSTALLS} installs")

    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda p: downloads_90d(p["slug"]), pool))

    rows = []
    for p, (d90, last30, first30) in zip(pool, results):
        if d90 is None:
            continue
        installs = p.get("active_installs", 0)
        threads = p.get("support_threads", 0) or 0
        resolved = p.get("support_threads_resolved", 0) or 0
        # Demand dominates; staleness adds a mild bonus (longer neglect = weaker competition from the owner).
        score = math.log10(d90 + 1) * 10 + min(p["_stale_days"] / 365, 6)
        rows.append({
            "slug": p["slug"],
            "name": p["name"],
            "downloads_90d": d90,
            "downloads_last30": last30,
            "downloads_first30": first30,
            "trend": round(last30 / first30, 2) if first30 else "",
            "active_installs": installs,
            "dl90_per_1k_installs": round(d90 / installs * 1000, 1),
            "stale_years": round(p["_stale_days"] / 365, 1),
            "last_updated": p["last_updated"][:10],
            "tested": p.get("tested", ""),
            "rating": p.get("rating", ""),
            "num_ratings": p.get("num_ratings", 0),
            "support_threads_2mo": threads,
            "unresolved_2mo": threads - resolved,
            "tags": ", ".join((p.get("tags") or {}).values()) if isinstance(p.get("tags"), dict) else "",
            "short_description": p.get("short_description", ""),
            "url": f"https://wordpress.org/plugins/{p['slug']}/",
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    with (DATA / "wp_candidates.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {DATA / 'wp_candidates.csv'}")


if __name__ == "__main__":
    main()
