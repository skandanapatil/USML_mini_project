#!/usr/bin/env python3
"""
MODULE 1 - Spatial Clustering Engine (macro-hubs / RDCs and micro-hubs)

Input : data/orders_simulated.csv   (from quickcommerce_data_prep.py)
        data/anchors_used.csv       (optional; only used to name hubs after the nearest area)

Steps
  1. Macro-hubs (RDCs): agglomerative (Ward) hierarchical clustering on city-wide
     order coordinates. K is picked by silhouette score between --k-min and --k-max,
     unless you force it with --k.
  2. Micro-hubs: DBSCAN (haversine distance) inside each RDC zone. Each dense
     pocket becomes one dark-store zone.
      Hub-capacity rule (--max-hub-orders N, default: 6.25% of all orders, i.e. 500 for 8,000
     orders; 0 turns it off): a dense pocket with more than N orders is split with K-Means
     into ceil(n / N) neighbouring hubs. This puts a second, nearby hub where
     demand is highest, so a gridlocked or overloaded hub has a close backup (module 4 reroutes
     to it) without shrinking DBSCAN's neighbourhood everywhere.
  3. Coverage: every micro-hub gets a coverage radius (90th percentile distance of
     its orders), a coverage circle at --coverage-km (default 2 km, roughly a
     5-minute ride) and a convex "demand hull". These are APPROXIMATE isochrones
     built from straight-line distance, not road-network travel time.

Outputs (in --out-dir, default data/)
  hubs_macro.csv, hubs_micro.csv, orders_clustered.csv, hubs.geojson,
  cluster_summary.json, and cluster_map.png with --plot

Usage
  python 01_spatial_clustering.py --data-dir data --plot
Requires: numpy, pandas, scikit-learn, scipy (matplotlib for --plot)
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# Same folders as quickcommerce_data_prep.py
RAW_DIR = Path(r"D:\PES study\Sem2\USL\Mini projects\Datasets")
DATA_DIR = RAW_DIR / "processed"
from scipy.spatial import ConvexHull
from sklearn.cluster import AgglomerativeClustering, DBSCAN, KMeans
from sklearn.metrics import silhouette_score

R_EARTH = 6371.0088  # km


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance in km."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


def to_km_xy(lat, lon, lat0, lon0):
    """Local flat projection in km, accurate enough at city scale."""
    x = (np.asarray(lon) - lon0) * 111.320 * np.cos(np.radians(lat0))
    y = (np.asarray(lat) - lat0) * 110.574
    return np.column_stack([x, y])


def circle_polygon(lat, lon, radius_km, n=48):
    """GeoJSON ring approximating a circle on the map."""
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    dlat = (radius_km / 110.574) * np.sin(ang)
    dlon = (radius_km / (111.320 * np.cos(np.radians(lat)))) * np.cos(ang)
    ring = [[float(lon + b), float(lat + a)] for a, b in zip(dlat, dlon)]
    return [ring + [ring[0]]]


def hull_polygon(lats, lons):
    pts = np.column_stack([lons, lats])
    if len(pts) < 3:
        return None
    try:
        h = ConvexHull(pts)
    except Exception:
        return None
    ring = [[float(x), float(y)] for x, y in pts[h.vertices]]
    return [ring + [ring[0]]], float(h.volume)


def nearest_area(lat, lon, anchors):
    if anchors is None or anchors.empty:
        return None
    d = haversine_km(lat, lon, anchors["lat"].to_numpy(), anchors["lon"].to_numpy())
    return str(anchors.iloc[int(np.argmin(d))]["area"])


# ------------------------------------------------------------------ macro hubs
def fit_macro(orders, k, k_min, k_max, hc_sample, seed):
    lat0, lon0 = orders["lat"].mean(), orders["lon"].mean()
    xy = to_km_xy(orders["lat"], orders["lon"], lat0, lon0)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(xy), size=min(hc_sample, len(xy)), replace=False)
    xs = xy[idx]

    scores = {}
    ks = [k] if k else range(k_min, k_max + 1)
    best_k, best_s = None, -2
    for kk in ks:
        lab = AgglomerativeClustering(n_clusters=kk, linkage="ward").fit_predict(xs)
        s = silhouette_score(xs, lab, sample_size=min(3000, len(xs)), random_state=seed) if len(set(lab)) > 1 else -1
        scores[kk] = round(float(s), 3)
        if s > best_s:
            best_k, best_s = kk, s
    print(f"[macro] silhouette by K: {scores} -> using K={best_k}")

    lab = AgglomerativeClustering(n_clusters=best_k, linkage="ward").fit_predict(xs)
    centroids = np.array([xs[lab == c].mean(axis=0) for c in range(best_k)])
    # assign every order to the nearest RDC centroid
    d = np.linalg.norm(xy[:, None, :] - centroids[None, :, :], axis=2)
    assign = d.argmin(axis=1)
    # rebuild centroids from all orders, then in lat/lon
    out = []
    for c in range(best_k):
        m = assign == c
        out.append((float(orders.loc[m, "lat"].mean()), float(orders.loc[m, "lon"].mean()), int(m.sum())))
    return assign, out, scores, best_k


# ------------------------------------------------------------------ micro hubs
def fit_micro(orders, rdc_assign, rdcs, eps_km, min_samples, max_hub_orders=0, seed=42):
    micro_rows, micro_label = [], np.full(len(orders), -1, dtype=int)
    next_id = 0
    for r, (rlat, rlon, _) in enumerate(rdcs):
        idx = np.where(rdc_assign == r)[0]
        pts = orders.iloc[idx]
        coords = np.radians(pts[["lat", "lon"]].to_numpy())
        lab = DBSCAN(eps=eps_km / R_EARTH, min_samples=min_samples,
                     metric="haversine", algorithm="ball_tree").fit_predict(coords)
        for c in sorted(set(lab) - {-1}):
            m = idx[lab == c]
            parts = [m]
            if max_hub_orders and len(m) > max_hub_orders:       # capacity rule: split into neighbouring hubs
                k = int(np.ceil(len(m) / max_hub_orders))
                xy = to_km_xy(orders.iloc[m]["lat"], orders.iloc[m]["lon"], orders["lat"].mean(), orders["lon"].mean())
                km_lab = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(xy)
                parts = [m[km_lab == j] for j in range(k)]
            for part in parts:
                sub = orders.iloc[part]
                micro_rows.append({"_gid": next_id, "rdc_idx": r, "lat": float(sub["lat"].mean()),
                                   "lon": float(sub["lon"].mean()), "dense_orders": len(part)})
                micro_label[part] = next_id
                next_id += 1
    return pd.DataFrame(micro_rows), micro_label


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR), help="folder with the generated files")
    ap.add_argument("--orders", help="override path to orders_simulated.csv")
    ap.add_argument("--out-dir", help="defaults to --data-dir")
    ap.add_argument("--k", type=int, help="force the number of RDCs")
    ap.add_argument("--k-min", type=int, default=3)
    ap.add_argument("--k-max", type=int, default=6)
    ap.add_argument("--hc-sample", type=int, default=5000, help="orders used to fit the hierarchy")
    ap.add_argument("--eps-km", type=float, default=0.6, help="DBSCAN neighbourhood radius")
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--max-hub-orders", type=int, default=-1,
                    help="split any dense pocket with more orders than this into neighbouring hubs "
                         "(-1 = auto: 6.25%% of all orders, 0 = off)")
    ap.add_argument("--coverage-km", type=float, default=2.0, help="micro-hub delivery radius (about 5 min)")
    ap.add_argument("--rdc-radius-km", type=float, default=5.0, help="max micro-hub distance from its RDC to flag")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args()

    data = Path(a.data_dir)
    out = Path(a.out_dir) if a.out_dir else data
    out.mkdir(parents=True, exist_ok=True)
    orders = pd.read_csv(a.orders or data / "orders_simulated.csv")
    anchors = pd.read_csv(data / "anchors_used.csv") if (data / "anchors_used.csv").exists() else None
    print(f"[load] {len(orders):,} orders")

    # 1. macro hubs
    rdc_assign, rdcs, scores, k = fit_macro(orders, a.k, a.k_min, a.k_max, a.hc_sample, a.seed)
    macro = pd.DataFrame(rdcs, columns=["lat", "lon", "n_orders"])
    macro.insert(0, "rdc_id", [f"RDC-{i + 1}" for i in range(k)])
    macro["nearest_area"] = [nearest_area(r.lat, r.lon, anchors) for r in macro.itertuples()]
    macro["share_of_orders"] = (macro["n_orders"] / len(orders)).round(3)

    # 2. micro hubs
    cap = int(np.ceil(0.0625 * len(orders))) if a.max_hub_orders < 0 else a.max_hub_orders
    if cap:
        print(f"[micro] hub capacity rule: dense pockets above {cap} orders are split into neighbouring hubs")
    micro, micro_label = fit_micro(orders, rdc_assign, rdcs, a.eps_km, a.min_samples, cap, a.seed)
    if micro.empty:
        raise SystemExit("DBSCAN found no dense pockets. Lower --min-samples or raise --eps-km.")
    micro["rdc_id"] = micro["rdc_idx"].map(lambda i: macro.loc[i, "rdc_id"])
    micro["hub_id"] = [f"MH-{i + 1:02d}" for i in range(len(micro))]
    micro["nearest_area"] = [nearest_area(r.lat, r.lon, anchors) for r in micro.itertuples()]
    micro["dist_to_rdc_km"] = [
        float(haversine_km(r.lat, r.lon, macro.loc[r.rdc_idx, "lat"], macro.loc[r.rdc_idx, "lon"]))
        for r in micro.itertuples()]
    micro["within_rdc_radius"] = micro["dist_to_rdc_km"] <= a.rdc_radius_km

    # 3. assign every order to its nearest micro-hub, test coverage
    d_all = np.column_stack([haversine_km(orders["lat"].to_numpy(), orders["lon"].to_numpy(), h.lat, h.lon)
                             for h in micro.itertuples()])
    nearest = d_all.argmin(axis=1)
    dist = d_all[np.arange(len(orders)), nearest]
    served = dist <= a.coverage_km
    orders_out = orders.copy()
    orders_out["rdc_id"] = macro["rdc_id"].to_numpy()[rdc_assign]
    orders_out["micro_hub_id"] = np.where(served, micro["hub_id"].to_numpy()[nearest], "UNSERVED")
    orders_out["dist_to_hub_km"] = dist.round(3)
    orders_out["served"] = served

    # coverage stats and geometry per micro hub
    feats, p90, area, n_served = [], [], [], []
    for i, h in enumerate(micro.itertuples()):
        m = (nearest == i) & served
        n_served.append(int(m.sum()))
        p90.append(float(np.percentile(dist[m], 90)) if m.any() else np.nan)
        hull = hull_polygon(orders.loc[m, "lat"], orders.loc[m, "lon"]) if m.sum() >= 3 else None
        area.append(hull[1] * 111.32 * 110.574 * np.cos(np.radians(h.lat)) if hull else np.nan)
        props = {"hub_id": h.hub_id, "rdc_id": h.rdc_id, "nearest_area": h.nearest_area}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [h.lon, h.lat]},
                      "properties": {**props, "kind": "micro_hub", "orders_served": n_served[-1]}})
        feats.append({"type": "Feature", "geometry": {"type": "Polygon",
                      "coordinates": circle_polygon(h.lat, h.lon, a.coverage_km)},
                      "properties": {**props, "kind": "coverage_circle", "radius_km": a.coverage_km}})
        if hull:
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": hull[0]},
                          "properties": {**props, "kind": "demand_hull"}})
    micro["orders_served"] = n_served
    micro["coverage_radius_p90_km"] = np.round(p90, 3)
    micro["demand_hull_km2"] = np.round(area, 2)
    for r in macro.itertuples():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                      "properties": {"kind": "rdc", "rdc_id": r.rdc_id, "nearest_area": r.nearest_area,
                                     "n_orders": r.n_orders}})

    micro_out = micro[["hub_id", "rdc_id", "nearest_area", "lat", "lon", "dense_orders", "orders_served",
                       "coverage_radius_p90_km", "demand_hull_km2", "dist_to_rdc_km", "within_rdc_radius"]].round(5)
    macro.round(5).to_csv(out / "hubs_macro.csv", index=False)
    micro_out.to_csv(out / "hubs_micro.csv", index=False)
    orders_out.to_csv(out / "orders_clustered.csv", index=False)
    (out / "hubs.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))

    summary = {
        "n_orders": int(len(orders)), "k_rdc": int(k), "silhouette_by_k": scores,
        "n_micro_hubs": int(len(micro)), "coverage_km": a.coverage_km,
        "pct_orders_served": round(float(served.mean()) * 100, 1),
        "micro_hubs_outside_rdc_radius": int((~micro["within_rdc_radius"]).sum()),
        "dbscan": {"eps_km": a.eps_km, "min_samples": a.min_samples},
    }
    (out / "cluster_summary.json").write_text(json.dumps(summary, indent=2))
    print(macro[["rdc_id", "nearest_area", "n_orders"]].to_string(index=False))
    print(f"[micro] {len(micro)} micro-hubs; {summary['pct_orders_served']}% of orders within "
          f"{a.coverage_km} km of a hub; {summary['micro_hubs_outside_rdc_radius']} hub(s) farther than "
          f"{a.rdc_radius_km} km from their RDC")
    print(f"[write] hubs_macro.csv, hubs_micro.csv, orders_clustered.csv, hubs.geojson -> {out}")

    if a.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("[plot] matplotlib not installed; skipping")
            return
        fig, ax = plt.subplots(figsize=(9, 9))
        ax.scatter(orders["lon"], orders["lat"], c=rdc_assign, cmap="tab10", s=3, alpha=.35)
        for h in micro.itertuples():
            ang = np.linspace(0, 2 * np.pi, 80)
            ax.plot(h.lon + a.coverage_km / (111.32 * np.cos(np.radians(h.lat))) * np.cos(ang),
                    h.lat + a.coverage_km / 110.574 * np.sin(ang), color="#0F6E56", lw=1)
            ax.scatter(h.lon, h.lat, marker="o", s=40, c="#0F6E56", edgecolor="white", zorder=3)
        ax.scatter(macro["lon"], macro["lat"], marker="s", s=140, c="#534AB7", edgecolor="white", zorder=4)
        for r in macro.itertuples():
            ax.annotate(r.rdc_id, (r.lon, r.lat), xytext=(6, 6), textcoords="offset points", fontsize=9, weight="bold")
        ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
        ax.set_title(f"{k} RDCs, {len(micro)} micro-hubs, {a.coverage_km:g} km coverage circles")
        ax.set_aspect(1 / np.cos(np.radians(orders["lat"].mean())))
        fig.savefig(out / "cluster_map.png", dpi=150, bbox_inches="tight")
        print(f"[plot] wrote {out / 'cluster_map.png'}")


if __name__ == "__main__":
    main()
