#!/usr/bin/env python3
"""
GGS Free-Gift Program — Streamlit dashboard.

Reads ONLY the local snapshot (data/gsc_bills.parquet). Refresh it with
`python snapshot.py`. Run this app with `streamlit run app.py`.
"""
import hmac
import json
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "gsc_bills.parquet"
META = HERE / "data" / "snapshot_meta.json"
CATALOG = HERE / "catalog.json"
ENV_PATH = HERE / ".env" if (HERE / ".env").exists() else HERE.parent / ".env"

TIERS = ["ZGA", "ZGB", "ZGC", "ZGD", "ZGE", "ZGF", "ZGG", "ZGH"]
NEW_TIERS = ["ZGL", "ZGM", "ZGN", "ZGO", "ZGP", "ZGQ", "ZGR", "ZGS"]
THRESHOLDS = [149, 249, 349, 499, 699, 999, 1599, 1999]
COUPON_COLORS = {"GSC": "#2563eb", "Rewards": "#16a34a", "Other": "#f59e0b", "None": "#cbd5e1"}
FREE_COLORS = {"Free": "#16a34a", "Discounted": "#f59e0b"}

st.set_page_config(layout="wide", page_title="GGS Free-Gift Dashboard", page_icon="🎁")

# ------------------------------------------------------------------ styling
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 2rem;}
      [data-testid="stMetricValue"] {font-size: 1.7rem;}
      [data-testid="stMetric"] {
          background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;
          padding: 12px 16px;
      }
      h1, h2, h3 {letter-spacing: -0.01em;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ auth
@st.cache_data
def load_credentials():
    """Load dashboard logins from the project .env (DASH_USER{n}/DASH_PASS{n})."""
    env = dotenv_values(ENV_PATH)
    creds, i = {}, 1
    while env.get(f"DASH_USER{i}"):
        creds[env[f"DASH_USER{i}"]] = env.get(f"DASH_PASS{i}", "")
        i += 1
    return creds


def require_login():
    """Gate the whole app behind a username/password login. Stops if not authed."""
    if st.session_state.get("auth_user"):
        return
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown("### 🎁 GGS Dashboard")
        st.caption("Please sign in to continue.")
        with st.form("login"):
            user = st.text_input("Username")
            pwd = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", width="stretch")
        if submitted:
            creds = load_credentials()
            stored = creds.get(user)
            if stored is not None and hmac.compare_digest(str(stored), str(pwd)):
                st.session_state["auth_user"] = user
                st.rerun()
            else:
                st.error("Invalid username or password.")
    st.stop()


require_login()


# ------------------------------------------------------------------ data load
@st.cache_data
def load_data():
    df = pd.read_parquet(DATA)
    df["bill_date"] = pd.to_datetime(df["bill_date"]).dt.date
    return df


@st.cache_data
def load_meta():
    return json.loads(META.read_text()) if META.exists() else {}


@st.cache_data
def load_catalog():
    return {int(k): v for k, v in json.loads(CATALOG.read_text()).items()}


if not DATA.exists():
    st.title("🎁 GGS Free-Gift Dashboard")
    st.error(
        "No data snapshot found.\n\n"
        "Build it first from the `dashboard/` folder:\n\n"
        "```\npython snapshot.py\n```"
    )
    st.stop()

df = load_data()
meta = load_meta()
catalog = load_catalog()

# ------------------------------------------------------------------ sidebar / filters
acc1, acc2 = st.sidebar.columns([2, 1])
acc1.markdown(f"👤 **{st.session_state['auth_user']}**")
if acc2.button("Log out"):
    del st.session_state["auth_user"]
    st.rerun()
st.sidebar.markdown("---")

st.sidebar.header("Filters")
if meta:
    st.sidebar.caption(
        f"📦 Data updated: **{meta.get('generated_at','?')}**  \n"
        f"Coverage: {meta.get('date_min','?')} → {meta.get('date_max','?')}  ·  "
        f"{meta.get('row_count','?'):,} bills"
    )

all_dates = sorted(df["bill_date"].unique())
dmin, dmax = all_dates[0], all_dates[-1]
# Default to the latest day that actually has gift redemptions (today's data may
# be only partially loaded); fall back to the overall latest day.
gift_dates = sorted(df.loc[df["coupon_cat"] == "GSC", "bill_date"].unique())
default_day = gift_dates[-1] if gift_dates else dmax

date_sel = st.sidebar.date_input(
    "Date range", value=(default_day, default_day), min_value=dmin, max_value=dmax,
    help="Defaults to the latest day with gift redemptions.",
)
if isinstance(date_sel, (tuple, list)):
    d_from, d_to = (date_sel if len(date_sel) == 2 else (date_sel[0], date_sel[0]))
else:
    d_from = d_to = date_sel

order_sel = st.sidebar.radio("Order type", ["All", "Walking", "Non-walking"], horizontal=False)
pat_sel = st.sidebar.radio("Patient", ["All", "New", "Old"], horizontal=False)

store_opts = (
    df[["store_id", "store_name"]].drop_duplicates().sort_values("store_name")
)
store_labels = {r.store_name: r.store_id for r in store_opts.itertuples()}
store_pick = st.sidebar.multiselect(
    "Stores", options=list(store_labels), default=list(store_labels),
)
picked_ids = {store_labels[s] for s in store_pick} if store_pick else set(store_labels.values())

st.sidebar.markdown("---")
st.sidebar.caption(
    "**Definitions** — Non-walking = home-delivery/online (hd / ecom / zeno order). "
    "New patient = bill is the patient's first-ever Zeno bill. "
    "GSC gift = ZGA–ZGH (old) / ZGL–ZGS (new). Rewards = ZRD/ZRF. "
    "Free vs Discounted from bill net-payable."
)

# ------------------------------------------------------------------ apply filters
f = df[(df["bill_date"] >= d_from) & (df["bill_date"] <= d_to)]
if order_sel != "All":
    f = f[f["order_type"] == order_sel]
if pat_sel != "All":
    f = f[f["patient_type"] == pat_sel]
f = f[f["store_id"].isin(picked_ids)]

gift = f[f["coupon_cat"] == "GSC"].copy()

# ------------------------------------------------------------------ header
st.title("🎁 GGS Free-Gift Program — Dashboard")
date_lbl = f"{d_from:%d %b %Y}" if d_from == d_to else f"{d_from:%d %b} – {d_to:%d %b %Y}"
st.markdown(
    f"**{date_lbl}**  ·  Order type: **{order_sel}**  ·  Patient: **{pat_sel}**  ·  "
    f"Stores: **{'All' if len(picked_ids)==len(store_labels) else len(picked_ids)}**"
)

if f.empty:
    st.warning("No bills match the current filters.")
    st.stop()

# ------------------------------------------------------------------ KPI helpers
total_bills = len(f)
bills_ge149 = int((f["bill_total"] >= 149).sum())
cc = f["coupon_cat"].value_counts().to_dict()
n_gsc = cc.get("GSC", 0)
n_rew = cc.get("Rewards", 0)
n_oth = cc.get("Other", 0)
n_none = cc.get("None", 0)
n_free = int((gift["gift_is_free"] == True).sum())
n_disc = int((gift["gift_is_free"] == False).sum())
collected = float(pd.to_numeric(gift["gift_net_payable"], errors="coerce").fillna(0).sum())
redempt_rate = (n_gsc / bills_ge149 * 100) if bills_ge149 else 0


def section(title):
    st.markdown(f"### {title}")


tabs = st.tabs(["📊 Overview", "🏬 Stores", "🎯 Tiers & Products", "🕐 Hourly", "🧾 Raw data"])

# ============================================================== OVERVIEW
with tabs[0]:
    r1 = st.columns(4)
    r1[0].metric("Total bills", f"{total_bills:,}")
    r1[1].metric("Bills ≥ ₹149", f"{bills_ge149:,}")
    r1[2].metric("GSC gift redeemed", f"{n_gsc:,}")
    r1[3].metric("Redemption rate", f"{redempt_rate:.1f}%", help="GSC gift ÷ bills ≥ ₹149")

    r2 = st.columns(4)
    r2[0].metric("Rewards coupon", f"{n_rew:,}")
    r2[1].metric("Other coupon", f"{n_oth:,}")
    r2[2].metric("Free products", f"{n_free:,}")
    r2[3].metric("Discounted products", f"{n_disc:,}")

    r3 = st.columns(4)
    r3[0].metric("₹ collected (gift bills)", f"₹{collected:,.0f}")
    r3[1].metric("No-coupon bills", f"{n_none:,}")
    r3[2].metric("New-patient bills", f"{int((f['patient_type']=='New').sum()):,}")
    r3[3].metric("Walking bills", f"{int((f['order_type']=='Walking').sum()):,}")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        section("Coupon usage")
        cudf = (
            pd.DataFrame({"Category": ["GSC", "Rewards", "Other", "None"],
                          "Bills": [n_gsc, n_rew, n_oth, n_none]})
        )
        fig = px.pie(cudf, names="Category", values="Bills", hole=0.55,
                     color="Category", color_discrete_map=COUPON_COLORS)
        fig.update_traces(textinfo="label+value")
        fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=320)
        st.plotly_chart(fig, width="stretch")
    with c2:
        section("Gift pick: Free vs Discounted")
        if n_free + n_disc:
            fd = pd.DataFrame({"Type": ["Free", "Discounted"], "Count": [n_free, n_disc]})
            fig = px.pie(fd, names="Type", values="Count", hole=0.55,
                         color="Type", color_discrete_map=FREE_COLORS)
            fig.update_traces(textinfo="label+percent")
            fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=320)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No gift redemptions in the current selection.")

# ============================================================== STORES
with tabs[1]:
    section("Store-wise summary")
    g = f.groupby(["store_id", "store_name"], dropna=False)
    rows = []
    for (sid, sname), grp in g:
        gg = grp[grp.coupon_cat == "GSC"]
        rows.append({
            "Store": sname, "ID": sid,
            "Total bills": len(grp),
            "Bills ≥149": int((grp.bill_total >= 149).sum()),
            "GSC gift": int((grp.coupon_cat == "GSC").sum()),
            "Rewards": int((grp.coupon_cat == "Rewards").sum()),
            "Other": int((grp.coupon_cat == "Other").sum()),
            "No coupon": int((grp.coupon_cat == "None").sum()),
            "Free": int((gg.gift_is_free == True).sum()),
            "Disc": int((gg.gift_is_free == False).sum()),
            "₹ collected": round(float(pd.to_numeric(gg.gift_net_payable, errors="coerce").fillna(0).sum())),
        })
    sdf = pd.DataFrame(rows).sort_values("GSC gift", ascending=False).reset_index(drop=True)
    sdf["ID"] = sdf["ID"].astype(str)
    total_row = {c: sdf[c].sum() if sdf[c].dtype != object else "" for c in sdf.columns}
    total_row["Store"] = "TOTAL"; total_row["ID"] = ""
    sdf_disp = pd.concat([sdf, pd.DataFrame([total_row])], ignore_index=True)
    st.dataframe(sdf_disp, width="stretch", hide_index=True)
    st.download_button("⬇ Download store-wise report (CSV)",
                       sdf.to_csv(index=False).encode(), "store_wise_report.csv", "text/csv")

    fig = px.bar(sdf, x="Store", y=["GSC gift", "Rewards", "Other"],
                 barmode="stack", color_discrete_map=COUPON_COLORS, height=420,
                 title="Coupon bills by store")
    fig.update_layout(xaxis_tickangle=-30, legend_title="", margin=dict(t=50))
    st.plotly_chart(fig, width="stretch")

# ============================================================== TIERS & PRODUCTS
with tabs[2]:
    section("Redemptions by tier (threshold)")
    if gift.empty:
        st.info("No gift redemptions in the current selection.")
    else:
        tier_rows = []
        for i, thr in enumerate(THRESHOLDS):
            codes = {TIERS[i], NEW_TIERS[i]}
            gt = gift[gift.gift_tier.isin(codes)]
            tier_rows.append({
                "Threshold": f"₹{thr}",
                "Free": int((gt.gift_is_free == True).sum()),
                "Discounted": int((gt.gift_is_free == False).sum()),
            })
        tdf = pd.DataFrame(tier_rows)
        tdf["Total"] = tdf["Free"] + tdf["Discounted"]
        fig = px.bar(tdf, x="Threshold", y=["Free", "Discounted"], barmode="stack",
                     color_discrete_map=FREE_COLORS, height=380)
        fig.update_layout(legend_title="", margin=dict(t=20), yaxis_title="Redemptions")
        st.plotly_chart(fig, width="stretch")

        st.markdown("---")
        section("Product distribution — what customers took")
        pg = gift.dropna(subset=["gift_product_id"]).groupby(
            ["gift_product_id", "gift_product_name", "gift_product_tier"], dropna=False)
        prows = []
        for (pid, pname, ptier), grp in pg:
            thr = THRESHOLDS[TIERS.index(ptier)] if ptier in TIERS else None
            prows.append({
                "Product": pname,
                "Times taken": len(grp),
                "Free": int((grp.gift_is_free == True).sum()),
                "Discounted": int((grp.gift_is_free == False).sum()),
                "Product tier": f"{ptier} (≥₹{thr})" if ptier else "?",
            })
        pdf = pd.DataFrame(prows).sort_values("Times taken", ascending=False).reset_index(drop=True)
        c1, c2 = st.columns([1, 1])
        with c1:
            st.dataframe(pdf, width="stretch", hide_index=True, height=460)
            st.download_button("⬇ Download product report (CSV)",
                               pdf.to_csv(index=False).encode(), "product_report.csv", "text/csv")
        with c2:
            top = pdf.head(15).sort_values("Times taken")
            fig = px.bar(top, x="Times taken", y="Product", orientation="h",
                         color_discrete_sequence=["#2563eb"], height=460,
                         title="Top 15 products")
            fig.update_layout(margin=dict(t=50, l=10), yaxis_title="")
            st.plotly_chart(fig, width="stretch")

# ============================================================== HOURLY
with tabs[3]:
    section("Bills by hour (coupon usage)")
    hr = f.groupby("bill_hour").apply(
        lambda g: pd.Series({
            "Total bills": len(g),
            "Bills ≥149": int((g.bill_total >= 149).sum()),
            "GSC gift": int((g.coupon_cat == "GSC").sum()),
            "Rewards": int((g.coupon_cat == "Rewards").sum()),
            "Other": int((g.coupon_cat == "Other").sum()),
        }), include_groups=False
    ).reset_index().rename(columns={"bill_hour": "Hour"})
    hr["Hour"] = hr["Hour"].apply(lambda h: f"{int(h):02d}:00")
    fig = go.Figure()
    fig.add_bar(x=hr["Hour"], y=hr["Total bills"], name="Total bills", marker_color="#cbd5e1")
    fig.add_scatter(x=hr["Hour"], y=hr["GSC gift"], name="GSC gift", mode="lines+markers",
                    line=dict(color="#2563eb", width=3))
    fig.add_scatter(x=hr["Hour"], y=hr["Rewards"], name="Rewards", mode="lines+markers",
                    line=dict(color="#16a34a", width=2))
    fig.add_scatter(x=hr["Hour"], y=hr["Other"], name="Other", mode="lines+markers",
                    line=dict(color="#f59e0b", width=2))
    fig.update_layout(height=440, margin=dict(t=20), legend_title="",
                      yaxis_title="Bills", xaxis_title="")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(hr, width="stretch", hide_index=True)

# ============================================================== RAW DATA
with tabs[4]:
    section("Filtered bills")
    st.caption(f"{len(f):,} bills match the current filters.")
    show_cols = [
        "bill_id", "bill_date", "bill_hour", "store_name", "bill_total",
        "coupon_cat", "order_type", "patient_type",
        "gift_tier", "gift_threshold", "gift_product_name", "gift_is_free", "gift_net_payable",
    ]
    st.dataframe(f[show_cols].sort_values(["bill_date", "bill_hour"]),
                 width="stretch", hide_index=True, height=520)
    st.download_button("⬇ Download filtered bills (CSV)",
                       f[show_cols].to_csv(index=False).encode(), "filtered_bills.csv", "text/csv")
