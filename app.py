#!/usr/bin/env python3
"""
GGS Free-Gift Program — Streamlit dashboard.

Reads ONLY the local snapshot (data/gsc_bills.parquet). Refresh it with
`python snapshot.py`. Run this app with `streamlit run app.py`.
"""
import hmac
import json
import os
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

# Gift price ladder (net-payable paid for the gift): free + token tiers, else "Other".
PRICE_LADDER = [0, 5, 9, 15, 19, 29, 59, 79]
PRICE_BUCKETS = ["Free (₹0)", "₹5", "₹9", "₹15", "₹19", "₹29", "₹59", "₹79", "Other"]


def price_bucket(p):
    if p == 0:
        return "Free (₹0)"
    return f"₹{int(p)}" if p in PRICE_LADDER else "Other"

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
    """Load dashboard logins from any available source: Streamlit Cloud secrets,
    environment variables, or the local .env. Two formats are accepted:

      A) Combined:  DASHBOARD_USER_1 = "Manish:IamHero"   (key contains USER, value is user:pass)
      B) Indexed:   DASH_USER1 = "Manish"  /  DASH_PASS1 = "IamHero"
    """
    sources = {}
    try:                       # Streamlit Cloud secrets (raises locally if none)
        sources.update(dict(st.secrets))
    except Exception:
        pass
    sources.update(os.environ)
    if ENV_PATH.exists():      # local .env
        sources.update({k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None})

    creds = {}
    # Format A — "username:password" in any DASH*USER* key
    for key, val in sources.items():
        ku = str(key).upper()
        if ku.startswith("DASH") and "USER" in ku and isinstance(val, str) and ":" in val:
            user, pwd = val.split(":", 1)
            creds[user.strip()] = pwd
    # Format B — indexed DASH_USER{n} / DASH_PASS{n}
    i = 1
    while sources.get(f"DASH_USER{i}"):
        creds.setdefault(sources[f"DASH_USER{i}"], sources.get(f"DASH_PASS{i}", ""))
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
total_bills = int((f["coupon_cat"] != "GSC").sum())   # purchase bills only (exclude gift bills)
# GSC gift bills are SEPARATE redemption bills (earned by a qualifying purchase),
# so the qualifying base = purchase bills with NET-PAYABLE ≥₹149 that are NOT GSC gift bills.
purch = f[f["coupon_cat"] != "GSC"]
bills_ge149 = int((purch["net_payable"] >= 149).sum())           # qualifying purchases (net-payable)
n_gsc = int((f["coupon_cat"] == "GSC").sum())                    # gift redemptions
n_rew = int(((f["coupon_cat"] == "Rewards") & (f["net_payable"] >= 149)).sum())
n_oth = int(((f["coupon_cat"] == "Other") & (f["net_payable"] >= 149)).sum())
# A qualifying purchase either redeemed GSC, used Rewards/Other, or availed nothing.
n_none = max(0, bills_ge149 - n_gsc - n_rew - n_oth)            # availed nothing
n_free = int((gift["gift_is_free"] == True).sum())
n_disc = int((gift["gift_is_free"] == False).sum())
collected = float(pd.to_numeric(gift["gift_net_payable"], errors="coerce").fillna(0).sum())
redempt_rate = (n_gsc / bills_ge149 * 100) if bills_ge149 else 0


def section(title):
    st.markdown(f"### {title}")


tabs = st.tabs(["📊 Overview", "🏬 Stores", "🎯 Tiers & Products", "🕐 Hourly", "🧾 Raw data"])

# ============================================================== OVERVIEW
with tabs[0]:
    st.markdown("#### 📦 Bills & qualifying base")
    a = st.columns(5)
    a[0].metric("Total bills", f"{total_bills:,}",
                help="Purchase bills — excludes separate GSC gift-redemption bills.")
    a[1].metric("Bills ≥ ₹149", f"{bills_ge149:,}",
                help="Qualifying purchases with net-payable ≥₹149 (excludes separate GSC gift bills). "
                     "= GSC + Rewards + Other + No-coupon.")
    a[2].metric("No-coupon bills", f"{n_none:,}",
                help="Qualifying (net-payable ≥₹149) purchases that availed nothing "
                     "(GSC redeemers + Rewards + Other removed).")
    a[3].metric("Rewards coupon", f"{n_rew:,}")
    a[4].metric("Other coupon", f"{n_oth:,}")

    st.markdown("#### 🎁 GSC gift redemptions")
    rg_rate = ((n_rew + n_gsc) / bills_ge149 * 100) if bills_ge149 else 0
    b = st.columns(6)
    b[0].metric("GSC gift redeemed", f"{n_gsc:,}", help="Separate gift bills (the reward)")
    b[1].metric("Redemption rate", f"{redempt_rate:.1f}%",
                help="GSC gift ÷ qualifying bills (net-payable ≥ ₹149)")
    b[2].metric("Rewards + GSC Red %", f"{rg_rate:.1f}%",
                help="(Rewards coupon + GSC gift) ÷ qualifying bills (net-payable ≥ ₹149)")
    b[3].metric("Free products", f"{n_free:,}")
    b[4].metric("Discounted products", f"{n_disc:,}")
    b[5].metric("₹ collected (gift bills)", f"₹{collected:,.0f}")

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
        st.caption("Split of qualifying purchases (net-payable ≥ ₹149): GSC = redeemed a gift (separate bill), "
                   "None = availed nothing. These add up to Bills ≥ ₹149.")
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
        purch = grp[grp.coupon_cat != "GSC"]                       # gift bills are separate
        ge149 = int((purch.net_payable >= 149).sum())             # qualifying purchases (net-payable)
        gsc_n = int((grp.coupon_cat == "GSC").sum())
        rew_n = int(((grp.coupon_cat == "Rewards") & (grp.net_payable >= 149)).sum())
        oth_n = int(((grp.coupon_cat == "Other") & (grp.net_payable >= 149)).sum())
        none_n = max(0, ge149 - gsc_n - rew_n - oth_n)            # availed nothing
        rows.append({
            "Store": sname, "ID": sid,
            "Total bills": int((grp.coupon_cat != "GSC").sum()),
            "Bills ≥149": ge149,
            "GSC gift": gsc_n,
            "GSC Red %": round(gsc_n / ge149 * 100, 1) if ge149 else 0.0,
            "Rew+GSC Red %": round((rew_n + gsc_n) / ge149 * 100, 1) if ge149 else 0.0,
            "Rewards": rew_n,
            "Other": oth_n,
            "No coupon": none_n,
            "Free": int((gg.gift_is_free == True).sum()),
            "Disc": int((gg.gift_is_free == False).sum()),
            "₹ collected": float(pd.to_numeric(gg.gift_net_payable, errors="coerce").fillna(0).sum()),
        })
    sdf = pd.DataFrame(rows).sort_values("GSC gift", ascending=False).reset_index(drop=True)
    sdf["ID"] = sdf["ID"].astype(str)
    total_row = {c: sdf[c].sum() if sdf[c].dtype != object else "" for c in sdf.columns}
    total_row["Store"] = "TOTAL"; total_row["ID"] = ""
    # Redemption % on the TOTAL row must be recomputed from totals, not summed.
    tg, te = sdf["GSC gift"].sum(), sdf["Bills ≥149"].sum()
    total_row["GSC Red %"] = round(tg / te * 100, 1) if te else 0.0
    total_row["Rew+GSC Red %"] = round((tg + sdf["Rewards"].sum()) / te * 100, 1) if te else 0.0
    sdf_disp = pd.concat([sdf, pd.DataFrame([total_row])], ignore_index=True)
    st.dataframe(
        sdf_disp, width="stretch", hide_index=True,
        column_config={
            "GSC Red %": st.column_config.NumberColumn(
                "GSC Red %", help="GSC gift ÷ qualifying Bills (net-payable ≥149)", format="%.1f%%"),
            "Rew+GSC Red %": st.column_config.NumberColumn(
                "Rew+GSC Red %", help="(Rewards + GSC gift) ÷ qualifying Bills (net-payable ≥149)", format="%.1f%%"),
            "Bills ≥149": st.column_config.NumberColumn(
                "Bills ≥149", help="Qualifying purchases with net-payable ≥₹149 (excl. separate GSC gift bills) "
                                   "= GSC + Rewards + Other + No coupon"),
            "No coupon": st.column_config.NumberColumn(
                "No coupon", help="Qualifying (net-payable ≥149) purchases that availed nothing"),
            "₹ collected": st.column_config.NumberColumn("₹ collected", format="₹%.0f"),
        },
    )
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
        gp = gift.dropna(subset=["gift_product_id"]).copy()
        gp["price"] = pd.to_numeric(gp["gift_net_payable"], errors="coerce").fillna(0)
        prows = []
        for (pid, pname, ptier), grp in gp.groupby(
                ["gift_product_id", "gift_product_name", "gift_product_tier"], dropna=False):
            thr = THRESHOLDS[TIERS.index(ptier)] if ptier in TIERS else None
            rev = float(grp["price"].sum())
            prows.append({
                "Drug ID": int(pid),
                "Product": pname,
                "Times taken": len(grp),
                "Free": int((grp.gift_is_free == True).sum()),
                "Discounted": int((grp.gift_is_free == False).sum()),
                "Revenue (₹)": rev,
                "Avg ₹": round(rev / len(grp), 1),
                "Product tier": f"{ptier} (≥₹{thr})" if ptier else "?",
            })
        pdf = pd.DataFrame(prows).sort_values("Times taken", ascending=False).reset_index(drop=True)
        c1, c2 = st.columns([1.3, 1])
        with c1:
            st.dataframe(pdf, width="stretch", hide_index=True, height=460,
                         column_config={
                             "Drug ID": st.column_config.NumberColumn("Drug ID", format="%d"),
                             "Revenue (₹)": st.column_config.NumberColumn("Revenue (₹)", format="₹%.0f"),
                             "Avg ₹": st.column_config.NumberColumn("Avg ₹", format="₹%.1f")})
            st.download_button("⬇ Download product report (CSV)",
                               pdf.to_csv(index=False).encode(), "product_report.csv", "text/csv")
        with c2:
            top = pdf.head(15).sort_values("Times taken")
            fig = px.bar(top, x="Times taken", y="Product", orientation="h",
                         color_discrete_sequence=["#2563eb"], height=460,
                         title="Top 15 products by units")
            fig.update_layout(margin=dict(t=50, l=10), yaxis_title="")
            st.plotly_chart(fig, width="stretch")

        # ---------------------------------------------------------- price breakdown
        st.markdown("---")
        section("💰 Price breakdown — units sold at each price")
        st.caption("Each cell = units of that product sold at that price. "
                   "₹0 = free; others = the token the customer paid. "
                   "Revenue = total net collected for the product.")
        gp["bucket"] = gp["price"].apply(price_bucket)

        # overall price-mix bar (units per price point)
        mix = (gp["bucket"].value_counts()
               .reindex(PRICE_BUCKETS, fill_value=0).reset_index())
        mix.columns = ["Price", "Units"]
        figm = px.bar(mix, x="Price", y="Units", text="Units", height=260,
                      color_discrete_sequence=["#16a34a"], title="Units by price point (all products)")
        figm.update_traces(textposition="outside")
        figm.update_layout(margin=dict(t=50, b=10), xaxis_title="", yaxis_title="Units")
        st.plotly_chart(figm, width="stretch")

        # product × price matrix
        mat = (gp.pivot_table(index="gift_product_name", columns="bucket",
                              values="bill_id", aggfunc="count", fill_value=0)
                 .reindex(columns=PRICE_BUCKETS, fill_value=0))
        mat["Units"] = mat.sum(axis=1)
        # keep exact float so totals reconcile; the Styler formats it to ₹ at display
        mat["Revenue (₹)"] = gp.groupby("gift_product_name")["price"].sum()
        mat = mat.sort_values("Revenue (₹)", ascending=False)
        mat.index.name = "Product"
        mat_disp = mat.reset_index()
        # add Drug ID (one id per product name)
        id_map = gp.groupby("gift_product_name")["gift_product_id"].first()
        mat_disp.insert(0, "Drug ID", mat_disp["Product"].map(id_map).astype("Int64"))

        # Manual green heatmap shading (avoids the matplotlib dependency of background_gradient).
        vmax = int(mat[PRICE_BUCKETS].to_numpy().max()) if len(mat) else 0

        def _shade(v):
            if not vmax or not v:
                return ""
            a = 0.10 + 0.65 * (v / vmax)
            return f"background-color: rgba(22,163,74,{a:.2f})"

        sty = mat_disp.style.format({"Revenue (₹)": "₹{:,.0f}", "Drug ID": "{:d}"})
        for col in PRICE_BUCKETS:
            sty = sty.map(_shade, subset=[col])
        st.dataframe(sty, width="stretch", hide_index=True, height=480)
        st.download_button("⬇ Download price breakdown (CSV)",
                           mat_disp.to_csv(index=False).encode(), "price_breakdown.csv", "text/csv")

# ============================================================== HOURLY
with tabs[3]:
    section("Bills by hour (coupon usage)")
    hr = f.groupby("bill_hour").apply(
        lambda g: pd.Series({
            "Total bills": int((g.coupon_cat != "GSC").sum()),
            "Bills ≥149": int(((g.coupon_cat != "GSC") & (g.net_payable >= 149)).sum()),
            "GSC gift": int((g.coupon_cat == "GSC").sum()),
            "Rewards": int(((g.coupon_cat == "Rewards") & (g.net_payable >= 149)).sum()),
            "Other": int(((g.coupon_cat == "Other") & (g.net_payable >= 149)).sum()),
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
    section("Filtered purchase bills")
    raw = f[f["coupon_cat"] != "GSC"]   # exclude separate GSC gift-redemption bills (matches Total bills)
    st.caption(f"{len(raw):,} purchase bills match the current filters "
               "(excludes separate GSC gift bills; gift detail is in the Tiers & Products tab).")
    show_cols = [
        "bill_id", "bill_date", "bill_hour", "store_name", "bill_total", "net_payable",
        "coupon_cat", "order_type", "patient_type",
    ]
    st.dataframe(raw[show_cols].sort_values(["bill_date", "bill_hour"]),
                 width="stretch", hide_index=True, height=520)
    st.download_button("⬇ Download filtered bills (CSV)",
                       raw[show_cols].to_csv(index=False).encode(), "filtered_bills.csv", "text/csv")
