#!/usr/bin/env python3
"""
MODULE 5 - SLA simulation: delivery tiers, micro-batching economics, healing and replenishment

Replays a busy window of the simulated orders TWICE on identical orders, carts and inventory:

  BASELINE      every order is picked and dispatched on its own rider at once; no rerouting,
                no substitution, no batching. A gridlocked hub just gets slow.
  SELF-HEALING  orders are served in three delivery tiers:
                  EXPRESS  system-flagged (carts with baby care, plus a small random share
                           of urgent orders): bypass the batch buffer, immediate single-rider
                           dispatch, standard SLA.
                  INSTANT  customer picked Option A at checkout: same dispatch as Express,
                           standard SLA, no incentive.
                  GREEN    customer picked Option B (consolidated delivery): held in the
                           3-minute micro-batch buffer, merged with neighbours within 1 km
                           (up to 3 per rider), gets a relaxed SLA (+5 min) and an incentive.
                gridlock rerouting and stock-out healing (module 4) apply to every tier.
                Customers who set "drop item if out of stock" skip substitution.

Both runs share the same inventory rules: Pareto (A/B/C) starting stock, and macro-hub
replenishment trucks (module 4). Use --surge to multiply demand (weekend rush).

The economics: courier cost per order (trip fee + per-km cost) plus the Green incentive.
`--incentive` chooses how Green customers are rewarded:
  cashback     Rs 5 to every Green order
  conditional  Rs 5 ONLY IF batch_size >= 2 AND the promised SLA was met (cost follows real savings)
  nudge        non-cash recognition (Green badge, priority support, a virtual tree), ~Rs 0.5 each
  hybrid       the nudge for every Green order, plus the cashback when batched AND on time
`--sweep` re-runs the self-healing mode at different Green adoption rates and shows the net cost
under all four incentive options. `--resilience` gridlocks each hub in turn to test how well
the hub network can cover for a failed hub (use it to compare clustering settings).

Inputs  (DATA_DIR): hubs_micro.csv, orders_clustered.csv, sku_master.csv, substitution_matrix.csv
Outputs (DATA_DIR): sla_sim_orders.csv, sla_sim_summary.csv, sla_tier_summary.csv, sla_alerts.csv,
                    replenishment_alerts.csv, sla_timeline_example.txt, green_adoption_sweep.csv (--sweep),
                    gridlock_resilience.csv (--resilience)

Usage
  python 05_sla_simulation.py
  python 05_sla_simulation.py --green-share 0.6 --surge 3 --sweep
  python 05_sla_simulation.py --incentive hybrid --resilience
  python 05_sla_simulation.py --start-hour 17 --end-hour 22 --sla-min 12

All timing, cost, share and capacity numbers are ASSUMPTIONS, not measured values. Change them
with the flags below (see --help).
Requires: numpy, pandas
"""
import argparse
import heapq
import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
hub_mod = importlib.import_module("04_hub_rerouting")
HubNetwork, haversine_km, hhmm = hub_mod.HubNetwork, hub_mod.haversine_km, hub_mod.hhmm

# Same folders as quickcommerce_data_prep.py
RAW_DIR = Path(r"D:\PES study\Sem2\USL\Mini projects\Datasets")
DATA_DIR = RAW_DIR / "processed"

EXPRESS_CATEGORIES = {"Baby Care"}      # carts containing these are flagged Express automatically


def mmss(minutes):
    s = int(round(minutes * 60))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- inputs
def load_orders(data, start_hour, end_hour, day, surge, rng):
    o = pd.read_csv(data / "orders_clustered.csv", parse_dates=["order_timestamp"])
    o = o[o["served"]].copy()
    if day:
        o = o[o["order_timestamp"].dt.date == pd.Timestamp(day).date()]
    ts = o["order_timestamp"]
    o["t"] = ts.dt.hour * 60 + ts.dt.minute + ts.dt.second / 60.0   # minutes since midnight
    lo, hi = start_hour * 60, end_hour * 60
    o = o[(o["t"] >= lo) & (o["t"] < hi)]
    o = o.rename(columns={"micro_hub_id": "home_hub"})[["order_id", "lat", "lon", "t", "home_hub"]].reset_index(drop=True)
    if surge > 1 and len(o):
        n_extra = int(round(len(o) * (surge - 1)))
        ex = o.iloc[rng.integers(0, len(o), n_extra)].copy()
        ex["order_id"] = [f"{oid}-S{i:04d}" for i, oid in enumerate(ex["order_id"], 1)]
        ex["t"] = np.clip(ex["t"] + rng.uniform(-3, 3, n_extra), lo, hi - 0.01)
        ex["lat"] += rng.normal(0, 0.0012, n_extra)
        ex["lon"] += rng.normal(0, 0.0012, n_extra)
        o = pd.concat([o, ex], ignore_index=True)
    return o.sort_values("t").reset_index(drop=True)


def prepare_orders(orders, net, cfg, rng):
    """Carts (Pareto-weighted), substitution preference, express flag and a checkout random draw."""
    n = len(orders)
    sizes = rng.integers(1, 5, n)
    carts = {}
    for oid, k in zip(orders["order_id"], sizes):
        carts[oid] = [net.sku_ids[i] for i in rng.choice(len(net.sku_ids), size=k, p=net.sku_weights)]
    orders = orders.copy()
    orders["pref"] = np.where(rng.random(n) < cfg.drop_pref_share, "drop", "substitute")
    has_express_item = [any(net.sku_cat.get(s) in EXPRESS_CATEGORIES for s in carts[o]) for o in orders["order_id"]]
    orders["express"] = np.array(has_express_item) | (rng.random(n) < cfg.express_share)
    orders["u_choice"] = rng.random(n)           # fixed draw, so --sweep changes only the Green threshold
    return orders, carts


def assign_tiers(orders, green_share):
    orders = orders.copy()
    orders["tier"] = np.where(orders["express"], "express", np.where(orders["u_choice"] < green_share, "green", "instant"))
    return orders


# ---------------------------------------------------------------- phase 1: who serves each order
def assign_phase(orders, net, carts, heal, cfg):
    rows, alerts = [], []
    for o in orders.itertuples():
        if heal:
            hub, act, extra = net.assign(o.lat, o.lon, o.home_hub, o.t)
        else:
            hub, act, extra = o.home_hub, "none", 0.0
        r = net.resolve_items(hub, carts[o.order_id], heal=heal, t=o.t, pref=o.pref)
        delay = len(r["substituted"]) * cfg.sub_delay + (cfg.sourcing_delay if r["sourced"] else 0.0)
        for s, sub in r["substituted"]:
            alerts.append((o.t, "STOCKOUT_SUBSTITUTE", hub, f"{net.describe(s)} -> margin-optimised alternative {net.describe(sub)}"))
        for s, h, km in r["sourced"]:
            alerts.append((o.t, "STOCKOUT_SOURCING", hub, f"{net.describe(s)} sourced from adjacent hub {h} ({km} km)"))
        for s in r["dropped"]:
            alerts.append((o.t, "ITEM_DROPPED_BY_PREFERENCE", hub, f"{net.describe(s)} out of stock; customer preference is drop item"))
        rows.append({"order_id": o.order_id, "t": o.t, "lat": o.lat, "lon": o.lon, "home_hub": o.home_hub,
                     "hub": hub, "reroute": act, "extra_km": round(extra, 2), "tier": o.tier, "pref": o.pref,
                     "n_items": len(carts[o.order_id]), "n_ok": len(r["ok"]), "n_substituted": len(r["substituted"]),
                     "n_sourced": len(r["sourced"]), "n_dropped": len(r["dropped"]), "n_missing": len(r["missing"]),
                     "pick_delay": delay})
    return pd.DataFrame(rows), alerts


# ---------------------------------------------------------------- phase 2: batching, riders, delivery
def simulate_hub(orders, hub_id, hub_lat, hub_lon, net, cfg, healed, n_riders):
    """orders: list of dicts sorted by arrival time t. Only GREEN orders (healed mode) are batched."""
    riders = [0.0] * n_riders
    heapq.heapify(riders)
    open_b, trips, counter = [], [], [0]

    def finalize(b):
        counter[0] += 1
        bid = f"{hub_id}-B{counter[0]:03d}"
        n_items = sum(o["n_items"] for o in b["orders"])
        delay = max(o["pick_delay"] for o in b["orders"])
        packed = b["close"] + cfg.pick_base + cfg.pick_per_item * n_items + delay
        dispatch = max(packed, heapq.heappop(riders))
        speed = cfg.speed * (cfg.grid_factor if net.is_gridlocked(hub_id, dispatch) else 1.0)
        pos, tm, km, left = (hub_lat, hub_lon), dispatch, 0.0, list(b["orders"])
        while left:
            nxt = min(left, key=lambda o: haversine_km(pos[0], pos[1], o["lat"], o["lon"]))
            d = float(haversine_km(pos[0], pos[1], nxt["lat"], nxt["lon"]))
            km += d
            tm += d / speed * 60
            nxt["delivered"] = tm
            tm += cfg.handover
            pos = (nxt["lat"], nxt["lon"])
            left.remove(nxt)
        back = float(haversine_km(pos[0], pos[1], hub_lat, hub_lon))
        km += back
        heapq.heappush(riders, tm + back / speed * 60)
        for o in b["orders"]:
            o.update(batch_id=bid, batch_size=len(b["orders"]), packed=packed, dispatch=dispatch,
                     join_events=list(b["joins"]))
        trips.append({"hub": hub_id, "batch_id": bid, "orders": len(b["orders"]), "km": km})

    for o in orders:
        t = o["t"]
        for b in sorted([b for b in open_b if b["close"] <= t], key=lambda b: b["close"]):
            finalize(b)
            open_b.remove(b)
        if not (healed and o["tier"] == "green"):          # express, instant and all baseline orders
            finalize({"close": t, "orders": [o], "joins": []})
            continue
        target = None
        for b in open_b:
            if len(b["orders"]) < cfg.max_batch and any(
                    haversine_km(o["lat"], o["lon"], x["lat"], x["lon"]) <= cfg.radius for x in b["orders"]):
                target = b
                break
        if target:
            target["orders"].append(o)
            target["joins"].append((t, o["order_id"]))
            if len(target["orders"]) >= cfg.max_batch:       # full: stop waiting
                target["close"] = t
                finalize(target)
                open_b.remove(target)
        else:
            open_b.append({"close": t + cfg.window, "orders": [o], "joins": []})
    for b in sorted(open_b, key=lambda b: b["close"]):
        finalize(b)
    return orders, trips


def rider_plan(orders, net, cfg):
    """Riders per hub. 'auto' sizes each hub's fleet for its own demand at --target-util in the
    baseline (one order per trip), so the comparison starts from a healthy network."""
    if str(cfg.riders).lower() != "auto":
        return {h: int(cfg.riders) for h in net.hub_ids}
    hours = max(cfg.end_hour - cfg.start_hour, 1)
    plan = {}
    for h in net.hub_ids:
        g = orders[orders["home_hub"] == h]
        if g.empty:
            plan[h] = 2
            continue
        km = float(haversine_km(g["lat"].to_numpy(), g["lon"].to_numpy(),
                                net.lat[net.hidx[h]], net.lon[net.hidx[h]]).mean())
        cycle = 2 * km / cfg.speed * 60 + cfg.handover          # minutes a rider is busy per order
        plan[h] = max(2, int(np.ceil(len(g) / hours / 60 * cycle / cfg.target_util)))
    return plan


def incentive_rs(df, mode, cfg):
    """Rupees paid to each order under an incentive rule (Green orders only)."""
    green = (df["tier"] == "green").to_numpy()
    earned = green & (df["batch_size"] >= 2).to_numpy() & df["sla_met"].to_numpy()   # batched AND on time
    cash, nudge = cfg.green_cashback, cfg.nudge_cost
    if mode == "cashback":
        return green * cash
    if mode == "conditional":
        return earned * cash
    if mode == "nudge":
        return green * nudge
    return green * nudge + earned * cash                                              # hybrid


def run_mode(orders, net, carts, healed, cfg, rider_map):
    assigned, alerts = assign_phase(orders, net, carts, healed, cfg)
    out, trips = [], []
    hubs = net.hubs.set_index("hub_id")
    for hub, g in assigned.groupby("hub"):
        recs = g.sort_values("t").to_dict("records")
        res, tr = simulate_hub(recs, hub, hubs.loc[hub, "lat"], hubs.loc[hub, "lon"], net, cfg, healed, rider_map[hub])
        out += res
        trips += tr
    df = pd.DataFrame(out).sort_values("t").reset_index(drop=True)
    df["total_min"] = df["delivered"] - df["t"]
    # promised SLA: standard for everyone in baseline; Green gets the relaxed window in self-healing mode
    extra = np.where((df["tier"] == "green") & healed, cfg.green_extra, 0.0)
    df["sla_limit"] = cfg.sla_min + extra
    df["sla_met"] = df["total_min"] <= df["sla_limit"]
    df["timer"] = np.where(~df["sla_met"], "RED", np.where(df["total_min"] <= 0.8 * df["sla_limit"], "GREEN", "AMBER"))
    df["mode"] = "self_healing" if healed else "baseline"
    df["incentive_rs"] = incentive_rs(df, cfg.incentive, cfg) if healed else 0.0
    return df, pd.DataFrame(trips), alerts


def summarise(df, trips, cfg, healed, net):
    items = df["n_items"].sum()
    delivered = items - df["n_missing"].sum() - df["n_dropped"].sum()
    courier = trips["trip_cost"].sum() if "trip_cost" in trips else (len(trips) * cfg.cost_per_trip + trips["km"].sum() * cfg.cost_per_km)
    incentive = float(df["incentive_rs"].sum()) if healed else 0.0
    n = len(df)
    rl = net.replenishment_log()
    return {
        "orders": n,
        "sla_met_pct": round(df["sla_met"].mean() * 100, 1),
        "met_standard_sla_pct": round((df["total_min"] <= cfg.sla_min).mean() * 100, 1),
        "timer_green_pct": round((df["timer"] == "GREEN").mean() * 100, 1),
        "avg_delivery_min": round(df["total_min"].mean(), 1),
        "p90_delivery_min": round(df["total_min"].quantile(0.9), 1),
        "rider_trips": len(trips),
        "orders_per_trip": round(n / max(len(trips), 1), 2),
        "rider_km_per_order": round(trips["km"].sum() / n, 2),
        "courier_cost_per_order": round(courier / n, 2),
        "incentive_cost_per_order": round(incentive / n, 2),
        "net_cost_per_order": round((courier + incentive) / n, 2),
        "orders_rerouted": int((df["reroute"] == "rerouted_gridlock").sum()),
        "orders_kept_at_gridlocked_hub": int(df["reroute"].isin(["kept_no_faster_option", "no_healthy_neighbour"]).sum()),
        "items_substituted": int(df["n_substituted"].sum()),
        "items_sourced_from_neighbour": int(df["n_sourced"].sum()),
        "items_dropped_by_preference": int(df["n_dropped"].sum()),
        "items_missing_unavoidable": int(df["n_missing"].sum()),
        "item_fill_rate_pct": round(delivered / items * 100, 1),
        "orders_complete_pct": round((df["n_missing"] == 0).mean() * 100, 1),
        "replenishment_trucks": int(len(rl)),
        "units_replenished": int(rl["units"].sum()) if len(rl) else 0,
        "lowest_hub_stock_pct": round(min(net.min_hub_pct.values()) * 100, 1),
    }


def add_trip_cost(trips, cfg):
    trips = trips.copy()
    trips["trip_cost"] = cfg.cost_per_trip + trips["km"] * cfg.cost_per_km
    return trips


def timeline_text(df, cfg):
    """Formatted stories of real orders from the self-healing run."""
    verdict = {"GREEN": "SLA met [GREEN]", "AMBER": "SLA met, close to the limit [AMBER]", "RED": "SLA breached [RED]"}
    parts = []
    multi = df[(df["batch_size"] >= 2) & (df["tier"] == "green")]
    if multi.empty:
        parts.append("No consolidated Green batches occurred in this run (try a larger --green-share or --surge).\n")
    else:
        first = multi.sort_values("t").iloc[0]
        batch = df[df["batch_id"] == first["batch_id"]].sort_values("t")
        o = batch.iloc[0]
        t0 = o["t"]
        L = [f"GREEN / CONSOLIDATED order {o['order_id']} at hub {o['hub']}, placed {hhmm(t0)}  "
             f"(promised SLA {o['sla_limit']:g} min = {cfg.sla_min:g} + {cfg.green_extra:g} for choosing Green; incentive rule: {cfg.incentive})", ""]
        L.append(f"[{mmss(0)}] Order placed -> enters {cfg.window:g}-min micro-batch buffer window")
        for jt, oid in o["join_events"]:
            L.append(f"[{mmss(jt - t0)}] Neighbor order {oid} detected (within {cfg.radius:g} km) -> batched into single courier payload (consolidated)")
        L.append(f"[{mmss(o['packed'] - t0)}] Picked & packed at micro-hub -> single-bag manifest finalized ({int(o['batch_size'])} orders)")
        if o["dispatch"] - o["packed"] > 0.01:
            L.append(f"[{mmss(o['dispatch'] - t0)}] Rider free and dispatched (waited {mmss(o['dispatch'] - o['packed'])} for a rider)")
        L.append(f"[{mmss(o['total_min'])}] Delivered to customer doorstep -> {verdict[o['timer']]}")
        L.append(f"        Incentive: Rs {o['incentive_rs']:g} credited ({cfg.incentive} rule; batch of {int(o['batch_size'])}, "
                 f"{'SLA met' if o['sla_met'] else 'SLA missed'})")
        parts.append("\n".join(L) + "\n")
    ex = df[df["tier"] == "express"]
    if not ex.empty:
        o = ex.sort_values("t").iloc[0]
        t0 = o["t"]
        L = [f"EXPRESS order {o['order_id']} at hub {o['hub']}, placed {hhmm(t0)}  (promised SLA {o['sla_limit']:g} min)", ""]
        L.append(f"[{mmss(0)}] Order placed -> flagged Express, bypasses the micro-batch buffer")
        L.append(f"[{mmss(o['packed'] - t0)}] Picked & packed at micro-hub -> single-order manifest")
        L.append(f"[{mmss(o['dispatch'] - t0)}] Immediate single-rider dispatch")
        L.append(f"[{mmss(o['total_min'])}] Delivered to customer doorstep -> {verdict[o['timer']]}")
        parts.append("\n".join(L) + "\n")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--start-hour", type=int, default=18, help="window start (hour of day)")
    ap.add_argument("--end-hour", type=int, default=21)
    ap.add_argument("--day", help="YYYY-MM-DD to replay a single day; default overlays all days (denser, good for demos)")
    ap.add_argument("--surge", type=float, default=1.0, help="demand multiplier for a weekend rush, e.g. 3")
    # service levels and tiers
    ap.add_argument("--sla-min", type=float, default=15.0, help="standard SLA promise (min)")
    ap.add_argument("--green-share", type=float, default=0.4, help="share of non-express customers who pick Green")
    ap.add_argument("--green-extra", type=float, default=5.0, help="extra SLA minutes granted to Green orders")
    ap.add_argument("--green-cashback", type=float, default=5.0, help="cashback per Green order (Rs)")
    ap.add_argument("--incentive", choices=["cashback", "conditional", "nudge", "hybrid"], default="conditional",
                    help="how Green customers are rewarded (see top of file)")
    ap.add_argument("--nudge-cost", type=float, default=0.5, help="cost per Green order of a badge / priority support / virtual tree (Rs)")
    ap.add_argument("--express-share", type=float, default=0.02, help="extra random share flagged Express (carts with baby care are always Express)")
    ap.add_argument("--drop-pref-share", type=float, default=0.2, help="share of customers with 'drop item if out of stock'")
    # batching and operations
    ap.add_argument("--window", type=float, default=3.0, help="micro-batch buffer window (min)")
    ap.add_argument("--radius", type=float, default=1.0, help="neighbour radius for batching (km)")
    ap.add_argument("--max-batch", type=int, default=3, help="orders per rider payload")
    ap.add_argument("--riders", default="auto", help="riders per hub: a number, or auto (sized from each hub's demand)")
    ap.add_argument("--target-util", type=float, default=0.7, help="rider utilisation used by --riders auto")
    ap.add_argument("--speed", type=float, default=20.0, help="average rider speed km/h")
    ap.add_argument("--handover", type=float, default=1.0, help="minutes at each doorstep")
    ap.add_argument("--pick-base", type=float, default=2.0)
    ap.add_argument("--pick-per-item", type=float, default=0.25)
    ap.add_argument("--sub-delay", type=float, default=0.25, help="extra minutes when an item is substituted")
    ap.add_argument("--sourcing-delay", type=float, default=4.0, help="extra minutes when an item comes from a neighbour hub")
    ap.add_argument("--grid-factor", type=float, default=0.3, help="speed multiplier at a gridlocked hub")
    ap.add_argument("--gridlock-hub", default="auto", help="hub id, 'auto' (busiest hub with a neighbour) or 'none'")
    ap.add_argument("--gridlock-start", type=float, default=75, help="minutes after the window start")
    ap.add_argument("--gridlock-minutes", type=float, default=30)
    ap.add_argument("--adjacent-km", type=float, default=8.0)
    ap.add_argument("--reroute-max-km", type=float, default=4.0, help="max customer distance to the adjacent hub when rerouting")
    ap.add_argument("--reroute-min-gain", type=float, default=3.0, help="minutes a reroute must save to be worth it")
    # costs
    ap.add_argument("--cost-per-trip", type=float, default=30.0, help="fixed courier cost per trip (Rs)")
    ap.add_argument("--cost-per-km", type=float, default=6.0, help="courier cost per km (Rs)")
    # inventory and replenishment
    ap.add_argument("--stockout-rate", type=float, default=0.05)
    ap.add_argument("--no-replenishment", action="store_true", help="turn macro-hub trucks off")
    ap.add_argument("--par-scale", type=float, default=1.0, help="scale all par levels (e.g. 0.3 for lean stores)")
    ap.add_argument("--hub-low-pct", type=float, default=0.30, help="hub-level total-stock alert threshold")
    ap.add_argument("--sweep", action="store_true", help="also sweep Green adoption 0-100%")
    ap.add_argument("--resilience", action="store_true", help="gridlock each hub in turn and measure how well neighbours cover for it")
    ap.add_argument("--seed", type=int, default=42)
    cfg = ap.parse_args()

    data = Path(cfg.data_dir)
    rng = np.random.default_rng(cfg.seed + 1)
    orders = load_orders(data, cfg.start_hour, cfg.end_hour, cfg.day, cfg.surge, rng)
    if orders.empty:
        raise SystemExit("No served orders in that window. Check --start-hour/--end-hour/--day.")
    print(f"[load] {len(orders):,} orders between {cfg.start_hour:02d}:00 and {cfg.end_hour:02d}:00 "
          f"({'day ' + cfg.day if cfg.day else 'all days overlaid'}, surge x{cfg.surge:g})")

    hubs = pd.read_csv(data / "hubs_micro.csv")
    sku = pd.read_csv(data / "sku_master.csv")
    sp = data / "substitution_matrix.csv"
    subs = pd.read_csv(sp) if sp.exists() else None
    base_net = HubNetwork(hubs, sku, subs, cfg.adjacent_km, cfg.stockout_rate, cfg.seed,
                          replenish=not cfg.no_replenishment, par_scale=cfg.par_scale, hub_low_pct=cfg.hub_low_pct,
                          speed_kmph=cfg.speed, grid_factor=cfg.grid_factor,
                          reroute_max_km=cfg.reroute_max_km, reroute_min_gain=cfg.reroute_min_gain)

    orders, carts = prepare_orders(orders, base_net, cfg, rng)
    orders = assign_tiers(orders, cfg.green_share)
    print("[tiers] " + ", ".join(f"{k} {v}" for k, v in orders["tier"].value_counts().items())
          + f"  |  drop-preference customers: {(orders['pref'] == 'drop').sum()}")

    # gridlock event
    g_hub, gs, ge = None, 0, 0
    if cfg.gridlock_hub != "none":
        g_hub = (next((h for h in orders["home_hub"].value_counts().index if base_net.adjacent(h)), None)
                 if cfg.gridlock_hub == "auto" else cfg.gridlock_hub)
        if g_hub:
            gs = cfg.start_hour * 60 + cfg.gridlock_start
            ge = gs + cfg.gridlock_minutes
            print(f"[event] gridlock at {g_hub} from {hhmm(gs)} to {hhmm(ge)}")

    rider_map = rider_plan(orders, base_net, cfg)
    print(f"[riders] per hub: {rider_map}")

    def run(healed, ords, gl_hub="default", no_reroute=False):
        gl_hub = g_hub if gl_hub == "default" else gl_hub
        net = base_net.copy()
        if no_reroute:
            net.reroute_max_km = 0.0          # rerouting switched off; everything else stays on
        if gl_hub:
            net.gridlock(gl_hub, gs, ge)
        df, trips, alerts = run_mode(ords, net, carts, healed, cfg, rider_map)
        trips = add_trip_cost(trips, cfg)
        return df, trips, alerts, net

    results, summaries, repl_logs, heal = [], {}, [], {}
    for healed in (False, True):
        df, trips, alerts, net = run(healed, orders)
        name = "self_healing" if healed else "baseline"
        summaries[name] = summarise(df, trips, cfg, healed, net)
        results.append(df)
        repl_logs.append(net.replenishment_log(name))
        if healed:
            heal = {"df": df, "alerts": alerts, "net": net}

    summary = pd.DataFrame(summaries)
    print("\n" + summary.to_string() + "\n")
    sb, sh = summaries["baseline"], summaries["self_healing"]
    print(f"Self-healing vs baseline: promised SLA met {sb['sla_met_pct']}% -> {sh['sla_met_pct']}%, "
          f"standard-SLA met {sb['met_standard_sla_pct']}% -> {sh['met_standard_sla_pct']}%, "
          f"rider trips {sb['rider_trips']} -> {sh['rider_trips']}")
    print(f"Economics per order (incentive rule: {cfg.incentive}): courier + incentive Rs {sb['net_cost_per_order']} -> Rs {sh['net_cost_per_order']} "
          f"(saving Rs {sb['net_cost_per_order'] - sh['net_cost_per_order']:.2f}); "
          f"Green customers wait longer on purpose: avg {sh['avg_delivery_min']} min vs {sb['avg_delivery_min']} min overall")

    # tier table
    base_df, heal_df = results[0], results[1]
    tier_rows = []
    for tier in ("express", "instant", "green"):
        b, h = base_df[base_df["tier"] == tier], heal_df[heal_df["tier"] == tier]
        if len(h):
            tier_rows.append({"tier": tier, "orders": len(h), "avg_min_baseline": round(b["total_min"].mean(), 1),
                              "avg_min_healing": round(h["total_min"].mean(), 1),
                              "promised_sla_min": float(h["sla_limit"].iloc[0]),
                              "sla_met_pct_healing": round(h["sla_met"].mean() * 100, 1),
                              "standard_sla_met_pct_healing": round((h["total_min"] <= cfg.sla_min).mean() * 100, 1),
                              "avg_batch_size": round(h["batch_size"].mean(), 2)})
    tier_df = pd.DataFrame(tier_rows)
    print("\nBy delivery tier (self-healing run):")
    print(tier_df.to_string(index=False))

    # optional sweep of Green adoption
    if cfg.sweep:
        rows = []
        for share in (0.0, 0.25, 0.5, 0.75, 1.0):
            o2 = assign_tiers(orders, share)
            d2, t2, _, n2 = run(True, o2)
            s2 = summarise(d2, t2, cfg, True, n2)
            courier2 = float(t2["trip_cost"].sum())
            alt = {f"net_cost_{m}": round((courier2 + float(incentive_rs(d2, m, cfg).sum())) / len(d2), 2)
                   for m in ("cashback", "conditional", "nudge", "hybrid")}
            rows.append({**alt, "green_share": share, "green_orders": int((o2["tier"] == "green").sum()),
                         "promised_sla_met_pct": s2["sla_met_pct"], "standard_sla_met_pct": s2["met_standard_sla_pct"],
                         "avg_delivery_min": s2["avg_delivery_min"], "rider_trips": s2["rider_trips"],
                         "net_cost_per_order": s2["net_cost_per_order"],
                         "saving_vs_baseline_chosen_rule": round(sb["net_cost_per_order"] - s2["net_cost_per_order"], 2)})
        sweep = pd.DataFrame(rows)
        sweep.to_csv(data / "green_adoption_sweep.csv", index=False)
        print("\nGreen adoption sweep (self-healing mode):")
        print(sweep.to_string(index=False))

    # optional resilience test: gridlock every hub in turn
    if cfg.resilience:
        rows = []
        dm = base_net.D.copy()
        np.fill_diagonal(dm, np.inf)
        for h in base_net.hub_ids:
            if (orders["home_hub"] == h).sum() == 0:
                continue
            db, _, _, _ = run(False, orders, gl_hub=h)
            dh, _, _, _ = run(True, orders, gl_hub=h)
            dn, _, _, _ = run(True, orders, gl_hub=h, no_reroute=True)
            ab, ah, an = (x[x["home_hub"] == h] for x in (db, dh, dn))
            late_b, late_h, late_n = int((~ab["sla_met"]).sum()), int((~ah["sla_met"]).sum()), int((~an["sla_met"]).sum())
            rows.append({"gridlocked_hub": h, "nearest_hub_km": round(float(dm[base_net.hidx[h]].min()), 1),
                         "hub_orders": len(ab), "rerouted": int((ah["reroute"] == "rerouted_gridlock").sum()),
                         "kept_at_hub": int(ah["reroute"].isin(["kept_no_faster_option", "no_healthy_neighbour"]).sum()),
                         "late_baseline": late_b, "late_healing_no_reroute": late_n, "late_healing": late_h,
                         "avoided_by_rerouting": late_n - late_h})
        res = pd.DataFrame(rows)
        res.to_csv(data / "gridlock_resilience.csv", index=False)
        print("\nGridlock resilience (each hub gridlocked in turn, {:g} min):".format(cfg.gridlock_minutes))
        print(res.to_string(index=False))
        print(f"Hubs where at least one order was rerouted: {(res['rerouted'] > 0).sum()} of {len(res)}; "
              f"late orders avoided by rerouting alone: {res['avoided_by_rerouting'].sum()} in total "
              f"(average {res['avoided_by_rerouting'].mean():.1f} per gridlock); "
              f"hubs with a neighbour within {cfg.reroute_max_km:g} km: {(res['nearest_hub_km'] <= cfg.reroute_max_km).sum()} of {len(res)}")

    # alerts (flash messages) from the self-healing run
    net = heal["net"]
    rows = []
    if g_hub:
        moved = heal_df[heal_df["reroute"] == "rerouted_gridlock"]
        kept = int(heal_df["reroute"].isin(["kept_no_faster_option", "no_healthy_neighbour"]).sum())
        tgt = ", ".join(f"{h} ({n})" for h, n in moved["hub"].value_counts().items()) or "none"
        rows.append((gs, "GRIDLOCK", g_hub, f"GRIDLOCK! {g_hub} blocked until {hhmm(ge)}: rerouting {len(moved)} orders -> {tgt}; "
                                             f"{kept} orders stayed (no faster option)"))
    for w in net.waves:
        why = ", ".join(f"{k.lower()} x{v}" for k, v in w["triggers"].items())
        rows.append((w["created"], "REPLENISHMENT", w["hub_id"],
                     f"Truck {w['wave_id']} from {w['rdc_id']} to {w['hub_id']}: {len(w['lines'])} lines, {w['units']} units "
                     f"({why}); departs {hhmm(w['depart'])}, arrives {hhmm(w['arrive'])}"))
    rows += sorted(heal["alerts"])[:300]
    alert_df = pd.DataFrame([{"time": hhmm(t), "type": ty, "hub": h, "message": m} for t, ty, h, m in sorted(rows)])

    out_cols = ["mode", "order_id", "tier", "pref", "home_hub", "hub", "reroute", "extra_km", "t", "batch_id", "batch_size",
                "packed", "dispatch", "delivered", "total_min", "sla_limit", "sla_met", "timer", "n_items", "n_ok",
                "n_substituted", "n_sourced", "n_dropped", "n_missing", "incentive_rs"]
    pd.concat(results)[out_cols].round(2).to_csv(data / "sla_sim_orders.csv", index=False)
    summary.to_csv(data / "sla_sim_summary.csv")
    tier_df.to_csv(data / "sla_tier_summary.csv", index=False)
    alert_df.to_csv(data / "sla_alerts.csv", index=False)
    repl = pd.concat(repl_logs, ignore_index=True)
    repl.to_csv(data / "replenishment_alerts.csv", index=False)
    tl = timeline_text(heal_df, cfg)
    (data / "sla_timeline_example.txt").write_text(tl, encoding="utf-8")

    # replenishment digest
    hr = repl[repl["mode"] == "self_healing"]
    if len(hr):
        print("\nReplenishment trucks needed (self-healing run), by hub:")
        print(hr.groupby("hub_id").agg(trucks=("wave_id", "count"), units=("units", "sum"),
                                       first_truck=("triggered_at", "min")).to_string())
    else:
        print("\nNo replenishment trucks were triggered in this window (try --surge 3 or a longer window).")
    print(f"Lowest hub-level stock reached: {sh['lowest_hub_stock_pct']}% of par "
          f"(hub alert threshold {cfg.hub_low_pct:.0%})")
    print("\n--- Example timelines (self-healing run) ---")
    print(tl)
    print(f"[write] sla_sim_orders.csv, sla_sim_summary.csv, sla_tier_summary.csv, sla_alerts.csv, "
          f"replenishment_alerts.csv, sla_timeline_example.txt -> {data}")


if __name__ == "__main__":
    main()
