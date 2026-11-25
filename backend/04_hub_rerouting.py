#!/usr/bin/env python3
"""
MODULE 4 - Hub Network: traffic-aware rerouting, stock-out healing, replenishment, proactive P2P

Inputs (in DATA_DIR, produced by the earlier scripts)
  hubs_micro.csv, orders_clustered.csv, sku_master.csv, substitution_matrix.csv,
  colocation_pairs.csv (optional, from 03)

HubNetwork models the micro-hub swarm with a simulated inventory per hub.

  0. ROAD TIME, NOT STRAIGHT LINES. Every distance used for travel is the straight-line
     distance x a circuity factor (default 1.4, because roads are not straight). Rider
     speed depends on the time of day: 20 km/h normally, 11 km/h in the evening peak
     (18:00-20:00). A gridlocked hub multiplies the current speed by 0.55 (so about 11 km/h
     off-peak and about 6 km/h at peak); with grid_mode="cap" it only caps speed at 11 km/h. All numbers are assumptions; calibrate them with
     real travel-time data (for example a routing API) if you have it.

  1. PARETO INVENTORY SEEDING (ABC). abc_class and popularity_score come from sku_master.csv.
     Starting stock is deep for A items (15-30 units), medium for B (8-15), shallow for the
     long tail C (2-5); `par` is that full level. A few SKUs start at zero (stock-outs).
     Demand weights follow the same split (A = 80% of units, B = 15%, C = 5%).

  2. GRIDLOCK REROUTING (ETA-based). Orders of a gridlocked hub move to a healthy adjacent hub
     only if that hub is within --reroute-max-km (road km) AND saves at least
     --reroute-min-gain minutes at the current traffic speed; otherwise they stay.

  3. STOCK-OUT HEALING with a CUSTOMER PREFERENCE layer:
       pref = "substitute": margin-aware substitute, then adjacent-hub sourcing, else missing
       pref = "drop"      : the customer opted out; the item is dropped and refunded

  4. MACRO-HUB REPLENISHMENT. Trucks run on a schedule: at most one departure per hub every
     60 minutes (--truck-min-gap), and requests raised meanwhile share it. A truck is triggered by: a fast mover (A or B) at
     <= 20% of par, an item requested at zero, or hub total stock under 30% of par.
     CO-LOCATION KITTING: when a truck line is raised for an item, fast movers of co-located
     categories (from colocation_pairs.csv, e.g. Eggs & Meat with Frozen) that are below 60% of
     par are added to the same truck, because categories bought together deplete together.

  5. PROACTIVE P2P REBALANCING (predictive, demand-driven). Every --p2p-interval minutes (default
     30) the network forecasts each fast mover's demand for the next 45 minutes (a per-hub
     demand forecast spread by SKU popularity, blended with recent sales). A SKU is flagged
     BEFORE it runs dry if its stock (including stock already on the way) will not cover that
     demand, or is at <= 30% of par with real expected demand. Items nobody is expected to buy
     are left to the RDC, so the network never ships dead stock. The nearest healthy adjacent
     hubs with surplus (donors keep at least 50% of par and their own 90-minute demand) send
     enough to cover about two horizons of demand, capped at 60% of par. A van only leaves if it
     carries at least --p2p-min-units (default 50); smaller loads wait for the next check. A transfer takes
     handling time plus road time, which is faster than an RDC truck, and it cancels the RDC
     request for the same item. Compare with reactive-only behaviour using --no-proactive in
     module 5. Call set_forecast({hub_id: units_per_min}) so the first check is not blind.

  Module 5 (SLA simulation) imports HubNetwork from this file.

Run the demo
  python 04_hub_rerouting.py

Use it in code
  import importlib
  m = importlib.import_module("04_hub_rerouting")
  net = m.HubNetwork(hubs, sku, subs)
  hub, action, extra_km = net.assign(lat, lon, "MH-04", t=1160)      # t = minutes since midnight
  net.maybe_rebalance(t=1160)                                         # proactive P2P check
  result = net.resolve_items(hub, ["SKU00042"], pref="drop", t=1160)

Requires: numpy, pandas
"""
import argparse
import copy
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"

R_EARTH = 6371.0088
CIRCUITY = 1.4                                               # road km per straight-line km

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
    def __init__(self, hubs, sku, subs=None, adjacent_km=10.0, stockout_rate=0.05, seed=42,
                 replenish=True, reorder_pct=0.20, hub_low_pct=0.30, par_scale=1.0,
                 load_min=10.0, wave_min=8.0, truck_kmph=18.0, truck_capacity=1500,
                 speed_kmph=20.0, peak_kmph=11.0, peak_window=(18 * 60, 20 * 60), grid_factor=0.55, grid_mode="compound",
                 circuity=CIRCUITY, reroute_max_km=5.0, reroute_min_gain=3.0,
                 proactive=True, p2p_interval=30.0, p2p_trigger=0.30, p2p_cover_min=45.0, p2p_target=0.60,
                 p2p_donor_floor=0.50, p2p_capacity=300, p2p_handling=5.0, p2p_min_units=50, truck_min_gap=60.0,
                 coloc_pairs=None, kit_lines=5, kit_below=0.60):
        self.hubs = hubs.reset_index(drop=True)
        self.hub_ids = self.hubs["hub_id"].tolist()
        self.hidx = {h: i for i, h in enumerate(self.hub_ids)}
        self.lat = self.hubs["lat"].to_numpy(float)
        self.lon = self.hubs["lon"].to_numpy(float)

        # ---- distances and speeds (road, not straight line)
        self.circuity = circuity
        self.adjacent_km = adjacent_km                      # road km
        self.speed_kmph, self.peak_kmph, self.peak_window = speed_kmph, peak_kmph, peak_window
        self.grid_factor, self.grid_mode = grid_factor, grid_mode
        self.reroute_max_km, self.reroute_min_gain = reroute_max_km, reroute_min_gain
        self.D = haversine_km(self.lat[:, None], self.lon[:, None], self.lat[None, :], self.lon[None, :])
        self.R = self.D * circuity                           # hub-to-hub road km

        self.sku_ids = sku["product_id"].tolist()
        self.sidx = {s: i for i, s in enumerate(self.sku_ids)}
        self.sku_name = dict(zip(sku["product_id"], sku["product_name"]))
        self.sku_cat = dict(zip(sku["product_id"], sku["category"]))
        self.cat_arr = sku["category"].to_numpy()
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
        self.popularity = pop

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

        # ---- RDC replenishment settings and state
        self.replenish = replenish
        self.hub_low_pct = hub_low_pct
        self.load_min, self.wave_min, self.truck_kmph, self.truck_capacity = load_min, wave_min, truck_kmph, truck_capacity
        rdc = self.hubs["rdc_id"] if "rdc_id" in self.hubs else pd.Series(["RDC"] * H)
        dist = self.hubs["dist_to_rdc_km"] if "dist_to_rdc_km" in self.hubs else pd.Series([5.0] * H)
        self.hub_rdc = rdc.tolist()
        self.hub_rdc_km = (dist.to_numpy(float) * circuity).tolist()                              # road km RDC -> hub
        self.hub_lead = (load_min + dist.to_numpy(float) * circuity / truck_kmph * 60).tolist()   # depart -> arrive
        self.waves, self.pending, self.open_wave, self.n_waves = [], {}, {}, 0
        self.hub_flag = {h: False for h in self.hub_ids}
        self.min_hub_pct = {h: 1.0 for h in self.hub_ids}
        self.gridlocks = {}

        # ---- co-location kitting
        self.kit_lines, self.kit_below = kit_lines, kit_below
        self.partners = {}
        for a, b in (coloc_pairs or []):
            self.partners.setdefault(a, set()).add(b)
            self.partners.setdefault(b, set()).add(a)
        self.cat_idx = {c: np.where(self.cat_arr == c)[0] for c in set(self.cat_arr)}

        # ---- proactive P2P state
        self.proactive = proactive
        self.p2p_interval, self.p2p_trigger, self.p2p_cover_min = p2p_interval, p2p_trigger, p2p_cover_min
        self.p2p_target, self.p2p_donor_floor = p2p_target, p2p_donor_floor
        self.p2p_capacity, self.p2p_handling, self.p2p_min_units = p2p_capacity, p2p_handling, p2p_min_units
        self.truck_min_gap, self.last_depart = truck_min_gap, {}
        self.sales = np.zeros((H, S), dtype=np.int32)       # units sold since the last check
        self.rate = np.zeros((H, S))                          # smoothed units per minute
        self.inbound = np.zeros((H, S), dtype=int)            # P2P units on the way
        self.transfers, self.n_transfers, self.next_rebalance = [], 0, None
        self._checks = 0

    def copy(self):
        """Independent copy (own inventory, trucks, transfers, gridlocks) so runs can be compared fairly."""
        return copy.deepcopy(self)

    # ---------------------------------------------------------------- gridlock state and speed
    def gridlock(self, hub_id, start=0.0, end=float("inf")):
        self.gridlocks.setdefault(hub_id, []).append((start, end))

    def clear_gridlock(self, hub_id):
        self.gridlocks.pop(hub_id, None)

    def is_gridlocked(self, hub_id, t=0.0):
        return any(s <= t < e for s, e in self.gridlocks.get(hub_id, []))

    def base_speed(self, t):
        """km/h by time of day: slower in the evening peak."""
        tod = t % 1440
        return self.peak_kmph if self.peak_window[0] <= tod < self.peak_window[1] else self.speed_kmph

    def speed_at(self, hub_id, t):
        """Rider speed around a hub at time t (minutes since midnight): time of day, then gridlock."""
        v = self.base_speed(t)
        if not self.is_gridlocked(hub_id, t):
            return v
        # compound: gridlock slows the current speed further (about 11 km/h off-peak, about 6 km/h at peak)
        # cap: gridlock just caps speed at the peak speed (11 km/h), so it changes nothing during the peak
        return min(v, self.peak_kmph) if self.grid_mode == "cap" else v * self.grid_factor

    # ---------------------------------------------------------------- geometry (road km)
    def dist_to_hub(self, lat, lon, hub_id):
        """Straight-line km."""
        i = self.hidx[hub_id]
        return float(haversine_km(lat, lon, self.lat[i], self.lon[i]))

    def road_km(self, lat, lon, hub_id):
        return self.circuity * self.dist_to_hub(lat, lon, hub_id)

    def adjacent(self, hub_id, t=0.0, k=5):
        """Healthy hubs within adjacent_km (road) of hub_id, nearest first: [(hub_id, road_km)]."""
        i = self.hidx[hub_id]
        out = [(self.hub_ids[j], float(self.R[i, j])) for j in np.argsort(self.R[i])
               if j != i and self.R[i, j] <= self.adjacent_km and not self.is_gridlocked(self.hub_ids[j], t)]
        return out[:k]

    # ---------------------------------------------------------------- 2. gridlock rerouting
    def assign(self, lat, lon, home_hub, t=0.0):
        """Return (hub_id, action, extra_road_km).

        Actions: none (home hub healthy), rerouted_gridlock, kept_no_faster_option (a neighbour
        exists but is too far or not faster enough), no_healthy_neighbour (none within adjacent_km).
        """
        if not self.is_gridlocked(home_hub, t):
            return home_hub, "none", 0.0
        nbs = self.adjacent(home_hub, t)
        if not nbs:
            return home_hub, "no_healthy_neighbour", 0.0
        d_home = self.road_km(lat, lon, home_hub)
        stay_min = d_home / self.speed_at(home_hub, t) * 60                    # slowed by gridlock and peak
        d, h = min((self.road_km(lat, lon, h_), h_) for h_, _ in nbs)
        go_min = d / self.speed_at(h, t) * 60
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

    # ---------------------------------------------------------------- arrivals (trucks and P2P)
    def tick(self, t):
        """Deliver every truck and P2P transfer that has arrived by time t (minutes since midnight)."""
        for w in self.waves:
            if not w["delivered"] and w["arrive"] <= t:
                h = self.hidx[w["hub_id"]]
                for s, qty in w["lines"].items():
                    self.stock[h, s] = min(self.par[h, s], self.stock[h, s] + qty)
                    self.pending.pop((h, s), None)
                w["delivered"] = True
        for tr in self.transfers:
            if not tr["delivered"] and tr["arrive"] <= t:
                h = self.hidx[tr["receiver"]]
                for s, qty in tr["lines"].items():
                    self.stock[h, s] = min(self.par[h, s], self.stock[h, s] + qty)
                    self.inbound[h, s] -= qty
                tr["delivered"] = True

    # ---------------------------------------------------------------- 4. RDC replenishment
    def _wave_for(self, h, t, units):
        w = self.open_wave.get(h)
        if w is None or w["depart"] <= t or w["units"] + units > self.truck_capacity:
            self.n_waves += 1
            depart = max(t + self.wave_min, self.last_depart.get(h, -1e9) + self.truck_min_gap)   # scheduled runs
            self.last_depart[h] = depart
            w = {"wave_id": f"TRK-{self.n_waves:03d}", "hub_id": self.hub_ids[h], "rdc_id": self.hub_rdc[h],
                 "created": t, "depart": depart, "arrive": depart + self.hub_lead[h],
                 "km": self.hub_rdc_km[h], "lines": {}, "units": 0, "triggers": Counter(), "delivered": False}
            self.waves.append(w)
            self.open_wave[h] = w
        return w

    def _request(self, h, s, t, trigger, kit=True):
        if not self.replenish or (h, s) in self.pending:
            return
        if trigger != "HUB_LOW_STOCK" and self.inbound[h, s] > 0 and self.stock[h, s] + self.inbound[h, s] > self.rop[h, s]:
            return                                              # a P2P transfer is already on its way
        qty = int(self.par[h, s] - self.stock[h, s] - self.inbound[h, s])
        if qty <= 0:
            return
        w = self._wave_for(h, t, qty)
        w["lines"][s] = qty
        w["units"] += qty
        w["triggers"][trigger] += 1
        self.pending[(h, s)] = w
        if kit and trigger in ("BELOW_REORDER_POINT", "STOCKOUT_DEMAND"):
            self._kit(h, s, t)

    def _kit(self, h, s, t):
        """Co-location kitting: add low fast movers of co-located categories to the same truck."""
        for cat in self.partners.get(self.cat_arr[s], ()):
            idx = self.cat_idx.get(cat)
            if idx is None:
                continue
            low = idx[(self.abc[idx] != "C") & (self.stock[h, idx] < self.kit_below * self.par[h, idx])]
            low = low[np.argsort(self.stock[h, low] / self.par[h, low])][: self.kit_lines]
            for s2 in low:
                if (h, int(s2)) not in self.pending:
                    self._request(h, int(s2), t, "COLOCATION_KIT", kit=False)

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
                    self._request(h, s, t, "HUB_LOW_STOCK", kit=False)
        elif pct >= 0.5:
            self.hub_flag[hub_id] = False

    def replenishment_log(self, mode=""):
        """One row per truck: when it was triggered, why, from which RDC, how many lines/units."""
        rows = []
        for w in self.waves:
            rows.append({"mode": mode, "wave_id": w["wave_id"], "hub_id": w["hub_id"], "rdc_id": w["rdc_id"],
                         "triggered_at": hhmm(w["created"]), "departs": hhmm(w["depart"]), "arrives": hhmm(w["arrive"]),
                         "lines": len(w["lines"]), "units": w["units"], "road_km": round(w["km"], 1),
                         "below_reorder_point": w["triggers"]["BELOW_REORDER_POINT"],
                         "stockout_demand": w["triggers"]["STOCKOUT_DEMAND"],
                         "hub_low_stock": w["triggers"]["HUB_LOW_STOCK"],
                         "colocation_kit": w["triggers"]["COLOCATION_KIT"],
                         "created_min": round(w["created"], 2), "arrive_min": round(w["arrive"], 2)})
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- 5. proactive P2P rebalancing
    def set_forecast(self, units_per_min):
        """Seed the demand forecast: {hub_id: expected units sold per minute}, spread over SKUs by popularity."""
        for hid, v in units_per_min.items():
            self.rate[self.hidx[hid]] = v * self.sku_weights

    def maybe_rebalance(self, t):
        """Run the proactive check every p2p_interval minutes. Call this as simulated time advances."""
        if not self.proactive:
            return
        if self.next_rebalance is None:
            self.next_rebalance = t                      # first look as the shift opens
        while t >= self.next_rebalance:
            self.rebalance(self.next_rebalance)
            self.next_rebalance += self.p2p_interval

    def rebalance(self, t):
        """Move stock from adjacent surplus hubs to fast movers that are about to run short."""
        self.tick(t)
        if self._checks > 0:                                                    # learn from sales since the last check
            self.rate = 0.5 * self.rate + 0.5 * (self.sales / self.p2p_interval)
        self._checks += 1
        self.sales[:] = 0
        fast = np.isin(self.abc, ("A", "B"))
        batches = {}                                      # (donor, receiver) -> transfer dict
        for h, hid in enumerate(self.hub_ids):
            eff = self.stock[h] + self.inbound[h]
            exp = self.rate[h] * self.p2p_cover_min                             # expected units over the horizon
            projected = fast & (eff < exp)
            low_stock = fast & ~projected & (eff <= self.p2p_trigger * self.par[h]) & (exp >= 0.5)
            flagged = np.where(projected | low_stock)[0]
            if len(flagged) == 0:
                continue
            flagged = flagged[np.argsort(eff[flagged] - exp[flagged])]          # biggest shortfall first
            donors = self.adjacent(hid, t, k=4)
            for s in flagged:
                want = min(int(np.ceil(self.p2p_target * self.par[h, s])), max(int(np.ceil(2 * exp[s])), 2))
                need = want - int(eff[s])
                reason = "PROJECTED_STOCKOUT" if projected[s] else "LOW_STOCK"
                for did, km in donors:
                    if need <= 0:
                        break
                    d = self.hidx[did]
                    keep = max(int(np.ceil(self.p2p_donor_floor * self.par[d, s])),
                               int(np.ceil(self.rate[d, s] * self.p2p_cover_min * 2)))
                    give = min(need, int(self.stock[d, s]) - keep)
                    if give <= 0:
                        continue
                    b = batches.get((did, hid))
                    if b is None:
                        b = batches[(did, hid)] = {"donor": did, "receiver": hid, "km": km, "lines": {}, "units": 0,
                                                   "reasons": Counter()}
                    if b["units"] + give > self.p2p_capacity:
                        give = self.p2p_capacity - b["units"]
                        if give <= 0:
                            continue
                    b["lines"][int(s)] = b["lines"].get(int(s), 0) + give
                    b["units"] += give
                    b["reasons"][reason] += 1
                    self.stock[d, s] -= give                # leaves the donor now
                    self.inbound[h, s] += give
                    need -= give
        for (did, hid), b in list(batches.items()):
            if b["units"] < self.p2p_min_units:             # not worth a van trip: put the stock back, retry next check
                d, h = self.hidx[did], self.hidx[hid]
                for s, q in b["lines"].items():
                    self.stock[d, s] += q
                    self.inbound[h, s] -= q
                del batches[(did, hid)]
        for (did, hid), b in batches.items():
            self.n_transfers += 1
            speed = self.speed_at(did, t)
            b.update(transfer_id=f"P2P-{self.n_transfers:03d}", created=t,
                     arrive=t + self.p2p_handling + b["km"] / speed * 60, delivered=False)
            self.transfers.append(b)

    def transfer_log(self, mode=""):
        """One row per P2P transfer, with the flash message the UI would show."""
        rows = []
        for tr in self.transfers:
            sk = next(iter(tr["lines"]))
            rows.append({"mode": mode, "transfer_id": tr["transfer_id"], "donor_hub": tr["donor"],
                         "receiver_hub": tr["receiver"], "triggered_at": hhmm(tr["created"]),
                         "arrives": hhmm(tr["arrive"]), "road_km": round(tr["km"], 1), "lines": len(tr["lines"]),
                         "units": tr["units"], "low_stock_lines": tr["reasons"]["LOW_STOCK"],
                         "projected_stockout_lines": tr["reasons"]["PROJECTED_STOCKOUT"],
                         "created_min": round(tr["created"], 2),
                         "message": f"P2P transfer triggered: rebalance {len(tr['lines'])} SKUs "
                                    f"(e.g. {self.sku_ids[sk]}) from {tr['donor']} to {tr['receiver']}"})
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- 3. stock-out healing
    def stock_of(self, hub_id, sku_id):
        return int(self.stock[self.hidx[hub_id], self.sidx[sku_id]])

    def _take(self, hub_id, sku_id, t):
        h, s = self.hidx[hub_id], self.sidx[sku_id]
        self.stock[h, s] -= 1
        self.sales[h, s] += 1
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
            self.sales[h, s] += 1                              # unmet demand still counts towards the sales rate
            self._request(h, s, t, "STOCKOUT_DEMAND")          # demand signal to the macro hub
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
    cp = data / "colocation_pairs.csv"
    pairs = []
    if cp.exists():
        c = pd.read_csv(cp)
        pairs = list(zip(c.loc[c["colocate"], "category_a"], c.loc[c["colocate"], "category_b"]))
    return HubNetwork(hubs, sku, subs, adjacent_km, stockout_rate, seed, coloc_pairs=pairs, **kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--adjacent-km", type=float, default=10.0, help="max ROAD km to an adjacent hub")
    ap.add_argument("--stockout-rate", type=float, default=0.05, help="share of SKUs that start out of stock per hub")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    data = Path(a.data_dir)
    net = load_network(data, a.adjacent_km, a.stockout_rate, a.seed)
    orders = pd.read_csv(data / "orders_clustered.csv")
    orders = orders[orders["served"]]
    log = []

    print("=== ROAD TIME MODEL ===")
    print(f"  circuity {net.circuity:g}x: a 2.0 km straight line is {2 * net.circuity:.1f} road km")
    for label, t in (("17:00 (off-peak)", 17 * 60), ("19:00 (evening peak)", 19 * 60), ("21:00 (off-peak)", 21 * 60)):
        v = net.base_speed(t)
        print(f"  {label}: {v:g} km/h -> {2 * net.circuity / v * 60:.1f} min for 2 straight-line km; "
              f"gridlocked hub: {v * net.grid_factor:.1f} km/h -> {2 * net.circuity / (v * net.grid_factor) * 60:.1f} min")

    print("\n=== PARETO INVENTORY SEEDING ===")
    for c in "ABC":
        m = net.abc == c
        print(f"  class {c}: {m.mean():.0%} of SKUs, {net.sku_weights[m].sum():.0%} of demand, "
              f"avg par {net.par[:, m].mean():.1f} units, avg start stock {net.stock[:, m].mean():.1f}")

    # ---- Scenario A: gridlock
    isolated = [h for h in net.hub_ids if not net.adjacent(h)]
    print(f"\n[resilience] hubs with NO healthy neighbour within {a.adjacent_km:g} road km (single points of failure): "
          f"{', '.join(isolated) if isolated else 'none'}")
    counts = orders["micro_hub_id"].value_counts()
    busiest = next(h for h in counts.index if net.adjacent(h))
    pend = orders[orders["micro_hub_id"] == busiest].sort_values("dist_to_hub_km", ascending=False).head(8)
    t_peak = 19 * 60.0
    print(f"\n=== SCENARIO A: gridlock at {busiest} (19:00, evening peak) ===")
    net.gridlock(busiest)
    res = net.reroute_pending(pend, busiest, t=t_peak)
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
            print(f"  ALERT  Stockout! Sourcing {net.describe(s)} from adjacent hub {h_} ({km:.1f} road km)")
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
        n.proactive = False
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

    # ---- Scenario C: RDC replenishment with co-location kitting
    print(f"\n=== SCENARIO C: RDC replenishment (with co-location kitting) at {hub} ===")
    nc = net.copy()
    nc.proactive = False
    h = nc.hidx[hub]
    star = int(np.argmax(np.where(nc.abc == "A", nc.sku_weights, 0)))
    sid = nc.sku_ids[star]
    nc.stock[h, star] = nc.par[h, star]
    cat = nc.cat_arr[star]
    partner_cats = sorted(nc.partners.get(cat, ()))
    for pc in partner_cats:                                   # make the partner categories run low
        idx = nc.cat_idx[pc]
        nc.stock[h, idx] = np.maximum(1, (nc.par[h, idx] * 0.4).astype(int))
    print(f"Fast mover {nc.describe(sid)} ({cat}): par {nc.par[h, star]}, reorder point {nc.rop[h, star]} (20% of par); "
          f"co-located categories: {', '.join(partner_cats) or 'none'}")
    t = 19 * 60.0
    n_sold = 0
    while nc.stock[h, star] > nc.rop[h, star]:
        nc.resolve_items(hub, [sid], t=t)
        n_sold += 1
        t += 1.0
    w = nc.waves[-1]
    print(f"  after {n_sold} sales stock hit {nc.stock[h, star]} -> {w['wave_id']} triggered at {hhmm(w['created'])} "
          f"from {w['rdc_id']}: {len(w['lines'])} line(s), {w['units']} units "
          f"({w['triggers']['COLOCATION_KIT']} added by co-location kitting), departs {hhmm(w['depart'])}, arrives {hhmm(w['arrive'])}")
    nc.tick(w["arrive"])
    print(f"  truck arrives {hhmm(w['arrive'])}: stock restored to {nc.stock[h, star]} (par {nc.par[h, star]})")
    log.append({"scenario": "replenishment", "order_id": "", "detail": f"{w['wave_id']} {hub} {w['units']} units", "action": "truck_dispatched"})

    # ---- Scenario D: proactive P2P rebalancing
    print(f"\n=== SCENARIO D: proactive (predictive) P2P rebalancing for {hub} ===")
    nd = net.copy()
    nd.replenish = False
    nbs = nd.adjacent(hub, t=19 * 60.0)
    if not nbs:
        print("  no adjacent hub: nothing to rebalance with")
    else:
        h = nd.hidx[hub]
        donor, km = nbs[0]
        d = nd.hidx[donor]
        star = int(np.argmax(np.where(nd.abc == "A", nd.sku_weights, 0)))
        sid = nd.sku_ids[star]
        nd.stock[h, star] = max(1, int(0.25 * nd.par[h, star]))
        nd.stock[d, star] = nd.par[d, star]
        nd.set_forecast({hub: 100 * 2.5 / 60})                  # forecast: about 100 orders an hour, 2.5 items each
        print(f"  {nd.describe(sid)}: {hub} holds {nd.stock[h, star]} of {nd.par[h, star]} (25%), "
              f"{donor} ({km:.1f} road km away) holds {nd.stock[d, star]} of {nd.par[d, star]}. Nothing has stocked out yet.")
        t = 19 * 60.0
        nd.maybe_rebalance(t)
        tl = nd.transfer_log()
        mine = tl[(tl["receiver_hub"] == hub) & (tl["donor_hub"] == donor)]
        if mine.empty:
            print("  no transfer was needed")
        else:
            r = mine.iloc[0]
            print(f"  FLASH ALERT  {r['message']}; {r['units']} units, arrives {r['arrives']} "
                  f"(RDC truck lead time here would be {net.hub_lead[h]:.0f} min)")
            log.append({"scenario": "proactive_p2p", "order_id": "", "detail": f"{donor} -> {hub} {r['units']} units",
                        "action": "p2p_transfer"})
        nd.tick(t + 120)
        print(f"  after the transfer: {hub} holds {nd.stock[h, star]} of {nd.par[h, star]}, {donor} holds {nd.stock[d, star]}")

    pd.DataFrame(log).to_csv(data / "reroute_demo_log.csv", index=False)
    print(f"\n[write] {data / 'reroute_demo_log.csv'}")


if __name__ == "__main__":
    main()
