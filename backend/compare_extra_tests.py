"""Collect test summaries from backend/data/extra_tests into backend/data."""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
TEST_DIR = HERE / "data" / "extra_tests"
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

rows = []
for d in sorted(TEST_DIR.glob("*/")):
    f = d / "sla_sim_summary.csv"
    if not f.exists():
        continue
    s = pd.read_csv(f, index_col=0)
    sh = s["self_healing"]
    rows.append({"test": d.name, "promised_sla_pct": sh["sla_met_pct"], "strict_sla_pct": sh["met_standard_sla_pct"],
                 "avg_delivery_min": sh["avg_delivery_min"], "p90_min": sh["p90_delivery_min"],
                 "rider_trips": sh["rider_trips"], "net_cost_per_order": sh["net_cost_per_order"],
                 "contribution_per_order": sh["contribution_per_order"], "stockout_events": sh["stockout_events"],
                 "addon_accepted": sh["addon_accepted"], "p2p_transfers": sh["p2p_transfers"]})
out = pd.DataFrame(rows)
out.to_csv(DATA_DIR / "extra_tests_summary.csv", index=False)
print(out.to_string(index=False))
