"""Find GitHub repos that stopped being maintained but still attract new stars and issues.

Runs in GitHub Actions (GraphQL with the workflow token). Two phases:
1. search: stale, non-archived, non-fork repos above MIN_STARS (split star ranges to
   stay under the 1000-results-per-query cap)
2. details: recent stars / issues / PRs and the real last commit date, 50 repos per query
Writes data/gh_repos.jsonl and data/gh_candidates.csv.
"""
import csv
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parent.parent / "data"
MIN_STARS = int(os.environ.get("MIN_STARS", 500))
MAX_STARS = 400_000
NOW = datetime.now(timezone.utc)
STALE_BEFORE = (NOW - timedelta(days=540)).date().isoformat()
WINDOW = NOW - timedelta(days=90)
NOT_SOFTWARE = re.compile(
    r"awesome|tutorial|interview|course|cheat.?sheet|roadmap|books?\b|learn|resources|"
    r"dotfiles|notes|lecture|examples?\b|demo|study|guide|list|papers|homework|leetcode",
    re.I,
)

S = requests.Session()
S.headers.update({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"})


def gql(query, variables=None):
    for attempt in range(8):
        r = S.post("https://api.github.com/graphql",
                   json={"query": query, "variables": variables or {}}, timeout=120)
        if r.status_code in (403, 429, 502, 503):
            wait = int(r.headers.get("Retry-After", 0)) or 30 * (attempt + 1)
            print(f"  http {r.status_code}, sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        body = r.json()
        rl = (body.get("data") or {}).get("rateLimit")
        if rl and rl["remaining"] < 50:
            reset = datetime.fromisoformat(rl["resetAt"].replace("Z", "+00:00"))
            wait = max((reset - datetime.now(timezone.utc)).total_seconds(), 0) + 5
            print(f"  rate limit low ({rl['remaining']}), sleeping {wait:.0f}s", flush=True)
            time.sleep(wait)
        if body.get("errors") and not body.get("data"):
            print(f"  graphql errors: {body['errors'][:2]}", flush=True)
            time.sleep(10)
            continue
        return body["data"]
    raise RuntimeError("GraphQL request kept failing")


SEARCH = """
query($q: String!, $after: String) {
  rateLimit { remaining resetAt }
  search(query: $q, type: REPOSITORY, first: 100, after: $after) {
    repositoryCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on Repository {
      id nameWithOwner url description stargazerCount forkCount pushedAt createdAt
      homepageUrl hasIssuesEnabled
      primaryLanguage { name }
      licenseInfo { spdxId }
      repositoryTopics(first: 10) { nodes { topic { name } } }
    } }
  }
}"""

DETAILS = """
query($ids: [ID!]!) {
  rateLimit { remaining resetAt }
  nodes(ids: $ids) { ... on Repository {
    id
    stargazers(last: 100, orderBy: {field: STARRED_AT, direction: ASC}) { edges { starredAt } }
    issues(last: 50, orderBy: {field: CREATED_AT, direction: ASC}) { totalCount nodes { createdAt title } }
    openIssues: issues(states: OPEN) { totalCount }
    pullRequests(last: 30, orderBy: {field: CREATED_AT, direction: ASC}) { nodes { createdAt } }
    defaultBranchRef { target { ... on Commit { committedDate } } }
  } }
}"""


def q(lo, hi):
    return f"stars:{lo}..{hi} pushed:<{STALE_BEFORE} archived:false fork:false"


def count(lo, hi):
    return gql(SEARCH.replace("first: 100", "first: 1"), {"q": q(lo, hi)})["search"]["repositoryCount"]


def ranges(lo, hi):
    total = count(lo, hi)
    if total <= 1000 or lo == hi:
        return [(lo, hi, total)]
    mid = (lo + hi) // 2
    return ranges(lo, mid) + ranges(mid + 1, hi)


def search_all():
    repos = {}
    for lo, hi, total in ranges(MIN_STARS, MAX_STARS):
        after = None
        while True:
            s = gql(SEARCH, {"q": q(lo, hi), "after": after})["search"]
            for n in s["nodes"]:
                if n:
                    repos[n["id"]] = n
            if not s["pageInfo"]["hasNextPage"]:
                break
            after = s["pageInfo"]["endCursor"]
        print(f"stars {lo}..{hi}: {total} (collected {len(repos)})", flush=True)
    return list(repos.values())


def looks_like_software(r):
    text = f"{r['nameWithOwner']} {r.get('description') or ''}"
    return r.get("primaryLanguage") and r.get("hasIssuesEnabled") and not NOT_SOFTWARE.search(text)


def recent(dates):
    return sum(1 for d in dates if d and datetime.fromisoformat(d.replace("Z", "+00:00")) >= WINDOW)


def main():
    repos = search_all()
    print(f"{len(repos)} stale repos >= {MIN_STARS} stars", flush=True)
    repos = [r for r in repos if looks_like_software(r)]
    print(f"{len(repos)} after software filter", flush=True)

    by_id = {r["id"]: r for r in repos}
    ids = list(by_id)
    for i in range(0, len(ids), 50):
        for n in gql(DETAILS, {"ids": ids[i:i + 50]})["nodes"]:
            if not n:
                continue
            r = by_id[n["id"]]
            stars = [e["starredAt"] for e in n["stargazers"]["edges"]]
            issues = n["issues"]["nodes"]
            r["stars_90d"] = recent(stars)
            r["stars_90d_capped"] = r["stars_90d"] == len(stars) == 100
            r["issues_90d"] = recent([x["createdAt"] for x in issues])
            r["recent_issue_titles"] = [x["title"] for x in issues
                                        if recent([x["createdAt"]])][-8:]
            r["prs_90d"] = recent([x["createdAt"] for x in n["pullRequests"]["nodes"]])
            r["open_issues"] = n["openIssues"]["totalCount"]
            r["last_commit"] = ((n.get("defaultBranchRef") or {}).get("target") or {}).get("committedDate")
        if i % 1000 == 0:
            print(f"  details {i}/{len(ids)}", flush=True)

    DATA.mkdir(exist_ok=True)
    with (DATA / "gh_repos.jsonl").open("w") as f:
        for r in repos:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    rows = []
    for r in repos:
        if "stars_90d" not in r:
            continue
        last = r.get("last_commit") or r["pushedAt"]
        stale_years = (NOW - datetime.fromisoformat(last.replace("Z", "+00:00"))).days / 365
        if stale_years < 1.5:
            continue
        demand = r["stars_90d"] + 3 * r["issues_90d"] + 2 * r["prs_90d"]
        rows.append({
            "repo": r["nameWithOwner"],
            "stars": r["stargazerCount"],
            "stars_90d": f"{r['stars_90d']}+" if r["stars_90d_capped"] else r["stars_90d"],
            "issues_90d": r["issues_90d"],
            "prs_90d": r["prs_90d"],
            "open_issues": r["open_issues"],
            "forks": r["forkCount"],
            "stale_years": round(stale_years, 1),
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "license": (r.get("licenseInfo") or {}).get("spdxId"),
            "topics": ", ".join(t["topic"]["name"] for t in r["repositoryTopics"]["nodes"]),
            "description": (r.get("description") or "")[:200],
            "recent_issue_titles": " | ".join(r["recent_issue_titles"]),
            "homepage": r.get("homepageUrl") or "",
            "url": r["url"],
            "score": round(math.log10(demand + 1) * 10 + min(stale_years, 6), 2),
        })
    rows.sort(key=lambda x: x["score"], reverse=True)
    with (DATA / "gh_candidates.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} candidates", flush=True)


if __name__ == "__main__":
    sys.exit(main())
