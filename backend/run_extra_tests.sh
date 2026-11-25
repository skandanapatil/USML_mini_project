#!/usr/bin/env bash
# Optional what-if runs of 05_sla_simulation.py. Each writes into data/extra_tests/<name>/.
# None of these are needed by the docs; they test the report's untested suggestions and assumptions.
# Not run by Claude (the data files are on your computer). Run from the folder holding 05_sla_simulation.py.
FAILED=""
run () { name="$1"; shift; echo "== $name: $*"; python 05_sla_simulation.py --out-dir "data/extra_tests/$name" "$@" || { echo "!! $name FAILED"; FAILED="$FAILED $name"; }; }

# Green wait (report suggests a shorter window to cut P90)
run window_1min      --window 1
run window_2min      --window 2
run window_5min      --window 5
run maxbatch_2       --max-batch 2
run maxbatch_4       --max-batch 4
run no_sla_batching  --no-sla-aware-batching

# Add-ons, P2P, stock
run addon_off        --addon-accept 0
run addon_30pct      --addon-accept 0.3
run p2p_hourly       --p2p-interval 60
run p2p_min25        --p2p-min-units 25
run no_replenish     --no-replenishment

# Fleet assumptions (stress-test caveat)
run fleet_flex_0     --fleet-flex 0
run fleet_flex_1     --fleet-flex 1
run util_80          --target-util 0.8

# Customer behaviour assumptions
run drop_pref_0      --drop-pref-share 0
run drop_pref_50     --drop-pref-share 0.5
run green_share_60   --green-share 0.6

python compare_extra_tests.py
echo "Failed tests:${FAILED:- none}"
