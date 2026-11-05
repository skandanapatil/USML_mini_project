#!/usr/bin/env python3
"""
MODULE 3 - Market Basket Association and Consolidation Engine (Apriori)

Input : Instacart files in --instacart-dir
        aisles.csv, departments.csv, products.csv, orders.csv, order_products__prior.csv
        (the 577 MB order_products__prior.csv is read in chunks, on a random sample of orders)

Two rule sets, mined at two levels of detail
  A. Recipe completion (ingredient level): Apriori on Instacart AISLES, e.g.
     {dry pasta, pasta sauce} -> {fresh herbs}. Used by the forward-looking intent
     engine: if a cart looks like a recipe and a likely ingredient is missing or
     out of stock, suggest it or a swap.
  B. Co-location affinity (category level): Apriori on YOUR quick-commerce
     categories (Staples, Dairy & Bakery, Munchies & Snacks, ...). Category pairs
     that are bought together more often than chance are flagged to be housed in
     the same micro-hub so the basket does not split into two shipments.

Instacart is US grocery data, so the logic transfers but not the product names.
Say this in your write-up. Aisles are mapped to your categories in AISLE_TO_CATEGORY below.

Outputs (in --out-dir, default data/)
  rules_recipe_completion.csv   aisle-level rules
  rules_colocation.csv          category-level rules
  colocation_pairs.csv          one row per category pair, with a colocate flag
  basket_summary.json

Use in code
  import importlib   # file names start with a digit, so use import_module
  m = importlib.import_module("03_basket_affinity")
  m.RecipeCompleter("data/rules_recipe_completion.csv").suggest({"dry pasta", "pasta sauce"})
  m.CoLocationRules("data/colocation_pairs.csv").meets_criteria({"Staples", "Dairy & Bakery", "Fresh"})

Usage
  python 03_basket_affinity.py --instacart-dir "C:/path/instacart" --out-dir data --demo
Requires: numpy, pandas
"""
import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

# Same folders as quickcommerce_data_prep.py
RAW_DIR = Path(r"D:\PES study\Sem2\USL\Mini projects\Datasets")
DATA_DIR = RAW_DIR / "processed"

# Instacart department -> quick-commerce category (None = left out)
DEPT_TO_CATEGORY = {
    "frozen": "Frozen", "bakery": "Dairy & Bakery", "produce": "Fresh", "alcohol": None,
    "international": "Staples", "beverages": "Beverages", "pets": None, "dry goods pasta": "Staples",
    "bulk": "Staples", "personal care": "Personal Care", "meat seafood": "Eggs & Meat",
    "pantry": "Staples", "breakfast": "Staples", "canned goods": "Staples", "dairy eggs": "Dairy & Bakery",
    "household": "Household", "babies": "Baby Care", "snacks": "Munchies & Snacks", "deli": "Fresh",
    "other": None, "missing": None,
}
# Aisle-level exceptions to the department rule
AISLE_OVERRIDE = {"eggs": "Eggs & Meat"}


# ------------------------------------------------------------------ data loading
def load_baskets(idir, sample_orders, chunk_rows, seed):
    """Return (order_ids, aisle_matrix_inputs). Reads the big file in chunks and keeps only sampled orders."""
    aisles = pd.read_csv(idir / "aisles.csv")
    depts = pd.read_csv(idir / "departments.csv")
    prods = pd.read_csv(idir / "products.csv")
    prods = prods.merge(aisles, on="aisle_id").merge(depts, on="department_id")
    prods["category"] = prods["department"].map(DEPT_TO_CATEGORY)
    ov = prods["aisle"].map(AISLE_OVERRIDE)
    prods["category"] = ov.fillna(prods["category"])

    orders = pd.read_csv(idir / "orders.csv", usecols=["order_id", "eval_set"])
    ids = orders.loc[orders["eval_set"] == "prior", "order_id"].to_numpy()
    rng = np.random.default_rng(seed)
    if sample_orders and sample_orders < len(ids):
        ids = rng.choice(ids, size=sample_orders, replace=False)
    ids = np.sort(ids)
    print(f"[load] sampling {len(ids):,} prior orders")

    aisle_of = prods.set_index("product_id")["aisle"]
    cat_of = prods.set_index("product_id")["category"]
    parts = []
    n_rows = 0
    for chunk in pd.read_csv(idir / "order_products__prior.csv", usecols=["order_id", "product_id"],
                             dtype={"order_id": np.int32, "product_id": np.int32}, chunksize=chunk_rows):
        n_rows += len(chunk)
        pos = np.searchsorted(ids, chunk["order_id"].to_numpy())
        pos[pos == len(ids)] = 0
        keep = ids[pos] == chunk["order_id"].to_numpy()
        c = chunk[keep]
        if len(c):
            parts.append(pd.DataFrame({"order_id": c["order_id"].to_numpy(),
                                       "aisle": c["product_id"].map(aisle_of).to_numpy(),
                                       "category": c["product_id"].map(cat_of).to_numpy()}))
        print(f"\r[load] scanned {n_rows / 1e6:.0f}M rows", end="", flush=True)
    print()
    return ids, pd.concat(parts, ignore_index=True), prods


def to_matrix(ids, df, col):
    """Boolean basket matrix (orders x items) for the chosen level (aisle or category)."""
    d = df.dropna(subset=[col])[["order_id", col]].drop_duplicates()
    items = sorted(d[col].unique())
    item_idx = {it: i for i, it in enumerate(items)}
    rows = np.searchsorted(ids, d["order_id"].to_numpy())
    cols = d[col].map(item_idx).to_numpy()
    X = np.zeros((len(ids), len(items)), dtype=bool)
    X[rows, cols] = True
    keep = X.sum(axis=1) > 0
    return X[keep], items


# ------------------------------------------------------------------ apriori
def apriori(X, min_support, max_len):
    """Level-wise Apriori on a boolean matrix. Returns {tuple_of_item_idx: support}."""
    n = X.shape[0]
    sup1 = X.mean(axis=0)
    freq = [i for i in range(X.shape[1]) if sup1[i] >= min_support]
    itemsets = {(i,): float(sup1[i]) for i in freq}
    if max_len < 2 or len(freq) < 2:
        return itemsets

    Xf = X[:, freq].astype(np.float32)
    co = (Xf.T @ Xf) / n                                   # pair supports in one matrix product
    level = []
    for a, b in combinations(range(len(freq)), 2):
        if co[a, b] >= min_support:
            key = (freq[a], freq[b])
            itemsets[key] = float(co[a, b])
            level.append(key)

    cols = {i: X[:, i] for i in freq}
    k = 3
    while level and k <= max_len:
        prev = set(level)
        cands = set()
        for s1 in level:
            for s2 in level:
                if s1[:-1] == s2[:-1] and s1[-1] < s2[-1]:
                    c = s1 + (s2[-1],)
                    if all(sub in prev for sub in combinations(c, k - 1)):
                        cands.add(c)
        level = []
        for c in sorted(cands):
            m = cols[c[0]]
            for j in c[1:]:
                m = m & cols[j]
            sup = float(m.mean())
            if sup >= min_support:
                itemsets[c] = sup
                level.append(c)
        k += 1
    return itemsets


def make_rules(itemsets, items, min_conf, min_lift, max_antecedent):
    """Rules with a single consequent: antecedent -> consequent."""
    rows = []
    for iset, sup in itemsets.items():
        if len(iset) < 2:
            continue
        for c in iset:
            ante = tuple(i for i in iset if i != c)
            if len(ante) > max_antecedent or ante not in itemsets:
                continue
            conf = sup / itemsets[ante]
            lift = conf / itemsets[(c,)]
            if conf >= min_conf and lift >= min_lift:
                rows.append({"antecedents": " + ".join(items[i] for i in ante), "consequent": items[c],
                             "n_antecedents": len(ante), "support": round(sup, 4),
                             "confidence": round(conf, 3), "lift": round(lift, 2),
                             "leverage": round(sup - itemsets[ante] * itemsets[(c,)], 4)})
    cols = ["antecedents", "consequent", "n_antecedents", "support", "confidence", "lift", "leverage"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols].sort_values(["lift", "confidence"], ascending=False).reset_index(drop=True)


# ------------------------------------------------------------------ runtime helpers
class RecipeCompleter:
    """Given the aisles already in a cart, rank the most likely missing ones."""

    def __init__(self, rules_csv):
        r = pd.read_csv(rules_csv)
        r["ante_set"] = r["antecedents"].str.split(r" \+ ").map(frozenset)
        self.r = r

    def suggest(self, cart_aisles, unavailable=(), top_n=5):
        cart, bad = set(cart_aisles), set(unavailable)
        hit = self.r[self.r["ante_set"].map(lambda s: s <= cart) & ~self.r["consequent"].isin(cart)]
        if hit.empty:
            return []
        hit = hit.assign(score=hit["confidence"] * hit["lift"])
        best = hit.sort_values("score", ascending=False).drop_duplicates("consequent")
        res = []
        for row in best.itertuples():
            res.append({"missing_item": row.consequent, "because": row.antecedents, "confidence": row.confidence,
                        "lift": row.lift, "in_stock": row.consequent not in bad})
        res = [x for x in res if x["in_stock"]] + [x for x in res if not x["in_stock"]]
        return res[:top_n]


class CoLocationRules:
    """Flags carts whose categories should be housed in the same micro-hub."""

    def __init__(self, pairs_csv):
        p = pd.read_csv(pairs_csv)
        self.pairs = {frozenset((r.category_a, r.category_b)): r for r in p.itertuples() if r.colocate}

    def meets_criteria(self, cart_categories):
        cats = set(cart_categories)
        return [tuple(sorted(k)) for k in self.pairs if k <= cats]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instacart-dir", default=str(RAW_DIR), help="folder with the Instacart CSVs")
    ap.add_argument("--out-dir", default=str(DATA_DIR))
    ap.add_argument("--sample-orders", type=int, default=300000, help="0 = use all 3.2M prior orders (needs lots of RAM)")
    ap.add_argument("--chunk-rows", type=int, default=5_000_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-support", type=float, default=0.01, help="aisle-level support")
    ap.add_argument("--min-confidence", type=float, default=0.25)
    ap.add_argument("--min-lift", type=float, default=1.5)
    ap.add_argument("--cat-min-support", type=float, default=0.03, help="category-level support")
    ap.add_argument("--colocate-lift", type=float, default=1.2, help="category pair lift needed to flag co-location")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()

    idir, out = Path(a.instacart_dir), Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids, df, prods = load_baskets(idir, a.sample_orders, a.chunk_rows, a.seed)
    unmapped = df["category"].isna().mean()
    print(f"[map] {1 - unmapped:.0%} of basket lines map to one of your categories "
          f"(alcohol, pets, other and missing are left out)")

    # A. recipe completion (aisle level)
    XA, aisles = to_matrix(ids, df, "aisle")
    print(f"[apriori] aisle level: {XA.shape[0]:,} baskets x {XA.shape[1]} aisles, min_support={a.min_support}")
    sets_a = apriori(XA, a.min_support, max_len=3)
    rules_a = make_rules(sets_a, aisles, a.min_confidence, a.min_lift, max_antecedent=2)
    rules_a.to_csv(out / "rules_recipe_completion.csv", index=False)
    print(f"[rules] recipe completion: {len(sets_a):,} frequent itemsets -> {len(rules_a):,} rules")

    # B. co-location affinity (category level)
    XC, cats = to_matrix(ids, df, "category")
    sets_c = apriori(XC, a.cat_min_support, max_len=3)
    rules_c = make_rules(sets_c, cats, 0.0, 0.0, max_antecedent=2)
    rules_c.to_csv(out / "rules_colocation.csv", index=False)
    pairs = []
    for (i, j), sup in ((k, v) for k, v in sets_c.items() if len(k) == 2):
        lift = sup / (sets_c[(i,)] * sets_c[(j,)])
        pairs.append({"category_a": cats[i], "category_b": cats[j], "support": round(sup, 4),
                      "conf_a_to_b": round(sup / sets_c[(i,)], 3), "conf_b_to_a": round(sup / sets_c[(j,)], 3),
                      "lift": round(lift, 2), "colocate": bool(lift >= a.colocate_lift)})
    pairs_df = pd.DataFrame(pairs).sort_values("lift", ascending=False).reset_index(drop=True)
    pairs_df.to_csv(out / "colocation_pairs.csv", index=False)
    print(f"[rules] co-location: {len(pairs_df)} category pairs, {int(pairs_df['colocate'].sum())} flagged (lift >= {a.colocate_lift})")
    print(pairs_df.head(8).to_string(index=False))

    summary = {"baskets_used": int(XA.shape[0]), "aisles": len(aisles), "categories": cats,
               "recipe_rules": int(len(rules_a)), "colocation_pairs_flagged": int(pairs_df["colocate"].sum()),
               "thresholds": {"aisle_support": a.min_support, "confidence": a.min_confidence, "lift": a.min_lift,
                              "category_support": a.cat_min_support, "colocate_lift": a.colocate_lift}}
    (out / "basket_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[write] rules_recipe_completion.csv, rules_colocation.csv, colocation_pairs.csv -> {out}")

    if a.demo:
        rc = RecipeCompleter(out / "rules_recipe_completion.csv")
        for cart in ({"dry pasta", "pasta sauce"}, {"tortillas flat bread", "packaged cheese"}, {"fresh vegetables", "spices seasonings"}):
            print(f"\nCART {sorted(cart)}")
            sug = rc.suggest(cart, unavailable={"fresh herbs"})
            for s in sug or [{"missing_item": "(no rule above thresholds)", "because": "", "confidence": "", "lift": "", "in_stock": True}]:
                flag = "" if s["in_stock"] else "  [OUT OF STOCK: trigger swap / adjacent-hub sourcing]"
                print(f"  missing: {s['missing_item']}  (conf {s['confidence']}, lift {s['lift']}){flag}")
        cl = CoLocationRules(out / "colocation_pairs.csv")
        print("\nCo-location check for a Staples + Dairy & Bakery + Fresh basket ->", cl.meets_criteria({"Staples", "Dairy & Bakery", "Fresh"}))


if __name__ == "__main__":
    main()
