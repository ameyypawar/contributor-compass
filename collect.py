"""
Collect the raw GitHub data behind the Contributor-Friendliness Index.

Two phases:
  1. discover() - search for repos across languages and star buckets, so the
     sample is stratified rather than "whatever is trending".
  3. fetch_locations() - profile locations for the most frequent committers.

Phase 2 pulls per-repo detail: PRs with author_association, issues with their
first few comments, and recent commit history.

Writes newline-delimited JSON to data/raw.jsonl and is resumable: rerunning
skips repos already on disk. Respects the GraphQL rate limit (5000 points/hr)
by watching the cost/remaining fields GitHub returns with every response.

Auth comes from the gh CLI, so there is no token to manage or leak:
    gh auth token
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.github.com/graphql"
RAW = Path(__file__).parent / "data" / "raw.jsonl"
DISCOVERED = Path(__file__).parent / "data" / "discovered.json"
LOCATIONS = Path(__file__).parent / "data" / "locations.json"

# Stratified sample. Comparing a 200-star library against a 40k-star monorepo on
# raw response time is meaningless, so we deliberately collect across the whole
# range and compare within buckets later.
LANGUAGES = ["Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C++", "Ruby"]
STAR_BUCKETS = ["50..200", "200..1000", "1000..5000", "5000..20000", ">20000"]
PER_BUCKET = 13  # 8 langs x 5 buckets x 13 = 520 repos

# Per-repo page sizes. These drive the GraphQL cost (roughly nodes/100), which
# is what the 5000 points/hour limit is actually spent on.
N_PRS = 50
N_ISSUES = 50
N_COMMENTS = 5
N_COMMITS = 100
N_GFI = 30

RATE_FLOOR = 150  # pause when remaining points drop below this


def token() -> str:
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("Could not read a token from `gh auth token`. Run `gh auth login` first.")


TOKEN = token()
_local = threading.local()


def session() -> requests.Session:
    """One Session per thread - requests.Session is not safe to share across them."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"Authorization": f"bearer {TOKEN}"})
        _local.session = s
    return s


# Kept as a module-level name so ad-hoc scripts and notebooks can reach a session.
SESSION = session()


def gql(query: str, variables: dict) -> dict:
    """POST a query, retrying on transient failures and honouring the rate limit."""
    for attempt in range(5):
        try:
            r = session().post(API, json={"query": query, "variables": variables}, timeout=60)
        except requests.RequestException as e:
            print(f"    network error ({e}), retrying", flush=True)
            time.sleep(5 * (attempt + 1))
            continue

        if r.status_code in (502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 403:
            # Secondary rate limit. Back off hard.
            print("    secondary rate limit, sleeping 60s", flush=True)
            time.sleep(60)
            continue

        r.raise_for_status()
        payload = r.json()

        if "errors" in payload:
            msgs = [e.get("message", "") for e in payload["errors"]]
            # A repo that vanished or went private is expected; let the caller skip it.
            if any("Could not resolve" in m or "NOT_FOUND" in m for m in msgs):
                return {"_skip": "; ".join(msgs)}
            if any("rate limit" in m.lower() for m in msgs):
                time.sleep(60)
                continue
            # Partial data with errors still beats nothing.
            if payload.get("data"):
                print(f"    partial: {msgs[:1]}", flush=True)
            else:
                return {"_skip": "; ".join(msgs)}

        data = payload.get("data") or {}
        limit = data.get("rateLimit") or {}
        if limit.get("remaining", 9999) < RATE_FLOOR:
            reset = limit.get("resetAt")
            wait = 60
            if reset:
                dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                wait = max(30, (dt - datetime.now(timezone.utc)).total_seconds() + 10)
            print(f"    rate limit low ({limit.get('remaining')}), sleeping {wait:.0f}s", flush=True)
            time.sleep(wait)
        return data

    return {"_skip": "gave up after retries"}


SEARCH_Q = """
query($q: String!) {
  rateLimit { remaining resetAt }
  search(query: $q, type: REPOSITORY, first: %d) {
    nodes {
      ... on Repository {
        nameWithOwner
        stargazerCount
        primaryLanguage { name }
      }
    }
  }
}
""" % PER_BUCKET


def discover() -> list[dict]:
    """Search each language x star bucket so the sample spans the whole range."""
    if DISCOVERED.exists():
        found = json.loads(DISCOVERED.read_text())
        print(f"Reusing {len(found)} discovered repos from {DISCOVERED.name}")
        return found

    seen: dict[str, dict] = {}
    for lang in LANGUAGES:
        for bucket in STAR_BUCKETS:
            q = (
                f"language:{lang} stars:{bucket} archived:false "
                f"pushed:>2026-01-01 sort:stars-desc"
            )
            data = gql(SEARCH_Q, {"q": q})
            nodes = (data.get("search") or {}).get("nodes") or []
            for n in nodes:
                if not n or not n.get("nameWithOwner"):
                    continue
                seen[n["nameWithOwner"]] = {
                    "repo": n["nameWithOwner"],
                    "stars": n["stargazerCount"],
                    "language": (n.get("primaryLanguage") or {}).get("name"),
                    "bucket": bucket,
                }
            print(f"  {lang:12} {bucket:12} -> {len(nodes):3} repos", flush=True)
            time.sleep(0.4)

    found = list(seen.values())
    DISCOVERED.parent.mkdir(parents=True, exist_ok=True)
    DISCOVERED.write_text(json.dumps(found, indent=2))
    print(f"\nDiscovered {len(found)} unique repos")
    return found


# Note: committedDate is typed GitTimestamp, but GitHub returns it normalised to
# UTC (verified against django, rust and pandas - every offset came back as Z).
# The original git timezone offset is therefore NOT recoverable from the API, so
# geography comes from profile location instead. See fetch_locations().
DETAIL_Q = """
query($owner: String!, $name: String!) {
  rateLimit { remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    description
    stargazerCount
    forkCount
    diskUsage
    createdAt
    pushedAt
    isArchived
    isFork
    primaryLanguage { name }
    licenseInfo { spdxId }
    repositoryTopics(first: 10) { nodes { topic { name } } }

    openIssues: issues(states: OPEN) { totalCount }
    closedIssues: issues(states: CLOSED) { totalCount }
    openPRs: pullRequests(states: OPEN) { totalCount }
    mergedPRs: pullRequests(states: MERGED) { totalCount }
    closedPRs: pullRequests(states: CLOSED) { totalCount }
    releases { totalCount }
    latestRelease { publishedAt }

    contributing: object(expression: "HEAD:CONTRIBUTING.md") { ... on Blob { byteSize } }
    contributingAlt: object(expression: "HEAD:.github/CONTRIBUTING.md") { ... on Blob { byteSize } }
    contributingRst: object(expression: "HEAD:CONTRIBUTING.rst") { ... on Blob { byteSize } }
    contributingDocs: object(expression: "HEAD:docs/CONTRIBUTING.md") { ... on Blob { byteSize } }
    codeOfConduct: object(expression: "HEAD:CODE_OF_CONDUCT.md") { ... on Blob { byteSize } }
    codeOfConductAlt: object(expression: "HEAD:.github/CODE_OF_CONDUCT.md") { ... on Blob { byteSize } }
    readme: object(expression: "HEAD:README.md") { ... on Blob { byteSize } }
    issueTemplates: object(expression: "HEAD:.github/ISSUE_TEMPLATE") { ... on Tree { entries { name } } }
    prTemplate: object(expression: "HEAD:.github/PULL_REQUEST_TEMPLATE.md") { ... on Blob { byteSize } }
    workflows: object(expression: "HEAD:.github/workflows") { ... on Tree { entries { name } } }

    gfiOpen: issues(states: OPEN, labels: ["good first issue"]) { totalCount }
    helpWantedOpen: issues(states: OPEN, labels: ["help wanted"]) { totalCount }
    gfiDetail: issues(states: OPEN, labels: ["good first issue"], first: %(n_gfi)d,
                      orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number createdAt updatedAt
        assignees { totalCount }
        comments { totalCount }
      }
    }

    prs: pullRequests(first: %(n_prs)d, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number state createdAt closedAt mergedAt
        authorAssociation
        author { login }
        additions deletions changedFiles
        reviews(first: 1) { nodes { createdAt author { login } } }
        comments(first: 1) { nodes { createdAt author { login } authorAssociation } }
      }
    }

    issuesDetail: issues(first: %(n_issues)d, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number state createdAt closedAt
        authorAssociation
        author { login }
        assignees { totalCount }
        labels(first: 5) { nodes { name } }
        comments(first: %(n_comments)d) {
          nodes { createdAt author { login } authorAssociation }
        }
      }
    }

    defaultBranchRef {
      target {
        ... on Commit {
          history(first: %(n_commits)d) {
            totalCount
            nodes {
              committedDate
              author { name user { login } }
            }
          }
        }
      }
    }
  }
}
""" % {
    "n_gfi": N_GFI,
    "n_prs": N_PRS,
    "n_issues": N_ISSUES,
    "n_comments": N_COMMENTS,
    "n_commits": N_COMMITS,
}


def already_done() -> set[str]:
    if not RAW.exists():
        return set()
    done = set()
    with RAW.open() as f:
        for line in f:
            try:
                done.add(json.loads(line)["nameWithOwner"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def fetch(repos: list[dict], workers: int = 6) -> None:
    """Fetch repo detail concurrently.

    Each query takes about five seconds, and the whole run costs ~1000 of the
    5000 hourly points, so this is latency-bound rather than quota-bound. A
    small pool cuts the wall time without tripping the secondary rate limit.
    """
    done = already_done()
    todo = [r for r in repos if r["repo"] not in done]
    print(f"\n{len(done)} already collected, {len(todo)} to go ({workers} workers)\n")
    if not todo:
        return

    RAW.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    counter = {"n": 0}

    def one(r: dict) -> None:
        owner, name = r["repo"].split("/", 1)
        data = gql(DETAIL_Q, {"owner": owner, "name": name})
        repo = data.get("repository") if not data.get("_skip") else None

        with write_lock:
            counter["n"] += 1
            i = counter["n"]
            if not repo:
                reason = data.get("_skip", "no data")
                print(f"[{i:4}/{len(todo)}] skip {r['repo']}: {reason[:50]}", flush=True)
                return
            repo["_bucket"] = r["bucket"]
            with RAW.open("a") as out:
                out.write(json.dumps(repo) + "\n")
            budget = (data.get("rateLimit") or {}).get("remaining", "?")
            print(
                f"[{i:4}/{len(todo)}] {r['repo']:48} "
                f"{repo['stargazerCount']:>7} stars  budget={budget}",
                flush=True,
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))


def fetch_locations(top_n: int = 5, batch: int = 50) -> None:
    """Pull the profile location of each repo's most frequent committers.

    The GitHub API normalises every commit timestamp to UTC, so the original
    git timezone offset is not recoverable from the API - profile location is
    the workable route to geography. It is free text ("NYC", "Berlin, Germany",
    "Earth, Milky Way"), so normalising it to countries is a real cleaning step.
    """
    if not RAW.exists():
        sys.exit("No raw data yet - run the detail phase first.")

    logins: set[str] = set()
    with RAW.open() as f:
        for line in f:
            try:
                repo = json.loads(line)
            except json.JSONDecodeError:
                continue
            ref = repo.get("defaultBranchRef") or {}
            target = ref.get("target") or {}
            counts: dict[str, int] = {}
            for c in (target.get("history") or {}).get("nodes") or []:
                user = ((c.get("author") or {}).get("user") or {})
                login = user.get("login")
                if login:
                    counts[login] = counts.get(login, 0) + 1
            top = sorted(counts, key=counts.get, reverse=True)[:top_n]
            logins.update(top)

    known: dict[str, str | None] = {}
    if LOCATIONS.exists():
        known = json.loads(LOCATIONS.read_text())
    todo = sorted(logins - set(known))
    print(f"{len(known)} locations cached, {len(todo)} contributors to look up")

    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        # GraphQL has no bulk users() field, so alias one lookup per login.
        parts = " ".join(
            f'u{j}: user(login: "{lg}") {{ login location company }}'
            for j, lg in enumerate(chunk)
        )
        data = gql("query { rateLimit { remaining resetAt } " + parts + " }", {})
        if data.get("_skip"):
            print(f"  batch {i // batch + 1}: skipped ({data['_skip'][:50]})", flush=True)
            # A single deleted account fails the whole batch, so record them as
            # unknown rather than retrying forever.
            for lg in chunk:
                known.setdefault(lg, None)
        else:
            for key, val in data.items():
                if key.startswith("u") and val and val.get("login"):
                    known[val["login"]] = val.get("location")
            for lg in chunk:
                known.setdefault(lg, None)

        LOCATIONS.write_text(json.dumps(known, indent=2))
        filled = sum(1 for v in known.values() if v)
        print(
            f"  {min(i + batch, len(todo)):5}/{len(todo)} looked up, "
            f"{filled} with a location",
            flush=True,
        )
        time.sleep(0.3)


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"

    if stage in ("all", "discover", "fetch"):
        print("Phase 1: discovery")
        repos = discover()
    if stage in ("all", "fetch"):
        print("\nPhase 2: detail")
        fetch(repos)
    if stage in ("all", "locations"):
        print("\nPhase 3: contributor locations")
        fetch_locations()

    print(f"\nDone. Raw data in {RAW}")
