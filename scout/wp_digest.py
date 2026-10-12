"""Print compact per-plugin digests (for the AI review step) from data/wp_enriched.jsonl.

Usage: python scout/wp_digest.py START END
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
start, end = (int(a) for a in sys.argv[1:3])
rows = [json.loads(line) for line in (DATA / "wp_enriched.jsonl").open()][start:end]
for i, r in enumerate(rows, start=start + 1):
    print(f"#{i} {r['slug']} — {r['name']}")
    print(f"  dl90={r['downloads_90d']} trend={r['trend']} installs={r['active_installs']} "
          f"dl90/1k={r['dl90_per_1k_installs']} stale={r['stale_years']}y tested={r['tested']} "
          f"rating={r['rating']}({r['num_ratings']}) author={r['author']}")
    print(f"  desc: {r['short_description'][:160]}")
    print(f"  tags: {r['tags']}")
    print(f"  forum(12mo): topics={r['topics_last_year']} unanswered={r['unanswered_last_year']} "
          f"reviews={r['reviews_last_year']}")
    for t in r["recent_topics"][:6]:
        print(f"    - [{t['date']}] {t['title']} :: {t['text'][:110]}")
    for t in r["recent_reviews"][:3]:
        print(f"    * review [{t['date']}] {t['title']} :: {t['text'][:90]}")
    print(f"  maintained competitors={r['maintained_competitors']}: {', '.join(r['top_competitors'][:3])}")
