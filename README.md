# Contributor Compass

**Which open-source repos actually welcome new contributors?**

A data analysis and visualization project. Existing tools list beginner-friendly *issues*.
None of them tell you whether anyone will actually review your pull request.

---

## The question

> Is a repository's popularity a good proxy for how welcoming it is to new contributors?
> And can we build a better measure?

Expected finding: **stars are a poor predictor of contributor-friendliness.** A repo can have
50,000 stars, a tidy `good first issue` backlog, and still not have merged an outside pull
request in six months.

## The gap this fills

| Tool | What it does | What it misses |
|---|---|---|
| goodfirstissue.dev, up-for-grabs, CodeTriage | Aggregate individual issues | No signal on maintainer responsiveness |
| OSSInsight | Ecosystem trends over 10B+ events | Built for analysts, not newcomers |
| OpenSauced | Scores *contributors* (OSCR) | Opposite direction — doesn't score repos |
| CHAOSS | Rigorous health metrics | Designed for maintainers self-assessing |

Nobody scores repos **from the newcomer's point of view**.

---

## Metrics

Five dimensions, combined into a **Contributor-Friendliness Index**.

### 1. Real opportunity — are there tasks actually available?
- `good first issue` / `help wanted` counts
- **Unassigned** share of those (`no:assignee` — an assigned issue is taken)
- **Ghost ratio** — share of open good-first-issues that are unassigned *and* untouched for 90+ days
- Issue template / CONTRIBUTING.md / CODE_OF_CONDUCT present

### 2. Responsiveness — will anyone reply?
- Median **time to first human response** on issues (CHAOSS benchmark: 2 business days)
- Median time to first review on PRs
- Median PR merge time
- Open-PR backlog depth

### 3. Openness to outsiders — the headline metric
GitHub stamps every PR with `author_association`:
- **insiders** = `OWNER`, `MEMBER`, `COLLABORATOR`
- **outsiders** = `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, `FIRST_TIMER`, `NONE`

From this:
- Outsider merge rate = merged outsider PRs / closed outsider PRs
- **Insider-outsider merge gap** = insider rate − outsider rate

> A repo merging 92% of insider PRs and 18% of outsider PRs is hostile — and it looks
> perfectly healthy from the outside. No existing tool surfaces this.

### 4. Activity and health
- Commits per week (52-week series), days since last commit
- Release cadence
- Change request closure ratio (CHAOSS)
- Open vs closed issue trend

### 5. Barrier and concentration
- **Contributor Absence Factor** (CHAOSS) — smallest group making 50% of commits
- Gini coefficient of commits per contributor + Lorenz curve
- Repo size, language mix, issues per active contributor

---

## Method notes

**Stratified comparison.** CHAOSS explicitly warns that health metrics should not be compared
across projects. So repos are ranked by **percentile within strata** — same language, size
bucket, and age — never on raw values. A 3-day response time is excellent for a 40k-star
monorepo and mediocre for a 200-star library.

**Bot filtering.** Dependabot, Renovate, `github-actions[bot]` and Codecov inflate activity and
fake "first response". CHAOSS flags this directly. Bot removal is a documented cleaning step,
not an afterthought.

**Weight sensitivity.** The index weights are tunable in the dashboard, which doubles as a
sensitivity analysis on the composite score.

---

## Pipeline

```
GitHub GraphQL API  ->  raw JSON  ->  clean  ->  repos.csv / prs.csv / issues.csv  ->  dashboard
     (one dated pull)                                  (committed, reproducible)
```

No backend, no user accounts, no served model. Collect once, analyse, visualise.

**Scale:** ~500 repos across 8 languages, with their recent PRs and issues — roughly 100k rows.

---

## Visuals

- **Quadrant scatter** — Activity vs Responsiveness, bubble = open good-first-issues, colour =
  language. Quadrants labelled *Welcoming & Active · Busy but Unresponsive · Dormant · Hidden Gems*
- Index leaderboard with live weight sliders
- Radar chart comparing shortlisted repos across the five dimensions
- Correlation heatmap (where stars decouple from responsiveness)
- Box plots of response time by language and size bucket
- Lorenz curve for contribution concentration
- Commit calendar heatmap, issue-to-merge funnel

---

## Ethics

GitHub contributor data is personal data. This project:
- uses only public API data, aggregates to repo level, and publishes no contributor rankings
- ranks *repositories*, never individual maintainers — a low responsiveness score reflects an
  under-resourced project, not a negligent volunteer
- documents selection bias (public repos only) and survivorship bias (archived repos excluded)

---

## Status

Early. Design settled, collection not yet run.
