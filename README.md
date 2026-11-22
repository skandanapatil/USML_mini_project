# Self-Healing Quick-Commerce Command Center

This project models a self-healing quick-commerce network in Bengaluru. The React
dashboard compares the existing baseline and self-healing SLA simulation, visualizes
the generated hub network, and displays its fulfillment metrics and alert feed.

## Run the dashboard

Requirements: Python 3.10+ with the backend dependencies installed, and Node.js 18+.

If needed, install the Python packages used by the pipeline:

```powershell
python -m pip install numpy pandas scikit-learn scipy openpyxl
```

1. Start the local dashboard API from the repository root:

   ```powershell
   python backend\dashboard_api.py
   ```

2. In another terminal, start the React development server:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

3. Open the Vite URL (usually `http://127.0.0.1:5173`) and select **Run scenario**.
   The first run prepares the local SKU catalog, clusters demand into hubs, builds
   product substitutions, and runs the paired SLA simulation. Results are written to
   the ignored `backend/data/` directory.

Basket-rule mining and the seven-scenario Monte Carlo experiment ladder are optional
run settings. Basket mining scans the large Instacart order-products file; the
experiment ladder takes several minutes.

## Data and model notes

- Customer order coordinates are simulated around Bengaluru area anchors.
- Product catalog items come from the included BigBasket dataset.
- Instacart is an optional US grocery basket source for the association-rule model.
- Rider speeds, distances, delivery fees, stock levels, and customer behavior are
  model assumptions, not live operating data.
- The network map is a schematic view of generated coordinates; it is not a
  road-network routing map.

The Python modules in `backend/` remain runnable from the command line and accept
the data-directory options shown by `--help`.
