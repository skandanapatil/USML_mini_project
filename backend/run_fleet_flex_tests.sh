#!/usr/bin/env bash
# Tests the fleet-sizing assumption behind the stress ladder: how many extra riders appear in a surge.
# fleet-flex 0 = no extra riders, 0.5 = the value used everywhere so far (2.5x demand brings 1.75x riders), 1 = riders scale fully.
# Same setup as stress scenario 5 (surge x2.5 with a random hub gridlocked), one run per setting (seed 42).
# Not run by Claude. Run from the folder holding 05_sla_simulation.py. Takes about 5 minutes in total.
FAILED=""
for flex in 0 0.25 0.5 0.75 1; do
  name="surge25_flex_${flex}"
  echo "== $name"
  python 05_sla_simulation.py --surge 2.5 --gridlock-hub random --fleet-flex "$flex" --out-dir "data/extra_tests/$name" \
    || { echo "!! $name FAILED"; FAILED="$FAILED $name"; }
done
python compare_fleet_flex.py
echo "Failed tests:${FAILED:- none}"
