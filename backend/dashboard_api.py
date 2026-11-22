#!/usr/bin/env python3
"""Local HTTP adapter for the SCM Command Center React dashboard."""
import csv
import json
import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATA_DIR = BACKEND / "data"
FRONTEND_DIST = ROOT / "frontend" / "dist"
PORT = int(os.environ.get("SCM_API_PORT", "8787"))
LOCK = threading.Lock()
RUN_STATE = {
    "running": False,
    "stage": "Idle",
    "message": "Waiting for a scenario.",
    "error": "",
    "logs": [],
    "started_at": None,
    "finished_at": None,
}


def read_csv(path, limit=None):
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if limit is not None:
        rows = rows[-limit:]
    return rows


def numeric(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() else result


def typed_rows(path, numeric_columns=(), boolean_columns=(), limit=None):
    rows = read_csv(path, limit=limit)
    for row in rows:
        for column in numeric_columns:
            if column in row:
                row[column] = numeric(row[column])
        for column in boolean_columns:
            if column in row:
                row[column] = str(row[column]).strip().lower() in {"true", "1", "yes"}
    return rows


def read_summary(path):
    rows = read_csv(path)
    result = {}
    for row in rows:
        metric = row.get("") or row.get("Unnamed: 0")
        if not metric:
            continue
        result[metric] = {
            key: numeric(value)
            for key, value in row.items()
            if key and value not in (None, "")
        }
    return result


def read_json(path):
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def current_status():
    with LOCK:
        return dict(RUN_STATE)


def set_status(**values):
    with LOCK:
        RUN_STATE.update(values)


def add_log(message):
    with LOCK:
        RUN_STATE["logs"].append(message.rstrip())
        RUN_STATE["logs"] = RUN_STATE["logs"][-30:]


def dashboard_data():
    generated = {
        "network": (DATA_DIR / "hubs.geojson").is_file() and (DATA_DIR / "hubs_micro.csv").is_file(),
        "catalog": (DATA_DIR / "sku_master.csv").is_file(),
        "simulation": (DATA_DIR / "sla_sim_summary.csv").is_file(),
        "baskets": (DATA_DIR / "basket_summary.json").is_file(),
        "experiments": (DATA_DIR / "experiments" / "experiment_summary.csv").is_file(),
    }
    raw_summary = read_summary(DATA_DIR / "sla_sim_summary.csv")
    metrics = {}
    for key in ("baseline", "healing_reactive_only", "self_healing"):
        metrics[key] = {
            metric: values[key]
            for metric, values in raw_summary.items()
            if key in values
        }
    micro_numeric = (
        "lat", "lon", "dense_orders", "orders_served", "coverage_radius_p90_km",
        "demand_hull_km2", "dist_to_rdc_km",
    )
    macro_numeric = ("lat", "lon", "n_orders", "share_of_orders")
    order_numeric = (
        "total_min", "sla_limit", "n_items", "n_ok", "n_substituted", "n_sourced",
        "n_dropped", "n_missing", "t", "batch_size",
    )
    tiers = typed_rows(
        DATA_DIR / "sla_tier_summary.csv",
        numeric_columns=("orders", "avg_min_baseline", "avg_min_healing", "promised_sla_min", "sla_met_pct_healing", "standard_sla_met_pct_healing", "avg_batch_size"),
    )
    alerts = typed_rows(DATA_DIR / "sla_alerts.csv", limit=300)
    orders = typed_rows(
        DATA_DIR / "sla_sim_orders.csv",
        numeric_columns=order_numeric,
        boolean_columns=("sla_met",),
        limit=16,
    )
    orders = [row for row in orders if row.get("mode") == "self_healing"]

    experiment_rows = read_csv(DATA_DIR / "experiments" / "experiment_summary.csv")
    experiments = []
    for row in experiment_rows:
        if row.get("metric") != "sla_met_pct":
            continue
        experiments.append({
            "scenario": row.get("scenario", ""),
            "metric": row.get("metric", ""),
            "baseline_mean": numeric(row.get("baseline_mean")),
            "healing_mean": numeric(row.get("healing_mean")),
            "diff_mean": numeric(row.get("diff_mean")),
        })

    basket_summary = read_json(DATA_DIR / "basket_summary.json")
    basket = {
        "available": generated["baskets"],
        "recipe_rules": basket_summary.get("recipe_rules"),
        "colocation_pairs_flagged": basket_summary.get("colocation_pairs_flagged"),
        "baskets_used": basket_summary.get("baskets_used"),
    }
    return {
        "generated": generated,
        "metrics": metrics,
        "hubs": typed_rows(DATA_DIR / "hubs_micro.csv", micro_numeric),
        "rdcs": typed_rows(DATA_DIR / "hubs_macro.csv", macro_numeric),
        "geojson": read_json(DATA_DIR / "hubs.geojson"),
        "tiers": tiers,
        "alerts": alerts,
        "orders": orders,
        "experiments": experiments,
        "basket": basket,
        "run": current_status(),
    }


def run_command(stage, command):
    set_status(stage=stage, message="Running " + " ".join(Path(part).name if part.endswith(".py") else part for part in command[1:3]))
    add_log("$ " + subprocess.list2cmdline(command))
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError(f"Unable to capture output for stage: {stage}")
    for line in process.stdout:
        add_log(line)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"{stage} failed with exit code {return_code}. See the run log for details.")


def run_pipeline(config):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        prep_inputs = [
            sys.executable,
            str(BACKEND / "quickcommerce_data_prep.py"),
            "--bigbasket",
            str(ROOT / "dataset" / "BigBasket Products.csv"),
            "--out-dir",
            str(DATA_DIR),
        ]
        pincodes = ROOT / "dataset" / "BangaloreAreaLatLongDetails.xlsx"
        if pincodes.is_file():
            prep_inputs.extend(("--pincodes", str(pincodes)))
        required_catalog = DATA_DIR / "sku_master.csv"
        required_orders = DATA_DIR / "orders_simulated.csv"
        if not required_catalog.is_file() or not required_orders.is_file():
            set_status(stage="Preparing data", message="Preparing the product catalog and simulated Bangalore demand.")
            if not (ROOT / "dataset" / "BigBasket Products.csv").is_file():
                raise RuntimeError("The BigBasket catalog is missing from dataset/.")
            run_command("Preparing data", prep_inputs)

        required_hubs = DATA_DIR / "hubs_micro.csv"
        if not required_hubs.is_file() or not (DATA_DIR / "orders_clustered.csv").is_file():
            run_command("Building network", [
                sys.executable,
                str(BACKEND / "01_spatial_clustering.py"),
                "--data-dir",
                str(DATA_DIR),
            ])

        if not (DATA_DIR / "substitution_matrix.csv").is_file():
            run_command("Building substitutions", [
                sys.executable,
                str(BACKEND / "02_substitution_engine.py"),
                "--data-dir",
                str(DATA_DIR),
                "--no-evaluate",
            ])

        if config["include_baskets"]:
            basket_path = DATA_DIR / "basket_summary.json"
            if not basket_path.is_file():
                instacart_dir = ROOT / "dataset"
                required = ("products.csv", "aisles.csv", "departments.csv", "orders.csv", "order_products__prior.csv")
                missing = [name for name in required if not (instacart_dir / name).is_file()]
                if missing:
                    raise RuntimeError("Cannot build basket rules; missing dataset files: " + ", ".join(missing))
                run_command("Mining basket rules", [
                    sys.executable,
                    str(BACKEND / "03_basket_affinity.py"),
                    "--instacart-dir",
                    str(instacart_dir),
                    "--out-dir",
                    str(DATA_DIR),
                    "--sample-orders",
                    "30000",
                    "--basket-sample",
                    "5000",
                ])

        basket_source = "instacart" if (DATA_DIR / "baskets_sample.csv").is_file() else "pareto"
        simulation_command = [
            sys.executable,
            str(BACKEND / "05_sla_simulation.py"),
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(DATA_DIR),
            "--surge",
            str(config["surge"]),
            "--green-share",
            str(config["green_share"]),
            "--seed",
            str(config["seed"]),
            "--basket-source",
            basket_source,
        ]
        run_command("Running paired simulation", simulation_command)

        if config["run_experiments"]:
            run_command("Running experiment ladder", [
                sys.executable,
                str(BACKEND / "06_experiments.py"),
                "--data-dir",
                str(DATA_DIR),
                "--quick",
                "--workers",
                "2",
            ])

        set_status(
            running=False,
            stage="Complete",
            message="Scenario outputs are ready.",
            error="",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        add_log("Scenario completed successfully.")
    except Exception as exc:
        set_status(
            running=False,
            stage="Failed",
            message="The scenario did not complete.",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        add_log("ERROR: " + str(exc))


def parse_config(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    try:
        surge = float(payload.get("surge", 1.5))
        green_share = float(payload.get("green_share", 0.4))
        seed = int(payload.get("seed", 42))
    except (TypeError, ValueError) as exc:
        raise ValueError("Surge, Green adoption, and seed must be valid numbers.") from exc
    if not math.isfinite(surge) or not 1 <= surge <= 3:
        raise ValueError("Demand surge must be between 1× and 3×.")
    if not math.isfinite(green_share) or not 0 <= green_share <= 1:
        raise ValueError("Green delivery adoption must be between 0% and 100%.")
    if seed < 0 or seed > 2_147_483_647:
        raise ValueError("Seed must be an integer between 0 and 2,147,483,647.")
    return {
        "surge": surge,
        "green_share": green_share,
        "seed": seed,
        "include_baskets": bool(payload.get("include_baskets", False)),
        "run_experiments": bool(payload.get("run_experiments", False)),
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIST), **kwargs)

    def log_message(self, fmt, *args):
        print("[http] " + fmt % args)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {
                "ok": True,
                "data_dir": str(DATA_DIR),
                "source_catalog": (ROOT / "dataset" / "BigBasket Products.csv").is_file(),
                "source_instacart": (ROOT / "dataset" / "order_products__prior.csv").is_file(),
                "run": current_status(),
            })
            return
        if path == "/api/status":
            self.send_json(200, current_status())
            return
        if path == "/api/dashboard":
            try:
                self.send_json(200, dashboard_data())
            except (OSError, csv.Error, json.JSONDecodeError) as exc:
                self.send_json(500, {"error": f"Unable to load dashboard outputs: {exc}"})
            return
        if path == "/" and not (FRONTEND_DIST / "index.html").is_file():
            self.send_json(200, {
                "service": "SCM Command Center API",
                "message": "Start the React development server with npm run dev from frontend/.",
                "dev_url": "http://127.0.0.1:5173",
            })
            return
        if path.startswith("/api/"):
            self.send_json(404, {"error": "API route not found."})
            return
        if (FRONTEND_DIST / "index.html").is_file():
            return super().do_GET()
        self.send_error(404, "The React frontend has not been built. Start Vite with npm run dev.")

    def do_POST(self):
        if urlparse(self.path).path != "/api/run":
            self.send_json(404, {"error": "API route not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise ValueError("Request body must be between 1 byte and 1 MB.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            config = parse_config(payload)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
            return

        with LOCK:
            if RUN_STATE["running"]:
                self.send_json(409, {"error": "A scenario is already running."})
                return
            RUN_STATE.update({
                "running": True,
                "stage": "Queued",
                "message": "Starting the local simulation pipeline.",
                "error": "",
                "logs": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
            })
        worker = threading.Thread(target=run_pipeline, args=(config,), daemon=True)
        worker.start()
        self.send_json(202, current_status())


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"SCM Command Center API listening at http://127.0.0.1:{PORT}")
    print(f"Dashboard data directory: {DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
