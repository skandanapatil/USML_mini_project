# Simulation Results Report

**Project:** Self-Healing Quick-Commerce Command Center  
**Report date:** 24 November 2026  
**Scope:** Interpretation of the five dashboard tabs and the currently displayed saved simulation results.

## Executive summary

The dashboard presents a simulated Bengaluru quick-commerce network with 5 regional distribution centers (RDCs) and 20 micro-hubs. The results are **mixed**:

- The self-healing scenario uses fewer modeled rider trips and has slightly higher modeled contribution per order.
- It has slower average and 90th-percentile delivery times than the baseline in the base scenario.
- Against a fixed 15-minute deadline, self-healing meets the SLA less often, despite its higher result against tier-specific promised deadlines.
- Stress-test results suggest self-healing can help during some severe disruptions, but performance remains poor under extreme slow traffic.
- The map identifies possible coverage gaps, while the inventory page demonstrates modeled responses to stock-outs.

These are outputs of a simulation, not measurements of a live operation. They are useful for identifying trade-offs and follow-up questions, not for claiming guaranteed real-world performance.

## 1. Control map

### What it shows

The map displays 5 RDCs, 20 micro-hubs, approximate coverage areas, and demand-area outlines. It can be filtered by RDC. The accompanying control loop organizes events into Detect, Decide, Act, and Measure stages.

### Current result and interpretation

The dashboard reports **59.3% of modeled orders within 2 km** of a hub under its coverage rule.

- **Potentially positive:** The map makes the modeled network layout and areas of demand visible.
- **Concern:** The remaining modeled orders fall outside that coverage rule, suggesting locations to investigate.
- **Limit:** This is a distance-based proxy. It does not establish road accessibility, delivery-time performance, or actual service coverage. The map is not a live map or a road-routing engine.

The control-loop panel depends on the selected simulation time. At 18:00 it may show no alerts; advancing Playback can reveal later events.

## 2. Consolidation

### What it shows

This tab compares Baseline and Self-healing results, delivery-tier metrics, modeled add-on and co-location actions, and a Green-delivery incentive sweep.

### Current base-scenario comparison

| Metric | Baseline | Self-healing | Interpretation |
|---|---:|---:|---|
| Tier-specific promised SLA met | 92.1% | 93.5% | Self-healing is higher under each tier's promise. |
| Average delivery time | 9.2 min | 10.4 min | Self-healing is slower on average. |
| P90 delivery time | 14.0 min | 16.1 min | The slower tail is worse; 90% of orders are at or below this time. |
| Rider trips | 1,336 | 1,243 | Self-healing uses 93 fewer modeled trips. |
| Cancelled | 0.1% | 0.1% | No meaningful change in this run. |
| Contribution per order | Rs 7.76 | Rs 8.26 | A small modeled increase; this is not the same as net profit. |
| Fixed 15-minute SLA met | 92.1% | 85.6% | Self-healing is lower under a common deadline. |

The promised-SLA and fixed 15-minute figures answer different questions. Delivery tiers can have different promise lengths, so a good promised-SLA percentage does not necessarily mean more orders arrived within 15 minutes.

The displayed Self-healing tier results are:

| Tier | Orders | Average time | Tier SLA met |
|---|---:|---:|---:|
| Express | 36 | 9.4 min | 91.7% |
| Instant | 699 | 8.8 min | 93.6% |
| Green | 601 | 12.4 min | 93.5% |

The Green tier's longer average time is consistent with a slower-delivery option; its tier-specific SLA should not be read as a 15-minute SLA. The reported average Green batch size is 1.33.

Other displayed actions include 177 accepted recipe add-ons out of 957 offers, and 119 co-location orders, 98.3% of which were filled at one hub. These are modeled interactions and fulfillment outcomes, not proof of customer satisfaction or financial benefit. The incentive sweep is useful for comparing adoption and incentive settings; cost and service results should be considered together.

**Assessment: mixed.** Fewer trips and higher modeled contribution are positive; slower deliveries and weaker performance against a fixed deadline are negative.

## 3. RDC inventory

### What it shows

This tab selects a micro-hub and displays its modeled service and distance measures, stock-out responses, and truck or peer-to-peer (P2P) transfers.

For the currently selected **MH-05**, the dashboard reports **445 orders served**, **1.8 km P90 coverage**, and **3.9 km to its RDC**. Its event feed includes examples of sourcing an item from a neighboring hub, offering a substitute, or dropping an unavailable item.

- **Potentially positive:** The simulation has multiple ways to respond when a hub cannot fulfill an item.
- **Concern:** Stock-outs still occur, and some responses mean a substitute or a dropped item.
- **Limit:** This is a stock-out response view, not a live inventory balance or proof that a real transfer could arrive on time.

The “good” or “bad” outcome depends on whether the particular substitute, transfer, or item drop is acceptable and economical.

## 4. Stress tests

### What it shows

The stress-test chart compares modeled scenarios including normal peak demand, hub gridlock, demand surges, severe slow traffic, and a no-surcharge case. It can display promised SLA, delivery time, cancellations, or contribution, with an option to use a strict 15-minute SLA.

The current dashboard labels the experiment results **Mean of 3 seeds**. These are quick exploratory results, not a full or highly certain statistical study.

Examples from the current stress-test results:

- In a **2.5× surge plus gridlock** scenario, modeled promised-SLA performance changes from 45.6% to 85.0% with Self-healing; average delivery time changes from 18.0 to 12.9 minutes, and cancellations from 12.1% to 1.5%.
- In a **2.5× surge, gridlock, and 6 km/h** scenario, promised SLA changes from 4.7% to 9.6% and cancellations from 81.1% to 68.2%. This is an improvement, but absolute performance remains very poor.
- In the normal-peak experiment, promised SLA changes from 93.2% to 95.6%, while the strict 15-minute result changes from 93.2% to 86.9%.

**Assessment: conditionally positive, with important limits.** Self-healing helps in some modeled disruptions, but it does not solve extreme conditions. Three seeds are not enough to treat these values as dependable forecasts.

## 5. Playback

### What it shows

Playback moves through the saved scenario timeline. It shows active orders and event counts or feeds for actions such as reroutes, P2P transfers, trucks, and add-ons.

At 18:00, the dashboard shows zero active orders and zero events. That describes the selected point in the timeline; it does not mean the entire simulation had no events. Advancing the slider or pressing play shows later saved activity.

**Assessment:** useful for understanding when the model triggers actions. It is a replay of static scenario data, not live operations monitoring.

## Overall conclusion

The simulation suggests a trade-off: self-healing may reduce trips and improve results in some surge-and-gridlock cases, but the base run is slower on average and performs worse against a common 15-minute deadline. Network coverage and stock-outs also remain areas to investigate. No single tab establishes that the system is unconditionally “good” or “bad”; the answer depends on which outcome matters most—speed, promised SLA, trip count, cancellations, or contribution.

## Limitations and recommended interpretation

- Orders, hub locations, demand, stock, traffic, rider speeds, costs, customer choices, and delivery behavior are modeled or drawn from datasets that may not represent the target operation.
- Map coverage circles are approximations, not road-network service areas.
- Stress results currently use 3 seeds; rerun the full experiment configuration before making stronger comparisons.
- “Contribution per order” is a modeled metric and should not be treated as net profit without accounting for all relevant costs.
- Validate the assumptions and compare against real operational data before using these results for a deployment decision.
