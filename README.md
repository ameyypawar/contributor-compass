# Contributor Compass

**Which open-source repos actually welcome new contributors?**

Existing tools list beginner-friendly *issues*. None of them tell you whether anyone
will actually review your pull request. This ranks **repositories** from the
newcomer's point of view.

Data Analysis & Visualization capstone — 519 repositories, 42,000 rows, one dated
GitHub snapshot.

```bash
pip install -r requirements.txt
python collect.py      # GitHub GraphQL snapshot  -> data/raw.jsonl
python build.py        # metric layer             -> data/*.csv
streamlit run app.py   # dashboard
```

---

## The question

> Is a repository's popularity a good proxy for how welcoming it is to new
> contributors? And can we build a better measure?

**Answer: no, and yes.**

| Finding | Evidence |
|---|---|
| Stars predict **nothing** about responsiveness | Spearman ρ = **+0.011**, p = 0.81, n = 511 |
| Stars predict outsider merge rate **negatively** | ρ = **−0.233**, p = 3.3 × 10⁻⁷ |
| Outsiders face a large merge gap | **64.3%** merged vs **93.4%** for insiders — a **29-point gap** |
| Most issues are never answered by a human | only **57%** ever get a human reply |
| Entry points are scarce | only **15%** of repos have an unassigned `good first issue` |
| Bus factor is dire | median Contributor Absence Factor = **1** |
| Responsiveness is mostly acceptable | **60%** meet the CHAOSS 48-hour target |

The second row is the striking one: **the more popular a project, the *less* likely
your pull request is to be merged.** Popularity is not just uninformative — it points
the wrong way.

## The gap this fills

| Tool | What it does | What it misses |
|---|---|---|
| goodfirstissue.dev, up-for-grabs, CodeTriage | Aggregate individual issues | No signal on whether anyone will respond |
| OSSInsight | Ecosystem trends over 10B+ events | Built for analysts, not newcomers |
| OpenSauced | Scores *contributors* (OSCR) | Opposite direction — doesn't score repos |
| CHAOSS | Rigorous health metrics | Designed for maintainers self-assessing |

Nobody scores repos **from the newcomer's point of view**.

---

## The index

Five dimensions, weighted. Weights are tunable in the dashboard, which doubles as a
sensitivity analysis.

| Dimension | Weight | Key metrics |
|---|---|---|
| **Responsiveness** | 30% | time to first *human* response, PR review time, response rate |
| **Openness to outsiders** | 30% | outsider merge rate, insider–outsider merge gap |
| **Real opportunity** | 20% | unassigned good-first-issues, ghost ratio |
| **Activity** | 10% | commits/week, release cadence, days since push |
| **Sustainability** | 10% | Contributor Absence Factor, Gini of commits |

Weighting follows the newcomer-barrier literature, where *"receiving a response from
the community"* is the most-evidenced barrier to a first contribution.

### Two metrics worth calling out

**The insider–outsider merge gap.** GitHub stamps every PR with `author_association`.
Splitting on it gives you the one number no existing tool shows:

> A repo merging 92% of insider PRs and 18% of outsider PRs is hostile — and it looks
> perfectly healthy from the outside.

**Ghost ratio.** Share of open good-first-issues that are unassigned *and* untouched for
90+ days. An unassigned issue that nobody has looked at in two years is a ghost, not an
opportunity.

---

## Method notes

**Stratified comparison.** CHAOSS states explicitly that health metrics should not be
compared across projects. So repos are ranked by **percentile within language × size
bucket**, never on raw values — a 3-day response time is excellent for a 40k-star
monorepo and mediocre for a 200-star library. A Kruskal–Wallis test across languages
confirms the difference is real, which is what justifies the stratification.

**Bot filtering, in two stages.** This mattered more than expected.

1. *Name patterns* — `dependabot`, `renovate`, `[bot]` suffixes, and so on.
2. *Behavioural detection* — because patterns alone let `kubernetes-prow` through. It
   has no `[bot]` suffix and answered 42 of 50 sampled issues in **about seven
   seconds** each, which pushed `kubernetes/kubernetes` to **rank 1** on a fake
   response time. With behavioural detection it corrects to rank 51 and a 2.6-hour
   median.

   The rules: under 30s over 3+ replies (below human reaction time); 20+ replies under
   a 2-minute median (automation at volume, even under a human-looking account); or
   "bot" in the name with weak speed evidence. A looser 120s cut swept up accounts
   replying in 60–85s, who are plausibly just attentive maintainers — so the threshold
   sits *below* human reaction time rather than near it.

   22 automated accounts were caught this way that the name patterns missed.

**Outliers are kept.** Response times are heavily right-skewed, and that skew is real
signal — a repo taking 4,000 hours to reply is genuinely unresponsive. So the analysis
uses medians and rank-based tests (Mann–Whitney, Kruskal–Wallis, Spearman) rather than
means and t-tests.

---

## Repository layout

| Path | What it is |
|---|---|
| `collect.py` | GitHub GraphQL collection — stratified sample, resumable, rate-limit aware |
| `build.py` | Metric layer — bot filtering, derived metrics, stratified percentile scoring |
| `app.py` | Streamlit dashboard |
| `notebooks/analysis.ipynb` | Full analysis — cleaning, EDA, hypothesis tests, ARIMA, geospatial, ROC/SHAP |
| `data/*.csv` | Cleaned tables (committed; `raw.jsonl` is regenerable and gitignored) |

### Data

| Table | Rows |
|---|---|
| `repos.csv` | 519 × 52 columns |
| `prs.csv` | 18,384 |
| `issues.csv` | 21,391 |
| `contributors.csv` | 1,898 |

Sample: 8 languages × 5 star buckets, non-archived, pushed since 2026-01-01.

---

## Limitations

- **Snapshot, not history.** One dated pull; every metric moves over time.
- **Recent-window bias.** Commit metrics use the most recent 100 commits, so
  `absence_factor` and `unique_committers` understate long-lived projects.
- **Sampling artifact.** Repos were drawn as the top-starred within each bucket, so
  the star distribution clusters near bucket boundaries. Visible as vertical banding
  in star scatterplots. It does not affect within-stratum percentile ranking.
- **Location coverage.** Only 30% of sampled contributors resolve to a country;
  profile location is optional free text. Unresolvable entries are dropped, never
  guessed.
- **Observational.** Every relationship here is association, not causation.
- **Bot filter is imperfect.** A slow, human-named bot still survives it.

## Ethics

Repository-level aggregates only. No contributor is ranked, scored, or named in any
output. A low responsiveness score describes an **under-resourced project**, not a
negligent maintainer — most open source is maintained by volunteers under no
obligation to answer anyone. Contributor locations are aggregated to country and never
rejoined to individuals.

## References

- [CHAOSS Starter Project Health](https://chaoss.community/kb/metrics-model-starter-project-health/) — metric definitions and the 48-hour benchmark
- [CHAOSS: Time to First Response](https://www.chaoss.community/kb/metric-time-to-first-response/)
- Steinmacher et al., [*Barriers Faced by Newcomers to Open Source Projects: A Systematic Review*](https://link.springer.com/chapter/10.1007/978-3-642-55128-4_21)
- Steinmacher et al., [*Social Barriers Faced by Newcomers Placing Their First Contribution*](https://dl.acm.org/doi/10.1145/2675133.2675215)
