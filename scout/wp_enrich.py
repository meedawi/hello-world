"""Enrich top WordPress candidates with forum pain signals and maintained competitors.

Reads data/wp_candidates.csv, writes data/wp_enriched.jsonl.
"""
import csv
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parent.parent / "data"
TOP_N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
NOW = datetime.now(timezone.utc)
YEAR_AGO = NOW - timedelta(days=365)
ITEM = re.compile(r"<item>(.*?)</item>", re.S)


def tag(xml, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else ""


def clean(text, n=220):
    text = re.sub(r"<!\[CDATA\[|\]\]>", "", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()[:n]


def feed(url):
    r = None
    for attempt in range(6):  # the forums rate-limit aggressively (HTTP 429)
        try:
            r = requests.get(url, timeout=30)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code != 429:
            break
        time.sleep(5 * 2 ** attempt)
    if r is None or r.status_code != 200:
        return []
    time.sleep(1)
    items = []
    for raw in ITEM.findall(r.text):
        try:
            date = parsedate_to_datetime(tag(raw, "pubDate"))
        except (TypeError, ValueError):
            continue
        desc = clean(tag(raw, "description"), 400)
        m = re.match(r"Replies: (\d+)", desc)
        items.append({
            "date": date.date().isoformat(),
            "recent": date >= YEAR_AGO,
            "title": clean(tag(raw, "title"), 120),
            "replies": int(m.group(1)) if m else None,
            "text": re.sub(r"^Replies: \d+\s*", "", desc)[:220],
        })
    return items


def forum(slug):
    base = f"https://wordpress.org/support/plugin/{slug}"
    topics = feed(f"{base}/feed/")
    reviews = feed(f"{base}/reviews/feed/")
    recent_topics = [t for t in topics if t["recent"]]
    return {
        "topics_last_year": len(recent_topics),
        "unanswered_last_year": sum(1 for t in recent_topics if t["replies"] == 0),
        "recent_topics": recent_topics[:12],
        "reviews_last_year": sum(1 for r in reviews if r["recent"]),
        "recent_reviews": [r for r in reviews if r["recent"]][:8],
    }


def competitors(cand, plugins_by_tag, plugins):
    """Maintained plugins (release within 12 months) sharing >= 2 tags, at least half as big."""
    tags = list(cand["tags"])
    counts = {}
    for t in tags:
        for slug in plugins_by_tag.get(t, ()):
            counts[slug] = counts.get(slug, 0) + 1
    out = []
    for slug, shared in counts.items():
        p = plugins[slug]
        if slug == cand["slug"] or shared < min(2, len(tags)):
            continue
        if p["_age_days"] is None or p["_age_days"] > 365:
            continue
        if p.get("active_installs", 0) < cand["active_installs"] / 2:
            continue
        out.append((p.get("active_installs", 0), slug))
    out.sort(reverse=True)
    return {"maintained_competitors": len(out), "top_competitors": [f"{s} ({i:,})" for i, s in out[:5]]}


def main():
    plugins = {}
    plugins_by_tag = {}
    now = datetime.utcnow()
    for line in (DATA / "wp_plugins.jsonl").open():
        p = json.loads(line)
        try:
            p["_age_days"] = (now - datetime.strptime(p["last_updated"][:10], "%Y-%m-%d")).days
        except (KeyError, ValueError, TypeError):
            p["_age_days"] = None
        plugins[p["slug"]] = p
        tags = p.get("tags") if isinstance(p.get("tags"), dict) else {}
        for t in tags:
            plugins_by_tag.setdefault(t, []).append(p["slug"])

    rows = list(csv.DictReader((DATA / "wp_candidates.csv").open()))[:TOP_N]
    with ThreadPoolExecutor(2) as ex:
        forums = list(ex.map(lambda r: forum(r["slug"]), rows))

    with (DATA / "wp_enriched.jsonl").open("w") as f:
        for row, fo in zip(rows, forums):
            p = plugins[row["slug"]]
            cand = {
                "slug": row["slug"],
                "tags": list(p.get("tags").keys()) if isinstance(p.get("tags"), dict) else [],
                "active_installs": p.get("active_installs", 0),
            }
            rec = {**row, **fo, **competitors(cand, plugins_by_tag, plugins),
                   "author": clean(p.get("author", ""), 80), "homepage": p.get("homepage", "")}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"enriched {len(rows)} candidates -> {DATA / 'wp_enriched.jsonl'}")


if __name__ == "__main__":
    main()
