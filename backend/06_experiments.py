#!/usr/bin/env python3
"""
MODULE 6 - Stress tests with Monte Carlo replication (final results for the report)

Runs module 5 repeatedly, with a different random seed each time, over a ladder of scenarios:

  1 Normal peak hour
  2 + one hub gridlocked for 30 minutes (a random hub each run)
  3 Surge x1.5 + gridlock
  4 Surge x2.0 + gridlock
  5 Surge x2.5 + gridlock          (festival rush: Diwali, Big Billion Days)
  6 Surge x2.5 + gridlock + slow traffic (peak speed 6 km/h)
  7 Surge x2.5 + gridlock, but NO surcharge pricing (isolates what dynamic pricing contributes)

In every run baseline and self-healing see the SAME orders, carts, stock and incident, so the
comparison is paired. Each seed changes the carts, stock-outs, customer choices, surge jitter and the
gridlocked hub. The rider fleet is sized once for normal conditions (see module 5), so surges and
slow traffic really do overload it.

Outputs (in DATA_DIR / experiments)
  experiment_runs.csv      every run, every metric
  experiment_summary.csv   mean and 95% confidence interval per scenario, baseline vs self-healing,
                           and the paired difference (self-healing minus baseline)
  results_summary.pdf      a short easy-read summary: table plus stress-ladder charts

Usage
  python 06_experiments.py              # 10 seeds per scenario (about 5-10 minutes)
  python 06_experiments.py --quick      # 3 seeds, to check that everything works
  python 06_experiments.py --reps 20 --workers 6

Requires: numpy, pandas; matplotlib for the PDF; scipy optional (exact t-intervals)
"""
import argparse
import os
import time
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

# Same folders as quickcommerce_data_prep.py
RAW_DIR = Path(r"E:\USML_mini_project\dataset")
DATA_DIR = Path(r"E:\USML_mini_project\backend\data")
HERE = Path(__file__).resolve().parent

SCENARIOS = [
    ("1 Normal peak hour",               ["--surge", "1.0", "--gridlock-hub", "none"]),
    ("2 + hub gridlock",                 ["--surge", "1.0", "--gridlock-hub", "random"]),
    ("3 Surge x1.5 + gridlock",          ["--surge", "1.5", "--gridlock-hub", "random"]),
    ("4 Surge x2.0 + gridlock",          ["--surge", "2.0", "--gridlock-hub", "random"]),
    ("5 Surge x2.5 + gridlock",          ["--surge", "2.5", "--gridlock-hub", "random"]),
    ("6 Surge x2.5 + gridlock + 6 km/h", ["--surge", "2.5", "--gridlock-hub", "random", "--peak-speed", "6"]),
    ("7 Surge x2.5, no surcharge",       ["--surge", "2.5", "--gridlock-hub", "random", "--no-surcharge"]),
]

# metric -> (label, higher is better?)
METRICS = {
    "sla_met_pct": ("Promised SLA met (%)", True),
    "met_standard_sla_pct": ("Standard 15-min SLA met (%)", True),
    "avg_delivery_min": ("Average delivery time (min)", False),
    "p90_delivery_min": ("90th percentile delivery (min)", False),
    "trips_per_order": ("Rider trips per order", False),
    "orders_cancelled_pct": ("Orders cancelled (%)", False),
    "exact_item_fill_pct": ("Items delivered as ordered (%)", True),
    "net_cost_per_order": ("Logistics cost per order (Rs)", False),
    "contribution_per_order": ("Contribution per order (Rs)", True),
}
MODES = {"baseline": "baseline", "self_healing": "self_healing"}


def run_one(task):
    """One simulation. Finished runs are cached, so an interrupted experiment resumes where it stopped."""
    idx, name, args, seed, data_dir, work, cache, deadline = task
    cf = cache / f"{idx}.csv"
    if cf.exists():
        return pd.read_csv(cf).to_dict("records")
    if deadline and time.time() > deadline:
        return None                                      # out of time budget: resume on the next call
    out = work / f"run_{idx}"
    cmd = [sys.executable, str(HERE / "05_sla_simulation.py"), "--data-dir", str(data_dir), "--out-dir", str(out),
           "--seed", str(seed), *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"run failed ({name}, seed {seed}):\n{r.stderr[-1200:]}")
    summ = pd.read_csv(out / "sla_sim_summary.csv", index_col=0)
    shutil.rmtree(out, ignore_errors=True)
    rows = []
    for mode in MODES:
        v = summ[mode].astype(float).to_dict()
        v["trips_per_order"] = v["rider_trips"] / v["orders"]
        rows.append({"scenario": name, "seed": seed, "mode": mode, **{m: v[m] for m in METRICS}})
    pd.DataFrame(rows).to_csv(cf, index=False)
    return rows


def half_width(x):
    """95% confidence half-width of the mean."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float("nan")
    try:
        from scipy.stats import t
        crit = t.ppf(0.975, n - 1)
    except Exception:
        crit = 1.96
    return float(crit * x.std(ddof=1) / np.sqrt(n))


def summarise(runs):
    rows = []
    for sc, g in runs.groupby("scenario", sort=False):
        b = g[g["mode"] == "baseline"].set_index("seed")
        h = g[g["mode"] == "self_healing"].set_index("seed")
        for m in METRICS:
            d = (h[m] - b[m]).dropna()
            hw = half_width(d)
            rows.append({"scenario": sc, "metric": m, "reps": len(d),
                         "baseline_mean": b[m].mean(), "baseline_ci95": half_width(b[m]),
                         "healing_mean": h[m].mean(), "healing_ci95": half_width(h[m]),
                         "diff_mean": d.mean(), "diff_ci95": hw,
                         "clear_difference": bool(abs(d.mean()) > hw) if hw == hw else False})
    return pd.DataFrame(rows)


def pm(mean, hw, nd=1):
    return f"{mean:.{nd}f} +/- {hw:.{nd}f}"


def print_table(summ):
    keys = ["sla_met_pct", "met_standard_sla_pct", "avg_delivery_min", "orders_cancelled_pct", "contribution_per_order"]
    print("\nMean +/- 95% CI over the seeds (paired difference = self-healing minus baseline; * = interval excludes zero)\n")
    for sc, g in summ.groupby("scenario", sort=False):
        print(sc)
        for m in keys:
            r = g[g["metric"] == m].iloc[0]
            star = "*" if r["clear_difference"] else " "
            print(f"   {METRICS[m][0]:<32} baseline {pm(r['baseline_mean'], r['baseline_ci95']):>14} | "
                  f"self-healing {pm(r['healing_mean'], r['healing_ci95']):>14} | diff {r['diff_mean']:+7.1f} +/- {r['diff_ci95']:.1f} {star}")


def make_pdf(path, summ, reps):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except ImportError:
        print("[pdf] matplotlib not installed; skipping the PDF")
        return
    scen = list(summ["scenario"].unique())
    with PdfPages(path) as pdf:
        # page 1: table
        fig, ax = plt.subplots(figsize=(11.7, 8.3))
        ax.axis("off")
        ax.set_title(f"Self-healing quick-commerce network: stress-test results ({reps} seeds per scenario)",
                     fontsize=14, weight="bold", loc="left")
        rows = []
        for sc in scen:
            g = summ[summ["scenario"] == sc].set_index("metric")
            def cell(m, nd=1):
                r = g.loc[m]
                return f"{r['baseline_mean']:.{nd}f} -> {r['healing_mean']:.{nd}f}{'*' if r['clear_difference'] else ''}"
            rows.append([sc, cell("sla_met_pct"), cell("met_standard_sla_pct"), cell("avg_delivery_min"),
                         cell("orders_cancelled_pct"), cell("contribution_per_order")])
        tb = ax.table(cellText=rows, colLabels=["Scenario", "Promised\nSLA met %", "Standard 15-min\nSLA met %", "Avg delivery\n(min)",
                                                "Cancelled\n%", "Contribution\nRs per order"],
                      loc="upper center", cellLoc="center", colWidths=[0.28, 0.14, 0.15, 0.14, 0.12, 0.15])
        tb.auto_set_font_size(False)
        tb.set_fontsize(9)
        tb.scale(1, 2.6)
        ax.text(0, 0.30, "Each cell: baseline -> self-healing (mean of paired runs). * = the 95% interval of the paired difference excludes zero.\n"
                         "Promised SLA gives Green customers 20 min; the standard column holds everyone to 15 min.\n"
                         "All costs, fees, speeds and behaviour rates are assumptions of this model, not measured values.\n"
                         "Demand is simulated around real Bangalore areas; carts come from real Instacart baskets; no live traffic data.",
                fontsize=9, va="top", transform=ax.transAxes)
        pdf.savefig(fig)
        plt.close(fig)
        # page 2: stress ladder (scenarios 1-5)
        ladder = scen[:5]
        fig, axes = plt.subplots(1, 3, figsize=(11.7, 4.6))
        x = np.arange(len(ladder))
        labels = ["Normal", "Gridlock", "x1.5", "x2.0", "x2.5"]
        for ax, (m, title) in zip(axes, (("sla_met_pct", "On-time delivery (%)"), ("avg_delivery_min", "Average delivery (min)"),
                                         ("contribution_per_order", "Contribution per order (Rs)"))):
            g = summ[summ["metric"] == m].set_index("scenario").loc[ladder]
            ax.errorbar(x, g["baseline_mean"], yerr=g["baseline_ci95"], marker="o", capsize=3, label="Baseline (15 min)", color="#A32D2D")
            ax.errorbar(x, g["healing_mean"], yerr=g["healing_ci95"], marker="o", capsize=3,
                        label="Self-healing (promised: Green 20 min)" if m == "sla_met_pct" else "Self-healing", color="#0F6E56")
            if m == "sla_met_pct":                       # the like-for-like line: everyone held to 15 minutes
                g2 = summ[summ["metric"] == "met_standard_sla_pct"].set_index("scenario").loc[ladder]
                ax.errorbar(x, g2["healing_mean"], yerr=g2["healing_ci95"], marker="s", capsize=3, ls="--",
                            label="Self-healing (everyone held to 15 min)", color="#0F6E56", alpha=0.7)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=8)
            ax.set_title(title, fontsize=10)
            ax.grid(alpha=0.3)
        axes[0].legend(fontsize=7, loc="lower left")
        fig.suptitle("Stress ladder: calm hours look alike; the gap opens as demand surges (error bars: 95% CI over seeds)", fontsize=11)
        fig.tight_layout()
        pdf.savefig(fig)
        fig.savefig(Path(path).with_name("stress_ladder.png"), dpi=200)
        plt.close(fig)
    print(f"[pdf] wrote {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--reps", type=int, default=10, help="random seeds per scenario")
    ap.add_argument("--seed0", type=int, default=100, help="first seed")
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--quick", action="store_true", help="3 seeds, to check that everything runs")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--time-budget", type=float, default=0,
                    help="stop starting new runs after this many seconds (0 = no limit); rerun the same command to resume")
    ap.add_argument("--fresh", action="store_true", help="ignore cached runs and start over")
    a = ap.parse_args()
    if a.quick:
        a.reps = 3

    data = Path(a.data_dir)
    out = data / "experiments"
    work = out / "_runs"
    cache = out / "_cache"
    if a.fresh:
        shutil.rmtree(cache, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + a.time_budget if a.time_budget else 0
    tasks = []
    for i, (name, args) in enumerate(SCENARIOS):
        for k in range(a.reps):
            tasks.append((f"{i}_{k}", name, args, a.seed0 + k, data, work, cache, deadline))
    print(f"[plan] {len(SCENARIOS)} scenarios x {a.reps} seeds = {len(tasks)} runs on {a.workers} worker(s)")

    results, done = [], 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for n, rows in enumerate(ex.map(run_one, tasks), 1):
            if rows is not None:
                results.extend(rows)
                done += 1
            print(f"\r[run] {n}/{len(tasks)}", end="", flush=True)
    print()
    shutil.rmtree(work, ignore_errors=True)
    if done < len(tasks):
        print(f"[pause] {done}/{len(tasks)} runs are saved. Run the same command again to continue.")
        return

    runs = pd.DataFrame(results)
    runs.to_csv(out / "experiment_runs.csv", index=False)
    summ = summarise(runs)
    summ.to_csv(out / "experiment_summary.csv", index=False)
    print_table(summ)
    if not a.no_pdf:
        make_pdf(out / "results_summary.pdf", summ, a.reps)
    print(f"\n[write] experiment_runs.csv, experiment_summary.csv -> {out}")


if __name__ == "__main__":
    main()
