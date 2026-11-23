"""Streamlit dashboard: LoL 15-minute match data, read from Supabase.

    streamlit run dashboard/app.py

Needs SUPABASE_URL and SUPABASE_KEY (the public anon key; the table is read-only to it) in the environment.
On Modal they come from a Modal secret, see modal_app.py.
"""

import os
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

TABLE = "matches"
PAGE = 1000  # Supabase returns at most 1000 rows per request
RANKS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER_PLUS"]
FEATURES = {
    # column: (label, clip to +/- this, bin width)
    "level_sum_diff": ("Level lead (sum of 5 champions)", 8, 1),
    "kills_diff": ("Kill lead", 10, 1),
    "cs_diff": ("CS lead (20-CS bins)", 100, 20),
    "assists_diff": ("Assist lead", 15, 1),
    "towers_diff": ("Tower lead", 3, 1),
    "dragons_diff": ("Dragon lead", 2, 1),
    "voidgrubs_diff": ("Void grub lead", 3, 1),
}
BLUE, ORANGE, INK2 = "#2a78d6", "#eb6834", "#52514e"

DICTIONARY_CANDIDATES = [
    Path("/root/data_dictionary.csv"),  # bundled into the Modal image
    Path(__file__).resolve().parent.parent / "data" / "training" / "lol_15min_live_data_dictionary.csv",
]

st.set_page_config(page_title="LoL win-chance data", page_icon=":bar_chart:", layout="wide")


# --- data ------------------------------------------------------------------------
@st.cache_resource
def supabase_client():
    from supabase import create_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        st.error("SUPABASE_URL and SUPABASE_KEY are not set.")
        st.stop()
    return create_client(url, key)


@st.cache_data(ttl=3600, show_spinner="Loading matches from Supabase...")
def fetch_matches():
    rows, start = [], 0
    while True:
        chunk = (supabase_client().table(TABLE).select("*")
                 .order("match_id").range(start, start + PAGE - 1).execute().data)
        rows += chunk
        if len(chunk) < PAGE:
            break
        start += PAGE
    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df.start_time)
    df["game_minutes"] = df.game_duration_s / 60
    df["rank_tier"] = pd.Categorical(df.rank_tier, categories=RANKS, ordered=True)
    return df


@st.cache_data
def load_dictionary():
    for p in DICTIONARY_CANDIDATES:
        if p.exists():
            return pd.read_csv(p)
    return None


def win_rate_table(df, by):
    g = df.groupby(by, observed=True).blue_win.agg(win_rate="mean", n="size").reset_index()
    se = np.sqrt(g.win_rate * (1 - g.win_rate) / g.n)
    g["lo"] = (g.win_rate - 1.96 * se).clip(0, 1)
    g["hi"] = (g.win_rate + 1.96 * se).clip(0, 1)
    return g


def half_line():
    return alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(color=INK2, strokeDash=[4, 4]).encode(y="y:Q")


# --- page ------------------------------------------------------------------------
st.title("League of Legends: win chance at 15:00")
st.caption("Ranked solo/duo matches on NA at the 15:00 mark, using only stats a live game exposes "
           "(level, CS, kills, assists, towers, dragons, void grubs, first blood). Training split only: "
           "the held-out test matches are not in this database.")

data = fetch_matches()

with st.sidebar:
    st.header("Filters")
    ranks = st.multiselect("Rank", RANKS, default=RANKS)
    patches = st.multiselect("Patch", sorted(data.patch.unique()), default=sorted(data.patch.unique()))
    st.caption(f"{len(data):,} matches in Supabase")
    if st.button("Reload from Supabase"):
        fetch_matches.clear()
        st.rerun()

df = data[data.rank_tier.isin(ranks) & data.patch.isin(patches)]
if df.empty:
    st.warning("No matches for these filters.")
    st.stop()

tab_overview, tab_features = st.tabs(["Overview and data quality", "Feature explorer"])

# --- tab 1 ------------------------------------------------------------------------
with tab_overview:
    c = st.columns(4)
    c[0].metric("Matches", f"{len(df):,}")
    c[1].metric("Blue win rate", f"{df.blue_win.mean():.1%}")
    c[2].metric("Median game length", f"{df.game_minutes.median():.1f} min")
    c[3].metric("Collected", f"{df.start_time.min():%b %d} to {df.start_time.max():%b %d, %Y}")

    st.subheader("Data quality checks")
    checks = pd.DataFrame([
        ("Missing values", int(data.isna().sum().sum()), 0),
        ("Duplicate match ids", int(data.match_id.duplicated().sum()), 0),
        ("Games that ended before 15:00", int((data.game_duration_s < 900).sum()), 0),
        ("Win or first-blood flags not 0/1", int((~data.blue_win.isin([0, 1])).sum()
                                                  + (~data.blue_first_blood.isin([0, 1])).sum()), 0),
        ("Ranks outside the expected 8", int(data.rank_tier.isna().sum()), 0),
    ], columns=["check", "found", "expected"])
    checks["status"] = np.where(checks.found == checks.expected, "pass", "FAIL")
    st.dataframe(checks, hide_index=True, width="stretch")
    st.caption("These run on the whole table, ignoring the sidebar filters. The data was cleaned before upload "
               "(see prep/prepare.py), so every check should pass; a FAIL means something went wrong in the upload.")

    left, right = st.columns(2)
    with left:
        st.subheader("Win rate by rank")
        t = win_rate_table(df, "rank_tier")
        t["rank"] = t.rank_tier.astype(str)
        base = alt.Chart(t).encode(x=alt.X("rank:N", sort=RANKS, title=None, axis=alt.Axis(labelAngle=-30)))
        # Dots, not bars: the axis doesn't start at zero, and bars would run off the bottom of the plot.
        # Every layer needs the same y scale, or the layers with no domain pull the axis back to zero.
        yscale = alt.Scale(domain=[0.35, 0.65])
        dots = base.mark_point(color=BLUE, filled=True, size=120).encode(
            y=alt.Y("win_rate:Q", scale=yscale, title="Blue win rate"),
            tooltip=["rank", alt.Tooltip("win_rate:Q", format=".1%"), "n"])
        err = base.mark_rule(color=INK2).encode(y=alt.Y("lo:Q", scale=yscale), y2="hi:Q")
        even = (alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(color=INK2, strokeDash=[4, 4])
                .encode(y=alt.Y("y:Q", scale=yscale)))
        st.altair_chart((err + dots + even).properties(height=300), width="stretch")
        st.caption("Dots are win rates; whiskers are 95% intervals; the dashed line is 50%. Blue wins about half of "
                   "games overall; the rank-to-rank differences "
                   "are modest and don't follow a clear trend up the ladder.")
    with right:
        st.subheader("How long games last")
        hist = (alt.Chart(df[df.game_minutes <= 60]).mark_bar(color=BLUE)
                .encode(x=alt.X("game_minutes:Q", bin=alt.Bin(step=1), title="Game length (minutes)"),
                        y=alt.Y("count()", title="Matches")))
        st.altair_chart(hist.properties(height=300), width="stretch")
        st.caption("Every game lasted at least 15 minutes, because only games still running at 15:00 were sampled. "
                   "The spike just after 15:00 is early surrenders, which are usually lopsided, so this snapshot is "
                   "easier to predict than a typical moment in a game.")

    st.subheader("Sampling concerns")
    st.markdown(
        "- **Ranks are sampled evenly** (about 1,000 games each), not in proportion to how many games each rank plays.\n"
        "- **Survivorship at 15:00:** games that ended earlier are not here.\n"
        "- **One region, one month, three patches** (NA, mid-September to mid-October 2026).\n"
        "- **Repeated players:** about 58,000 unique players fill 81,670 slots, so matches are not fully independent.\n"
        "- **Live vs training mismatch:** live CS updates in steps of 10, and the live script counts events slightly "
        "differently from the post-game data."
    )

    dictionary = load_dictionary()
    if dictionary is not None:
        with st.expander("Data dictionary"):
            shown = dictionary[dictionary.column.isin(data.columns)]
            st.dataframe(shown, hide_index=True, width="stretch")
            st.caption("Columns of the Supabase table only. The full dictionary is data/training/"
                       "lol_15min_live_data_dictionary.csv in the repository.")

# --- tab 2 ------------------------------------------------------------------------
with tab_features:
    st.write("Pick a lead and see how blue's win rate changes with it. Points are groups of matches with "
             "the same lead; whiskers are 95% intervals.")
    c1, c2 = st.columns([2, 1])
    feature = c1.selectbox("Feature", list(FEATURES), format_func=lambda f: FEATURES[f][0])
    min_n = c2.slider("Minimum matches per point", 10, 200, 40, step=10)
    label, clip, width = FEATURES[feature]

    x = df[feature].clip(-clip, clip - (1e-9 if width > 1 else 0))
    x = (np.floor(x / width) * width + (width / 2 if width > 1 else 0))
    t = win_rate_table(df.assign(x=x), "x")
    t = t[t.n >= min_n]
    if t.empty:
        st.info("No groups have that many matches; lower the minimum.")
    else:
        base = alt.Chart(t).encode(x=alt.X("x:Q", title=f"{label}, clipped to +/-{clip}"))
        line = base.mark_line(color=BLUE).encode(y=alt.Y("win_rate:Q", scale=alt.Scale(domain=[0, 1]),
                                                          title="Blue win rate"))
        pts = base.mark_point(color=BLUE, filled=True, size=60).encode(
            y="win_rate:Q", tooltip=[alt.Tooltip("x:Q", title="lead"), alt.Tooltip("win_rate:Q", format=".1%"), "n"])
        err = base.mark_rule(color=INK2).encode(y="lo:Q", y2="hi:Q")
        zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=INK2, strokeDash=[4, 4]).encode(x="x:Q")
        st.altair_chart((line + err + pts + half_line() + zero).properties(height=360), width="stretch")
        st.caption(f"{int(t.n.sum()):,} of {len(df):,} matches shown; groups with fewer than {min_n} matches are hidden. "
                   "The end points are groups clipped at the limit, not single values.")
        with st.expander("Numbers behind the chart"):
            st.dataframe(t.rename(columns={"x": "lead", "n": "matches"}).round(3), hide_index=True, width="stretch")

    st.subheader("Which features track the outcome?")
    cols = list(FEATURES) + ["blue_first_blood"]
    corr = df[cols + ["blue_win"]].corr()["blue_win"].drop("blue_win").sort_values().reset_index()
    corr.columns = ["feature", "correlation"]
    bars = (alt.Chart(corr).mark_bar(color=BLUE)
            .encode(x=alt.X("correlation:Q", title="Correlation with blue_win"), y=alt.Y("feature:N", sort="-x", title=None),
                    tooltip=["feature", alt.Tooltip("correlation:Q", format=".2f")]))
    st.altair_chart(bars.properties(height=280), width="stretch")
    st.caption("Level and kill leads are the strongest live signals. They overlap with each other (level vs kills is "
               "about 0.8), so a model gains less from combining them than these bars suggest.")
