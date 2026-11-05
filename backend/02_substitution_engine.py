#!/usr/bin/env python3
"""
MODULE 2 - Product Similarity and Margin-Aware Substitution Engine

Input : data/sku_master.csv   (from quickcommerce_data_prep.py)

How it works
  1. Builds a text profile per SKU (name, brand, sub-category, type, description)
     and vectorises it with TF-IDF (unigrams + bigrams).
  2. Cosine similarity between SKUs.
  3. For each SKU, candidate substitutes must be in the same sub-category (widened
     to the same category if too few) and within a price band of the original.
  4. Candidates are ranked by a blend of similarity and margin:
        final_score = alpha * similarity + (1 - alpha) * margin_weight
     where margin_weight = profit_margin_pct / 35 (35 is the top of the seeded range).
     Candidates below --min-similarity are dropped, so a poor match is never offered.

Outputs (in --data-dir)
  substitution_matrix.csv   long format, top-K substitutes per SKU
  (columns: target_product_id, rank, substitute_product_id, similarity_score,
   margin_weight, final_score, price_ratio, same_brand, plus readable names)

Use it in code (the stock-out lookup)
  import importlib   # file name starts with a digit, so use import_module
  m = importlib.import_module("02_substitution_engine")
  eng = m.SubstitutionEngine("data/substitution_matrix.csv")
  eng.suggest("SKU00042", unavailable={"SKU00099"})

Usage
  python 02_substitution_engine.py --data-dir data --demo
Requires: numpy, pandas, scikit-learn
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# Same folders as quickcommerce_data_prep.py
RAW_DIR = Path(r"D:\PES study\Sem2\USL\Mini projects\Datasets")
DATA_DIR = RAW_DIR / "processed"
from sklearn.feature_extraction.text import TfidfVectorizer

MARGIN_CEILING = 35.0


def build_text(df):
    def col(c):
        return df[c].fillna("").astype(str) if c in df.columns else pd.Series([""] * len(df), index=df.index)
    name, brand, sub, typ = col("product_name"), col("brand"), col("sub_category"), col("type")
    desc = col("description").str.slice(0, 300)
    # name and sub-category/type count most, so they are repeated to carry more weight
    return (name + " ") * 3 + brand + " " + (sub + " ") * 2 + (typ + " ") * 2 + desc


def build_matrix(sku, top_k, alpha, min_sim, price_lo, price_hi, min_candidates):
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=2,
                          sublinear_tf=True, strip_accents="unicode")
    X = vec.fit_transform(build_text(sku))
    print(f"[tfidf] {X.shape[0]:,} SKUs x {X.shape[1]:,} terms")
    sim = (X @ X.T).toarray().astype(np.float32)  # rows are L2-normalised, so this is cosine
    np.fill_diagonal(sim, -1)

    n = len(sku)
    pid = sku["product_id"].to_numpy()
    cat = sku["category"].to_numpy()
    sub = sku["sub_category"].fillna("General").to_numpy() if "sub_category" in sku else cat
    brand = sku["brand"].fillna("").to_numpy()
    price = sku["price"].to_numpy(dtype=float)
    margin = sku["profit_margin_pct"].to_numpy(dtype=float)
    mw = np.clip(margin / MARGIN_CEILING, 0, 1)

    rows = []
    for i in range(n):
        in_price = (price >= price_lo * price[i]) & (price <= price_hi * price[i])
        pool = (sub == sub[i]) & in_price
        pool[i] = False
        if pool.sum() < min_candidates:            # widen to the whole category
            pool = (cat == cat[i]) & in_price
            pool[i] = False
        cand = np.where(pool & (sim[i] >= min_sim))[0]
        if cand.size == 0:
            continue
        score = alpha * sim[i, cand] + (1 - alpha) * mw[cand]
        order = np.argsort(-score)[:top_k]
        for rank, o in enumerate(order, 1):
            j = cand[o]
            rows.append((pid[i], rank, pid[j], round(float(sim[i, j]), 4), round(float(mw[j]), 3),
                         round(float(score[o]), 4), round(float(price[j] / price[i]), 2), bool(brand[i] == brand[j])))
    out = pd.DataFrame(rows, columns=["target_product_id", "rank", "substitute_product_id", "similarity_score",
                                      "margin_weight", "final_score", "price_ratio", "same_brand"])
    names = sku.set_index("product_id")["product_name"]
    out.insert(1, "target_name", out["target_product_id"].map(names))
    out.insert(4, "substitute_name", out["substitute_product_id"].map(names))
    return out


class SubstitutionEngine:
    """Instant stock-out lookup over the pre-computed matrix."""

    def __init__(self, matrix_csv):
        self.m = pd.read_csv(matrix_csv)
        self.by_target = {k: g.sort_values("rank") for k, g in self.m.groupby("target_product_id")}

    def suggest(self, product_id, unavailable=(), k=1):
        """Best in-stock substitutes for an out-of-stock SKU (empty list if none is good enough)."""
        g = self.by_target.get(product_id)
        if g is None:
            return []
        g = g[~g["substitute_product_id"].isin(set(unavailable))]
        return g.head(k).to_dict("records")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR), help="folder with the generated files")
    ap.add_argument("--sku", help="override path to sku_master.csv")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--alpha", type=float, default=0.7, help="weight on similarity; 1-alpha goes to margin")
    ap.add_argument("--min-similarity", type=float, default=0.15)
    ap.add_argument("--price-band", type=float, nargs=2, default=(0.5, 1.5), metavar=("LOW", "HIGH"),
                    help="substitute price as a multiple of the original")
    ap.add_argument("--min-candidates", type=int, default=3)
    ap.add_argument("--demo", action="store_true", help="print a few stock-out examples")
    a = ap.parse_args()

    data = Path(a.data_dir)
    sku = pd.read_csv(a.sku or data / "sku_master.csv")
    need = {"product_id", "product_name", "category", "price", "profit_margin_pct"}
    if not need.issubset(sku.columns):
        raise SystemExit(f"sku_master.csv must have {sorted(need)}")
    sku = sku.dropna(subset=["price", "profit_margin_pct"]).reset_index(drop=True)

    out = build_matrix(sku, a.top_k, a.alpha, a.min_similarity, a.price_band[0], a.price_band[1], a.min_candidates)
    out.to_csv(data / "substitution_matrix.csv", index=False)
    covered = out["target_product_id"].nunique()
    top1 = out[out["rank"] == 1]
    print(f"[matrix] {len(out):,} rows; {covered:,}/{len(sku):,} SKUs ({covered / len(sku):.0%}) have at least one substitute")
    print(f"[matrix] rank-1 substitutes: mean similarity {top1['similarity_score'].mean():.2f}, "
          f"same brand {top1['same_brand'].mean():.0%}, mean margin weight {top1['margin_weight'].mean():.2f}")
    print(f"[write] {data / 'substitution_matrix.csv'}")

    if a.demo:
        eng = SubstitutionEngine(data / "substitution_matrix.csv")
        for pid in top1["target_product_id"].sample(3, random_state=7):
            tgt = sku.loc[sku["product_id"] == pid].iloc[0]
            print(f"\nSTOCKOUT: {tgt['product_name']} ({tgt['category']}, price {tgt['price']}, margin {tgt['profit_margin_pct']}%)")
            for s in eng.suggest(pid, k=2):
                print(f"  -> {s['substitute_name']}  sim {s['similarity_score']}  margin_w {s['margin_weight']}  "
                      f"price x{s['price_ratio']}  score {s['final_score']}")


if __name__ == "__main__":
    main()
