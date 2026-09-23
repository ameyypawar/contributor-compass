"""
Turn the raw GitHub dump into the analysis tables.

raw.jsonl  ->  repos.csv       one row per repo, every metric, plus the index
               prs.csv         one row per pull request
               issues.csv      one row per issue
               contributors.csv one row per contributor, with a cleaned country

The interesting work here is in three places:
  - bot filtering, because bots fake "first response" and inflate activity
  - time-to-first-*human*-response, which needs the comment author compared
    against the issue author
  - stratified percentile scoring, because CHAOSS is explicit that health
    metrics should not be compared across projects on raw values
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
RAW = DATA / "raw.jsonl"
LOCATIONS = DATA / "locations.json"

NOW = datetime.now(timezone.utc)

# Accounts that comment, open PRs and push commits without being people.
# CHAOSS flags these as the main distortion in time-to-first-response.
BOT_PATTERNS = re.compile(
    r"(\[bot\]$|^dependabot|^renovate|^github-actions|^greenkeeper|^snyk-bot"
    r"|^codecov|^mergify|^allcontributors|^stale|^imgbot|^pre-commit-ci"
    r"|^sonarcloud|^netlify|^vercel|^changeset-bot|^semantic-release"
    r"|^whitesource|^deepsource|^restyled|^pyup|^scala-steward"
    r"|^release-please|^copybara|^gitpod-io|^sourcery-ai|-bot$|^bot-)",
    re.IGNORECASE,
)

INSIDER = {"OWNER", "MEMBER", "COLLABORATOR"}
OUTSIDER = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"}


# Populated by detect_behavioural_bots() before any metric is computed.
BEHAVIOURAL_BOTS: set[str] = set()


def is_bot(login: str | None) -> bool:
    if not login:
        return False
    return bool(BOT_PATTERNS.search(login)) or login in BEHAVIOURAL_BOTS


def detect_behavioural_bots(path: Path, min_replies: int = 3,
                            max_median_seconds: float = 30.0) -> set[str]:
    """Catch bots the name patterns miss, by how impossibly fast they reply.

    Pattern matching alone let `kubernetes-prow` through - it has no [bot]
    suffix and no known prefix, yet it answered 42 of 50 sampled issues in
    about seven seconds each, which pushed kubernetes/kubernetes to the top of
    the index on a fake response time.

    Two tiers, because one threshold cannot separate a bot from a fast
    maintainer:

    * **under 30 seconds, 5+ replies** - nobody reads an issue and composes a
      reply that fast. A looser 120s cut also swept up `wangela` and `Shirman`,
      who reply in 60-85s and are plausibly just attentive humans, so the
      threshold sits below human reaction time rather than near it.
    * **20+ replies with a median under two minutes** - sustained near-instant
      response at volume. `Newrelic-Boomi` is first responder on all 50 sampled
      issues at a 42s median, which no person sustains. Note this catches
      automation posting under a human-looking account, which is the right
      outcome: CHAOSS excludes responses that are not human-driven, regardless
      of whose name is on them.
    * **"bot" anywhere in the name, under 10 minutes, 3+ replies** - weak name
      evidence plus weak speed evidence. This catches `dosubot`, which the
      strict `-bot$` pattern misses, without flagging a human surnamed Talbot
      who answers in days.
    """
    latencies: dict[str, list[float]] = {}
    with path.open() as f:
        for line in f:
            try:
                repo = json.loads(line)
            except json.JSONDecodeError:
                continue
            for iss in (repo.get("issuesDetail") or {}).get("nodes") or []:
                if not iss:
                    continue
                author = (iss.get("author") or {}).get("login")
                opened = ts(iss.get("createdAt"))
                if not opened:
                    continue
                for c in (iss.get("comments") or {}).get("nodes") or []:
                    login = (c.get("author") or {}).get("login")
                    if not login or login == author:
                        continue
                    at = ts(c.get("createdAt"))
                    if at:
                        latencies.setdefault(login, []).append(
                            (at - opened).total_seconds()
                        )
                    break

    found = set()
    for login, secs in latencies.items():
        n, median = len(secs), float(np.median(secs))
        if n >= min_replies and median < max_median_seconds:
            found.add(login)                       # impossible for a human
        elif n >= 20 and median < 120:
            found.add(login)                       # automated at volume
        elif "bot" in login.lower() and n >= 3 and median < 600:
            found.add(login)                       # weak name + weak speed
    return found


def ts(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hours_between(start, end) -> float | None:
    if not start or not end:
        return None
    return (end - start).total_seconds() / 3600.0


def gini(values: list[int]) -> float | None:
    """Inequality of contribution. 0 = everyone commits equally, 1 = one person does everything."""
    if not values or sum(values) == 0:
        return None
    arr = np.sort(np.asarray(values, dtype=float))
    n = arr.size
    if n < 2:
        return 0.0
    index = np.arange(1, n + 1)
    return float((np.sum((2 * index - n - 1) * arr)) / (n * np.sum(arr)))


def absence_factor(counts: list[int]) -> int | None:
    """CHAOSS Contributor Absence Factor: smallest group making 50% of contributions."""
    if not counts:
        return None
    ordered = sorted(counts, reverse=True)
    half, running = sum(ordered) / 2, 0
    for i, c in enumerate(ordered, 1):
        running += c
        if running >= half:
            return i
    return len(ordered)


# ---------------------------------------------------------------- per repo

def pr_rows(repo: dict) -> list[dict]:
    out = []
    for pr in (repo.get("prs") or {}).get("nodes") or []:
        if not pr:
            continue
        login = (pr.get("author") or {}).get("login")
        if is_bot(login):
            continue
        created, merged, closed = ts(pr["createdAt"]), ts(pr.get("mergedAt")), ts(pr.get("closedAt"))
        reviews = (pr.get("reviews") or {}).get("nodes") or []
        comments = (pr.get("comments") or {}).get("nodes") or []

        # First sign of life from anyone other than the PR author.
        responses = []
        for r in reviews:
            if not is_bot((r.get("author") or {}).get("login")):
                responses.append(ts(r["createdAt"]))
        for c in comments:
            cl = (c.get("author") or {}).get("login")
            if cl and cl != login and not is_bot(cl):
                responses.append(ts(c["createdAt"]))
        responses = [r for r in responses if r]

        assoc = pr.get("authorAssociation")
        out.append(
            {
                "repo": repo["nameWithOwner"],
                "number": pr["number"],
                "state": pr["state"],
                "author": login,
                "author_association": assoc,
                "is_outsider": assoc in OUTSIDER,
                "created_at": created,
                "merged_at": merged,
                "closed_at": closed,
                "is_merged": pr["state"] == "MERGED",
                "is_resolved": pr["state"] in ("MERGED", "CLOSED"),
                "hours_to_first_review": hours_between(created, min(responses)) if responses else None,
                "hours_to_merge": hours_between(created, merged),
                "additions": pr.get("additions"),
                "deletions": pr.get("deletions"),
                "changed_files": pr.get("changedFiles"),
            }
        )
    return out


def issue_rows(repo: dict) -> list[dict]:
    out = []
    for iss in (repo.get("issuesDetail") or {}).get("nodes") or []:
        if not iss:
            continue
        login = (iss.get("author") or {}).get("login")
        if is_bot(login):
            continue
        created, closed = ts(iss["createdAt"]), ts(iss.get("closedAt"))

        # Time to first response counts only humans who are not the author.
        first_response = None
        for c in (iss.get("comments") or {}).get("nodes") or []:
            cl = (c.get("author") or {}).get("login")
            if cl and cl != login and not is_bot(cl):
                first_response = ts(c["createdAt"])
                break

        labels = [l["name"] for l in ((iss.get("labels") or {}).get("nodes") or []) if l]
        assoc = iss.get("authorAssociation")
        out.append(
            {
                "repo": repo["nameWithOwner"],
                "number": iss["number"],
                "state": iss["state"],
                "author": login,
                "author_association": assoc,
                "is_outsider": assoc in OUTSIDER,
                "created_at": created,
                "closed_at": closed,
                "hours_to_first_response": hours_between(created, first_response),
                "got_response": first_response is not None,
                "n_assignees": (iss.get("assignees") or {}).get("totalCount", 0),
                "labels": "|".join(labels),
                "is_good_first_issue": any(
                    "good first issue" in l.lower() or "good-first-issue" in l.lower()
                    for l in labels
                ),
            }
        )
    return out


def repo_row(repo: dict, prs: list[dict], issues: list[dict]) -> dict:
    name = repo["nameWithOwner"]
    created, pushed = ts(repo["createdAt"]), ts(repo["pushedAt"])

    # --- commit history: velocity, concentration, bus factor
    history = ((repo.get("defaultBranchRef") or {}).get("target") or {}).get("history") or {}
    commits = [c for c in (history.get("nodes") or []) if c]
    commit_dates, counts = [], {}
    for c in commits:
        d = ts(c.get("committedDate"))
        if d:
            commit_dates.append(d)
        user = (c.get("author") or {}).get("user") or {}
        login = user.get("login") or (c.get("author") or {}).get("name")
        if login and not is_bot(login):
            counts[login] = counts.get(login, 0) + 1

    span_days = None
    if len(commit_dates) > 1:
        span_days = (max(commit_dates) - min(commit_dates)).days or 1

    # --- good first issues: available vs merely present
    gfi = [g for g in ((repo.get("gfiDetail") or {}).get("nodes") or []) if g]
    unassigned = [g for g in gfi if (g.get("assignees") or {}).get("totalCount", 0) == 0]
    ghosts = [
        g for g in unassigned
        if (upd := ts(g.get("updatedAt"))) and (NOW - upd).days > 90
    ]

    pr_df = pd.DataFrame(prs)
    iss_df = pd.DataFrame(issues)

    def merge_rate(mask) -> float | None:
        sub = pr_df[mask & pr_df["is_resolved"]] if len(pr_df) else pr_df
        return float(sub["is_merged"].mean()) if len(sub) else None

    out_rate = merge_rate(pr_df["is_outsider"]) if len(pr_df) else None
    in_rate = merge_rate(~pr_df["is_outsider"]) if len(pr_df) else None

    row = {
        "repo": name,
        "description": repo.get("description"),
        "language": (repo.get("primaryLanguage") or {}).get("name"),
        "stars": repo["stargazerCount"],
        "forks": repo["forkCount"],
        "size_kb": repo.get("diskUsage"),
        "age_years": round((NOW - created).days / 365.25, 2) if created else None,
        "days_since_push": (NOW - pushed).days if pushed else None,
        "license": (repo.get("licenseInfo") or {}).get("spdxId"),
        "topics": "|".join(
            t["topic"]["name"] for t in ((repo.get("repositoryTopics") or {}).get("nodes") or []) if t
        ),
        "star_bucket": repo.get("_bucket"),

        # documentation and process signals
        "has_contributing": bool(
            repo.get("contributing") or repo.get("contributingAlt")
            or repo.get("contributingRst") or repo.get("contributingDocs")
        ),
        "has_code_of_conduct": bool(repo.get("codeOfConduct") or repo.get("codeOfConductAlt")),
        "has_issue_template": bool(repo.get("issueTemplates")),
        "has_pr_template": bool(repo.get("prTemplate")),
        "has_ci": bool(repo.get("workflows")),
        "readme_bytes": (repo.get("readme") or {}).get("byteSize", 0),

        # volume
        "open_issues": repo["openIssues"]["totalCount"],
        "closed_issues": repo["closedIssues"]["totalCount"],
        "open_prs": repo["openPRs"]["totalCount"],
        "merged_prs": repo["mergedPRs"]["totalCount"],
        "closed_prs": repo["closedPRs"]["totalCount"],
        "releases": repo["releases"]["totalCount"],
        "days_since_release": (
            (NOW - ts(repo["latestRelease"]["publishedAt"])).days
            if repo.get("latestRelease") else None
        ),

        # 1. real opportunity
        "gfi_open": repo["gfiOpen"]["totalCount"],
        "help_wanted_open": repo["helpWantedOpen"]["totalCount"],
        "gfi_unassigned": len(unassigned),
        "gfi_ghosts": len(ghosts),
        "ghost_ratio": round(len(ghosts) / len(gfi), 3) if gfi else None,

        # 2. responsiveness
        "median_issue_response_hrs": (
            float(iss_df["hours_to_first_response"].median()) if len(iss_df) else None
        ),
        "issue_response_rate": float(iss_df["got_response"].mean()) if len(iss_df) else None,
        "median_pr_review_hrs": (
            float(pr_df["hours_to_first_review"].median()) if len(pr_df) else None
        ),
        "median_pr_merge_hrs": (
            float(pr_df["hours_to_merge"].median()) if len(pr_df) else None
        ),

        # 3. openness to outsiders
        "outsider_merge_rate": out_rate,
        "insider_merge_rate": in_rate,
        "merge_gap": (in_rate - out_rate) if (out_rate is not None and in_rate is not None) else None,
        "pct_prs_from_outsiders": float(pr_df["is_outsider"].mean()) if len(pr_df) else None,
        "n_prs_sampled": len(pr_df),
        "n_issues_sampled": len(iss_df),

        # 4. activity
        "total_commits": history.get("totalCount"),
        "commits_per_week": (
            round(len(commit_dates) / (span_days / 7), 2) if span_days else None
        ),

        # 5. concentration
        "unique_committers": len(counts),
        "gini_commits": gini(list(counts.values())),
        "absence_factor": absence_factor(list(counts.values())),
        "top_committer_share": (
            round(max(counts.values()) / sum(counts.values()), 3) if counts else None
        ),
    }
    return row


# ------------------------------------------------------------- geography

COUNTRY_HINTS = {
    "usa": "United States", "u.s.a": "United States", "u.s.": "United States",
    "united states": "United States", "america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom", "britain": "United Kingdom",
    "deutschland": "Germany", "brasil": "Brazil", "españa": "Spain",
    "nederland": "Netherlands", "holland": "Netherlands", "suisse": "Switzerland",
    "österreich": "Austria", "polska": "Poland", "sverige": "Sweden",
    "россия": "Russia", "中国": "China", "日本": "Japan", "한국": "South Korea",
    "prc": "China", "korea": "South Korea", "uae": "United Arab Emirates",
}

CITY_TO_COUNTRY = {
    "san francisco": "United States", "sf": "United States", "bay area": "United States",
    "new york": "United States", "nyc": "United States", "brooklyn": "United States",
    "seattle": "United States", "boston": "United States", "austin": "United States",
    "chicago": "United States", "los angeles": "United States", "la": "United States",
    "portland": "United States", "denver": "United States", "atlanta": "United States",
    "mountain view": "United States", "palo alto": "United States", "redmond": "United States",
    "london": "United Kingdom", "cambridge": "United Kingdom", "manchester": "United Kingdom",
    "berlin": "Germany", "munich": "Germany", "münchen": "Germany", "hamburg": "Germany",
    "paris": "France", "lyon": "France", "amsterdam": "Netherlands",
    "madrid": "Spain", "barcelona": "Spain", "lisbon": "Portugal", "lisboa": "Portugal",
    "stockholm": "Sweden", "oslo": "Norway", "copenhagen": "Denmark", "helsinki": "Finland",
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "vienna": "Austria", "wien": "Austria", "prague": "Czechia", "praha": "Czechia",
    "warsaw": "Poland", "kraków": "Poland", "krakow": "Poland", "budapest": "Hungary",
    "moscow": "Russia", "saint petersburg": "Russia", "st petersburg": "Russia",
    "kyiv": "Ukraine", "kiev": "Ukraine", "minsk": "Belarus",
    "bangalore": "India", "bengaluru": "India", "mumbai": "India", "delhi": "India",
    "new delhi": "India", "hyderabad": "India", "chennai": "India", "pune": "India",
    "kolkata": "India", "kerala": "India", "noida": "India", "gurgaon": "India",
    "beijing": "China", "shanghai": "China", "shenzhen": "China", "hangzhou": "China",
    "guangzhou": "China", "chengdu": "China", "hong kong": "Hong Kong",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan",
    "seoul": "South Korea", "singapore": "Singapore", "taipei": "Taiwan",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia",
    "auckland": "New Zealand", "wellington": "New Zealand",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada", "ottawa": "Canada",
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "buenos aires": "Argentina", "santiago": "Chile", "bogotá": "Colombia",
    "mexico city": "Mexico", "cdmx": "Mexico", "lima": "Peru",
    "tel aviv": "Israel", "jerusalem": "Israel", "istanbul": "Turkey", "ankara": "Turkey",
    "dubai": "United Arab Emirates", "cairo": "Egypt", "lagos": "Nigeria",
    "nairobi": "Kenya", "cape town": "South Africa", "johannesburg": "South Africa",
    "dublin": "Ireland", "edinburgh": "United Kingdom", "brussels": "Belgium",
    "milan": "Italy", "rome": "Italy", "roma": "Italy", "athens": "Greece",
    "bucharest": "Romania", "sofia": "Bulgaria", "belgrade": "Serbia",
    "jakarta": "Indonesia", "manila": "Philippines", "bangkok": "Thailand",
    "kuala lumpur": "Malaysia", "hanoi": "Vietnam", "ho chi minh": "Vietnam",
    "karachi": "Pakistan", "lahore": "Pakistan", "dhaka": "Bangladesh",
    "us": "United States",
}

# Country names that appear verbatim and need no lookup table.
PLAIN_COUNTRIES = {
    "germany", "france", "spain", "italy", "portugal", "netherlands", "belgium",
    "switzerland", "austria", "sweden", "norway", "denmark", "finland", "iceland",
    "poland", "czechia", "czech republic", "hungary", "romania", "bulgaria",
    "greece", "serbia", "croatia", "slovenia", "slovakia", "estonia", "latvia",
    "lithuania", "ireland", "russia", "ukraine", "belarus", "turkey", "israel",
    "india", "china", "japan", "singapore", "taiwan", "thailand", "vietnam",
    "indonesia", "malaysia", "philippines", "pakistan", "bangladesh", "nepal",
    "sri lanka", "australia", "new zealand", "canada", "mexico", "brazil",
    "argentina", "chile", "colombia", "peru", "uruguay", "venezuela", "ecuador",
    "egypt", "nigeria", "kenya", "ghana", "south africa", "morocco", "tunisia",
    "iran", "iraq", "saudi arabia", "qatar", "jordan", "lebanon", "armenia",
    "georgia", "kazakhstan", "uzbekistan", "south korea", "north macedonia",
    "bosnia and herzegovina", "albania", "moldova", "luxembourg", "malta",
    "cyprus", "hong kong", "united states", "united kingdom",
}

US_STATES = {
    "california", "texas", "florida", "washington", "oregon", "nevada", "arizona",
    "colorado", "utah", "illinois", "michigan", "ohio", "pennsylvania", "virginia",
    "maryland", "massachusetts", "new jersey", "georgia state", "north carolina",
    "south carolina", "tennessee", "indiana", "wisconsin", "minnesota", "missouri",
    "louisiana", "alabama", "kentucky", "oklahoma", "kansas", "iowa", "nebraska",
    "connecticut", "new hampshire", "vermont", "maine", "rhode island", "delaware",
    "montana", "idaho", "wyoming", "alaska", "hawaii", "new mexico", "arkansas",
    "mississippi", "west virginia", "north dakota", "south dakota",
    "ca", "tx", "fl", "wa", "ny", "ma", "il", "pa", "co", "or", "nc", "va", "mi",
}


def to_country(raw: str | None) -> str | None:
    """Normalise free-text profile locations like 'Earth, Milky Way' into countries."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.lower().strip()
    text = re.sub(r"[^\w\s,./|&-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    if not text or text in {"earth", "remote", "worldwide", "internet", "/dev/null",
                            "the moon", "mars", "everywhere", "nowhere", "localhost"}:
        return None

    # Longest-match first so "new york" beats "york", "south korea" beats "korea".
    for table in (COUNTRY_HINTS, CITY_TO_COUNTRY):
        for key in sorted(table, key=len, reverse=True):
            if re.search(rf"(^|[\s,./|-]){re.escape(key)}($|[\s,./|-])", text):
                return table[key]
    for name in sorted(PLAIN_COUNTRIES, key=len, reverse=True):
        if re.search(rf"(^|[\s,./|-]){re.escape(name)}($|[\s,./|-])", text):
            return name.title().replace("Usa", "United States")
    for state in sorted(US_STATES, key=len, reverse=True):
        if re.search(rf"(^|[\s,./|-]){re.escape(state)}($|[\s,./|-])", text):
            return "United States"
    return None


# ------------------------------------------------------------------ index

DIMENSIONS = {
    # column, whether lower is better
    "opportunity": [("gfi_unassigned", False), ("help_wanted_open", False), ("ghost_ratio", True)],
    "responsiveness": [
        ("median_issue_response_hrs", True),
        ("median_pr_review_hrs", True),
        ("issue_response_rate", False),
    ],
    "openness": [("outsider_merge_rate", False), ("merge_gap", True), ("pct_prs_from_outsiders", False)],
    "activity": [("commits_per_week", False), ("days_since_push", True), ("releases", False)],
    "sustainability": [("absence_factor", False), ("gini_commits", True), ("unique_committers", False)],
}

# Weights follow the newcomer-barrier literature, where "receiving a response
# from the community" is the most evidenced barrier to a first contribution.
WEIGHTS = {
    "responsiveness": 0.30,
    "openness": 0.30,
    "opportunity": 0.20,
    "activity": 0.10,
    "sustainability": 0.10,
}

MIN_STRATUM = 8


def stratified_percentile(df: pd.DataFrame, col: str, lower_is_better: bool) -> pd.Series:
    """Rank each repo against its own peer group, not the whole sample.

    CHAOSS warns that health metrics are not comparable across projects. A
    three-day response time is excellent for a huge monorepo and mediocre for a
    small library, so repos are ranked within language x size bucket and only
    fall back to a global ranking when a stratum is too small to be stable.
    """
    scores = pd.Series(np.nan, index=df.index, dtype=float)
    for _, idx in df.groupby(["language", "star_bucket"], dropna=False).groups.items():
        group = df.loc[idx, col]
        target = group if group.notna().sum() >= MIN_STRATUM else df[col]
        ranked = group.rank(pct=True) if group.notna().sum() >= MIN_STRATUM else (
            group.apply(lambda v: (target < v).mean() if pd.notna(v) else np.nan)
        )
        scores.loc[idx] = ranked
    if lower_is_better:
        scores = 1 - scores
    return scores * 100


def add_index(df: pd.DataFrame) -> pd.DataFrame:
    for dim, cols in DIMENSIONS.items():
        parts = []
        for col, lower_better in cols:
            if col in df.columns:
                parts.append(stratified_percentile(df, col, lower_better))
        df[f"score_{dim}"] = pd.concat(parts, axis=1).mean(axis=1) if parts else np.nan

    weighted = sum(df[f"score_{d}"] * w for d, w in WEIGHTS.items())
    df["friendliness_index"] = weighted.round(1)

    # Quadrant labels for the headline scatter.
    act, resp = df["score_activity"].median(), df["score_responsiveness"].median()
    df["quadrant"] = np.select(
        [
            (df["score_activity"] >= act) & (df["score_responsiveness"] >= resp),
            (df["score_activity"] >= act) & (df["score_responsiveness"] < resp),
            (df["score_activity"] < act) & (df["score_responsiveness"] >= resp),
        ],
        ["Welcoming & Active", "Busy but Unresponsive", "Hidden Gem"],
        default="Dormant",
    )
    return df


# -------------------------------------------------------------------- main

def main() -> None:
    if not RAW.exists():
        raise SystemExit(f"No raw data at {RAW}. Run collect.py first.")

    BEHAVIOURAL_BOTS.update(detect_behavioural_bots(RAW))
    print(f"bot accounts caught behaviourally: {len(BEHAVIOURAL_BOTS)}")
    if BEHAVIOURAL_BOTS:
        print("  e.g.", ", ".join(sorted(BEHAVIOURAL_BOTS)[:8]))

    repos, all_prs, all_issues = [], [], []
    with RAW.open() as f:
        for line in f:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            prs, issues = pr_rows(raw), issue_rows(raw)
            all_prs.extend(prs)
            all_issues.extend(issues)
            repos.append(repo_row(raw, prs, issues))

    repo_df = pd.DataFrame(repos).drop_duplicates(subset="repo")
    repo_df = add_index(repo_df)

    pr_df = pd.DataFrame(all_prs)
    issue_df = pd.DataFrame(all_issues)

    # Contributor geography
    # Deliberately no login column: the analysis only ever needs the country,
    # and a committed file mapping named people to their locations is exactly
    # what the ethics note says this project does not publish. locations.json
    # keeps the logins locally and is gitignored.
    contributors = []
    if LOCATIONS.exists():
        for _login, loc in json.loads(LOCATIONS.read_text()).items():
            contributors.append({"location_raw": loc, "country": to_country(loc)})
    contrib_df = pd.DataFrame(contributors)

    DATA.mkdir(exist_ok=True)
    repo_df.to_csv(DATA / "repos.csv", index=False)
    pr_df.to_csv(DATA / "prs.csv", index=False)
    issue_df.to_csv(DATA / "issues.csv", index=False)
    contrib_df.to_csv(DATA / "contributors.csv", index=False)

    print(f"repos.csv         {len(repo_df):>7} rows x {repo_df.shape[1]} cols")
    print(f"prs.csv           {len(pr_df):>7} rows")
    print(f"issues.csv        {len(issue_df):>7} rows")
    if len(contrib_df):
        resolved = contrib_df["country"].notna().sum()
        print(
            f"contributors.csv  {len(contrib_df):>7} rows "
            f"({resolved} resolved to a country, {resolved / len(contrib_df):.0%})"
        )
    print(f"\ntotal rows: {len(repo_df) + len(pr_df) + len(issue_df) + len(contrib_df):,}")


if __name__ == "__main__":
    main()
