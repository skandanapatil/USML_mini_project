#!/usr/bin/env python3
"""
Data prep for the Self-Healing Quick-Commerce mini project (Bangalore).

Builds two CSVs:
  1. orders_simulated.csv  - 5,000-10,000 jittered customer orders around real
                             Bangalore area anchors (for DBSCAN / K-Means).
  2. sku_master.csv        - a lean 2,000-3,000 SKU catalog filtered from the
                             BigBasket product list, with a
                             category-seeded profit_margin_pct.

Usage
-----
  python quickcommerce_data_prep.py            # uses the paths set in the PATHS block below
  python quickcommerce_data_prep.py --check    # also runs a DBSCAN sanity check
  Any path can still be overridden, e.g. --out-dir other_folder

Requires: numpy, pandas, openpyxl (to read the .xlsx). Optional: scikit-learn (for --check).
The orders are SIMULATED. Say so in your README.
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- PATHS
# Edit these once; the other scripts (01, 02, 03) use the same folders.
RAW_DIR = Path(r"D:\PES study\Sem2\USL\Mini projects\Datasets")
PINCODE_FILE = RAW_DIR / "BangaloreAreaLatLongDetails.xlsx"
BIGBASKET_FILE = RAW_DIR / "BigBasket Products.csv"
OUT_DIR = RAW_DIR / "processed"   # all generated files go here

# --------------------------------------------------------------------------
# PART A: simulated order coordinates
# --------------------------------------------------------------------------

# Approximate area-centre coordinates, used as the fallback when no pincode
# file is supplied or an area is not found in it. Check against your file.
# name: (lat, lon, demand_weight, spread_km)
ANCHORS = {
    "Koramangala":     (12.9352, 77.6245, 1.4, 1.3),
    "Indiranagar":     (12.9784, 77.6408, 1.3, 1.2),
    "Whitefield":      (12.9698, 77.7500, 1.3, 2.0),
    "Hebbal":          (13.0358, 77.5970, 0.9, 1.5),
    "Jayanagar":       (12.9250, 77.5938, 1.0, 1.3),
    "Rajajinagar":     (12.9910, 77.5520, 0.8, 1.2),
    "Electronic City": (12.8452, 77.6602, 0.9, 1.9),
    "HSR Layout":      (12.9116, 77.6474, 1.3, 1.3),
    "Marathahalli":    (12.9569, 77.7011, 1.0, 1.5),
    "BTM Layout":      (12.9166, 77.6101, 1.1, 1.2),
    "JP Nagar":        (12.9063, 77.5857, 0.9, 1.4),
    "Yelahanka":       (13.1007, 77.5963, 0.6, 1.8),
    "Malleshwaram":    (13.0035, 77.5647, 0.7, 1.0),
    "Banashankari":    (12.9255, 77.5468, 0.7, 1.4),
    "Bellandur":       (12.9304, 77.6784, 1.0, 1.4),
    "Sarjapur Road":   (12.9100, 77.6850, 0.8, 1.8),
}

# Alternative spellings used in the pincode file
AREA_ALIASES = {
    "Malleshwaram": ["Malleswaram"], "Marathahalli": ["Marathahally"],
    "Yelahanka": ["Yelhanka"], "JP Nagar": ["J P Nagar"],
}

# Rough bounding box for Bangalore, used for background noise and sanity checks
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 12.80, 13.14, 77.45, 77.80

# Share of orders by hour of day (peaks around breakfast and evening)
HOUR_WEIGHTS = np.array([
    0.3, 0.2, 0.1, 0.1, 0.2, 0.5, 1.2, 2.5, 3.5, 3.0, 2.2, 2.0,
    2.4, 2.6, 2.2, 2.0, 2.4, 3.2, 4.5, 5.0, 4.6, 3.5, 2.0, 0.9,
])


def _find_col(df, patterns):
    """Return the first column whose name matches any regex in patterns."""
    for p in patterns:
        for c in df.columns:
            if re.search(p, str(c), re.I):
                return c
    return None


def load_anchors(pincode_csv):
    """Start from the built-in anchors; overwrite lat/lon with the pincode
    file's values wherever an area name matches and the point is in Bangalore."""
    anchors = {k: list(v) for k, v in ANCHORS.items()}
    if not pincode_csv:
        return anchors
    if str(pincode_csv).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(pincode_csv)  # needs: pip install openpyxl
    else:
        try:
            df = pd.read_csv(pincode_csv, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(pincode_csv, encoding="latin-1")
    lat_c = _find_col(df, [r"^lat"])
    lon_c = _find_col(df, [r"^lon", r"^lng", r"^long"])
    name_c = _find_col(df, [r"area", r"locality", r"place", r"office", r"name"])
    if not (lat_c and lon_c and name_c):
        print(f"[warn] could not detect lat/lon/name columns in {pincode_csv}: "
              f"{list(df.columns)}. Using built-in anchors.")
        return anchors
    df = df[[name_c, lat_c, lon_c]].copy()
    df.columns = ["name", "lat", "lon"]
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna()
    df = df[df.lat.between(LAT_MIN, LAT_MAX) & df.lon.between(LON_MIN, LON_MAX)]
    norm = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
    df["_n"] = df["name"].map(norm)
    updated, missing = 0, []
    for area, vals in anchors.items():
        keys = [norm(re.sub(r"\s*(layout|road)$", "", area, flags=re.I))]
        keys += [norm(a) for a in AREA_ALIASES.get(area, [])]
        hit = df[df["_n"].map(lambda t: any(k and k in t for k in keys))]
        if len(hit):
            vals[0], vals[1] = float(hit.lat.mean()), float(hit.lon.mean())
            updated += 1
        else:
            missing.append(area)
    print(f"[anchors] {updated}/{len(anchors)} anchors taken from {Path(pincode_csv).name}")
    if missing:
        print(f"[anchors] not in your file, built-in coordinates used for: {', '.join(missing)}")
    return anchors


def simulate_orders(anchors, n_orders, noise_frac, start_date, days, rng):
    """Gaussian jitter around each anchor. Each area gets its own spread,
    elongation and orientation, plus uniform background noise."""
    names = list(anchors)
    weights = np.array([anchors[n][2] for n in names], dtype=float)
    weights /= weights.sum()

    n_noise = int(n_orders * noise_frac)
    n_core = n_orders - n_noise
    counts = rng.multinomial(n_core, weights)

    rows = []
    for name, cnt in zip(names, counts):
        lat0, lon0, _, spread_km = anchors[name]
        # elongated, rotated Gaussian in km space
        major = spread_km * rng.uniform(0.8, 1.2)
        minor = major * rng.uniform(0.45, 0.85)
        theta = rng.uniform(0, np.pi)
        cov = np.array([[major**2, 0], [0, minor**2]])
        rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        cov = rot @ cov @ rot.T
        xy = rng.multivariate_normal([0, 0], cov, size=cnt)  # km east, km north
        lat = lat0 + xy[:, 1] / 111.0
        lon = lon0 + xy[:, 0] / (111.0 * np.cos(np.radians(lat0)))
        rows.append(pd.DataFrame({"lat": lat, "lon": lon, "source_area": name}))

    noise = pd.DataFrame({
        "lat": rng.uniform(LAT_MIN, LAT_MAX, n_noise),
        "lon": rng.uniform(LON_MIN, LON_MAX, n_noise),
        "source_area": "noise",
    })
    df = pd.concat(rows + [noise], ignore_index=True)
    df["lat"] = df["lat"].clip(LAT_MIN, LAT_MAX)
    df["lon"] = df["lon"].clip(LON_MIN, LON_MAX)
    df = df.sample(frac=1, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)

    n = len(df)
    day = rng.integers(0, days, n)
    hour = rng.choice(24, size=n, p=HOUR_WEIGHTS / HOUR_WEIGHTS.sum())
    minute = rng.integers(0, 60, n)
    second = rng.integers(0, 60, n)
    ts = (pd.Timestamp(start_date) + pd.to_timedelta(day, unit="D")
          + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(minute, unit="m")
          + pd.to_timedelta(second, unit="s"))
    df["customer_id"] = [f"CUST{c:05d}" for c in rng.integers(1, max(2, n // 4), n)]
    df["order_timestamp"] = ts
    df["lat"] = df["lat"].round(6)
    df["lon"] = df["lon"].round(6)
    df = df.sort_values("order_timestamp").reset_index(drop=True)
    df.insert(0, "order_id", [f"ORD{i:06d}" for i in range(1, n + 1)])
    return df[["order_id", "customer_id", "order_timestamp", "lat", "lon", "source_area"]]


def check_dbscan(orders, eps_km=0.6, min_samples=30):
    """Quick sanity check: DBSCAN with haversine distance. eps must be in
    radians for the haversine metric, i.e. km / earth radius."""
    try:
        from sklearn.cluster import DBSCAN
        from sklearn.metrics import adjusted_rand_score
    except ImportError:
        print("[check] scikit-learn not installed; skipping DBSCAN check")
        return
    coords = np.radians(orders[["lat", "lon"]].to_numpy())
    labels = DBSCAN(eps=eps_km / 6371.0088, min_samples=min_samples,
                    metric="haversine", algorithm="ball_tree").fit_predict(coords)
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    print(f"[check] DBSCAN eps={eps_km} km, min_samples={min_samples}: "
          f"{n_clusters} clusters, {np.mean(labels == -1):.1%} labelled noise")
    truth = orders["source_area"].astype("category").cat.codes
    print(f"[check] adjusted Rand index vs. source_area: {adjusted_rand_score(truth, labels):.2f} "
          f"(rough guide only: neighbouring areas overlap on purpose, so tune eps/min_samples "
          f"and expect well below 1.0)")


# --------------------------------------------------------------------------
# PART B: lean SKU master catalog from the BigBasket product list
# --------------------------------------------------------------------------

# Target mix of the lean catalog. Shares are renormalised if a category is
# missing from your file.
CATEGORY_SHARES = {
    "Staples": 0.23, "Dairy & Bakery": 0.16, "Munchies & Snacks": 0.18,
    "Beverages": 0.14, "Personal Care": 0.14, "Household": 0.08,
    "Baby Care": 0.03, "Fresh": 0.02, "Eggs & Meat": 0.01, "Frozen": 0.01,
}

# Seeded profit-margin range (%) by category. Everything stays inside 10-35.
MARGIN_RANGES = {
    "Staples": (10, 16), "Dairy & Bakery": (10, 16), "Fresh": (10, 18),
    "Eggs & Meat": (10, 16), "Beverages": (14, 24), "Munchies & Snacks": (18, 28),
    "Household": (18, 28), "Baby Care": (20, 30), "Personal Care": (25, 35),
    "Frozen": (15, 25),
}

UNIT_MAP = {"g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
            "kg": "kg", "kgs": "kg",
            "ml": "ml", "l": "l", "ltr": "l", "ltrs": "l", "litre": "l", "liter": "l",
            "litres": "l", "liters": "l",
            "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs",
            "unit": "pcs", "units": "pcs"}
PACK_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kgs?|gms?|grams?|g|ml|ltrs?|litres?|liters?|l|pcs?|pieces?|units?)\b",
    re.I)


def map_category(raw):
    """Map BigBasket top-level categories (spelling varies between versions of
    the file, e.g. '&' vs 'and') to quick-commerce categories."""
    s = str(raw).lower().replace("&", "and")
    rules = [("dairy", "Dairy & Bakery"), ("bakery", "Dairy & Bakery"),
             ("snack", "Munchies & Snacks"), ("beverage", "Beverages"),
             ("foodgrain", "Staples"), ("masala", "Staples"),
             ("beauty", "Personal Care"), ("hygiene", "Personal Care"),
             ("cleaning", "Household"), ("household", "Household"),
             ("baby", "Baby Care"), ("fruit", "Fresh"), ("vegetable", "Fresh"),
             ("egg", "Eggs & Meat"), ("meat", "Eggs & Meat")]
    for key, target in rules:
        if key in s:
            return target
    return None


def parse_pack(name):
    """Return (pack_size_text, size_in_g_or_ml_or_None) from a product name.
    Uses the last 'number + unit' match, e.g. 'Amul Taaza Milk, 500 ml'."""
    matches = PACK_RE.findall(str(name))
    if not matches:
        return None, None
    val, unit = matches[-1]
    val, unit = float(val), UNIT_MAP[unit.lower()]
    base = {"g": val, "kg": val * 1000, "ml": val, "l": val * 1000}.get(unit)
    return f"{val:g} {unit}", base


def build_sku_master(bb_csv, n_skus, max_per_brand_sub, max_pack_base, seed):
    rng = np.random.default_rng(seed)
    try:
        df = pd.read_csv(bb_csv, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(bb_csv, encoding="latin-1")
    df.columns = [c.strip().lower() for c in df.columns]
    need = {"product", "category", "sale_price"}
    if not need.issubset(df.columns):
        raise SystemExit(f"BigBasket file must contain {sorted(need)}; found {list(df.columns)}")
    for col in ("sub_category", "brand", "type", "description", "market_price", "rating"):
        if col not in df.columns:
            df[col] = np.nan
    print(f"[catalog] loaded {len(df):,} rows")

    df = df.dropna(subset=["product", "category", "sale_price"])
    df["sale_price"] = pd.to_numeric(df["sale_price"], errors="coerce")
    df = df[df["sale_price"] > 0]
    df["brand"] = df["brand"].fillna("Unbranded").astype(str).str.strip()
    df["sub_category"] = df["sub_category"].fillna("General").astype(str).str.strip()
    df = df.drop_duplicates(subset=["product", "brand"])

    df["qc_category"] = df["category"].map(map_category)
    # BigBasket has no top-level Frozen category: frozen foods and ice cream sit under
    # sub-categories such as "Frozen Veggies & Snacks" and "Ice Creams & Desserts".
    is_frozen = df["sub_category"].str.contains("frozen|ice cream", case=False, na=False)
    df.loc[is_frozen, "qc_category"] = "Frozen"
    dropped = df["qc_category"].isna().sum()
    df = df.dropna(subset=["qc_category"])
    print(f"[catalog] {len(df):,} rows after cleaning ({dropped:,} dropped as non-quick-commerce categories)")

    # The real BigBasket file rarely states pack size (about 5% of rows),
    # so try the name first, then the description; leave blank otherwise.
    packs = [parse_pack(n) if parse_pack(n)[0] else parse_pack(d)
             for n, d in zip(df["product"], df["description"].fillna(""))]
    df["pack_size"] = [p[0] for p in packs]
    df["_pack_base"] = [p[1] for p in packs]
    big = df["_pack_base"].gt(max_pack_base)
    print(f"[catalog] dropping {big.sum():,} bulk packs above {max_pack_base:g} g/ml")
    df = df[~big]

    # No sales field exists, so use proxies for velocity:
    # popular brands (many listings) and well-rated products rank higher.
    df["_brand_cnt"] = df.groupby("brand")["product"].transform("count")
    rating = pd.to_numeric(df["rating"], errors="coerce")
    df["_rating"] = rating.fillna(rating.median() if rating.notna().any() else 0)
    df["_pop"] = 0.6 * df["_brand_cnt"].rank(pct=True) + 0.4 * df["_rating"].rank(pct=True)
    df["_score"] = df["_pop"] + rng.uniform(0, 0.05, len(df))

    avail = df["qc_category"].value_counts()
    shares = {c: s for c, s in CATEGORY_SHARES.items() if c in avail.index}
    total = sum(shares.values())
    targets = {c: int(round(n_skus * s / total)) for c, s in shares.items()}

    picked = []
    for cat, target in targets.items():
        pool = df[df["qc_category"] == cat].sort_values("_score", ascending=False)
        counts, chosen, rest = {}, [], []
        for idx, row in pool.iterrows():
            key = (row["sub_category"], row["brand"])
            if counts.get(key, 0) < max_per_brand_sub:
                counts[key] = counts.get(key, 0) + 1
                chosen.append(idx)
            else:
                rest.append(idx)
            if len(chosen) >= target:
                break
        if len(chosen) < target:  # relax the brand cap if the category is thin
            chosen += rest[: target - len(chosen)]
        if len(chosen) < target:
            print(f"[warn] only {len(chosen)} SKUs available for {cat} (wanted {target})")
        picked.extend(chosen)
    out = df.loc[picked].copy()

    lo_hi = out["qc_category"].map(MARGIN_RANGES)
    out["profit_margin_pct"] = [round(float(rng.uniform(lo, hi)), 1) for lo, hi in lo_hi]

    # Pareto / ABC classification inside each category: the top 20% most popular SKUs are A
    # (fast movers), the next 30% B, the rest C (long tail). Popularity is a proxy built from
    # brand listing count and rating, because the BigBasket file has no sales data.
    out["popularity_score"] = out["_pop"].round(4)
    tie = rng.uniform(0, 1e-6, len(out))
    rank_pct = (out["_pop"] + tie).groupby(out["qc_category"]).rank(pct=True, ascending=False)
    out["abc_class"] = np.where(rank_pct <= 0.20, "A", np.where(rank_pct <= 0.50, "B", "C"))

    out = out.sort_values(["qc_category", "sub_category", "brand", "product"]).reset_index(drop=True)
    out["product_id"] = [f"SKU{i:05d}" for i in range(1, len(out) + 1)]
    out = out.rename(columns={"product": "product_name", "qc_category": "category_qc",
                              "category": "category_source", "sale_price": "price"})
    out["category"] = out["category_qc"]
    cols = ["product_id", "product_name", "category", "brand", "pack_size", "price",
            "profit_margin_pct", "popularity_score", "abc_class", "sub_category", "type", "market_price", "rating",
            "description", "category_source"]
    out = out[cols]
    print(f"[catalog] final: {len(out):,} SKUs, "
          f"{out['brand'].nunique():,} brands, pack size found for {out['pack_size'].notna().mean():.0%} "
          f"(BigBasket rarely states it; blank otherwise)")
    print(out["category"].value_counts().to_string())
    print("[catalog] ABC classes:", out["abc_class"].value_counts().sort_index().to_dict())
    return out


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pincodes", default=str(PINCODE_FILE), help="Bangalore area lat/long file (.xlsx or .csv)")
    ap.add_argument("--bigbasket", default=str(BIGBASKET_FILE), help="BigBasket Products.csv")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--n-orders", type=int, default=8000, help="5000-10000 recommended")
    ap.add_argument("--noise-frac", type=float, default=0.10, help="share of uniform background orders")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--start-date", default="2026-10-19")
    ap.add_argument("--n-skus", type=int, default=2500, help="2000-3000 recommended")
    ap.add_argument("--max-per-brand-sub", type=int, default=4,
                    help="cap on SKUs per brand within a sub-category (keeps substitutes diverse)")
    ap.add_argument("--max-pack", type=float, default=5000, help="drop packs above this many g/ml")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--check", action="store_true", help="run a DBSCAN sanity check on the orders")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    if args.pincodes and not Path(args.pincodes).exists():
        print(f"[warn] area file not found: {args.pincodes} - using built-in coordinates")
        args.pincodes = None
    if args.bigbasket and not Path(args.bigbasket).exists():
        raise SystemExit(f"BigBasket file not found: {args.bigbasket}\nEdit BIGBASKET_FILE at the top of this script.")
    anchors = load_anchors(args.pincodes)
    pd.DataFrame([{"area": k, "lat": v[0], "lon": v[1], "demand_weight": v[2], "spread_km": v[3]}
                  for k, v in anchors.items()]).to_csv(out_dir / "anchors_used.csv", index=False)

    orders = simulate_orders(anchors, args.n_orders, args.noise_frac, args.start_date, args.days, rng)
    orders.to_csv(out_dir / "orders_simulated.csv", index=False)
    print(f"[orders] wrote {len(orders):,} simulated orders -> {out_dir / 'orders_simulated.csv'}")
    if args.check:
        check_dbscan(orders)

    if args.bigbasket:
        sku = build_sku_master(args.bigbasket, args.n_skus, args.max_per_brand_sub, args.max_pack, args.seed)
        sku.to_csv(out_dir / "sku_master.csv", index=False)
        print(f"[catalog] wrote {out_dir / 'sku_master.csv'}")
    else:
        print("[catalog] --bigbasket not given; skipped SKU master")


if __name__ == "__main__":
    main()
