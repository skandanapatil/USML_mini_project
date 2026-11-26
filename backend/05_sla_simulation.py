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

Demand realism: each customer cart is drawn from a REAL Instacart basket (baskets_sample.csv from
step 3): a random subset of the basket's aisles, each turned into a catalog SKU (Pareto-weighted
inside the aisle, or inside the category when no aisle bridge exists). So co-purchase patterns,
co-location badges and recipe add-ons all come from real shopping behaviour. Use
--basket-source pareto for independent random carts.

Pricing and contribution margin (all fee levels are assumptions):
  every order pays a base delivery fee; Instant orders also pay a TRAFFIC fee when the rider speed at
  their hub is under 15 km/h (evening peak or a gridlock) and a SURGE fee that grows with the demand
  multiplier (--surge). Express orders (baby care and urgent) are EXEMPT from surcharges, and Green
  orders never pay them. Price-sensitive Instant customers SWITCH to Green with a probability that
  rises with the surcharge (--switch-elasticity), which relieves riders. The baseline charges the same
  surcharges but has no Green tier, so nobody can switch.
  contribution per order = product gross margin + delivery fee - (courier + incentive + RDC trucks + P2P vans).
  Orders delivered later than --cancel-min (30) are treated as cancelled: no fee, no product margin,
  but the courier cost is still paid. Use --no-surcharge to rerun without surcharges and switching.

Stress tests: the rider fleet is sized ONCE for normal demand and normal speeds (--fleet-basis nominal),
so --surge and --peak-speed genuinely overload it. Rider supply flexes part of the way
(--fleet-flex 0.5: a 2.5x surge brings 1.75x the riders). (--fleet-basis actual re-sizes the fleet for
whatever demand and speed you ask for, which hides the stress.)

Road realism: travel uses road distance (straight line x 1.4) and a time-of-day speed
(20 km/h, 11 km/h in the 18:00-20:00 peak, x0.55 more at a gridlocked hub).

Self-healing mode also switches on (each can be turned off):
  SLA-AWARE BATCHING  an order only joins a batch if every stop is still predicted to arrive
                    inside its promised SLA at the current traffic speed (--no-sla-aware-batching
                    turns the check off); in slow evening traffic fewer batches form
  PROACTIVE P2P     every 30 min, fast-depleting A/B items are topped up from adjacent surplus
                    hubs BEFORE they run out, based on a demand forecast (--no-proactive turns it off; the run
                    "healing_reactive_only" in the summary shows the effect)
  RECIPE ADD-ONS    the Apriori recipe-completion rules (step 3) suggest a missing item
                    ("Missing cheese? Add with 1-click"); a share of customers accept (--addon-accept)
  CO-LOCATION       multi-category carts that meet a co-location rule get a badge; the same
                    rules make RDC trucks carry co-located categories together (module 4)

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
                    replenishment_alerts.csv, p2p_transfers.csv, sla_timeline_example.txt, green_adoption_sweep.csv (--sweep),
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
RAW_DIR = Path(r"E:\USML_mini_project\dataset")
DATA_DIR = Path(r"E:\USML_mini_project\backend\data")

EXPRESS_CATEGORIES = {"Baby Care"}      # carts containing these are flagged Express automatically

# Bridge from BigBasket sub-categories to the Instacart aisles the Apriori rules are written in.
# Best-effort and keyword-based; SKUs with no sensible aisle (cleaners, stationery...) stay unmapped.
SUB_TO_AISLE = {
    "Breads & Buns": "bread", "Gourmet Breads": "bread", "Bakery Snacks": "breakfast bakery",
    "Cakes & Pastries": "cookies cakes", "Biscuits & Cookies": "cookies cakes", "Cookies, Rusk & Khari": "crackers",
    "Eggs": "eggs", "Fresh Vegetables": "fresh vegetables", "Organic Fruits & Vegetables": "fresh vegetables",
    "Cuts & Sprouts": "fresh vegetables", "Fresh Fruits": "fresh fruits", "Exotic Fruits & Veggies": "fresh fruits",
    "Masalas & Spices": "spices seasonings", "Herbs & Seasonings": "spices seasonings",
    "Edible Oils & Ghee": "oils vinegars", "Noodle, Pasta, Vermicelli": "dry pasta", "Tea": "tea", "Coffee": "coffee",
    "Breakfast Cereals": "cereal", "Snacks & Namkeen": "chips pretzels", "Chocolates & Candies": "candy chocolate",
    "Energy & Soft Drinks": "soft drinks", "Fruit Juices & Drinks": "juice nectars",
    "Water": "water seltzer sparkling water", "Non Dairy": "soy lactosefree",
    "Rice & Rice Products": "grains rice dried goods", "Dals & Pulses": "grains rice dried goods",
    "Dry Fruits": "nuts seeds dried fruit", "Ice Creams & Desserts": "ice cream ice",
    "Fish & Seafood": "meat counter", "Mutton & Lamb": "meat counter", "Sausages, Bacon & Salami": "hot dogs bacon sausage",
    "Ready To Cook & Eat": "instant foods", "Pickles & Chutney": "pickled goods olives",
    "Baby Food & Formula": "baby food formula", "Salt, Sugar & Jaggery": "baking ingredients",
    "Atta, Flours & Sooji": "baking ingredients",
}


def map_to_aisle(sub_category, name):
    """Best-guess Instacart aisle for a BigBasket SKU (None if there is no sensible match)."""
    n = str(name).lower()
    if sub_category == "Dairy":
        for key, aisle in (("cheese", "packaged cheese"), ("butter", "butter"), ("curd", "yogurt"), ("yogurt", "yogurt"),
                           ("yoghurt", "yogurt"), ("dahi", "yogurt"), ("cream", "cream")):
            if key in n:
                return aisle
        return "milk"
    if sub_category == "Spreads, Sauces, Ketchup":
        if "pasta" in n:
            return "pasta sauce"
        return "spreads" if any(k in n for k in ("jam", "spread", "peanut butter", "honey")) else "condiments"
    if sub_category == "Frozen Veggies & Snacks":
        return "frozen produce" if any(k in n for k in ("pea", "corn", "veg", "bean")) else "frozen appetizers sides"
    return SUB_TO_AISLE.get(sub_category)


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


def load_basket_pool(data):
    """Real Instacart baskets as lists of (aisle, category); None if step 3 has not produced them."""
    f = data / "baskets_sample.csv"
    if not f.exists():
        return None
    pool = []
    for row in pd.read_csv(f)["items"]:
        pool.append([tuple(x.split(":", 1)) for x in str(row).split("|") if ":" in x])
    return [b for b in pool if b]


def prepare_orders(orders, net, cfg, rng, pool=None, aisle_idx=None):
    """Carts, substitution preference, express flag and checkout random draws."""
    n = len(orders)
    sizes = rng.integers(1, 5, n)
    carts = {}
    for oid, k in zip(orders["order_id"], sizes):
        cart = []
        if pool:                                       # a random subset of a real basket
            basket = pool[rng.integers(len(pool))]
            for j in rng.choice(len(basket), size=min(int(k), len(basket)), replace=False):
                aisle, cat = basket[j]
                idx = aisle_idx.get(aisle)
                if idx is None or len(idx) == 0:
                    idx = net.cat_idx.get(cat)
                if idx is None or len(idx) == 0:
                    continue
                w = net.sku_weights[idx]
                cart.append(net.sku_ids[int(rng.choice(idx, p=w / w.sum()))])
        if not cart:                                   # independent Pareto draws
            cart = [net.sku_ids[i] for i in rng.choice(len(net.sku_ids), size=k, p=net.sku_weights)]
        carts[oid] = cart
    orders = orders.copy()
    orders["pref"] = np.where(rng.random(n) < cfg.drop_pref_share, "drop", "substitute")
    has_express_item = [any(net.sku_cat.get(s) in EXPRESS_CATEGORIES for s in carts[o]) for o in orders["order_id"]]
    orders["express"] = np.array(has_express_item) | (rng.random(n) < cfg.express_share)
    orders["u_choice"] = rng.random(n)           # fixed draw, so --sweep changes only the Green threshold
    orders["u_addon"] = rng.random(n)            # fixed draw for accepting a recipe add-on
    orders["u_switch"] = rng.random(n)           # fixed draw for switching to Green when surcharges bite
    return orders, carts


def assign_tiers(orders, green_share, elasticity=0.0):
    """express > green > instant. Green = chose Green at checkout, or switched because of the surcharge."""
    o = orders.copy()
    chose = (o["u_choice"] < green_share).to_numpy()
    switch = ((o["u_switch"] < np.minimum(1.0, elasticity * o["surcharge"])).to_numpy()) if elasticity > 0 else np.zeros(len(o), bool)
    ex = o["express"].to_numpy()
    o["tier"] = np.where(ex, "express", np.where(chose | switch, "green", "instant"))
    o["switched"] = switch & ~chose & ~ex
    return o


def compute_surcharge(orders, net, cfg, g_hub, gs, ge):
    """Traffic fee (slow riders at the order's hub) + surge fee (demand multiplier). Instant orders pay it."""
    if cfg.no_surcharge:
        return np.zeros(len(orders))
    tmp = net.copy()
    if g_hub:
        tmp.gridlock(g_hub, gs, ge)
    speed = np.array([tmp.speed_at(h, t) for h, t in zip(orders["home_hub"], orders["t"])])
    traffic = np.where(speed < cfg.traffic_threshold, cfg.traffic_fee, 0.0)
    surge = min(cfg.surge_fee_per_x * max(cfg.surge - 1.0, 0.0), cfg.surge_fee_cap)
    return traffic + surge


# ---------------------------------------------------------------- phase 1: who serves each order
def assign_phase(orders, net, carts, heal, cfg, ctx):
    rows, alerts = [], []
    for o in orders.itertuples():
        if heal:
            net.maybe_rebalance(o.t)                       # proactive P2P check (no-op if switched off)
            hub, act, extra = net.assign(o.lat, o.lon, o.home_hub, o.t)
        else:
            hub, act, extra = o.home_hub, "none", 0.0
        cart = carts[o.order_id]
        r = net.resolve_items(hub, cart, heal=heal, t=o.t, pref=o.pref)
        delay = len(r["substituted"]) * cfg.sub_delay + (cfg.sourcing_delay if r["sourced"] else 0.0)
        for s_, sub in r["substituted"]:
            alerts.append((o.t, "STOCKOUT_SUBSTITUTE", hub, f"{net.describe(s_)} -> margin-optimised alternative {net.describe(sub)}"))
        for s_, h, km in r["sourced"]:
            alerts.append((o.t, "STOCKOUT_SOURCING", hub, f"{net.describe(s_)} sourced from adjacent hub {h} ({km} road km)"))
        for s_ in r["dropped"]:
            alerts.append((o.t, "ITEM_DROPPED_BY_PREFERENCE", hub, f"{net.describe(s_)} out of stock; customer preference is drop item"))

        # co-location badge: the cart spans categories that are bought together (rules from step 3)
        badge = bool(ctx["cl"] and ctx["cl"].meets_criteria({net.sku_cat[s_] for s_ in cart}))

        # recipe-completion add-on (self-healing mode only): "Missing cheese? Add with 1-click"
        add = {"offered": False, "accepted": False, "sku": "", "revenue": 0.0, "margin": 0.0, "blocked": 0}
        if heal and ctx["rc"] is not None and cfg.addon_accept > 0:
            kept = r["ok"] + [sub for _, sub in r["substituted"]]
            have = {ctx["aisle_of"][s_] for s_ in kept if ctx["aisle_of"].get(s_)}
            if have:
                for sg in ctx["rc"].suggest(have, top_n=3):
                    cands = ctx["aisle_skus"].get(sg["missing_item"], [])
                    if not cands:
                        continue                              # no SKU maps to that aisle
                    pick = next((c for c in cands if net.stock_of(hub, c) > 0), None)
                    if pick is None:
                        add["blocked"] += 1                   # suggestion suppressed: nothing in stock
                        continue
                    add["offered"] = True
                    if o.u_addon < cfg.addon_accept:
                        net.resolve_items(hub, [pick], heal=False, t=o.t)
                        price = ctx["price"][pick]
                        add.update(accepted=True, sku=pick, revenue=price, margin=price * ctx["margin"][pick] / 100.0)
                        alerts.append((o.t, "ADDON_ACCEPTED", hub,
                                       f"Missing {sg['missing_item']}? 1-click add of {net.describe(pick)} (suggested because of {sg['because']})"))
                    break
        delivered = r["ok"] + [sub for _, sub in r["substituted"]] + [x for x, _, _ in r["sourced"]] + ([add["sku"]] if add["accepted"] else [])
        value = sum(ctx["price"][x] for x in delivered)
        gross = sum(ctx["price"][x] * ctx["margin"][x] / 100.0 for x in delivered)
        n_items = len(cart) + (1 if add["accepted"] else 0)
        n_ok = len(r["ok"]) + (1 if add["accepted"] else 0)
        rows.append({"order_id": o.order_id, "t": o.t, "lat": o.lat, "lon": o.lon, "home_hub": o.home_hub,
                     "hub": hub, "reroute": act, "extra_km": round(extra, 2), "tier": o.tier, "pref": o.pref,
                     "n_items": n_items, "n_ok": n_ok, "n_substituted": len(r["substituted"]),
                     "n_sourced": len(r["sourced"]), "n_dropped": len(r["dropped"]), "n_missing": len(r["missing"]),
                     "pick_delay": delay, "coloc_badge": badge, "addon_offered": add["offered"],
                     "addon_accepted": add["accepted"], "addon_sku": add["sku"], "addon_revenue": add["revenue"],
                     "addon_margin": add["margin"], "addon_blocked": add["blocked"],
                     "basket_value": value, "basket_margin": gross, "surcharge": o.surcharge, "switched": o.switched})
    return pd.DataFrame(rows), alerts


# ---------------------------------------------------------------- phase 2: batching, riders, delivery
def simulate_hub(orders, hub_id, hub_lat, hub_lon, net, cfg, healed, n_riders):
    """orders: list of dicts sorted by arrival time t. Only GREEN orders (healed mode) are batched."""
    riders = [0.0] * n_riders
    heapq.heapify(riders)
    open_b, trips, counter = [], [], [0]

    def route(ords, t0):
        """Nearest-neighbour delivery route from the hub starting at t0, at the traffic speed of each moment.
        Returns ({order_id: delivered_time}, road_km, time_back_at_hub)."""
        pos, tm, km, left, done = (hub_lat, hub_lon), t0, 0.0, list(ords), {}
        while left:
            nxt = min(left, key=lambda o: haversine_km(pos[0], pos[1], o["lat"], o["lon"]))
            d = float(haversine_km(pos[0], pos[1], nxt["lat"], nxt["lon"])) * net.circuity     # road km
            km += d
            tm += d / net.speed_at(hub_id, tm) * 60
            done[nxt["order_id"]] = tm
            tm += cfg.handover
            pos = (nxt["lat"], nxt["lon"])
            left.remove(nxt)
        back = float(haversine_km(pos[0], pos[1], hub_lat, hub_lon)) * net.circuity
        km += back
        return done, km, tm + back / net.speed_at(hub_id, tm) * 60

    def packed_time(ords, close):
        return close + cfg.pick_base + cfg.pick_per_item * sum(o["n_items"] for o in ords) \
               + max(o["pick_delay"] for o in ords)

    def feasible(b, new):
        """Would every stop still arrive inside its promised SLA if `new` joined batch b?"""
        ords = b["orders"] + [new]
        done, _, _ = route(ords, packed_time(ords, b["close"]))
        limit = cfg.sla_min + cfg.green_extra - cfg.batch_safety_min
        return all(done[o["order_id"]] - o["t"] <= limit for o in ords)

    def finalize(b):
        counter[0] += 1
        bid = f"{hub_id}-B{counter[0]:03d}"
        packed = packed_time(b["orders"], b["close"])
        dispatch = max(packed, heapq.heappop(riders))
        done, km, back_at = route(b["orders"], dispatch)
        heapq.heappush(riders, back_at)
        for o in b["orders"]:
            o.update(delivered=done[o["order_id"]], batch_id=bid, batch_size=len(b["orders"]), packed=packed,
                     dispatch=dispatch, join_events=list(b["joins"]))
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
                if cfg.no_sla_aware_batching or feasible(b, o):
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


NOMINAL_SPEED, NOMINAL_PEAK_SPEED = 20.0, 11.0


def rider_plan(orders, net, cfg):
    """Riders per hub, sized at --target-util for one order per trip. With --fleet-basis nominal (default) the
    fleet is sized for NORMAL demand (the surge multiplier removed) and NORMAL speeds, so a surge or slow
    traffic then overloads it, as it would in real life."""
    if str(cfg.riders).lower() != "auto":
        return {h: int(cfg.riders) for h in net.hub_ids}
    hours = max(cfg.end_hour - cfg.start_hour, 1)
    nominal = cfg.fleet_basis == "nominal"
    pk0, pk1 = cfg.peak_start * 60, cfg.peak_end * 60
    speed_at_t = (lambda t: NOMINAL_PEAK_SPEED if pk0 <= t % 1440 < pk1 else NOMINAL_SPEED) if nominal else net.base_speed
    v = float(np.mean([speed_at_t(t) for t in np.arange(cfg.start_hour * 60, cfg.end_hour * 60, 10)]))
    # nominal fleet: sized for normal demand, then flexed up part of the way (festival rider incentives):
    # a surge of x gives 1 + fleet_flex*(x-1) times the riders, so a 2.5x surge with flex 0.5 gives 1.75x riders
    scale = cfg.surge / (1.0 + cfg.fleet_flex * (cfg.surge - 1.0)) if nominal else 1.0
    plan = {}
    for h in net.hub_ids:
        g = orders[orders["home_hub"] == h]
        if g.empty:
            plan[h] = 2
            continue
        km = float(haversine_km(g["lat"].to_numpy(), g["lon"].to_numpy(),
                                net.lat[net.hidx[h]], net.lon[net.hidx[h]]).mean()) * net.circuity
        cycle = 2 * km / v * 60 + cfg.handover                  # minutes a rider is busy per order
        plan[h] = max(2, int(np.ceil(len(g) / scale / hours / 60 * cycle / cfg.target_util)))
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


def run_mode(orders, net, carts, healed, cfg, rider_map, ctx):
    assigned, alerts = assign_phase(orders, net, carts, healed, cfg, ctx)
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
    pays_surcharge = (df["tier"] == "instant") if healed else (df["tier"] != "express")
    df["delivery_fee"] = cfg.base_fee + np.where(pays_surcharge, df["surcharge"], 0.0)
    df["cancelled"] = df["total_min"] > cfg.cancel_min              # far too late: the customer cancels, no revenue
    df.loc[df["cancelled"], ["delivery_fee", "basket_margin", "basket_value"]] = 0.0
    return df, pd.DataFrame(trips), alerts


def summarise(df, trips, cfg, healed, net):
    items = df["n_items"].sum()
    delivered = items - df["n_missing"].sum() - df["n_dropped"].sum()
    courier = trips["trip_cost"].sum() if "trip_cost" in trips else (len(trips) * cfg.cost_per_trip + trips["km"].sum() * cfg.cost_per_km)
    incentive = float(df["incentive_rs"].sum()) if healed else 0.0
    n = len(df)
    rl = net.replenishment_log()
    tl = net.transfer_log()
    if len(tl):                                     # opposite-direction transfers between the same two hubs share one van round trip
        tl = tl.assign(pair=[tuple(sorted(x)) for x in zip(tl["donor_hub"], tl["receiver_hub"])])
        vans = tl.drop_duplicates(["created_min", "pair"])
        p2p_cost = len(vans) * cfg.van_cost_per_trip + 2 * vans["road_km"].sum() * cfg.van_cost_per_km
    else:
        p2p_cost = 0.0
    repl_cost = (len(rl) * cfg.truck_cost_per_trip + 2 * rl["road_km"].sum() * cfg.truck_cost_per_km) if len(rl) else 0.0
    badge = df[df["coloc_badge"]]
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
        "p2p_van_trips": int(len(vans)) if len(tl) else 0,
        "p2p_cost_total_rs": round(p2p_cost, 0),
        "p2p_cost_per_order": round(p2p_cost / n, 2),
        "rdc_truck_cost_per_order": round(repl_cost / n, 2),
        "net_cost_per_order": round((courier + incentive + p2p_cost + repl_cost) / n, 2),
        "avg_basket_value_rs": round(float(df["basket_value"].mean()), 1),
        "product_margin_per_order": round(float(df["basket_margin"].mean()), 2),
        "delivery_fee_per_order": round(float(df["delivery_fee"].mean()), 2),
        "surcharge_revenue_per_order": round(float((df["delivery_fee"] - cfg.base_fee).mean()), 2),
        "contribution_per_order": round(float(df["basket_margin"].mean() + df["delivery_fee"].mean()
                                              - (courier + incentive + p2p_cost + repl_cost) / n), 2),
        "orders_switched_to_green": int(df["switched"].sum()),
        "orders_cancelled_pct": round(float(df["cancelled"].mean() * 100), 1),
        "orders_rerouted": int((df["reroute"] == "rerouted_gridlock").sum()),
        "orders_kept_at_gridlocked_hub": int(df["reroute"].isin(["kept_no_faster_option", "no_healthy_neighbour"]).sum()),
        "items_substituted": int(df["n_substituted"].sum()),
        "items_sourced_from_neighbour": int(df["n_sourced"].sum()),
        "items_dropped_by_preference": int(df["n_dropped"].sum()),
        "items_missing_unavoidable": int(df["n_missing"].sum()),
        "item_fill_rate_pct": round(delivered / items * 100, 1),
        "exact_item_fill_pct": round(float((df["n_ok"] - df["addon_accepted"]).sum() / (df["n_items"] - df["addon_accepted"]).sum() * 100), 1),
        "orders_complete_pct": round((df["n_missing"] == 0).mean() * 100, 1),
        "stockout_events": int(df["n_substituted"].sum() + df["n_sourced"].sum() + df["n_dropped"].sum() + df["n_missing"].sum()),
        "p2p_transfers": int(len(tl)),
        "p2p_units_moved": int(tl["units"].sum()) if len(tl) else 0,
        "addon_offers": int(df["addon_offered"].sum()),
        "addon_accepted": int(df["addon_accepted"].sum()),
        "addon_revenue_rs": round(float(df["addon_revenue"].sum()), 1),
        "addon_margin_rs": round(float(df["addon_margin"].sum()), 1),
        "addon_suppressed_no_stock": int(df["addon_blocked"].sum()),
        "coloc_badge_orders": int(len(badge)),
        "coloc_badge_filled_at_one_hub_pct": round(float(((badge["n_sourced"] == 0) & (badge["n_missing"] == 0)).mean() * 100), 1) if len(badge) else 0.0,
        "colocation_kit_lines": int(rl["colocation_kit"].sum()) if len(rl) else 0,
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
    ap.add_argument("--no-sla-aware-batching", action="store_true", help="merge neighbours without checking the SLA")
    ap.add_argument("--batch-safety-min", type=float, default=1.0, help="safety margin (min) kept inside the Green SLA when batching")
    ap.add_argument("--riders", default="auto", help="riders per hub: a number, or auto (sized from each hub's demand)")
    ap.add_argument("--target-util", type=float, default=0.7, help="rider utilisation used by --riders auto")
    ap.add_argument("--speed", type=float, default=20.0, help="normal rider speed km/h")
    ap.add_argument("--peak-speed", type=float, default=11.0, help="rider speed km/h in the evening peak")
    ap.add_argument("--peak-start", type=float, default=18.0, help="peak start (hour of day)")
    ap.add_argument("--peak-end", type=float, default=20.0, help="peak end (hour of day)")
    ap.add_argument("--circuity", type=float, default=1.4, help="road km per straight-line km")
    ap.add_argument("--grid-mode", choices=["compound", "cap"], default="compound",
                    help="compound: gridlock multiplies the current speed (about 6 km/h at peak); cap: gridlock only caps speed at the peak speed")
    ap.add_argument("--handover", type=float, default=1.0, help="minutes at each doorstep")
    ap.add_argument("--pick-base", type=float, default=2.0)
    ap.add_argument("--pick-per-item", type=float, default=0.25)
    ap.add_argument("--sub-delay", type=float, default=0.25, help="extra minutes when an item is substituted")
    ap.add_argument("--sourcing-delay", type=float, default=4.0, help="extra minutes when an item comes from a neighbour hub")
    ap.add_argument("--grid-factor", type=float, default=0.55, help="extra speed multiplier at a gridlocked hub")
    ap.add_argument("--gridlock-hub", default="auto", help="hub id, 'auto' (busiest hub with a neighbour), 'random' (varies with --seed) or 'none'")
    ap.add_argument("--gridlock-start", type=float, default=75, help="minutes after the window start")
    ap.add_argument("--gridlock-minutes", type=float, default=30)
    ap.add_argument("--adjacent-km", type=float, default=10.0, help="max ROAD km between adjacent hubs")
    ap.add_argument("--reroute-max-km", type=float, default=5.0, help="max ROAD km from the customer to the adjacent hub when rerouting")
    ap.add_argument("--reroute-min-gain", type=float, default=3.0, help="minutes a reroute must save to be worth it")
    # costs
    ap.add_argument("--cost-per-trip", type=float, default=30.0, help="fixed courier cost per trip (Rs)")
    ap.add_argument("--cost-per-km", type=float, default=6.0, help="courier cost per km (Rs)")
    ap.add_argument("--truck-cost-per-trip", type=float, default=500.0, help="RDC replenishment truck, fixed cost per trip (Rs)")
    ap.add_argument("--truck-cost-per-km", type=float, default=20.0, help="RDC replenishment truck, cost per road km (Rs)")
    ap.add_argument("--van-cost-per-trip", type=float, default=120.0, help="P2P transfer van, fixed cost per trip (Rs)")
    ap.add_argument("--van-cost-per-km", type=float, default=10.0, help="P2P transfer van, cost per road km (Rs)")
    # inventory and replenishment
    ap.add_argument("--stockout-rate", type=float, default=0.05)
    ap.add_argument("--no-replenishment", action="store_true", help="turn macro-hub trucks off")
    ap.add_argument("--par-scale", type=float, default=1.0, help="scale all par levels (e.g. 0.3 for lean stores)")
    ap.add_argument("--hub-low-pct", type=float, default=0.30, help="hub-level total-stock alert threshold")
    ap.add_argument("--basket-source", choices=["instacart", "pareto"], default="instacart",
                    help="customer carts from real Instacart baskets (default) or independent Pareto draws")
    ap.add_argument("--out-dir", default=None, help="where to write results (default: --data-dir)")
    ap.add_argument("--base-fee", type=float, default=20.0, help="standard delivery fee per order (Rs)")
    ap.add_argument("--traffic-fee", type=float, default=10.0, help="traffic convenience fee for Instant orders (Rs)")
    ap.add_argument("--traffic-threshold", type=float, default=15.0, help="charge the traffic fee when rider speed is below this (km/h)")
    ap.add_argument("--surge-fee-per-x", type=float, default=10.0, help="surge fee (Rs) per 1.0 of demand multiplier above 1")
    ap.add_argument("--surge-fee-cap", type=float, default=30.0, help="maximum surge fee (Rs)")
    ap.add_argument("--switch-elasticity", type=float, default=0.02,
                    help="probability that an Instant customer switches to Green per Rs of surcharge")
    ap.add_argument("--no-surcharge", action="store_true", help="no traffic/surge fees and no switching")
    ap.add_argument("--fleet-flex", type=float, default=0.5,
                    help="share of the extra surge demand that extra riders cover (0 = none, 1 = full scaling)")
    ap.add_argument("--cancel-min", type=float, default=30.0,
                    help="customers cancel (no revenue, cost still incurred) if delivery takes longer than this")
    ap.add_argument("--fleet-basis", choices=["nominal", "actual"], default="nominal",
                    help="size the rider fleet for normal demand and speeds (stress tests bite) or for the run's own demand")
    ap.add_argument("--no-proactive", action="store_true", help="turn proactive P2P rebalancing off (reactive healing only)")
    ap.add_argument("--p2p-interval", type=float, default=30.0, help="minutes between proactive rebalancing checks (60 = hourly)")
    ap.add_argument("--p2p-min-units", type=int, default=50, help="smallest load worth a P2P van trip")
    ap.add_argument("--truck-min-gap", type=float, default=60.0, help="minutes between RDC trucks to the same hub")
    ap.add_argument("--addon-accept", type=float, default=0.20, help="share of customers who accept a recipe add-on (0 = off)")
    ap.add_argument("--sweep", action="store_true", help="also sweep Green adoption 0-100%")
    ap.add_argument("--resilience", action="store_true", help="gridlock each hub in turn and measure how well neighbours cover for it")
    ap.add_argument("--seed", type=int, default=42)
    cfg = ap.parse_args()

    data = Path(cfg.data_dir)
    out = Path(cfg.out_dir) if cfg.out_dir else data
    out.mkdir(parents=True, exist_ok=True)
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
    cp = data / "colocation_pairs.csv"
    pairs = []
    if cp.exists():
        c = pd.read_csv(cp)
        pairs = list(zip(c.loc[c["colocate"], "category_a"], c.loc[c["colocate"], "category_b"]))
    base_net = HubNetwork(hubs, sku, subs, cfg.adjacent_km, cfg.stockout_rate, cfg.seed,
                          replenish=not cfg.no_replenishment, par_scale=cfg.par_scale, hub_low_pct=cfg.hub_low_pct,
                          speed_kmph=cfg.speed, peak_kmph=cfg.peak_speed,
                          peak_window=(cfg.peak_start * 60, cfg.peak_end * 60), grid_factor=cfg.grid_factor, grid_mode=cfg.grid_mode,
                          circuity=cfg.circuity, reroute_max_km=cfg.reroute_max_km, reroute_min_gain=cfg.reroute_min_gain,
                          proactive=not cfg.no_proactive, p2p_interval=cfg.p2p_interval, coloc_pairs=pairs,
                          p2p_min_units=cfg.p2p_min_units, truck_min_gap=cfg.truck_min_gap)

    # basket rules from step 3, bridged to this catalog
    b3 = importlib.import_module("03_basket_affinity")
    rp = data / "rules_recipe_completion.csv"
    rc = b3.RecipeCompleter(rp) if (rp.exists() and cfg.addon_accept > 0) else None
    cl = b3.CoLocationRules(cp) if cp.exists() else None
    aisles = [map_to_aisle(sc, nm) for sc, nm in zip(sku["sub_category"], sku["product_name"])]
    pop_order = np.argsort(-sku["popularity_score"].to_numpy()) if "popularity_score" in sku else np.arange(len(sku))
    aisle_skus = {}
    for i in pop_order:
        if aisles[i]:
            aisle_skus.setdefault(aisles[i], []).append(sku["product_id"].iloc[i])
    ctx = {"rc": rc, "cl": cl, "aisle_of": dict(zip(sku["product_id"], aisles)), "aisle_skus": aisle_skus,
           "price": dict(zip(sku["product_id"], sku["price"])), "margin": dict(zip(sku["product_id"], sku["profit_margin_pct"]))}
    print(f"[rules] recipe rules {'on' if rc is not None else 'off'}, co-location rules {'on' if cl else 'off'}; "
          f"{sum(a is not None for a in aisles) / len(aisles):.0%} of SKUs bridge to an Instacart aisle "
          f"({len(aisle_skus)} aisles)")

    pool = None if cfg.basket_source == "pareto" else load_basket_pool(data)
    aisle_idx = {a_: np.array([i for i, x in enumerate(aisles) if x == a_]) for a_ in aisle_skus}
    print(f"[carts] {'real Instacart baskets (' + format(len(pool), ',') + ' in pool)' if pool else 'independent Pareto draws'}")
    orders, carts = prepare_orders(orders, base_net, cfg, rng, pool, aisle_idx)
    window_min = max(cfg.end_hour - cfg.start_hour, 1) * 60.0
    avg_items = float(np.mean([len(c) for c in carts.values()]))
    base_net.set_forecast({h: n / window_min * avg_items for h, n in orders["home_hub"].value_counts().items()})
    # gridlock event
    g_hub, gs, ge = None, 0, 0
    if cfg.gridlock_hub != "none":
        if cfg.gridlock_hub == "auto":
            g_hub = next((h for h in orders["home_hub"].value_counts().index if base_net.adjacent(h)), None)
        elif cfg.gridlock_hub == "random":                      # a random hub that has orders (varies with --seed)
            hubs_with_orders = sorted(orders["home_hub"].unique())
            g_hub = hubs_with_orders[int(np.random.default_rng(cfg.seed + 7).integers(len(hubs_with_orders)))]
        else:
            g_hub = cfg.gridlock_hub
        if g_hub:
            gs = cfg.start_hour * 60 + cfg.gridlock_start
            ge = gs + cfg.gridlock_minutes
            print(f"[event] gridlock at {g_hub} from {hhmm(gs)} to {hhmm(ge)}")

    orders["surcharge"] = compute_surcharge(orders, base_net, cfg, g_hub, gs, ge)
    orders = assign_tiers(orders, cfg.green_share, cfg.switch_elasticity)
    print("[tiers] " + ", ".join(f"{k} {v}" for k, v in orders["tier"].value_counts().items())
          + f"  |  drop-preference customers: {(orders['pref'] == 'drop').sum()}")

    rider_map = rider_plan(orders, base_net, cfg)
    print(f"[riders] per hub: {rider_map}")

    def run(healed, ords, gl_hub="default", no_reroute=False, proactive=None):
        gl_hub = g_hub if gl_hub == "default" else gl_hub
        net = base_net.copy()
        net.proactive = (healed and not cfg.no_proactive) if proactive is None else proactive   # baseline is reactive only
        if no_reroute:
            net.reroute_max_km = 0.0          # rerouting switched off; everything else stays on
        if gl_hub:
            net.gridlock(gl_hub, gs, ge)
        df, trips, alerts = run_mode(ords, net, carts, healed, cfg, rider_map, ctx)
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

    if not cfg.no_proactive:                       # the same healing, but with proactive P2P switched off
        dr, tr_, _, nr = run(True, orders, proactive=False)
        summaries["healing_reactive_only"] = summarise(dr, tr_, cfg, True, nr)
    summary = pd.DataFrame(summaries)[[c for c in ("baseline", "healing_reactive_only", "self_healing") if c in summaries]]
    print("\n" + summary.to_string() + "\n")
    sb, sh = summaries["baseline"], summaries["self_healing"]
    print(f"Self-healing vs baseline: promised SLA met {sb['sla_met_pct']}% -> {sh['sla_met_pct']}%, "
          f"standard-SLA met {sb['met_standard_sla_pct']}% -> {sh['met_standard_sla_pct']}%, "
          f"rider trips {sb['rider_trips']} -> {sh['rider_trips']}")
    if "healing_reactive_only" in summaries:
        sr = summaries["healing_reactive_only"]
        print(f"Proactive P2P effect (vs reactive-only healing): stock-out events {sr['stockout_events']} -> {sh['stockout_events']}, "
              f"substitutions {sr['items_substituted']} -> {sh['items_substituted']}, "
              f"RDC trucks {sr['replenishment_trucks']} -> {sh['replenishment_trucks']}, "
              f"exact-item fill {sr['exact_item_fill_pct']}% -> {sh['exact_item_fill_pct']}%; "
              f"{sh['p2p_transfers']} transfers ({sh['p2p_van_trips']} van round trips) moved {sh['p2p_units_moved']} units "
              f"(cost Rs {sh['p2p_cost_per_order']} per order)")
        avoided = sr["stockout_events"] - sh["stockout_events"]
        if avoided > 0 and sh["p2p_cost_total_rs"] > 0:
            print(f"P2P break-even: it pays for itself if one avoided stock-out is worth more than "
                  f"Rs {sh['p2p_cost_total_rs'] / avoided:.0f} to you (lost sale, substitution annoyance, churn)")
    print(f"Basket rules: {sh['addon_offers']} add-ons offered, {sh['addon_accepted']} accepted "
          f"(+Rs {sh['addon_revenue_rs']:.0f} sales, +Rs {sh['addon_margin_rs']:.0f} margin), {sh['addon_suppressed_no_stock']} suppressed for no stock; "
          f"{sh['coloc_badge_orders']} multi-category orders met a co-location rule "
          f"({sh['coloc_badge_filled_at_one_hub_pct']}% filled at one hub); {sh['colocation_kit_lines']} truck lines added by co-location kitting")
    print(f"Contribution per order (Rs): baseline {sb['contribution_per_order']} -> self-healing {sh['contribution_per_order']} "
          f"(product margin {sb['product_margin_per_order']} -> {sh['product_margin_per_order']}, delivery fees {sb['delivery_fee_per_order']} -> "
          f"{sh['delivery_fee_per_order']}, logistics cost {sb['net_cost_per_order']} -> {sh['net_cost_per_order']}); "
          f"{sh['orders_switched_to_green']} customers switched to Green because of the surcharge")
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
            o2 = assign_tiers(orders, share, cfg.switch_elasticity)
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
        sweep.to_csv(out / "green_adoption_sweep.csv", index=False)
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
        res.to_csv(out / "gridlock_resilience.csv", index=False)
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
    tl_h = net.transfer_log("self_healing")
    for r_ in tl_h.itertuples():
        rows.append((r_.created_min, "P2P_REBALANCE", r_.receiver_hub, r_.message + f"; {r_.units} units, arrives {r_.arrives}"))
    rows += sorted(heal["alerts"])[:300]
    alert_df = pd.DataFrame([{"time": hhmm(t), "type": ty, "hub": h, "message": m} for t, ty, h, m in sorted(rows)])

    out_cols = ["mode", "order_id", "tier", "pref", "home_hub", "hub", "reroute", "extra_km", "t", "batch_id", "batch_size",
                "packed", "dispatch", "delivered", "total_min", "sla_limit", "sla_met", "timer", "n_items", "n_ok",
                "n_substituted", "n_sourced", "n_dropped", "n_missing", "incentive_rs", "coloc_badge", "addon_offered",
                "addon_accepted", "addon_sku", "addon_revenue", "addon_margin"]
    pd.concat(results)[out_cols].round(2).to_csv(out / "sla_sim_orders.csv", index=False)
    summary.to_csv(out / "sla_sim_summary.csv")
    tier_df.to_csv(out / "sla_tier_summary.csv", index=False)
    alert_df.to_csv(out / "sla_alerts.csv", index=False)
    tl_h.drop(columns=["created_min"], errors="ignore").to_csv(out / "p2p_transfers.csv", index=False)   # empty log (e.g. --no-proactive) has no columns
    repl = pd.concat(repl_logs, ignore_index=True)
    repl.to_csv(out / "replenishment_alerts.csv", index=False)
    tl = timeline_text(heal_df, cfg)
    (out / "sla_timeline_example.txt").write_text(tl, encoding="utf-8")

    # replenishment digest
    hr = repl[repl["mode"] == "self_healing"] if "mode" in repl.columns else repl.iloc[0:0]   # no trucks (e.g. --no-replenishment): empty log has no columns
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
          f"replenishment_alerts.csv, p2p_transfers.csv, sla_timeline_example.txt -> {out}")


if __name__ == "__main__":
    main()
