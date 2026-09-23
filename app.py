"""
Contributor Compass - an interactive dashboard over the Contributor-Friendliness Index.

Run with:  streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

DATA = Path(__file__).parent / "data"

# ---------------------------------------------------------------- palette
# Validated with the data-viz palette checker: passes the lightness band,
# chroma floor, CVD separation and normal-vision floor on a #fcfcfb surface.
# Two slots sit below 3:1 contrast, so every chart that uses them also ships
# direct labels or a table view.
SURFACE = "#fcfcfb"
PLANE = "#f9f9f7"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GOOD, CRITICAL = "#0ca30c", "#d03b3b"

SEQ_BLUE = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
    "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
    "#184f95", "#104281", "#0d366b",
]
# Diverging: two poles that read as opposite, neutral gray midpoint.
DIVERGING = [
    [0.0, "#0d366b"], [0.25, "#3987e5"], [0.5, "#f0efec"],
    [0.75, "#e34948"], [1.0, "#8f1f1f"],
]

def tint(hex_color: str, alpha: float) -> str:
    """A translucent wash of a palette colour, for area fills under a line."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

st.set_page_config(
    page_title="Contributor Compass",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)


def style(fig: go.Figure, height: int = 420, legend: bool = False) -> go.Figure:
    """Recessive chrome, readable ink, no chartjunk."""
    # With a legend the title needs its own band, or the two collide in the
    # top margin.
    top = 82 if legend else 48
    fig.update_layout(
        height=height,
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=13, color=INK_2),
        margin=dict(l=8, r=8, t=top, b=8),
        title=dict(font=dict(size=15, color=INK), x=0, xanchor="left",
                   y=0.97, yanchor="top"),
        showlegend=legend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.015, xanchor="left", x=0,
            font=dict(size=12, color=INK_2), bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(bgcolor=SURFACE, font=dict(family=FONT, size=12, color=INK),
                        bordercolor=AXIS),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS,
                     tickfont=dict(color=MUTED, size=12),
                     title_font=dict(color=INK_2, size=12))
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS,
                     tickfont=dict(color=MUTED, size=12),
                     title_font=dict(color=INK_2, size=12))
    return fig


st.markdown(
    f"""
    <style>
      .stApp {{ background: {PLANE}; }}
      .block-container {{ padding-top: 2.2rem; max-width: 1500px; }}
      h1, h2, h3 {{ font-family: {FONT}; color: {INK}; letter-spacing: -0.015em; }}
      .tile {{
        background: {SURFACE}; border: 1px solid rgba(11,11,11,0.10);
        border-radius: 10px; padding: 14px 16px; height: 100%;
      }}
      .tile .label {{ font-size: 12px; color: {MUTED}; text-transform: uppercase;
                      letter-spacing: 0.06em; margin-bottom: 4px; }}
      .tile .value {{ font-size: 28px; color: {INK}; font-weight: 600; line-height: 1.1; }}
      .tile .note  {{ font-size: 12px; color: {INK_2}; margin-top: 4px; }}
      .lede {{ color: {INK_2}; font-size: 15px; max-width: 70ch; }}
      .finding {{
        background: {SURFACE}; border-left: 3px solid {BLUE};
        border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 6px 0 14px 0;
        color: {INK_2}; font-size: 14px;
      }}
      [data-testid="stMetricValue"] {{ font-size: 26px; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def tile(label: str, value: str, note: str = "") -> str:
    return (
        f'<div class="tile"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'<div class="note">{note}</div></div>'
    )


# ------------------------------------------------------------------- data

@st.cache_data(show_spinner=False)
def load(_stamp: tuple):
    """_stamp is the CSVs' mtimes, so rebuilding the data busts the cache."""
    need = ["repos.csv", "prs.csv", "issues.csv"]
    missing = [n for n in need if not (DATA / n).exists()]
    if missing:
        return None, None, None, None
    repos = pd.read_csv(DATA / "repos.csv")
    prs = pd.read_csv(DATA / "prs.csv", parse_dates=["created_at", "merged_at", "closed_at"])
    issues = pd.read_csv(DATA / "issues.csv", parse_dates=["created_at", "closed_at"])
    contrib = (
        pd.read_csv(DATA / "contributors.csv")
        if (DATA / "contributors.csv").exists() else pd.DataFrame()
    )
    return repos, prs, issues, contrib


stamp = tuple(
    (DATA / n).stat().st_mtime if (DATA / n).exists() else 0
    for n in ("repos.csv", "prs.csv", "issues.csv", "contributors.csv")
)
repos, prs, issues, contrib = load(stamp)

if repos is None:
    st.title("Contributor Compass")
    st.warning("No data yet. Run `python collect.py` then `python build.py`.")
    st.stop()

# ---------------------------------------------------------------- sidebar

st.sidebar.markdown("### Filters")
langs = sorted(repos["language"].dropna().unique())
pick_langs = st.sidebar.multiselect("Language", langs, default=langs)

buckets = [b for b in ["50..200", "200..1000", "1000..5000", "5000..20000", ">20000"]
           if b in set(repos["star_bucket"].dropna())]
pick_buckets = st.sidebar.multiselect("Size (stars)", buckets, default=buckets)

min_gfi = st.sidebar.slider("Minimum unassigned good-first-issues", 0, 20, 0)
active_only = st.sidebar.checkbox("Pushed within 90 days", value=False)

st.sidebar.markdown("---")
st.sidebar.markdown("### Index weights")
st.sidebar.caption(
    "The index is a weighted blend of five dimensions. Retune it and the "
    "ranking reorders - this doubles as a sensitivity analysis."
)
w_resp = st.sidebar.slider("Responsiveness", 0.0, 1.0, 0.30, 0.05)
w_open = st.sidebar.slider("Openness to outsiders", 0.0, 1.0, 0.30, 0.05)
w_opp = st.sidebar.slider("Real opportunity", 0.0, 1.0, 0.20, 0.05)
w_act = st.sidebar.slider("Activity", 0.0, 1.0, 0.10, 0.05)
w_sus = st.sidebar.slider("Sustainability", 0.0, 1.0, 0.10, 0.05)

weights = {"responsiveness": w_resp, "openness": w_open, "opportunity": w_opp,
           "activity": w_act, "sustainability": w_sus}
total_w = sum(weights.values()) or 1.0

df = repos.copy()
df["friendliness_index"] = sum(
    df[f"score_{d}"].fillna(df[f"score_{d}"].median()) * (w / total_w)
    for d, w in weights.items()
).round(1)

mask = pd.Series(True, index=df.index)
if pick_langs:
    mask &= df["language"].isin(pick_langs)
if pick_buckets:
    mask &= df["star_bucket"].isin(pick_buckets)
mask &= df["gfi_unassigned"].fillna(0) >= min_gfi
if active_only:
    mask &= df["days_since_push"].fillna(9999) <= 90
view = df[mask].copy()

# ------------------------------------------------------------------ header

st.markdown("# Contributor Compass")
st.markdown(
    '<p class="lede">Existing tools list beginner-friendly <em>issues</em>. '
    "None of them tell you whether anyone will actually review your pull request. "
    "This ranks repositories from the newcomer's point of view.</p>",
    unsafe_allow_html=True,
)
st.write("")

resolved = prs[prs["is_resolved"]]
out_rate = resolved[resolved["is_outsider"]]["is_merged"].mean()
in_rate = resolved[~resolved["is_outsider"]]["is_merged"].mean()
med_resp = issues["hours_to_first_response"].median()

c = st.columns(5)
c[0].markdown(tile("Repositories", f"{len(view):,}", f"of {len(repos):,} analysed"),
              unsafe_allow_html=True)
c[1].markdown(tile("Pull requests", f"{len(prs):,}", "bots removed"), unsafe_allow_html=True)
c[2].markdown(tile("Issues", f"{len(issues):,}", "with comment timelines"),
              unsafe_allow_html=True)
c[3].markdown(
    tile("Outsider merge rate", f"{out_rate:.0%}",
         f"insiders {in_rate:.0%} · gap {in_rate - out_rate:+.0%}"),
    unsafe_allow_html=True,
)
c[4].markdown(
    tile("Median first response", f"{med_resp:.0f}h" if pd.notna(med_resp) else "—",
         "CHAOSS target: under 48h"),
    unsafe_allow_html=True,
)
st.write("")

tabs = st.tabs(
    ["Find a repo", "Compare", "The finding", "Geography", "What predicts a merge", "Data"]
)

# ------------------------------------------------------------ 1. find a repo

with tabs[0]:
    if view.empty:
        st.info("No repositories match these filters.")
    else:
        left, right = st.columns([3, 2], gap="large")

        with left:
            s = view.dropna(subset=["score_activity", "score_responsiveness"])
            size = s["gfi_unassigned"].fillna(0)
            fig = go.Figure(
                go.Scatter(
                    x=s["score_activity"], y=s["score_responsiveness"],
                    mode="markers",
                    marker=dict(
                        size=np.clip(size, 0, 25) * 0.9 + 8,
                        color=s["friendliness_index"], colorscale=SEQ_BLUE,
                        cmin=0, cmax=100,
                        line=dict(width=2, color=SURFACE),  # surface ring on overlap
                        colorbar=dict(
                            title=dict(text="Index", font=dict(size=12, color=INK_2)),
                            thickness=10, len=0.55, outlinewidth=0,
                            tickfont=dict(size=11, color=MUTED),
                        ),
                    ),
                    customdata=np.stack([
                        s["repo"], s["stars"], s["gfi_unassigned"].fillna(0),
                        s["friendliness_index"],
                    ], axis=-1),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        "Index %{customdata[3]:.0f} · %{customdata[1]:,} stars<br>"
                        "Activity %{x:.0f} · Responsiveness %{y:.0f}<br>"
                        "%{customdata[2]:.0f} unassigned good-first-issues<extra></extra>"
                    ),
                )
            )
            mx, my = s["score_activity"].median(), s["score_responsiveness"].median()
            fig.add_hline(y=my, line=dict(color=AXIS, width=1, dash="dot"))
            fig.add_vline(x=mx, line=dict(color=AXIS, width=1, dash="dot"))
            for xa, ya, label, ax, ay in [
                (100, 100, "Welcoming & active", "right", "top"),
                (0, 100, "Hidden gems", "left", "top"),
                (100, 0, "Busy but unresponsive", "right", "bottom"),
                (0, 0, "Dormant", "left", "bottom"),
            ]:
                fig.add_annotation(
                    x=xa, y=ya, text=label, showarrow=False,
                    xanchor=ax, yanchor=ya if ya == "top" else "bottom",
                    font=dict(size=11, color=MUTED),
                )
            fig.update_layout(
                title="Where a repo sits: activity against responsiveness",
                xaxis_title="Activity percentile (within language and size)",
                yaxis_title="Responsiveness percentile",
            )
            fig.update_xaxes(range=[-4, 104])
            fig.update_yaxes(range=[-4, 104])
            st.plotly_chart(style(fig, 520), use_container_width=True)
            st.caption(
                "Bubble size is the count of unassigned good-first-issues. "
                "Both axes are percentiles within the repo's own language and size "
                "bucket, never raw values."
            )

        with right:
            top = view.nlargest(18, "friendliness_index").sort_values("friendliness_index")
            fig = go.Figure(
                go.Bar(
                    x=top["friendliness_index"], y=top["repo"], orientation="h",
                    marker=dict(color=BLUE, line=dict(width=0)),
                    text=[f"{v:.0f}" for v in top["friendliness_index"]],
                    textposition="outside",
                    textfont=dict(size=12, color=INK_2),
                    hovertemplate="<b>%{y}</b><br>Index %{x:.1f}<extra></extra>",
                )
            )
            fig.update_layout(
                title="Highest scoring, given your weights",
                xaxis_title="Contributor-Friendliness Index",
            )
            fig.update_xaxes(range=[0, 108])
            fig.update_yaxes(tickfont=dict(size=11, color=INK_2))
            st.plotly_chart(style(fig, 520), use_container_width=True)

        st.markdown("##### Shortlist")
        cols = ["repo", "language", "stars", "friendliness_index", "gfi_unassigned",
                "median_issue_response_hrs", "outsider_merge_rate", "merge_gap", "quadrant"]
        table = view.nlargest(40, "friendliness_index")[cols].rename(columns={
            "repo": "Repository", "language": "Language", "stars": "Stars",
            "friendliness_index": "Index", "gfi_unassigned": "Open GFIs",
            "median_issue_response_hrs": "First response (h)",
            "outsider_merge_rate": "Outsider merge rate",
            "merge_gap": "Merge gap", "quadrant": "Quadrant",
        })
        st.dataframe(
            table.style.format({
                "Stars": "{:,.0f}", "Index": "{:.1f}", "Open GFIs": "{:.0f}",
                "First response (h)": "{:.0f}",
                "Outsider merge rate": "{:.0%}", "Merge gap": "{:+.0%}",
            }, na_rep="—"),
            use_container_width=True, hide_index=True, height=420,
        )

# ---------------------------------------------------------------- 2. compare

with tabs[1]:
    st.markdown("##### Compare up to three repositories")
    options = view.sort_values("friendliness_index", ascending=False)["repo"].tolist()
    if not options:
        st.info("No repositories match these filters.")
    else:
        chosen = st.multiselect(
            "Repositories", options, default=options[:3], max_selections=3,
            label_visibility="collapsed",
        )
        if chosen:
            dims = ["opportunity", "responsiveness", "openness", "activity", "sustainability"]
            labels = ["Real opportunity", "Responsiveness", "Openness to outsiders",
                      "Activity", "Sustainability"]
            # Three slots is the validated cap for all-pairs comparisons.
            colors = [BLUE, ORANGE, AQUA]

            fig = go.Figure()
            for i, name in enumerate(chosen):
                row = view[view["repo"] == name].iloc[0]
                vals = [row.get(f"score_{d}", np.nan) for d in dims]
                fig.add_trace(go.Scatterpolar(
                    r=vals + [vals[0]], theta=labels + [labels[0]],
                    name=name, mode="lines+markers",
                    line=dict(color=colors[i], width=2),
                    marker=dict(size=8, color=colors[i],
                                line=dict(width=2, color=SURFACE)),
                    fill="toself", fillcolor=tint(colors[i], 0.10),
                    hovertemplate="<b>%{fullData.name}</b><br>%{theta}: %{r:.0f}<extra></extra>",
                ))
            fig.update_layout(
                title="Profile across the five dimensions",
                polar=dict(
                    bgcolor=SURFACE,
                    radialaxis=dict(visible=True, range=[0, 100], gridcolor=GRID,
                                    tickfont=dict(size=11, color=MUTED), linecolor=GRID),
                    angularaxis=dict(gridcolor=GRID, linecolor=AXIS,
                                     tickfont=dict(size=12, color=INK_2)),
                ),
            )
            st.plotly_chart(style(fig, 500, legend=True), use_container_width=True)

            detail = view[view["repo"].isin(chosen)].set_index("repo")[[
                "language", "stars", "friendliness_index", "median_issue_response_hrs",
                "median_pr_review_hrs", "outsider_merge_rate", "insider_merge_rate",
                "merge_gap", "gfi_unassigned", "ghost_ratio", "absence_factor",
                "gini_commits", "has_contributing", "has_ci",
            ]].T
            st.dataframe(detail, use_container_width=True)

# ------------------------------------------------------------- 3. the finding

with tabs[2]:
    st.markdown("##### Does popularity predict a welcome?")

    valid = df.dropna(subset=["stars", "score_responsiveness"])
    rho, pval = stats.spearmanr(valid["stars"], valid["score_responsiveness"])
    st.markdown(
        f'<div class="finding"><b>Spearman correlation between stars and '
        f"responsiveness: ρ = {rho:.3f}</b> (p = {pval:.3g}, n = {len(valid):,}). "
        + (
            "Effectively no relationship - a repository's popularity tells you "
            "almost nothing about whether it will answer you."
            if abs(rho) < 0.2 else
            "A relationship exists, but it is far weaker than the star count implies."
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    a, b = st.columns(2, gap="large")

    with a:
        s = valid
        fig = go.Figure(go.Scatter(
            x=s["stars"], y=s["score_responsiveness"], mode="markers",
            marker=dict(size=9, color=BLUE, opacity=0.55,
                        line=dict(width=1.5, color=SURFACE)),
            text=s["repo"],
            hovertemplate="<b>%{text}</b><br>%{x:,} stars<br>Responsiveness %{y:.0f}<extra></extra>",
        ))
        fig.update_layout(title="Stars against responsiveness",
                          xaxis_title="Stars (log scale)",
                          yaxis_title="Responsiveness percentile")
        fig.update_xaxes(type="log")
        st.plotly_chart(style(fig), use_container_width=True)

    with b:
        s = df.dropna(subset=["insider_merge_rate", "outsider_merge_rate"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines",
            line=dict(color=AXIS, width=1, dash="dot"), hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=s["insider_merge_rate"], y=s["outsider_merge_rate"], mode="markers",
            marker=dict(size=9, color=s["merge_gap"], colorscale=DIVERGING,
                        cmid=0, opacity=0.8, line=dict(width=1.5, color=SURFACE),
                        colorbar=dict(title=dict(text="Gap", font=dict(size=12, color=INK_2)),
                                      thickness=10, len=0.55, outlinewidth=0,
                                      tickfont=dict(size=11, color=MUTED))),
            text=s["repo"],
            hovertemplate=("<b>%{text}</b><br>Insider %{x:.0%} · Outsider %{y:.0%}"
                           "<extra></extra>"),
        ))
        fig.add_annotation(x=0.78, y=0.12, text="outsiders fare worse",
                           showarrow=False, font=dict(size=11, color=MUTED))
        fig.update_layout(title="Insider against outsider merge rate",
                          xaxis_title="Insider merge rate",
                          yaxis_title="Outsider merge rate")
        fig.update_xaxes(tickformat=".0%")
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(style(fig), use_container_width=True)
        st.caption("Everything below the dotted line merges insiders more readily "
                   "than newcomers.")

    st.markdown("##### Hypothesis tests")
    t1, t2 = st.columns(2, gap="large")

    with t1:
        with_c = df[df["has_contributing"] == True]["median_issue_response_hrs"].dropna()
        without_c = df[df["has_contributing"] == False]["median_issue_response_hrs"].dropna()
        if len(with_c) > 5 and len(without_c) > 5:
            u, p = stats.mannwhitneyu(with_c, without_c, alternative="two-sided")
            fig = go.Figure()
            for name, series in [("Has CONTRIBUTING.md", with_c),
                                 ("No CONTRIBUTING.md", without_c)]:
                fig.add_trace(go.Box(
                    y=series, name=name, marker=dict(color=BLUE, size=5),
                    line=dict(color=BLUE, width=2), fillcolor="rgba(42,120,214,0.10)",
                    boxpoints=False,
                ))
            fig.update_layout(title="Time to first response, by contributing guide",
                              yaxis_title="Median first response (hours)")
            fig.update_yaxes(type="log")
            st.plotly_chart(style(fig, 380), use_container_width=True)
            verdict = "significant" if p < 0.05 else "not significant"
            st.markdown(
                f'<div class="finding">Mann-Whitney U = {u:,.0f}, <b>p = {p:.4g}</b> '
                f"({verdict} at α = 0.05). Medians: {with_c.median():.0f}h with a guide, "
                f"{without_c.median():.0f}h without. A rank test is used because the "
                "distributions are heavily right-skewed, which rules out a t-test.</div>",
                unsafe_allow_html=True,
            )

    with t2:
        groups = [g["median_issue_response_hrs"].dropna().values
                  for _, g in df.groupby("language") if len(g) > 5]
        names = [n for n, g in df.groupby("language") if len(g) > 5]
        if len(groups) > 2:
            h, p = stats.kruskal(*groups)
            order = df[df["language"].isin(names)].groupby("language")[
                "median_issue_response_hrs"].median().sort_values().index.tolist()
            fig = go.Figure()
            for lang in order:
                vals = df[df["language"] == lang]["median_issue_response_hrs"].dropna()
                fig.add_trace(go.Box(
                    y=vals, name=lang, marker=dict(color=BLUE, size=5),
                    line=dict(color=BLUE, width=2), fillcolor="rgba(42,120,214,0.10)",
                    boxpoints=False,
                ))
            fig.update_layout(title="Time to first response, by language",
                              yaxis_title="Median first response (hours)")
            fig.update_yaxes(type="log")
            st.plotly_chart(style(fig, 380), use_container_width=True)
            st.markdown(
                f'<div class="finding">Kruskal-Wallis H = {h:.1f}, <b>p = {p:.4g}</b> '
                f"across {len(groups)} languages. This is exactly why the index ranks "
                "repositories within their own language rather than globally.</div>",
                unsafe_allow_html=True,
            )

    st.markdown("##### How the metrics relate")
    metric_cols = [
        "stars", "forks", "age_years", "open_issues", "gfi_unassigned", "ghost_ratio",
        "median_issue_response_hrs", "issue_response_rate", "median_pr_review_hrs",
        "outsider_merge_rate", "merge_gap", "commits_per_week", "unique_committers",
        "gini_commits", "absence_factor", "readme_bytes",
    ]
    present = [c for c in metric_cols if c in df.columns]
    corr = df[present].corr(method="spearman")
    pretty = [c.replace("_", " ") for c in present]
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=pretty, y=pretty, colorscale=DIVERGING, zmid=0, zmin=-1, zmax=1,
        xgap=2, ygap=2,  # surface gap between cells
        colorbar=dict(title=dict(text="ρ", font=dict(size=12, color=INK_2)),
                      thickness=10, len=0.6, outlinewidth=0,
                      tickfont=dict(size=11, color=MUTED)),
        hovertemplate="%{y} ↔ %{x}<br>ρ = %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(title="Spearman correlation across metrics")
    fig.update_xaxes(tickangle=-45, tickfont=dict(size=10, color=MUTED))
    fig.update_yaxes(tickfont=dict(size=10, color=MUTED))
    st.plotly_chart(style(fig, 600), use_container_width=True)

# -------------------------------------------------------------- 4. geography

with tabs[3]:
    if contrib.empty or contrib["country"].notna().sum() == 0:
        st.info("No contributor locations collected yet. Run `python collect.py locations`.")
    else:
        counts = (
            contrib.dropna(subset=["country"])
            .groupby("country").size().reset_index(name="contributors")
            .sort_values("contributors", ascending=False)
        )
        resolved = contrib["country"].notna().sum()
        st.markdown(
            f'<div class="finding">{resolved:,} of {len(contrib):,} top contributors '
            f"({resolved / len(contrib):.0%}) list a location GitHub profiles are free "
            "text, so these were normalised from strings like "
            "<em>&ldquo;Bay Area&rdquo;</em>, <em>&ldquo;NYC&rdquo;</em> and "
            "<em>&ldquo;Virginia, USA, Earth, Milky Way.&rdquo;</em> "
            "Unresolvable entries are excluded rather than guessed.</div>",
            unsafe_allow_html=True,
        )

        g1, g2 = st.columns([3, 2], gap="large")
        with g1:
            fig = go.Figure(go.Choropleth(
                locations=counts["country"], locationmode="country names",
                z=counts["contributors"], colorscale=SEQ_BLUE,
                marker=dict(line=dict(color=SURFACE, width=0.5)),
                colorbar=dict(title=dict(text="People", font=dict(size=12, color=INK_2)),
                              thickness=10, len=0.6, outlinewidth=0,
                              tickfont=dict(size=11, color=MUTED)),
                hovertemplate="<b>%{location}</b><br>%{z:,} contributors<extra></extra>",
            ))
            fig.update_layout(title="Where the top committers are")
            fig.update_geos(bgcolor=SURFACE, showframe=False, showcoastlines=False,
                            landcolor="#f0efec", projection_type="natural earth")
            st.plotly_chart(style(fig, 460), use_container_width=True)

        with g2:
            top = counts.head(15).sort_values("contributors")
            fig = go.Figure(go.Bar(
                x=top["contributors"], y=top["country"], orientation="h",
                marker=dict(color=BLUE), text=top["contributors"],
                textposition="outside", textfont=dict(size=12, color=INK_2),
                hovertemplate="<b>%{y}</b><br>%{x:,}<extra></extra>",
            ))
            fig.update_layout(title="Top 15 countries", xaxis_title="Contributors")
            fig.update_xaxes(range=[0, top["contributors"].max() * 1.12])
            fig.update_yaxes(tickfont=dict(size=11, color=INK_2))
            st.plotly_chart(style(fig, 460), use_container_width=True)

        st.dataframe(counts, use_container_width=True, hide_index=True, height=260)

# ------------------------------------------------------------------ 5. model

with tabs[4]:
    st.markdown("##### What actually predicts whether a newcomer's PR gets merged?")
    st.caption(
        "A model used as a measuring instrument, not a product: it is fitted only to "
        "read off which repository properties carry the signal."
    )

    @st.cache_data(show_spinner="Fitting model…")
    def fit_model(prs_df: pd.DataFrame, repos_df: pd.DataFrame):
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import roc_auc_score, roc_curve
        from sklearn.model_selection import train_test_split

        feats = [
            "stars", "age_years", "open_issues", "open_prs", "commits_per_week",
            "unique_committers", "gini_commits", "absence_factor",
            "median_issue_response_hrs", "issue_response_rate", "median_pr_review_hrs",
            "gfi_unassigned", "readme_bytes", "has_contributing", "has_ci",
            "has_issue_template",
        ]
        feats = [f for f in feats if f in repos_df.columns]

        d = prs_df[prs_df["is_outsider"] & prs_df["is_resolved"]].merge(
            repos_df[["repo"] + feats], on="repo", how="left"
        )
        d = d.dropna(subset=["is_merged"])
        for c in ["additions", "deletions", "changed_files"]:
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce")
                if c not in feats:
                    feats.append(c)

        X = d[feats].astype(float)
        X = X.fillna(X.median(numeric_only=True))
        y = d["is_merged"].astype(int)
        if len(X) < 200 or y.nunique() < 2:
            return None

        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=42, stratify=y)
        model = RandomForestClassifier(
            n_estimators=220, max_depth=12, min_samples_leaf=15,
            random_state=42, n_jobs=-1, class_weight="balanced",
        ).fit(Xtr, ytr)

        proba = model.predict_proba(Xte)[:, 1]
        fpr, tpr, _ = roc_curve(yte, proba)
        return {
            "fpr": fpr, "tpr": tpr, "auc": roc_auc_score(yte, proba),
            "model": model, "X": X, "Xte": Xte, "feats": feats,
            "n": len(X), "base": float(y.mean()),
        }

    res = fit_model(prs, df)
    if res is None:
        st.info("Not enough resolved outsider pull requests yet to fit a model.")
    else:
        m1, m2 = st.columns(2, gap="large")

        with m1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[0, 1], y=[0, 1], mode="lines", name="Random",
                line=dict(color=AXIS, width=1, dash="dot"),
                hovertemplate="Random<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=res["fpr"], y=res["tpr"], mode="lines",
                name=f"Model (AUC {res['auc']:.3f})",
                line=dict(color=BLUE, width=2),
                hovertemplate="FPR %{x:.2f} · TPR %{y:.2f}<extra></extra>",
            ))
            fig.update_layout(
                title="ROC - predicting an outsider PR merge",
                xaxis_title="False positive rate", yaxis_title="True positive rate",
            )
            st.plotly_chart(style(fig, 430, legend=True), use_container_width=True)
            st.caption(
                f"{res['n']:,} resolved outsider pull requests · "
                f"base merge rate {res['base']:.0%} · held-out AUC {res['auc']:.3f}"
            )

        with m2:
            try:
                import shap
                sample = res["Xte"].sample(min(300, len(res["Xte"])), random_state=42)
                explainer = shap.TreeExplainer(res["model"])
                sv = explainer.shap_values(sample)
                vals = sv[..., 1] if getattr(sv, "ndim", 2) == 3 else sv
                importance = pd.Series(
                    np.abs(vals).mean(axis=0), index=sample.columns
                ).sort_values().tail(14)
                subtitle = "Mean |SHAP value|"
            except Exception:
                importance = pd.Series(
                    res["model"].feature_importances_, index=res["feats"]
                ).sort_values().tail(14)
                subtitle = "Impurity-based importance"

            fig = go.Figure(go.Bar(
                x=importance.values,
                y=[i.replace("_", " ") for i in importance.index],
                orientation="h", marker=dict(color=BLUE),
                hovertemplate="<b>%{y}</b><br>%{x:.4f}<extra></extra>",
            ))
            fig.update_layout(title=f"Which features carry the signal", xaxis_title=subtitle)
            fig.update_yaxes(tickfont=dict(size=11, color=INK_2))
            st.plotly_chart(style(fig, 430), use_container_width=True)
            st.caption(
                "Read as association, not causation - this is an observational "
                "sample, so nothing here licenses a causal claim."
            )

# ------------------------------------------------------------------- 6. data

with tabs[5]:
    st.markdown("##### The tables behind every chart")
    which = st.radio(
        "Table", ["Repositories", "Pull requests", "Issues", "Contributors"],
        horizontal=True, label_visibility="collapsed",
    )
    table = {"Repositories": df, "Pull requests": prs,
             "Issues": issues, "Contributors": contrib}[which]
    st.caption(f"{len(table):,} rows × {table.shape[1]} columns")
    st.dataframe(table.head(1000), use_container_width=True, height=520)
    st.download_button(
        f"Download {which.lower()}.csv",
        table.to_csv(index=False).encode(),
        file_name=f"{which.lower().replace(' ', '_')}.csv",
        mime="text/csv",
    )

st.markdown("---")
st.caption(
    "Metric definitions follow the CHAOSS Starter Project Health model. "
    "Bot accounts are removed before any response-time or merge-rate calculation. "
    "Scores are percentiles within language and size bucket, never raw cross-project "
    "comparisons."
)
