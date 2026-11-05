#!/usr/bin/env python3
"""
run_all.py - runs the whole mini-project pipeline in order.

  1. quickcommerce_data_prep.py   simulated orders + 2,500-SKU master catalog
  2. 01_spatial_clustering.py     RDCs, micro-hubs, coverage geometry (hubs.geojson)
  3. 02_substitution_engine.py    TF-IDF substitution matrix
  4. 03_basket_affinity.py        Apriori recipe-completion and co-location rules (Instacart)
  5. 04_hub_rerouting.py          gridlock rerouting + stock-out healing demo
  6. 05_sla_simulation.py         SLA timer, micro-batching, baseline vs self-healing

Put this file in the same folder as the other scripts. File locations are set at the top
of each script (RAW_DIR); nothing else needs to be typed.

Usage
  python run_all.py                       # everything
  python run_all.py --skip basket         # leave out a step (names: prep, cluster, subs, basket, reroute, sla)
  python run_all.py --only reroute sla    # run just these steps (the earlier steps must have run before)
  python run_all.py --keep-going          # do not stop if a step fails

Requires: numpy, pandas, scikit-learn, scipy, openpyxl (matplotlib is optional, for the cluster map)
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

STEPS = [
    ("prep",    "quickcommerce_data_prep.py", []),
    ("cluster", "01_spatial_clustering.py",   ["--plot"]),
    ("subs",    "02_substitution_engine.py",  ["--demo"]),
    ("basket",  "03_basket_affinity.py",      ["--demo"]),
    ("reroute", "04_hub_rerouting.py",        []),
    ("sla",     "05_sla_simulation.py",       []),
]


def main():
    names = [n for n, _, _ in STEPS]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip", nargs="*", default=[], choices=names, help="steps to leave out")
    ap.add_argument("--only", nargs="*", default=[], choices=names, help="run only these steps")
    ap.add_argument("--keep-going", action="store_true", help="continue after a failed step")
    ap.add_argument("--quiet", action="store_true", help="hide each step's output (show only the status lines)")
    a = ap.parse_args()

    todo = [s for s in STEPS if (not a.only or s[0] in a.only) and s[0] not in a.skip]
    results = []
    t_all = time.time()
    for i, (name, script, extra) in enumerate(todo, 1):
        path = HERE / script
        print(f"\n{'=' * 70}\n[{i}/{len(todo)}] {name}: {script}\n{'=' * 70}", flush=True)
        if not path.exists():
            print(f"  missing file: {path}")
            results.append((name, "MISSING", 0.0))
            if not a.keep_going:
                break
            continue
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(path), *extra], cwd=str(HERE),
                              stdout=subprocess.PIPE if a.quiet else None,
                              stderr=subprocess.STDOUT if a.quiet else None, text=True)
        dt = time.time() - t0
        ok = proc.returncode == 0
        results.append((name, "ok" if ok else "FAILED", dt))
        if not ok:
            if a.quiet and proc.stdout:
                print(proc.stdout[-2000:])
            print(f"\n  step '{name}' failed (exit code {proc.returncode}).")
            if not a.keep_going:
                break

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for name, status, dt in results:
        print(f"  {name:<8} {status:<8} {dt:6.1f}s")
    print(f"  total {time.time() - t_all:.1f}s")
    sys.exit(0 if all(s == "ok" for _, s, _ in results) else 1)


if __name__ == "__main__":
    main()
