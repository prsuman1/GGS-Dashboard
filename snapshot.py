#!/usr/bin/env python3
"""
snapshot.py — Build the local data snapshot for the GGS Free-Gift dashboard.

Pulls ONE row per bill (all bills at the launch stores, all program dates) from
Redshift and writes it to data/gsc_bills.parquet, plus data/snapshot_meta.json.

The Streamlit app reads ONLY the local parquet — it never touches Redshift.
Run this manually whenever you want to refresh the data:

    python snapshot.py                       # 2026-06-23 .. today
    python snapshot.py --from 2026-06-23 --to 2026-06-24

Credentials are read from the project .env (REDSHIFT_* keys).
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
# Look for .env in this folder first (cloned repo), else the parent (local layout).
ENV_PATH = HERE / ".env" if (HERE / ".env").exists() else HERE.parent / ".env"
DATA_DIR = HERE / "data"
CATALOG_PATH = HERE / "catalog.json"

# 10 launch stores (fallback if dynamic discovery returns nothing)
DEFAULT_STORES = [391, 54, 280, 410, 395, 180, 392, 454, 421, 390]
PROGRAM_START = "2026-06-23"

# Coupon-code regex (Redshift POSIX). GSC gift = ZGA-ZGH (old) / ZGL-ZGS (new).
GSC_RE = r"^ZG[A-HL-S]"
REWARDS_RE = r"^ZR[DF]"

OLD_PREFIXES = ["ZGA", "ZGB", "ZGC", "ZGD", "ZGE", "ZGF", "ZGG", "ZGH"]
NEW_PREFIXES = ["ZGL", "ZGM", "ZGN", "ZGO", "ZGP", "ZGQ", "ZGR", "ZGS"]
THRESHOLDS = [149, 249, 349, 499, 699, 999, 1599, 1999]


def get_conn():
    env = dotenv_values(ENV_PATH)
    missing = [k for k in ("REDSHIFT_DB", "REDSHIFT_HOST", "REDSHIFT_USER",
                           "REDSHIFT_PASSWORD", "REDSHIFT_PORT") if not env.get(k)]
    if missing:
        sys.exit(f"Missing keys in {ENV_PATH}: {missing}")
    return psycopg2.connect(
        dbname=env["REDSHIFT_DB"], host=env["REDSHIFT_HOST"],
        user=env["REDSHIFT_USER"], password=env["REDSHIFT_PASSWORD"],
        port=env["REDSHIFT_PORT"], connect_timeout=60,
    )


def run(sql, args=None, tries=5):
    """Execute read-only SQL with retry for transient Redshift isolation errors."""
    last = None
    for i in range(tries):
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            conn.close()
            return cols, rows
        except psycopg2.errors.InternalError as e:   # serializable isolation, etc.
            last = e
            time.sleep(2 + i)
    raise last


def discover_stores(date_from, date_to):
    _, rows = run(
        f'''select distinct "store-id"
            from "prod2-generico".sales
            where "promo-code" ~ '{GSC_RE}'
              and "created-date" between %s and %s''',
        (date_from, date_to),
    )
    stores = sorted({r[0] for r in rows if r[0] is not None})
    return stores or DEFAULT_STORES


def store_name_map(stores):
    """Authoritative store-id -> name from the stores master table."""
    _, rows = run(
        'select id, name from "prod2-generico".stores where id in %s',
        (tuple(stores),),
    )
    return {r[0]: r[1] for r in rows if r[1]}


def tier_to_threshold(prefix):
    if prefix in OLD_PREFIXES:
        return THRESHOLDS[OLD_PREFIXES.index(prefix)]
    if prefix in NEW_PREFIXES:
        return THRESHOLDS[NEW_PREFIXES.index(prefix)]
    return None


def build(date_from, date_to):
    catalog = {int(k): v for k, v in json.loads(CATALOG_PATH.read_text()).items()}
    stores = discover_stores(date_from, date_to)
    stores_t = tuple(stores)
    print(f"Stores: {stores}")
    print(f"Date range: {date_from} .. {date_to}")

    # 1) Per-bill aggregation from sales (flags, coupon category, gift line, patient).
    #    The gift line is the one carrying a ZG code with a non-zero promo-discount.
    sql_sales = f'''
        select
            s."bill-id"                                            as bill_id,
            max(case when s."hd-flag"   then 1 else 0 end)         as hd,
            max(case when s."ecom-flag" then 1 else 0 end)         as ecom,
            max(case when s."promo-code" ~ '{GSC_RE}'     then 1 else 0 end) as gsc,
            max(case when s."promo-code" ~ '{REWARDS_RE}' then 1 else 0 end) as rewards,
            max(case when s."promo-code" is not null and s."promo-code" <> ''
                      and not (s."promo-code" ~ '{GSC_RE}')
                      and not (s."promo-code" ~ '{REWARDS_RE}')
                     then 1 else 0 end)                            as other,
            max(case when s."first-bill-date" = s."created-date" then 1 else 0 end) as is_new_patient,
            max(s."store-name")                                    as store_name,
            -- gift line details (max picks the single gift row's values)
            max(case when s."promo-code" ~ '{GSC_RE}' and s."promo-discount" is not null
                       and s."promo-discount" <> 0
                     then substring(s."promo-code", 1, 3) end)     as gift_tier,
            max(case when s."promo-code" ~ '{GSC_RE}' and s."promo-discount" is not null
                       and s."promo-discount" <> 0
                     then s."drug-id" end)                         as gift_drug_id,
            max(case when s."promo-code" ~ '{GSC_RE}' and s."promo-discount" is not null
                       and s."promo-discount" <> 0
                     then s."drug-name" end)                       as gift_drug_name
        from "prod2-generico".sales s
        where s."store-id" in %s
          and s."created-date" between %s and %s
        group by s."bill-id"
    '''
    cols, rows = run(sql_sales, (stores_t, date_from, date_to))
    sdf = pd.DataFrame(rows, columns=cols)
    print(f"sales-aggregated bills: {len(sdf)}")

    # 2) bills-1: authoritative totals, net-payable, time, online-order id.
    sql_bills = '''
        select id as bill_id, "store-id" as store_id, "created-at" as created_at,
               total, "net-payable" as net_payable, "zeno-order-id" as zeno_order_id
        from "prod2-generico"."bills-1"
        where "store-id" in %s and date("created-at") between %s and %s
    '''
    cols, rows = run(sql_bills, (stores_t, date_from, date_to))
    bdf = pd.DataFrame(rows, columns=cols)
    print(f"bills-1 bills: {len(bdf)}")

    # 3) Merge (bills-1 is the spine — one row per bill).
    df = bdf.merge(sdf, on="bill_id", how="left")

    # --- derive fields ---
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["bill_date"] = df["created_at"].dt.date.astype(str)
    df["bill_hour"] = df["created_at"].dt.hour.astype(int)
    df["bill_total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0.0)
    df["net_payable"] = pd.to_numeric(df["net_payable"], errors="coerce")

    for c in ("hd", "ecom", "gsc", "rewards", "other", "is_new_patient"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    # Order type: Non-walking = home delivery OR ecom OR online (zeno) order
    has_zeno = df["zeno_order_id"].notna()
    df["order_type"] = (
        (df["hd"] == 1) | (df["ecom"] == 1) | has_zeno
    ).map({True: "Non-walking", False: "Walking"})

    # Patient type
    df["patient_type"] = df["is_new_patient"].map({1: "New", 0: "Old"})

    # Coupon category — mutually exclusive, priority GSC > Rewards > Other > None
    def coupon_cat(r):
        if r["gsc"] == 1:
            return "GSC"
        if r["rewards"] == 1:
            return "Rewards"
        if r["other"] == 1:
            return "Other"
        return "None"
    df["coupon_cat"] = df.apply(coupon_cat, axis=1)
    df["is_gift"] = df["coupon_cat"] == "GSC"

    # Gift detail
    df["gift_segment"] = df["gift_tier"].apply(
        lambda t: "New-cust code" if t in NEW_PREFIXES else ("Old-cust code" if t in OLD_PREFIXES else None)
    )
    df["gift_threshold"] = df["gift_tier"].apply(tier_to_threshold)
    df["gift_product_id"] = pd.to_numeric(df["gift_drug_id"], errors="coerce").astype("Int64")
    df["gift_product_name"] = df["gift_drug_name"]
    df["gift_product_tier"] = df["gift_product_id"].apply(
        lambda d: catalog[int(d)]["tier"] if pd.notna(d) and int(d) in catalog else None
    )
    # Free vs discounted from authoritative net-payable
    df["gift_net_payable"] = df["net_payable"].where(df["is_gift"])
    df["gift_is_free"] = df.apply(
        lambda r: (float(r["gift_net_payable"]) == 0.0) if r["is_gift"] and pd.notna(r["gift_net_payable"]) else pd.NA,
        axis=1,
    )

    out_cols = [
        "bill_id", "bill_date", "bill_hour", "store_id", "store_name", "bill_total",
        "coupon_cat", "order_type", "patient_type", "is_gift",
        "gift_segment", "gift_tier", "gift_threshold",
        "gift_product_id", "gift_product_name", "gift_product_tier",
        "gift_is_free", "gift_net_payable",
    ]
    out = df[out_cols].copy()
    # Authoritative store names from the stores master (sales."store-name" can be
    # NULL on partially-loaded current-day data). Fall back to id only if truly absent.
    names = store_name_map(stores)
    out["store_name"] = out["store_id"].map(names).fillna(out["store_id"].astype(str))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(DATA_DIR / "gsc_bills.parquet", index=False)

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date_min": str(out["bill_date"].min()),
        "date_max": str(out["bill_date"].max()),
        "row_count": int(len(out)),
        "stores": stores,
    }
    (DATA_DIR / "snapshot_meta.json").write_text(json.dumps(meta, indent=2))

    # --- sanity summary ---
    print("\n=== Snapshot summary ===")
    print(f"rows (bills): {len(out)}")
    print("by date:")
    for d, g in out.groupby("bill_date"):
        cc = g["coupon_cat"].value_counts().to_dict()
        gift = g[g.is_gift]
        free = int((gift.gift_is_free == True).sum())
        disc = int((gift.gift_is_free == False).sum())
        npay = float(pd.to_numeric(gift.gift_net_payable, errors="coerce").fillna(0).sum())
        print(f"  {d}: total={len(g)}  GSC={cc.get('GSC',0)} Rewards={cc.get('Rewards',0)} "
              f"Other={cc.get('Other',0)} None={cc.get('None',0)} | free={free} disc={disc} collected=Rs{npay:.0f}")
    print(f"\nWrote {DATA_DIR/'gsc_bills.parquet'} and snapshot_meta.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default=PROGRAM_START)
    ap.add_argument("--to", dest="date_to", default=date.today().isoformat())
    a = ap.parse_args()
    build(a.date_from, a.date_to)


if __name__ == "__main__":
    main()
