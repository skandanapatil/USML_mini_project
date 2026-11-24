import { createReadStream } from "node:fs";
import { readFile } from "node:fs/promises";
import { resolve, relative, sep, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const DATA_DIR = fileURLToPath(new URL("../backend/data/", import.meta.url));
const REQUIRED_DATA_FILES = [
  "hubs.geojson",
  "hubs_micro.csv",
  "hubs_macro.csv",
  "cluster_summary.json",
  "sla_sim_summary.csv",
  "sla_tier_summary.csv",
  "sla_alerts.csv",
  "sla_sim_orders.csv",
  "replenishment_alerts.csv",
  "p2p_transfers.csv",
  "colocation_pairs.csv",
  "sla_timeline_example.txt",
];
const OPTIONAL_DATA_FILES = [
  "green_adoption_sweep.csv",
  "experiments/experiment_summary.csv",
];
const CONTENT_TYPES = {
  ".csv": "text/csv; charset=utf-8",
  ".geojson": "application/geo+json; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

function backendDataPlugin() {
  return {
    name: "serve-backend-data",
    configureServer(server) {
      server.middlewares.use("/data", (request, response, next) => {
        if (request.method !== "GET" && request.method !== "HEAD") {
          response.statusCode = 405;
          response.end("Method not allowed");
          return;
        }

        let requestedPath;
        try {
          requestedPath = decodeURIComponent((request.url || "").split("?")[0]);
        } catch {
          response.statusCode = 400;
          response.end("Invalid data path");
          return;
        }

        const filePath = resolve(DATA_DIR, `.${requestedPath}`);
        const relativePath = relative(DATA_DIR, filePath);
        if (!relativePath || relativePath === ".." || relativePath.startsWith(`..${sep}`) || relativePath.startsWith(sep)) {
          response.statusCode = 404;
          response.end("Data file not found");
          return;
        }

        const stream = createReadStream(filePath);
        stream.on("error", (error) => {
          if (error.code === "ENOENT" || error.code === "EISDIR") {
            response.statusCode = 404;
            response.end("Data file not found");
            return;
          }
          next(error);
        });
        response.setHeader("Content-Type", CONTENT_TYPES[extname(filePath)] || "application/octet-stream");
        response.setHeader("Cache-Control", "no-store");
        if (request.method === "HEAD") {
          stream.destroy();
          response.statusCode = 200;
          response.end();
          return;
        }
        stream.pipe(response);
      });
    },
    async buildStart() {
      for (const file of [...REQUIRED_DATA_FILES, ...OPTIONAL_DATA_FILES]) {
        try {
          const source = await readFile(resolve(DATA_DIR, file));
          this.emitFile({ type: "asset", fileName: `data/${file}`, source });
        } catch (error) {
          if (OPTIONAL_DATA_FILES.includes(file) && error.code === "ENOENT") continue;
          if (error.code === "ENOENT") {
            this.error(`Required backend data file is missing: backend/data/${file}. Run the backend pipeline first.`);
          }
          throw error;
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), backendDataPlugin()],
});
