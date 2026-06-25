# 🎁 GGS Free-Gift Program — Dashboard

Interactive Streamlit dashboard for the GGS bill-threshold free-gift program.
It reads a **local data snapshot** (`data/gsc_bills.parquet`) and never touches
Redshift at view time — refresh the snapshot only when you want to update.

## Setup (one time)
```bash
cd dashboard
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Refresh the data (run only when you want to update)
Pulls fresh data from Redshift (creds read from `../.env`) into the local parquet:
```bash
./.venv/bin/python snapshot.py                       # 2026-06-23 .. today
./.venv/bin/python snapshot.py --from 2026-06-23 --to 2026-06-24
```
Prints a per-date sanity summary (total bills, coupon mix, free/discounted, ₹ collected).

## Run the dashboard
```bash
./.venv/bin/streamlit run app.py
```
Then open the URL it prints (default http://localhost:8501).

## Login
The dashboard is gated by a username/password login. Credentials live in the
project `.env` file (`../.env`) as indexed pairs:
```
DASH_USER1=Manish
DASH_PASS1=IamHero
DASH_USER2=Harshit
DASH_PASS2=DarnaManaHai
```
Add or change users by editing those keys (increment the index for more users),
then restart the app. This is a basic access gate for an internal localhost tool
— not HTTPS/SSO.

## Filters
- **Date range** — defaults to the latest day in the snapshot.
- **Order type** — Walking / Non-walking.
- **Patient** — New / Old.
- **Stores** — multiselect.

## Definitions
| Concept | Rule |
|---|---|
| **Non-walking order** | `hd-flag` OR `ecom-flag` OR has a Zeno online order id. Everything else = Walking. |
| **New patient** | The bill is the patient's first-ever Zeno bill (`first-bill-date = bill date`). Else Old. |
| **GSC gift coupon** | `promo-code ~ '^ZG[A-HL-S]'` — ZGA–ZGH (old customers), ZGL–ZGS (new). |
| **Rewards coupon** | `promo-code ~ '^ZR[DF]'` (ZRD / ZRF). |
| **Other coupon** | any other promo code. |
| **Free vs Discounted** | gift bill `net-payable == 0` → Free, else Discounted (token). |

Coupon categories are mutually exclusive (priority GSC > Rewards > Other > None).

## Layout
- **Overview** — KPI cards + coupon-usage and free/discounted donuts.
- **Stores** — store-wise report table (downloadable) + coupon bars.
- **Tiers & Products** — redemptions by threshold + product distribution (downloadable).
- **Hourly** — bills/coupon usage by hour.
- **Raw data** — the filtered per-bill table (downloadable).

## Files
| File | Purpose |
|---|---|
| `snapshot.py` | Redshift → `data/gsc_bills.parquet` (+ `snapshot_meta.json`). On-demand. |
| `app.py` | The Streamlit dashboard (reads local parquet only). |
| `catalog.json` | 48-product catalog: drug-id → tier/threshold. |
| `data/` | Local snapshot + metadata. |
