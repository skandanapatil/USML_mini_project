#!/usr/bin/env python3
"""
MODULE 4 - Hub Rerouting, Stock-out Healing and Macro-hub Replenishment

Inputs (in DATA_DIR, produced by the earlier scripts)
  hubs_micro.csv, orders_clustered.csv, sku_master.csv, substitution_matrix.csv

HubNetwork models the micro-hub swarm with a simulated inventory per hub.

  1. PARETO INVENTORY SEEDING (ABC). Each SKU's class comes from sku_master.csv (abc_class,
     popularity_score). Starting stock is deep for A items (15-30 units), medium for B (8-15)
     and shallow for the long tail C (2-5); `par` is that full level. A small share of SKUs
     starts at zero to create stock-outs. Demand weights follow the same Pareto split
     (A = 80% of units, B = 15%, C = 5%), so fast movers come under pressure first.

  2. GRIDLOCK REROUTING (ETA-based). For a customer of a gridlocked hub, the network compares
     the estimated ride from the gridlocked hub (slowed down) with the ride from the healthy
     adjacent hub closest to the customer. It reroutes only if the neighbour is within
     --reroute-max-km AND saves at least --reroute-min-gain minutes; otherwise the order stays.
     Without this check, rerouting long distances just ties up neighbouring riders.

  3. STOCK-OUT HEALING with a CUSTOMER PREFERENCE layer ("smart defaults, opt-out"):
       pref = "substitute" (default): margin-aware substitute from the substitution matrix,
                                       then adjacent-hub sourcing, otherwise missing
       pref = "drop"                 : the customer opted out, so the item is dropped
                                       (refunded) and nothing is substituted or sourced

  4. MACRO-HUB AUTO-REPLENISHMENT. A truck from the hub's RDC is triggered when
       - a fast mover (class A or B) falls to <= 20% of its par level (BELOW_REORDER_POINT)
       - an item is requested while at zero (STOCKOUT_DEMAND, a demand signal)
       - the hub's total stock falls below 30% of its total par (HUB_LOW_STOCK)
     Requests within a few minutes share one truck wave (up to a truck capacity). A wave
     leaves after a consolidation wait and arrives after loading plus RDC-to-hub transit.
     `replenishment_log()` returns every truck: when, from which RDC, why, how many units.

  Module 5 (SLA simulation) imports HubNetwork from this file.

Run the demo
  python 04_hub_rerouting.py
  -> gridlock, stock-out, preference and replenishment scenarios; writes reroute_demo_log.csv

Use it in code
  import importlib
  m = importlib.import_module("04_hub_rerouting")
  net = m.HubNetwork(hubs, sku, subs)
  hub, action, extra_km = net.assign(lat, lon, "MH-04", t=1160)      # t = minutes since midnight
  result = net.resolve_items(hub, ["SKU00042"], pref="drop", t=1160)

Requires: numpy, pandas
"""
import argparse
import copy
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

# Same folders as quickcommerce_data_prep.py
RAW_DIR = Path(r"D:\PES study\Sem2\USL\Mini projects\Datasets")
DATA_DIR = RAW_DIR / "processed"

R_EARTH = 6371.0088

# Pareto inventory and demand split
PAR_RANGE = {"A": (15, 30), "B": (8, 15), "C": (2, 5)}      # starting / full stock per SKU per hub
DEMAND_MASS = {"A": 0.80, "B": 0.15, "C": 0.05}              # share of units demanded by class


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin((np.radians(lon2) - np.radians(lon1)) / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


def hhmm(t_min):
    """Minutes since midnight -> 'HH:MM:SS'."""
    s = int(round(t_min * 60))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


class HubNetwork:
    def __init__(self, hubs, sku, subs=None, adjacent_km=8.0, stockout_rate=0.05, seed=42,
                 replenish=True, reorder_pct=0.20, hub_low_pct=0.30, par_scale=1.0,
                 load_min=10.0, wave_min=8.0, truck_kmph=18.0, truck_capacity=1500,
                 speed_kmph=20.0, grid_factor=0.3, reroute_max_km=4.0, reroute_min_gain=3.0):
        self.hubs = hubs.reset_index(drop=True)
        self.hub_ids = self.hubs["hub_id"].tolist()
        self.hidx = {h: i for i, h in enumerate(self.hub_ids)}
        self.lat = self.hubs["lat"].to_numpy(float)
        self.lon = self.hubs["lon"].to_numpy(float)
        self.adjacent_km = adjacent_km
        self.speed_kmph, self.grid_factor = speed_kmph, grid_factor
        self.reroute_max_km, self.reroute_min_gain = reroute_max_km, reroute_min_gain
        self.D = haversine_km(self.lat[:, None], self.lon[:, None], self.lat[None, :], self.lon[None, :])

        self.sku_ids = sku["product_id"].tolist()
        self.sidx = {s: i for i, s in enumerate(self.sku_ids)}
        self.sku_name = dict(zip(sku["product_id"], sku["product_name"]))
        self.sku_cat = dict(zip(sku["product_id"], sku["category"]))
        self.subs = {}
        if subs is not None and len(subs):
            for t, g in subs.sort_values(["target_product_id", "rank"]).groupby("target_product_id"):
                self.subs[t] = g["substitute_product_id"].tolist()

        rng = np.random.default_rng(seed)
        H, S = len(self.hub_ids), len(self.sku_ids)

        # ---- ABC classes and popularity (from sku_master.csv; fallback if the columns are missing)
        if "abc_class" in sku.columns and "popularity_score" in sku.columns:
            self.abc = sku["abc_class"].to_numpy()
            pop = sku["popularity_score"].to_numpy(float)
        else:
            print("[warn] sku_master.csv has no abc_class/popularity_score; re-run quickcommerce_data_prep.py. "
                  "Using random Pareto classes instead.")
            u = rng.random(S)
            self.abc = np.where(u < 0.2, "A", np.where(u < 0.5, "B", "C"))
            pop = rng.random(S)

        # ---- Pareto demand weights: class mass split, decaying within each class by popularity rank
        w = np.zeros(S)
        for c, mass in DEMAND_MASS.items():
            idx = np.where(self.abc == c)[0]
            if len(idx) == 0:
                continue
            idx = idx[np.argsort(-pop[idx])]
            ww = 1.0 / np.arange(1, len(idx) + 1) ** 0.5
            w[idx] = ww / ww.sum() * mass
        self.sku_weights = w / w.sum()

        # ---- par levels (full stock) and starting stock per hub x SKU
        par = np.zeros((H, S), dtype=int)
        for c, (lo, hi) in PAR_RANGE.items():
            idx = np.where(self.abc == c)[0]
            par[:, idx] = rng.integers(lo, hi + 1, (H, len(idx)))
        self.par = np.maximum(1, np.rint(par * par_scale)).astype(int)
        start = np.maximum(1, np.rint(self.par * rng.uniform(0.7, 1.0, (H, S)))).astype(int)
        self.stock = np.where(rng.random((H, S)) < stockout_rate, 0, start).astype(int)
        self.rop = np.maximum(1, np.ceil(self.par * reorder_pct)).astype(int)

        # ---- replenishment settings and state
        self.replenish = replenish
        self.hub_low_pct = hub_low_pct
        self.load_min, self.wave_min, self.truck_kmph, self.truck_capacity = load_min, wave_min, truck_kmph, truck_capacity
        rdc = self.hubs["rdc_id"] if "rdc_id" in self.hubs else pd.Series(["RDC"] * H)
        dist = self.hubs["dist_to_rdc_km"] if "dist_to_rdc_km" in self.hubs else pd.Series([5.0] * H)
        self.hub_rdc = rdc.tolist()
        self.hub_lead = (load_min + dist.to_numpy(float) / truck_kmph * 60).tolist()   # minutes, depart to arrive
        self.waves, self.pending, self.open_wave, self.n_waves = [], {}, {}, 0
        self.hub_flag = {h: False for h in self.hub_ids}
        self.min_hub_pct = {h: 1.0 for h in self.hub_ids}
        self.gridlocks = {}

    def copy(self):
        """Independent copy (own inventory, trucks, gridlocks) so runs can be compared fairly."""
        return copy.deepcopy(self)

    # ---------------------------------------------------------------- gridlock state
    def gridlock(self, hub_id, start=0.0, end=float("inf")):
        self.gridlocks.setdefault(hub_id, []).append((start, end))

    def clear_gridlock(self, hub_id):
        self.gridlocks.pop(hub_id, None)

    def is_gridlocked(self, hub_id, t=0.0):
        return any(s <= t < e for s, e in self.gridlocks.get(hub_id, []))

    # ---------------------------------------------------------------- geometry
    def dist_to_hub(self, lat, lon, hub_id):
        i = self.hidx[hub_id]
        return float(haversine_km(lat, lon, self.lat[i], self.lon[i]))

    def adjacent(self, hub_id, t=0.0, k=5):
        """Healthy hubs within adjacent_km of hub_id, nearest first: [(hub_id, km)]."""
        i = self.hidx[hub_id]
        out = [(self.hub_ids[j], float(self.D[i, j])) for j in np.argsort(self.D[i])
               if j != i and self.D[i, j] <= self.adjacent_km and not self.is_gridlocked(self.hub_ids[j], t)]
        return out[:k]

    # ---------------------------------------------------------------- 2. gridlock rerouting
    def assign(self, lat, lon, home_hub, t=0.0):
        """Return (hub_id, action, extra_km).

        Actions: none (home hub healthy), rerouted_gridlock, kept_no_faster_option (a neighbour
        exists but is too far or not faster enough), no_healthy_neighbour (none within adjacent_km).
        """
        if not self.is_gridlocked(home_hub, t):
            return home_hub, "none", 0.0
        nbs = self.adjacent(home_hub, t)
        if not nbs:
            return home_hub, "no_healthy_neighbour", 0.0
        d_home = self.dist_to_hub(lat, lon, home_hub)
        stay_min = d_home / (self.speed_kmph * self.grid_factor) * 60          # slowed by the gridlock
        d, h = min((self.dist_to_hub(lat, lon, h_), h_) for h_, _ in nbs)
        go_min = d / self.speed_kmph * 60
        if d <= self.reroute_max_km and stay_min - go_min >= self.reroute_min_gain:
            return h, "rerouted_gridlock", d - d_home
        return home_hub, "kept_no_faster_option", 0.0

    def reroute_pending(self, orders, hub_id, t):
        """Reassign a table of pending orders (columns order_id, lat, lon) away from a gridlocked hub."""
        rows = []
        for o in orders.itertuples():
            h, act, extra = self.assign(o.lat, o.lon, hub_id, t)
            rows.append({"order_id": o.order_id, "from_hub": hub_id, "to_hub": h, "action": act,
                         "extra_km": round(extra, 2)})
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- 4. replenishment
    def tick(self, t):
        """Deliver every truck that has arrived by time t (minutes since midnight)."""
        for w in self.waves:
            if not w["delivered"] and w["arrive"] <= t:
                h = self.hidx[w["hub_id"]]
                for s, qty in w["lines"].items():
                    self.stock[h, s] = min(self.par[h, s], self.stock[h, s] + qty)
                    self.pending.pop((h, s), None)
                w["delivered"] = True

    def _wave_for(self, h, t, units):
        w = self.open_wave.get(h)
        if w is None or w["depart"] <= t or w["units"] + units > self.truck_capacity:
            self.n_waves += 1
            w = {"wave_id": f"TRK-{self.n_waves:03d}", "hub_id": self.hub_ids[h], "rdc_id": self.hub_rdc[h],
                 "created": t, "depart": t + self.wave_min, "arrive": t + self.wave_min + self.hub_lead[h],
                 "lines": {}, "units": 0, "triggers": Counter(), "delivered": False}
            self.waves.append(w)
            self.open_wave[h] = w
        return w

    def _request(self, h, s, t, trigger):
        if not self.replenish or (h, s) in self.pending:
            return
        qty = int(self.par[h, s] - self.stock[h, s])
        if qty <= 0:
            return
        w = self._wave_for(h, t, qty)
        w["lines"][s] = qty
        w["units"] += qty
        w["triggers"][trigger] += 1
        self.pending[(h, s)] = w

    def _hub_check(self, hub_id, t):
        """Hub-level working-capital flag: total stock below hub_low_pct of total par."""
        h = self.hidx[hub_id]
        pct = float(self.stock[h].sum() / self.par[h].sum())
        self.min_hub_pct[hub_id] = min(self.min_hub_pct[hub_id], pct)
        if pct < self.hub_low_pct and not self.hub_flag[hub_id]:
            self.hub_flag[hub_id] = True
            order = np.argsort(self.stock[h] / self.par[h])
            total = 0
            for s in order:
                if self.stock[h, s] >= 0.5 * self.par[h, s] or total >= self.truck_capacity:
                    break
                if (h, s) not in self.pending:
                    total += int(self.par[h, s] - self.stock[h, s])
                    self._request(h, s, t, "HUB_LOW_STOCK")
        elif pct >= 0.5:
            self.hub_flag[hub_id] = False

    def replenishment_log(self, mode=""):
        """One row per truck: when it was triggered, why, from which RDC, how many lines/units."""
        rows = []
        for w in self.waves:
            rows.append({"mode": mode, "wave_id": w["wave_id"], "hub_id": w["hub_id"], "rdc_id": w["rdc_id"],
                         "triggered_at": hhmm(w["created"]), "departs": hhmm(w["depart"]), "arrives": hhmm(w["arrive"]),
                         "lines": len(w["lines"]), "units": w["units"],
                         "below_reorder_point": w["triggers"]["BELOW_REORDER_POINT"],
                         "stockout_demand": w["triggers"]["STOCKOUT_DEMAND"],
                         "hub_low_stock": w["triggers"]["HUB_LOW_STOCK"], "created_min": round(w["created"], 2),
                         "arrive_min": round(w["arrive"], 2)})
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- 3. stock-out healing
    def stock_of(self, hub_id, sku_id):
        return int(self.stock[self.hidx[hub_id], self.sidx[sku_id]])

    def _take(self, hub_id, sku_id, t):
        h, s = self.hidx[hub_id], self.sidx[sku_id]
        self.stock[h, s] -= 1
        if self.abc[s] in ("A", "B") and self.stock[h, s] <= self.rop[h, s]:
            self._request(h, s, t, "BELOW_REORDER_POINT")

    def resolve_items(self, hub_id, sku_ids, heal=True, t=0.0, pref="substitute"):
        """Try to fill every item at hub_id. Inventory is decremented as items are used.

        pref: "substitute" (smart default) or "drop" (customer opted out of substitutes).
        heal=False switches the whole healing layer off (baseline): out-of-stock items are missing.
        """
        self.tick(t)
        out = {"ok": [], "substituted": [], "sourced": [], "missing": [], "dropped": []}
        h = self.hidx[hub_id]
        for sku in sku_ids:
            s = self.sidx[sku]
            if self.stock[h, s] > 0:
                self._take(hub_id, sku, t)
                out["ok"].append(sku)
                continue
            self._request(h, s, t, "STOCKOUT_DEMAND")        # demand signal to the macro hub
            if not heal:
                out["missing"].append(sku)
                continue
            if pref == "drop":
                out["dropped"].append(sku)
                continue
            sub = next((x for x in self.subs.get(sku, []) if self.stock_of(hub_id, x) > 0), None)
            if sub:
                self._take(hub_id, sub, t)
                out["substituted"].append((sku, sub))
                continue
            src = next(((nb, km) for nb, km in self.adjacent(hub_id, t) if self.stock_of(nb, sku) > 0), None)
            if src:
                self._take(src[0], sku, t)
                out["sourced"].append((sku, src[0], round(src[1], 2)))
            else:
                out["missing"].append(sku)
        if self.replenish:
            self._hub_check(hub_id, t)
        return out

    def describe(self, sku_id):
        return f"{self.sku_name.get(sku_id, sku_id)} [{sku_id}]"


# ---------------------------------------------------------------------- demo
def load_network(data, adjacent_km, stockout_rate, seed, **kw):
    hubs = pd.read_csv(data / "hubs_micro.csv")
    sku = pd.read_csv(data / "sku_master.csv")
    sp = data / "substitution_matrix.csv"
    subs = pd.read_csv(sp) if sp.exists() else None
    if subs is None:
        print("[warn] substitution_matrix.csv not found; run 02_substitution_engine.py first")
    return HubNetwork(hubs, sku, subs, adjacent_km, stockout_rate, seed, **kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--adjacent-km", type=float, default=8.0,
                    help="max distance to an adjacent hub (your sketch says 5 km; on this simulated network most hubs are 5-15 km apart)")
    ap.add_argument("--stockout-rate", type=float, default=0.05, help="share of SKUs that start out of stock per hub")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    data = Path(a.data_dir)
    net = load_network(data, a.adjacent_km, a.stockout_rate, a.seed)
    orders = pd.read_csv(data / "orders_clustered.csv")
    orders = orders[orders["served"]]
    log = []

    # ---- Pareto seeding summary
    print("=== PARETO INVENTORY SEEDING ===")
    for c in "ABC":
        m = net.abc == c
        print(f"  class {c}: {m.mean():.0%} of SKUs, {net.sku_weights[m].sum():.0%} of demand, "
              f"avg par {net.par[:, m].mean():.1f} units, avg start stock {net.stock[:, m].mean():.1f}")

    # ---- Scenario A: gridlock
    isolated = [h for h in net.hub_ids if not net.adjacent(h)]
    print(f"\n[resilience] hubs with NO healthy neighbour within {a.adjacent_km:g} km (single points of failure): "
          f"{', '.join(isolated) if isolated else 'none'}")
    counts = orders["micro_hub_id"].value_counts()
    busiest = next(h for h in counts.index if net.adjacent(h))
    pend = orders[orders["micro_hub_id"] == busiest].sort_values("dist_to_hub_km", ascending=False).head(8)
    print(f"\n=== SCENARIO A: gridlock at {busiest} ===")
    net.gridlock(busiest)
    res = net.reroute_pending(pend, busiest, t=0)
    moved = res[res["action"] == "rerouted_gridlock"]
    kept = int((res["action"] == "kept_no_faster_option").sum())
    if moved.empty:
        print(f"GRIDLOCK! {busiest}: no reroute is faster than waiting (neighbours too far or gain under "
              f"{net.reroute_min_gain:g} min)")
    else:
        targets = ", ".join(f"{h} ({n})" for h, n in moved["to_hub"].value_counts().items())
        print(f"FLASH ALERT  GRIDLOCK! {busiest} blocked: rerouting {len(moved)} orders -> {targets}"
              + (f"; {kept} kept (no faster option)" if kept else ""))
    print(res.to_string(index=False))
    for r in res.itertuples():
        log.append({"scenario": "gridlock", "order_id": r.order_id, "detail": f"{r.from_hub} -> {r.to_hub} (+{r.extra_km} km)",
                    "action": r.action})
    net.clear_gridlock(busiest)

    # ---- Scenario B: stock-out, with the customer preference layer
    hub = busiest
    target = next(s for s in net.sku_ids if len(net.subs.get(s, [])) >= 2)
    subs_of = net.subs[target]
    nb = net.adjacent(hub)
    print(f"\n=== SCENARIO B: stock-out of {net.describe(target)} at {hub} ===")

    def show(label, res_):
        print(f"\n{label}")
        for s, sub in res_["substituted"]:
            print(f"  ALERT  Stockout! Margin-optimised alternative deployed: {net.describe(sub)}")
            log.append({"scenario": label, "order_id": "", "detail": f"{s} -> {sub}", "action": "substituted"})
        for s, h_, km in res_["sourced"]:
            print(f"  ALERT  Stockout! Sourcing {net.describe(s)} from adjacent hub {h_} ({km} km)")
            log.append({"scenario": label, "order_id": "", "detail": f"{s} from {h_}", "action": "adjacent_hub_sourcing"})
        for s in res_["dropped"]:
            print(f"  NOTE   Customer preference is 'drop item': {net.describe(s)} removed and refunded")
            log.append({"scenario": label, "order_id": "", "detail": s, "action": "dropped_by_preference"})
        for s in res_["missing"]:
            print(f"  ALERT  Item unavailable network-wide: {net.describe(s)}")
            log.append({"scenario": label, "order_id": "", "detail": s, "action": "missing"})

    def fresh():
        n = net.copy()
        n.replenish = False                      # keep these small scenarios free of truck events
        n.stock[n.hidx[hub], n.sidx[target]] = 0
        return n

    n1 = fresh()
    for s in subs_of: n1.stock[n1.hidx[hub], n1.sidx[s]] = 8
    show("B1. Out of stock, substitute available (preference: substitute)", n1.resolve_items(hub, [target]))
    n1b = fresh()
    for s in subs_of: n1b.stock[n1b.hidx[hub], n1b.sidx[s]] = 8
    show("B1b. Same situation, customer preference: drop item", n1b.resolve_items(hub, [target], pref="drop"))
    n2 = fresh()
    for s in subs_of: n2.stock[n2.hidx[hub], n2.sidx[s]] = 0
    if nb: n2.stock[n2.hidx[nb[0][0]], n2.sidx[target]] = 8
    show("B2. Substitutes also out, adjacent hub has stock", n2.resolve_items(hub, [target]))
    n3 = fresh()
    n3.stock[:, n3.sidx[target]] = 0
    for s in subs_of: n3.stock[:, n3.sidx[s]] = 0
    show("B3. Out of stock everywhere", n3.resolve_items(hub, [target]))

    # ---- Scenario C: macro-hub auto-replenishment
    print(f"\n=== SCENARIO C: macro-hub replenishment at {hub} ===")
    nc = net.copy()
    h = nc.hidx[hub]
    star = int(np.argmax(np.where(nc.abc == "A", nc.sku_weights, 0)))
    sid = nc.sku_ids[star]
    nc.stock[h, star] = nc.par[h, star]
    print(f"Fast mover {nc.describe(sid)}: par {nc.par[h, star]}, reorder point {nc.rop[h, star]} (20% of par)")
    t = 19 * 60.0
    n_sold = 0
    while nc.stock[h, star] > nc.rop[h, star]:
        nc.resolve_items(hub, [sid], t=t)
        n_sold += 1
        t += 1.0
    w = nc.waves[-1]
    print(f"  after {n_sold} sales stock hit {nc.stock[h, star]} -> {w['wave_id']} triggered at {hhmm(w['created'])} "
          f"from {w['rdc_id']}: {len(w['lines'])} line(s), {w['units']} units, departs {hhmm(w['depart'])}, arrives {hhmm(w['arrive'])}")
    print(f"  (lead time {w['arrive'] - w['created']:.0f} min: stock keeps falling until the truck arrives)")
    nc.tick(w["arrive"])
    print(f"  truck arrives {hhmm(w['arrive'])}: stock restored to {nc.stock[h, star]} (par {nc.par[h, star]})")
    log.append({"scenario": "replenishment", "order_id": "", "detail": f"{w['wave_id']} {hub} {w['units']} units", "action": "truck_dispatched"})

    pd.DataFrame(log).to_csv(data / "reroute_demo_log.csv", index=False)
    print(f"\n[write] {data / 'reroute_demo_log.csv'}")


if __name__ == "__main__":
    main()
