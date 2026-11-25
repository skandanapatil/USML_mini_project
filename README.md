# Self-Healing Quick-Commerce Command Center

This project models a self-healing quick-commerce network in Bengaluru. The Python
pipeline generates simulated orders, hub-network outputs, and paired baseline versus
self-healing results. The React dashboard reads those saved outputs.

## Run the dashboard

Requirements: Python 3.10+ with the backend dependencies installed, and Node.js 18+.

If needed, install the Python packages used by the pipeline:

```powershell
python -m pip install numpy pandas scikit-learn scipy openpyxl
```

1. Generate the data from the repository root:

   ```powershell
   python backend\run_all.py
   ```

   Generated pipeline files are written to `backend\data`, which the frontend
   serves from `/data/`.

   This runs the preparation, clustering, substitution, basket, network-demo, and
   SLA-simulation steps in order. The experiment ladder is optional:

   ```powershell
   python backend\run_all.py --with-experiments
   ```

2. In another terminal, start the React development server:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

3. Open the Vite URL (usually `http://127.0.0.1:5173`). During development, Vite
   serves the generated files directly from `backend/data/`. A production build
   packages the required data files alongside the frontend.

Basket-rule mining reads the Instacart order-products file. The optional experiment
ladder takes several minutes.

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
