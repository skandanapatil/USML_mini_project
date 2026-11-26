#!/usr/bin/env python3
"""
Validate the hand-written Apriori in 03_basket_affinity.py.

Check 1 (always runs, needs only numpy/pandas): compare apriori() and make_rules() against an
        independent brute-force count of every itemset up to size 3 on synthetic baskets.
Check 2 (runs only if mlxtend is installed): compare the same itemsets and rules with
        mlxtend's apriori() and association_rules().
Check 3 (optional, --real): repeat check 2 on a sample of the real Instacart aisle baskets.

Usage (run from the folder that holds 03_basket_affinity.py):
    python validate_apriori.py
    python validate_apriori.py --real --instacart-dir "D:/path/Instacart" --sample-orders 50000
"""
import argparse
import importlib
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


def brute_force(X, min_support, max_len=3):
    n, m = X.shape
    out = {}
    for k in range(1, max_len + 1):
        for c in combinations(range(m), k):
            sup = float(X[:, list(c)].all(axis=1).mean())
            if sup >= min_support:
                out[c] = sup
    return out


def compare_itemsets(a, b, label, tol=1e-6):
    only_a, only_b = set(a) - set(b), set(b) - set(a)
    worst = max((abs(a[k] - b[k]) for k in set(a) & set(b)), default=0.0)
    ok = not only_a and not only_b and worst <= tol
    print(f"  {label}: {len(a)} vs {len(b)} itemsets, only-in-first={len(only_a)}, only-in-second={len(only_b)}, "
          f"max support difference={worst:.2e} -> {'MATCH' if ok else 'MISMATCH'}")
    return ok


def brute_rules(itemsets, min_conf, min_lift, max_ante):
    rules = set()
    for iset, sup in itemsets.items():
        if len(iset) < 2:
            continue
        for c in iset:
            ante = tuple(i for i in iset if i != c)
            if len(ante) > max_ante or ante not in itemsets:
                continue
            conf = sup / itemsets[ante]
            lift = conf / itemsets[(c,)]
            if conf >= min_conf and lift >= min_lift:
                rules.add((ante, c))
    return rules


def mlxtend_check(m, X, names, ms, mc, ml, max_ante, label):
    try:
        from mlxtend.frequent_patterns import apriori as mx_apriori, association_rules
    except ImportError:
        print(f"  {label}: mlxtend not installed, skipped (pip install mlxtend, then re-run)")
        return None
    df = pd.DataFrame(X, columns=names)
    fi = mx_apriori(df, min_support=ms, use_colnames=True, max_len=3)
    idx = {n: i for i, n in enumerate(names)}
    theirs = {tuple(sorted(idx[x] for x in s)): float(sup) for s, sup in zip(fi["itemsets"], fi["support"])}
    mine = m.apriori(X, ms, max_len=3)
    ok = compare_itemsets(mine, theirs, f"{label} itemsets (hand-written vs mlxtend)")
    try:
        r = association_rules(fi, num_itemsets=len(df), metric="confidence", min_threshold=mc)
    except TypeError:
        r = association_rules(fi, metric="confidence", min_threshold=mc)
    r = r[(r["lift"] >= ml) & (r["consequents"].map(len) == 1) & (r["antecedents"].map(len) <= max_ante)]
    theirs_r = {(tuple(sorted(idx[x] for x in a)), idx[next(iter(c))]) for a, c in zip(r["antecedents"], r["consequents"])}
    mine_r = brute_rules(mine, mc, ml, max_ante)
    same = mine_r == theirs_r
    print(f"  {label} rules: {len(mine_r)} vs {len(theirs_r)} -> {'MATCH' if same else 'MISMATCH'}")
    return ok and same


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module-dir", default=".")
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--instacart-dir")
    ap.add_argument("--sample-orders", type=int, default=50000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.module_dir).resolve()))
    m = importlib.import_module("03_basket_affinity")

    # ---- synthetic baskets with planted co-purchases
    rng = np.random.default_rng(a.seed)
    n, k = 6000, 14
    X = rng.random((n, k)) < rng.uniform(0.05, 0.4, size=k)
    for i in range(n):                                   # planted structure so there are real rules
        if X[i, 0] and rng.random() < 0.7: X[i, 1] = True
        if X[i, 0] and X[i, 1] and rng.random() < 0.6: X[i, 2] = True
        if X[i, 3] and rng.random() < 0.5: X[i, 4] = True
    names = [f"item{i}" for i in range(k)]
    ms, mc, ml, ante = 0.02, 0.25, 1.5, 2
    print(f"Check 1: {n} synthetic baskets x {k} items, min_support={ms}, confidence>={mc}, lift>={ml}")
    mine = m.apriori(X, ms, max_len=3)
    ref = brute_force(X, ms, 3)
    ok1 = compare_itemsets(mine, ref, "hand-written Apriori vs brute force")
    rules_df = m.make_rules(mine, names, mc, ml, ante)
    mine_r = {(tuple(sorted(names.index(x) for x in r.antecedents.split(" + "))), names.index(r.consequent))
              for r in rules_df.itertuples()}
    ref_r = brute_rules(ref, mc, ml, ante)
    ok1r = mine_r == ref_r
    print(f"  make_rules vs brute-force rules: {len(mine_r)} vs {len(ref_r)} -> {'MATCH' if ok1r else 'MISMATCH'}")

    print("Check 2: mlxtend on the same synthetic baskets")
    ok2 = mlxtend_check(m, X, names, ms, mc, ml, ante, "synthetic")

    ok3 = None
    if a.real:
        print("Check 3: real Instacart aisle baskets")
        ids, df, _ = m.load_baskets(Path(a.instacart_dir), a.sample_orders, 5_000_000, a.seed)
        XA, aisles = m.to_matrix(ids, df, "aisle")
        ok3 = mlxtend_check(m, XA, aisles, 0.01, 0.25, 1.5, 2, "real aisles")

    results = [r for r in (ok1 and ok1r, ok2, ok3) if r is not None]
    print("\nOVERALL:", "ALL CHECKS PASSED" if all(results) else "SOMETHING DID NOT MATCH")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
