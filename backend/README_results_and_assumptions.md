# Static results package: self-healing quick-commerce mini project

All numbers here were computed offline and are presented as static results. Nothing needs to be run live.
The headline experiment is **7 scenarios x 10 random seeds = 70 simulation runs**. In every run the baseline
and the self-healing network see exactly the same orders, carts, stock and incident (a paired comparison).
Intervals are 95% confidence intervals of the paired difference across seeds.

## 1. Headline: stress ladder (baseline → self-healing, mean of 10 seeds)

| Scenario | Promised SLA met % | Standard 15-min SLA met % | Avg delivery (min) | Cancelled % | Contribution (Rs/order) |
|---|---|---|---|---|---|
| 1 Normal peak hour | 93.3 → 95.5 | 93.3 → 86.7 | 9.0 → 10.3 | 0.1 → 0.2 | 5.3 → 5.5 |
| 2 + hub gridlock | 92.2 → 94.7 | 92.2 → 86.0 | 9.1 → 10.4 | 0.2 → 0.3 | 5.2 → 5.3 |
| 3 Surge x1.5 + gridlock | 76.4 → 88.3 | 76.4 → 77.0 | 11.9 → 11.8 | 2.7 → 1.3 | 15.5 → 18.3 |
| 4 Surge x2.0 + gridlock | 60.6 → 85.3 | 60.6 → 71.4 | 15.1 → 12.7 | 7.0 → 2.0 | 20.6 → 26.4 |
| 5 Surge x2.5 + gridlock | 46.9 → 83.9 | 46.9 → 68.4 | 18.1 → 13.2 | 13.1 → 2.4 | 22.3 → 32.5 |
| 6 Surge x2.5 + gridlock + 6 km/h | 4.7 → 9.2 | 4.7 → 5.1 | 45.3 → 40.7 | 82.5 → 69.6 | -42.6 → -26.6 |
| 7 Surge x2.5, no surcharge | 46.9 → 67.6 | 46.9 → 58.8 | 18.1 → 15.4 | 13.1 → 6.7 | 4.6 → 17.5 |

*Promised SLA* gives Green (consolidated) customers 20 minutes; *Standard* holds everyone to 15 minutes. Both are shown so the comparison is honest.

**How to read it**
- **Calm hours look alike, with a visible trade-off.** Self-healing is about 1.3 minutes slower on average (Green customers wait for a batch) and meets the strict 15-minute SLA less often (86.7% vs 93.3%).
- **The gap opens under stress.** At a 2.5x surge with a gridlocked hub, baseline on-time performance falls to 46.9% while self-healing holds 83.9% (68.4% on the strict 15-minute basis). Cancellations are 2.4% vs 13.1%, and contribution per order is Rs 32.5 vs Rs 22.3.
- **What drives the surge result is demand shaping and batching, not rerouting.** Scenario 7 removes surcharge pricing: self-healing then reaches only 67.6% (from 83.9%), so surcharge-driven switching to Green accounts for roughly 16 points of the 2.5x result.
- **There is a limit.** At a 6 km/h peak speed both networks collapse (4.7% vs 9.2% on time; 70-83% of orders cancelled). Software cannot fix a network that slow.
- **Rerouting is a minor lever on road distances.** With each hub gridlocked in turn, rerouting rescues about 16% of the orders that would otherwise be late; neighbouring hubs are usually too far to be faster at peak speeds.

## 2. Supporting results (single base scenario: evening peak, one gridlocked hub)
- **Delivery tiers** (`2_base_scenario/sla_tier_summary.csv`): Express 36 orders, Instant 699, Green 601 (average batch 1.35 orders). Green orders wait about 3 minutes longer by design.
- **Green adoption sweep** (`green_adoption_sweep.csv`), logistics cost per order under four incentive rules, in Rs:

| Green share | Promised SLA % | Standard SLA % | Rider trips | Cashback | Conditional | Nudge | Hybrid |
|---|---|---|---|---|---|---|---|
| 0% | 92.4 | 89.1 | 1331 | 44.31 | 43.74 | 43.77 | 43.8 |
| 25% | 93.4 | 87.0 | 1280 | 44.05 | 42.75 | 42.53 | 42.92 |
| 50% | 94.6 | 84.7 | 1203 | 42.92 | 41.13 | 40.49 | 41.4 |
| 75% | 95.1 | 80.4 | 1103 | 41.22 | 38.94 | 37.77 | 39.33 |
| 100% | 95.9 | 77.5 | 975 | 38.67 | 36.21 | 34.29 | 36.7 |

  Always-pay cashback is the most expensive rule at every adoption level; the conditional, nudge and hybrid rules all cost less.
- **Proactive P2P rebalancing:** stock-out events fall from 131 to 61 and exact-item fill rises from 95.8% to 98.0%. It costs about Rs 1.7 per order and does not reduce RDC truck trips, so it pays for itself only if one avoided stock-out is worth more than about Rs 33 to the business.
- **Basket rules:** recipe add-ons offered 956, accepted 177 (+Rs 18,682 sales, +Rs 2,402 margin); 119 multi-category orders met a co-location rule and 98.3% were filled at a single hub.
- **Substitution engine validation:** with the sub-category text hidden, the nearest neighbour is in the right product family 77% of the time (precision@1), against 3% for random guessing. This measures product-family recovery, not customer acceptance.
- **Network design:** 5 RDCs (hierarchical clustering), 20 micro-hubs (DBSCAN, with large hubs split), 59.3% of simulated orders within 2 road km of a hub.

## 3. Key assumptions (all adjustable, none measured)
| Area | Assumption |
|---|---|
| Road model | Road distance = 1.4 x straight line; rider speed 20 km/h, 11 km/h from 18:00 to 20:00, a gridlocked hub x0.55 |
| Service levels | Standard SLA 15 min; Green SLA 20 min; Green orders wait up to 3 min and merge within 1 km, up to 3 per rider, only if the SLA is still safe |
| Fleet | Sized once for normal demand at 70% utilisation; a surge of x gives 1 + 0.5(x-1) times the riders |
| Fees | Base fee Rs 20; traffic fee Rs 10 when speed < 15 km/h; surge fee Rs 10 per 1.0 above 1x (cap Rs 30); Express orders exempt; Rs 5 Green incentive |
| Behaviour | Instant customers switch to Green with probability 2% per Rs of surcharge; 20% accept a recipe add-on; 20% choose "drop item if out of stock"; orders later than 30 min are cancelled |
| Costs | Courier Rs 30 per trip + Rs 6 per km; RDC truck Rs 500 + Rs 20 per km; P2P van Rs 120 + Rs 10 per km |
| Inventory | A/B/C Pareto seeding (A items 15-30 units, B 8-15, C 2-5); demand 80% / 15% / 5% by class; 5% of SKUs start at zero |

## 4. Limitations to state when presenting
1. **Synthetic demand locations.** Orders are simulated around real Bangalore areas; 14 days are overlaid onto one evening to create density. Surges are modelled as extra jittered copies of orders.
2. **Real baskets, but US data.** Carts come from real Instacart baskets mapped onto an Indian grocery catalogue; 69% of SKUs map to an Instacart aisle, so the rules are a best-effort bridge.
3. **No live traffic.** Distances use a flat circuity factor and a time-of-day speed rule, not routing data. Coverage (2 road km) is fixed in step 1, so only 59% of simulated orders are serviceable.
4. **Behavioural rates are assumptions.** Switching to Green, add-on acceptance, cancellations and the Green incentive response have not been estimated from customers.
5. **Costs and fees are illustrative.** Contribution figures compare scenarios against each other; they are not a real P&L.
6. **No substitution acceptance data.** The validation measures product-family recovery only.
7. **Seeds vary the incident, stock and customer choices, but not the underlying order locations.** The intervals therefore do not cover uncertainty in the demand map itself.
