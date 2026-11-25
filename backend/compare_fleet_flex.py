"""Collect fleet-flex results from backend/data/extra_tests into backend/data."""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
TEST_DIR = HERE / "data" / "extra_tests"
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

KEYS = ["sla_met_pct", "met_standard_sla_pct", "avg_delivery_min", "p90_delivery_min", "orders_cancelled_pct",
        "rider_trips", "net_cost_per_order", "contribution_per_order"]
rows = []
for d in sorted(TEST_DIR.glob("surge25_flex_*")):
    f = d / "sla_sim_summary.csv"
    if not f.exists():
        continue
    s = pd.read_csv(f, index_col=0)
    row = {"fleet_flex": float(d.name.split("_")[-1])}
    for k in KEYS:
        row[f"baseline_{k}"] = s.loc[k, "baseline"]
        row[f"healing_{k}"] = s.loc[k, "self_healing"]
    row["promised_sla_gain_pts"] = round(row["healing_sla_met_pct"] - row["baseline_sla_met_pct"], 1)
    rows.append(row)
out = pd.DataFrame(rows).sort_values("fleet_flex")
out.to_csv(DATA_DIR / "fleet_flex_summary.csv", index=False)
print(out[["fleet_flex", "baseline_sla_met_pct", "healing_sla_met_pct", "promised_sla_gain_pts",
           "baseline_orders_cancelled_pct", "healing_orders_cancelled_pct",
           "baseline_p90_delivery_min", "healing_p90_delivery_min"]].to_string(index=False))
