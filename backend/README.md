# Self-Healing Quick-Commerce Intelligence Platform (Bangalore)

An academic mini project that replays the same evening of quick-commerce orders twice, once with a **baseline** network and once with a **self-healing** network, and measures the difference. It follows a Detect -> Decide -> Act -> Measure loop: spot a stock-out or a gridlocked hub, choose a fix, apply it, and measure the effect.

All results are precomputed and static. The frontend only displays the saved files; nothing runs live.

## What it does

| Step | Method | Output |
| --- | --- | --- |
| Network design | Ward hierarchical clustering (5 RDCs, chosen by silhouette) and DBSCAN with haversine distance (20 micro-hubs, large hubs split) | `hubs.geojson`, `hubs_micro.csv`, `hubs_macro.csv` |
| Substitution | TF-IDF cosine similarity, ranked by 0.7 x similarity + 0.3 x margin | `substitution_matrix.csv` |
| Basket rules | Apriori on 300,000 Instacart baskets (recipe add-ons, co-location pairs) | `rules_recipe_completion.csv`, `colocation_pairs.csv` |
| Healing logic | ETA-based rerouting, neighbour sourcing, predictive P2P rebalancing, ABC-based truck replenishment, Green micro-batching | `sla_alerts.csv`, `sla_sim_orders.csv`, `p2p_transfers.csv`, `replenishment_alerts.csv` |
| Stress tests | 7 scenarios x 10 seeds = 70 paired runs, 95% confidence intervals | `experiment_summary.csv`, `experiment_runs.csv` |

## Repository layout

```
quickcommerce_data_prep.py   simulated orders + SKU master catalogue
01_spatial_clustering.py     RDCs, micro-hubs, coverage geometry
02_substitution_engine.py    TF-IDF substitution matrix
03_basket_affinity.py        Apriori rules
04_hub_rerouting.py          HubNetwork class, rerouting and stock-out demo
05_sla_simulation.py         one scenario, baseline vs self-healing
06_experiments.py            stress ladder, resumable, writes the PDF summary
run_all.py                   runs the steps in order
data/                        generated results served by the frontend
```

## Setup

Python 3.10+ and: `numpy pandas scikit-learn scipy openpyxl` (`matplotlib` is optional, for the cluster map).

Raw datasets are stored in the repository's `dataset/` folder. Pipeline outputs are written to `backend/data/`, the directory served by the frontend. The exact input file names are listed in `quickcommerce_data_prep.py`.

## Run

```
python run_all.py                       # steps 1-6
python run_all.py --with-experiments    # also the 70-run stress ladder (several minutes)
python run_all.py --only sla            # one step; earlier steps must have run
```

`06_experiments.py` caches finished runs, so it can be stopped and resumed (`--time-budget` limits a session).

## Frontend

The UI reads `static_results/` only. See the frontend handover document for the data contract, element list and pop-up catalogue. Key rules: promised SLA and strict 15-minute SLA are both shown, and every number is bound to a file, not typed in.

## Headline results (promised SLA met, baseline -> self-healing, mean of 10 seeds)

| Scenario | Result |
| --- | --- |
| Normal peak hour | 93.3% -> 95.5% |
| Surge 1.5x + gridlock | 76.4% -> 88.3% |
| Surge 2.5x + gridlock | 46.9% -> 83.9% |
| Surge 2.5x, no surcharge | 46.9% -> 67.6% |
| Surge 2.5x + 6 km/h peak speed | 4.7% -> 9.2% (both networks collapse) |

Trade-off: on the strict 15-minute SLA in calm hours, self-healing is lower (93.3% -> 86.7%) because Green customers wait for a batch.

## Limitations

- Order locations are simulated around real Bangalore areas; 14 days are overlaid on one evening to create density.
- Carts come from US Instacart baskets mapped onto an Indian catalogue; 69% of SKUs map to an Instacart aisle.
- Distances use road km = 1.4 x straight line and a time-of-day speed rule, not live traffic.
- Customer behaviour rates (Green switching, add-on acceptance, cancellations) are assumptions, not estimates.
- Costs and fees are illustrative; compare scenarios with each other, not as a real P&L.
- Substitution precision@1 (77% vs 3% random) measures product-family recovery, not customer acceptance.
- Seeds vary the incident, stock and customer choices but not the order locations.
