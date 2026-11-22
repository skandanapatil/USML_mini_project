import { useCallback, useEffect, useMemo, useState } from "react";

const EMPTY_DASHBOARD = {
  generated: {},
  metrics: {},
  hubs: [],
  rdcs: [],
  orders: [],
  tiers: [],
  alerts: [],
  experiments: [],
  basket: {},
};

const NAV_ITEMS = ["Overview", "Network", "Service & SLA", "Playback"];

function Icon({ name, size = 16 }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    map: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z" /><path d="M9 3v15m6-12v15" /></>,
    chart: <><path d="M3 3v18h18" /><path d="m8 14 4-4 3 3 5-7" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    play: <path d="m8 5 12 7-12 7z" />,
    refresh: <><path d="M20 7v5h-5" /><path d="M4 17v-5h5" /><path d="M5.6 9a7 7 0 0 1 11.6-2L20 12M4 12l2.8 5a7 7 0 0 0 11.6-2" /></>,
    alert: <><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4m0 3h.01" /></>,
    arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
    box: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="M3 8v9l9 5 9-5V8m-9 5v9" /></>,
    close: <><path d="m18 6-12 12M6 6l12 12" /></>,
    pin: <><path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    up: <><path d="m5 15 7-7 7 7" /></>,
    pulse: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  };
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name] || paths.grid}
    </svg>
  );
}

function fmt(value, digits = 0) {
  if (value === undefined || value === null || value === "" || !Number.isFinite(Number(value))) return "--";
  return Number(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function metricValue(metrics, key) {
  return metrics?.self_healing?.[key];
}

function statusLabel(status) {
  if (status?.running) return status.stage || "Running";
  if (status?.error) return "Needs attention";
  return "Ready";
}

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function Header({ active, onNavigate, apiOnline, busy, onOpenRun }) {
  return (
    <header className="topbar">
      <a className="brand" href="#" onClick={(event) => event.preventDefault()}>
        <span className="brand-mark"><Icon name="pulse" size={18} /></span>
        <span className="brand-copy">
          <strong>SCM<span>.</span></strong>
          <small>COMMAND CENTER</small>
        </span>
      </a>
      <nav className="main-nav" aria-label="Main navigation">
        {NAV_ITEMS.map((item, index) => (
          <button
            className={`nav-item ${active === item ? "active" : ""}`}
            key={item}
            onClick={() => onNavigate(item)}
          >
            <Icon name={["grid", "map", "chart", "clock"][index]} size={15} />
            {item}
          </button>
        ))}
      </nav>
      <div className="top-actions">
        <span className={`connection ${apiOnline ? "online" : "offline"}`}>
          <i />
          {apiOnline ? "ENGINE CONNECTED" : "ENGINE OFFLINE"}
        </span>
        <button className="button button-primary header-run" onClick={onOpenRun} disabled={busy}>
          <Icon name={busy ? "refresh" : "play"} size={14} />
          {busy ? "Running..." : "Run scenario"}
        </button>
      </div>
    </header>
  );
}

function SectionHeading({ eyebrow, title, action }) {
  return (
    <div className="section-heading">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

function StatCard({ label, value, suffix, sub, icon, tone = "mint" }) {
  return (
    <article className="stat-card">
      <div className={`stat-icon ${tone}`}><Icon name={icon} size={16} /></div>
      <div className="stat-label">{label}</div>
      <div className="stat-number">{value}<span>{suffix}</span></div>
      <div className="stat-sub">{sub}</div>
    </article>
  );
}

function NetworkMap({ hubs, rdcs, selectedHub, onSelectHub, generated }) {
  const bounds = useMemo(() => {
    const points = [...hubs, ...rdcs].filter((point) => Number.isFinite(Number(point.lat)) && Number.isFinite(Number(point.lon)));
    if (!points.length) return null;
    const lats = points.map((point) => Number(point.lat));
    const lons = points.map((point) => Number(point.lon));
    const latPad = Math.max((Math.max(...lats) - Math.min(...lats)) * 0.18, 0.012);
    const lonPad = Math.max((Math.max(...lons) - Math.min(...lons)) * 0.18, 0.012);
    return {
      minLat: Math.min(...lats) - latPad,
      maxLat: Math.max(...lats) + latPad,
      minLon: Math.min(...lons) - lonPad,
      maxLon: Math.max(...lons) + lonPad,
    };
  }, [hubs, rdcs]);

  const project = (point) => ({
    x: 38 + ((Number(point.lon) - bounds.minLon) / (bounds.maxLon - bounds.minLon)) * 624,
    y: 30 + ((bounds.maxLat - Number(point.lat)) / (bounds.maxLat - bounds.minLat)) * 300,
  });

  const links = useMemo(() => {
    if (!bounds || hubs.length < 2) return [];
    const edges = new Map();
    hubs.forEach((hub, index) => {
      const nearest = hubs
        .map((other, otherIndex) => {
          if (otherIndex === index) return null;
          const dy = Number(hub.lat) - Number(other.lat);
          const dx = (Number(hub.lon) - Number(other.lon)) * Math.cos((Number(hub.lat) * Math.PI) / 180);
          return { otherIndex, distance: dx * dx + dy * dy };
        })
        .filter(Boolean)
        .sort((a, b) => a.distance - b.distance)[0];
      if (!nearest) return;
      const key = [index, nearest.otherIndex].sort((a, b) => a - b).join("-");
      edges.set(key, [hubs[index], hubs[nearest.otherIndex]]);
    });
    return Array.from(edges.values());
  }, [bounds, hubs]);

  const selected = hubs.find((hub) => hub.hub_id === selectedHub);

  return (
    <section className="panel map-panel">
      <SectionHeading
        eyebrow="BENGALURU · NETWORK OVERVIEW"
        title="Micro-hub control map"
        action={<span className="map-legend"><i className="legend-dot rdc" />RDC <i className="legend-dot hub" />Micro-hub</span>}
      />
      {bounds ? (
        <>
          <div className="map-canvas">
            <svg viewBox="0 0 700 360" role="img" aria-label="Schematic map of the generated Bangalore hub network">
              <defs>
                <pattern id="map-grid" width="36" height="36" patternUnits="userSpaceOnUse">
                  <path d="M 36 0 L 0 0 0 36" fill="none" stroke="rgba(164,184,199,.065)" strokeWidth="1" />
                </pattern>
                <radialGradient id="map-glow">
                  <stop offset="0" stopColor="#0d3a38" stopOpacity=".38" />
                  <stop offset="1" stopColor="#0b1018" stopOpacity="0" />
                </radialGradient>
              </defs>
              <rect width="700" height="360" fill="url(#map-grid)" />
              <ellipse cx="350" cy="180" rx="270" ry="155" fill="url(#map-glow)" />
              <path className="road road-major" d="M33 292 C130 245 157 166 248 146 S384 175 467 109 582 80 668 37" />
              <path className="road" d="M47 48 C143 123 172 226 257 275 S422 294 493 224 599 178 668 213" />
              <path className="road" d="M111 344 C164 265 249 251 294 174 S364 50 444 37" />
              <path className="road road-dash" d="M36 189 C140 205 201 78 310 86 S472 210 659 137" />
              {links.map(([from, to], index) => {
                const a = project(from);
                const b = project(to);
                return <line key={`link-${index}`} className="hub-link" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
              })}
              {hubs.map((hub) => {
                const point = project(hub);
                const selectedClass = selectedHub === hub.hub_id ? " selected" : "";
                return (
                  <g
                    className={`hub-marker${selectedClass}`}
                    key={hub.hub_id}
                    onClick={() => onSelectHub(hub.hub_id)}
                    onKeyDown={(event) => event.key === "Enter" && onSelectHub(hub.hub_id)}
                    role="button"
                    tabIndex="0"
                    aria-label={`${hub.hub_id}, ${hub.nearest_area || "Bengaluru"}`}
                  >
                    <circle className="hub-halo" cx={point.x} cy={point.y} r="12" />
                    <circle className="hub-dot" cx={point.x} cy={point.y} r="4.3" />
                    <text x={point.x + 8} y={point.y - 7}>{hub.hub_id}</text>
                  </g>
                );
              })}
              {rdcs.map((rdc) => {
                const point = project(rdc);
                return (
                  <g key={rdc.rdc_id} className="rdc-marker">
                    <rect x={point.x - 5} y={point.y - 5} width="10" height="10" rx="2" />
                    <text x={point.x + 9} y={point.y + 3}>{rdc.rdc_id}</text>
                  </g>
                );
              })}
              <text className="map-label" x="30" y="343">BENGALURU · SCHEMATIC ROAD NETWORK</text>
              <text className="map-label map-label-right" x="665" y="343" textAnchor="end">NOT TO SCALE</text>
            </svg>
            {selected && (
              <div className="map-tooltip">
                <span className="tooltip-dot" />
                <div><strong>{selected.hub_id}</strong><small>{selected.nearest_area || "Micro-hub"} · {selected.rdc_id || "Network"}</small></div>
                <button aria-label="Close hub details" onClick={() => onSelectHub(null)}><Icon name="close" size={13} /></button>
              </div>
            )}
          </div>
          <div className="map-footer">
            <span><i className="live-dot" />{hubs.length} MICRO-HUBS ACTIVE</span>
            <span>{rdcs.length} REGIONAL DISTRIBUTION CENTERS</span>
            <span>ROAD LINKS ARE APPROXIMATE</span>
          </div>
        </>
      ) : (
        <div className="map-empty">
          <div className="empty-map-icon"><Icon name="map" size={22} /></div>
          <strong>{generated?.network ? "Network map is unavailable" : "No network data loaded"}</strong>
          <span>Run a scenario to cluster the Bangalore order data and build the hub network.</span>
        </div>
      )}
    </section>
  );
}

function EventFeed({ alerts, busy }) {
  const typeLabel = (type) => ({
    GRIDLOCK: "TRAFFIC",
    REPLENISHMENT: "REPLENISH",
    P2P_REBALANCE: "P2P TRANSFER",
    STOCKOUT: "STOCK-OUT",
  }[type] || String(type || "NETWORK").replaceAll("_", " "));

  return (
    <section className="panel event-panel">
      <SectionHeading
        eyebrow="NETWORK SIGNALS"
        title="Live event feed"
        action={<span className="event-count">{alerts.length.toString().padStart(2, "0")} EVENTS</span>}
      />
      <div className="event-list">
        {alerts.length ? alerts.slice(0, 7).map((alert, index) => (
          <article className="event-item" key={`${alert.time}-${alert.type}-${index}`}>
            <span className={`event-icon ${String(alert.type).toLowerCase()}`}>
              <Icon name={alert.type === "GRIDLOCK" ? "alert" : alert.type === "P2P_REBALANCE" ? "refresh" : "box"} size={14} />
            </span>
            <div className="event-body">
              <div className="event-meta"><span>{typeLabel(alert.type)}</span><time>{alert.time || "--:--"}</time></div>
              <p>{alert.message || alert.hub || "Network event"}</p>
            </div>
          </article>
        )) : (
          <div className="feed-empty">
            <span className="feed-line" />
            <span className="feed-indicator"><i /></span>
            <div><strong>{busy ? "Preparing simulation" : "Standing by"}</strong><p>{busy ? "Pipeline output will appear here as it is generated." : "Run a scenario to populate the operational event feed."}</p></div>
          </div>
        )}
      </div>
      {alerts.length > 7 && <div className="feed-more">+ {alerts.length - 7} earlier events in playback</div>}
    </section>
  );
}

function CompareBar({ label, baseline, healing, suffix = "%", lowerIsBetter = false }) {
  const max = Math.max(Number(baseline) || 0, Number(healing) || 0, 1);
  const baseWidth = Math.max(3, ((Number(baseline) || 0) / max) * 100);
  const healWidth = Math.max(3, ((Number(healing) || 0) / max) * 100);
  const improvement = Number.isFinite(Number(baseline)) && Number.isFinite(Number(healing))
    ? Number(healing) - Number(baseline)
    : null;
  const better = improvement === null ? null : lowerIsBetter ? improvement < 0 : improvement > 0;
  return (
    <div className="compare-row">
      <div className="compare-label">
        <span>{label}</span>
        {improvement !== null && <em className={better ? "improved" : "muted"}>{improvement > 0 ? "+" : ""}{fmt(improvement, 1)}{suffix}</em>}
      </div>
      <div className="compare-bars">
        <div className="bar-track"><i className="bar-baseline" style={{ width: `${baseWidth}%` }} /><span>{fmt(baseline, 1)}{suffix}</span></div>
        <div className="bar-track"><i className="bar-healing" style={{ width: `${healWidth}%` }} /><span>{fmt(healing, 1)}{suffix}</span></div>
      </div>
    </div>
  );
}

function ServicePanel({ dashboard }) {
  const baseline = dashboard.metrics?.baseline || {};
  const healing = dashboard.metrics?.self_healing || {};
  const hasMetrics = dashboard.generated?.simulation;
  return (
    <section className="panel service-panel">
      <SectionHeading
        eyebrow="PAIRED SIMULATION · SAME ORDERS"
        title="Baseline vs self-healing"
        action={<span className="paired-badge"><Icon name="check" size={12} /> FAIR COMPARISON</span>}
      />
      {!hasMetrics ? (
        <div className="chart-empty">Run a scenario to compare the baseline and self-healing modes on the same simulated orders.</div>
      ) : (
        <>
          <div className="chart-legend"><span><i className="bar-legend baseline" />Baseline</span><span><i className="bar-legend healing" />Self-healing</span></div>
          <div className="comparison">
            <CompareBar label="Promised SLA met" baseline={baseline.sla_met_pct} healing={healing.sla_met_pct} />
            <CompareBar label="Standard 15-min SLA" baseline={baseline.met_standard_sla_pct} healing={healing.met_standard_sla_pct} />
            <CompareBar label="Avg delivery time" baseline={baseline.avg_delivery_min} healing={healing.avg_delivery_min} suffix=" min" lowerIsBetter />
            <CompareBar label="Cancelled orders" baseline={baseline.orders_cancelled_pct} healing={healing.orders_cancelled_pct} suffix="%" lowerIsBetter />
            <CompareBar label="Contribution / order" baseline={baseline.contribution_per_order} healing={healing.contribution_per_order} suffix=" Rs" />
          </div>
          <p className="chart-footnote">Green orders have a longer promised SLA. The standard-SLA row holds both modes to the same 15-minute promise.</p>
        </>
      )}
    </section>
  );
}

function HubPanel({ hubs, selectedHub, onSelectHub, generated }) {
  const visible = selectedHub ? hubs.filter((hub) => hub.hub_id === selectedHub) : hubs;
  const list = visible.length ? visible.slice(0, 5) : hubs.slice(0, 5);
  return (
    <section className="panel hub-panel">
      <SectionHeading
        eyebrow="FULFILLMENT NODES"
        title="Micro-hub network"
        action={<span className="node-total">{hubs.length.toString().padStart(2, "0")} NODES</span>}
      />
      {!generated?.network ? (
        <div className="compact-empty">Hub capacity and coverage populate after clustering runs.</div>
      ) : list.length ? (
        <div className="hub-list">
          {list.map((hub) => {
            const served = Number(hub.orders_served) || 0;
            const total = Number(hub.dense_orders) || served || 1;
            const pct = Math.min(100, Math.round((served / total) * 100));
            return (
              <button
                className={`hub-row ${selectedHub === hub.hub_id ? "selected" : ""}`}
                key={hub.hub_id}
                onClick={() => onSelectHub(selectedHub === hub.hub_id ? null : hub.hub_id)}
              >
                <span className="hub-row-mark"><i /></span>
                <span className="hub-row-copy"><strong>{hub.hub_id}</strong><small>{hub.nearest_area || hub.rdc_id || "Bengaluru"}</small></span>
                <span className="hub-row-capacity"><span>{fmt(served)} <small>served</small></span><i><b style={{ width: `${pct}%` }} /></i></span>
                <Icon name="arrow" size={13} />
              </button>
            );
          })}
          {hubs.length > list.length && <div className="hub-list-foot">Showing {list.length} of {hubs.length} generated micro-hubs</div>}
        </div>
      ) : <div className="compact-empty">No micro-hubs in the generated network.</div>}
    </section>
  );
}

function TierPanel({ tiers, generated }) {
  const colors = { express: "coral", instant: "blue", green: "mint" };
  return (
    <section className="panel tier-panel">
      <SectionHeading eyebrow="CHECKOUT EXPERIENCE" title="Delivery tier mix" />
      {!generated?.simulation || !tiers.length ? (
        <div className="compact-empty">Express, Instant, and Green order mix appears after a simulation run.</div>
      ) : (
        <div className="tier-list">
          {tiers.map((tier) => (
            <article className="tier-row" key={tier.tier}>
              <span className={`tier-tag ${colors[tier.tier] || "blue"}`}>{tier.tier}</span>
              <span className="tier-orders">{fmt(tier.orders)} <small>orders</small></span>
              <span className="tier-sla"><strong>{fmt(tier.sla_met_pct_healing, 1)}%</strong><small>on time</small></span>
              <span className="tier-time">{fmt(tier.avg_min_healing, 1)}<small> min avg</small></span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function BasketPanel({ dashboard }) {
  const basket = dashboard.basket || {};
  const metrics = dashboard.metrics?.self_healing || {};
  return (
    <section className="panel basket-panel">
      <SectionHeading eyebrow="BASKET AFFINITY" title="Recipe & co-location" />
      {basket.available ? (
        <>
          <div className="basket-stat-grid">
            <div><strong>{fmt(metrics.addon_offers)}</strong><small>recipe add-on offers</small></div>
            <div><strong>{fmt(metrics.coloc_badge_orders)}</strong><small>co-location orders</small></div>
            <div><strong>{fmt(basket.recipe_rules)}</strong><small>recipe rules mined</small></div>
            <div><strong>{fmt(basket.colocation_pairs_flagged)}</strong><small>category pairs flagged</small></div>
          </div>
          <p className="panel-note">Affinity rules are learned from the included Instacart basket sample; product names are from the project catalog.</p>
        </>
      ) : (
        <div className="basket-unavailable">
          <span className="basket-icon"><Icon name="box" size={17} /></span>
          <div><strong>Basket rules not generated</strong><p>Optional: enable basket mining when running a scenario. It scans the large Instacart order file.</p></div>
        </div>
      )}
    </section>
  );
}

function OrderTable({ orders, generated }) {
  return (
    <section className="panel order-panel">
      <SectionHeading
        eyebrow="LATEST SIMULATED ORDERS"
        title="Order ledger"
        action={generated?.simulation && <span className="ledger-count">{orders.length} RECORDS</span>}
      />
      {!generated?.simulation ? (
        <div className="compact-empty">The paired order ledger is created when a simulation completes.</div>
      ) : orders.length ? (
        <div className="table-scroll">
          <table>
            <thead><tr><th>ORDER</th><th>TIER</th><th>FULFILLED AT</th><th>DELIVERY</th><th>ITEMS HEALED</th><th>STATUS</th></tr></thead>
            <tbody>{orders.slice(0, 8).map((order) => (
              <tr key={`${order.mode}-${order.order_id}`}>
                <td className="order-id">{order.order_id}</td>
                <td><span className={`mini-tier ${order.tier}`}>{order.tier}</span></td>
                <td>{order.hub || order.home_hub || "--"}</td>
                <td>{fmt(order.total_min, 1)} <small>min</small></td>
                <td>{fmt((Number(order.n_substituted) || 0) + (Number(order.n_sourced) || 0))}</td>
                <td><span className={`sla-state ${String(order.sla_met).toLowerCase() === "true" ? "met" : "late"}`}><i />{String(order.sla_met).toLowerCase() === "true" ? "On time" : "Late"}</span></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <div className="compact-empty">No order rows were returned by the simulation.</div>}
    </section>
  );
}

function PlaybackPanel({ alerts, generated }) {
  const selected = alerts.slice(0, 5);
  return (
    <section className="panel playback-panel">
      <SectionHeading
        eyebrow="INCIDENT REVIEW"
        title="Playback & alert feed"
        action={<span className="playback-time"><Icon name="clock" size={13} /> SIMULATED WINDOW</span>}
      />
      {!generated?.simulation ? (
        <div className="compact-empty">Run the simulation to build a time-ordered incident playback.</div>
      ) : selected.length ? (
        <div className="playback-track">
          <div className="track-line" />
          {selected.map((alert, index) => (
            <div className="track-event" key={`${alert.time}-${alert.type}-${index}`}>
              <span className={`track-dot ${String(alert.type).toLowerCase()}`} />
              <time>{alert.time || "--:--"}</time>
              <div><strong>{String(alert.type || "event").replaceAll("_", " ")}</strong><small>{alert.hub || alert.message || "Network event"}</small></div>
            </div>
          ))}
        </div>
      ) : (
        <div className="feed-empty small"><span className="feed-line" /><span className="feed-indicator"><i /></span><div><strong>No alerts in this window</strong><p>The run completed without an alert event.</p></div></div>
      )}
    </section>
  );
}

function ExperimentPanel({ experiments, generated }) {
  return (
    <section className="panel experiment-panel">
      <SectionHeading
        eyebrow="SCENARIO LADDER"
        title="Demand stress response"
        action={generated?.experiments && <span className="paired-badge"><Icon name="check" size={12} /> MONTE CARLO</span>}
      />
      {generated?.experiments && experiments.length ? (
        <div className="experiment-list">
          {experiments.slice(0, 7).map((row, index) => (
            <div className="experiment-row" key={row.scenario}>
              <span className="experiment-index">{String(index + 1).padStart(2, "0")}</span>
              <span className="experiment-name">{row.scenario.replace(/^\d+\s*/, "")}</span>
              <span className="experiment-value">{fmt(row.baseline_mean, 1)}<i> → </i><strong>{fmt(row.healing_mean, 1)}{row.metric === "sla_met_pct" ? "%" : ""}</strong></span>
            </div>
          ))}
          <p className="chart-footnote">Paired runs · mean of seeds · baseline → self-healing. Metric: promised SLA met.</p>
        </div>
      ) : (
        <div className="experiment-empty">
          <div className="experiment-summary"><span><Icon name="chart" size={18} /></span><div><strong>Seven scenarios, paired runs</strong><small>Normal peak through 2.5× surge, gridlock, and slow traffic.</small></div></div>
          <p>Enable the optional experiment ladder in Run scenario to calculate confidence intervals from the project's Monte Carlo runner.</p>
        </div>
      )}
    </section>
  );
}

function RunDrawer({ open, onClose, onRun, busy, apiOnline, status }) {
  const [surge, setSurge] = useState(1.5);
  const [greenShare, setGreenShare] = useState(0.4);
  const [seed, setSeed] = useState(42);
  const [includeBaskets, setIncludeBaskets] = useState(false);
  const [runExperiments, setRunExperiments] = useState(false);

  if (!open) return null;
  return (
    <div className="drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <aside className="run-drawer" aria-label="Run simulation controls">
        <div className="drawer-head">
          <div><span className="eyebrow">SIMULATION CONTROL</span><h2>Run a scenario</h2></div>
          <button className="icon-button" aria-label="Close settings" onClick={onClose} disabled={busy}><Icon name="close" size={18} /></button>
        </div>
        <p className="drawer-intro">The engine replays the same orders against baseline and self-healing operations. A first run also prepares the local network and product catalog.</p>
        <label className="field-label" htmlFor="surge-control">Demand surge <strong>{Number(surge).toFixed(1)}×</strong></label>
        <input id="surge-control" className="range-input" type="range" min="1" max="3" step="0.5" value={surge} onChange={(event) => setSurge(Number(event.target.value))} />
        <div className="range-labels"><span>Normal · 1×</span><span>Rush · 3×</span></div>
        <label className="field-label" htmlFor="green-control">Green delivery adoption <strong>{Math.round(greenShare * 100)}%</strong></label>
        <input id="green-control" className="range-input green-range" type="range" min="0" max="1" step="0.1" value={greenShare} onChange={(event) => setGreenShare(Number(event.target.value))} />
        <div className="range-labels"><span>Instant first</span><span>Consolidated first</span></div>
        <label className="field-label" htmlFor="seed-control">Random seed</label>
        <input id="seed-control" className="text-input" type="number" min="0" value={seed} onChange={(event) => setSeed(event.target.value)} />
        <label className="option-row">
          <input type="checkbox" checked={includeBaskets} onChange={(event) => setIncludeBaskets(event.target.checked)} />
          <span><strong>Build recipe & co-location rules</strong><small>Optional. Scans the large Instacart order file; may take several minutes.</small></span>
        </label>
        <label className="option-row">
          <input type="checkbox" checked={runExperiments} onChange={(event) => setRunExperiments(event.target.checked)} />
          <span><strong>Run the 7-scenario experiment ladder</strong><small>Optional. Runs 3 seeds per scenario and saves the Monte Carlo summary.</small></span>
        </label>
        {busy && (
          <div className="run-progress">
            <span className="spinner" />
            <div><strong>{status?.stage || "Starting pipeline"}</strong><small>{status?.message || "Working with the local project data."}</small></div>
            <div className="progress-track"><i /></div>
          </div>
        )}
        {status?.error && !busy && <div className="drawer-error"><Icon name="alert" size={15} />{status.error}</div>}
        <div className="drawer-bottom">
          <span>{apiOnline ? "Local Python simulation engine" : "Start the backend to run scenarios"}</span>
          <button
            className="button button-primary full-button"
            disabled={!apiOnline || busy}
            onClick={() => onRun({ surge, green_share: greenShare, seed: Number(seed), include_baskets: includeBaskets, run_experiments: runExperiments })}
          >
            <Icon name={busy ? "refresh" : "play"} size={15} />
            {busy ? "Simulation running..." : "Prepare & run simulation"}
          </button>
        </div>
      </aside>
    </div>
  );
}

function DataNotes({ dashboard }) {
  const generated = dashboard.generated || {};
  const tags = [
    ["NETWORK", generated.network, "Clustered order coordinates"],
    ["SKU CATALOG", generated.catalog, "BigBasket product catalog"],
    ["SIMULATION", generated.simulation, "Paired SLA results"],
    ["BASKET RULES", generated.baskets, "Optional Instacart mining"],
  ];
  return (
    <footer className="data-notes">
      <div><span className="footer-mark">SCM</span><span>Self-healing quick-commerce network <i>·</i> Bangalore</span></div>
      <div className="source-chips">{tags.map(([label, ready, hint]) => <span className={`source-chip ${ready ? "ready" : ""}`} title={hint} key={label}><i />{label}</span>)}</div>
      <p>Simulated orders, assumed fees and speeds. Analytics are decision-support estimates, not live operations data.</p>
    </footer>
  );
}

export default function App() {
  const [activeNav, setActiveNav] = useState("Overview");
  const [dashboard, setDashboard] = useState(EMPTY_DASHBOARD);
  const [status, setStatus] = useState({ running: false });
  const [apiOnline, setApiOnline] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedHub, setSelectedHub] = useState(null);
  const [error, setError] = useState("");

  const loadData = useCallback(async () => {
    try {
      const [health, data, runStatus] = await Promise.all([
        request("/api/health"),
        request("/api/dashboard"),
        request("/api/status"),
      ]);
      setApiOnline(Boolean(health.ok));
      setDashboard(data);
      setStatus(runStatus);
      setError("");
    } catch (loadError) {
      setApiOnline(false);
      setError(loadError.message);
    }
  }, []);

  useEffect(() => {
    loadData();
    const timer = window.setInterval(loadData, status.running ? 1500 : 6000);
    return () => window.clearInterval(timer);
  }, [loadData, status.running]);

  const runScenario = async (config) => {
    setError("");
    try {
      const result = await request("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      setStatus(result);
    } catch (runError) {
      setError(runError.message);
    }
  };

  const scrollToSection = (section) => {
    setActiveNav(section);
    const target = document.getElementById(section.toLowerCase().replaceAll(" ", "-").replace("&", "and"));
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const metrics = dashboard.metrics?.self_healing || {};
  const generated = dashboard.generated || {};
  const onTime = metricValue(dashboard.metrics, "sla_met_pct");
  const updateText = status.running ? status.message || "Pipeline is running" : generated.simulation ? "Latest simulation output" : "Run to generate metrics";

  return (
    <div className="app-shell">
      <Header
        active={activeNav}
        onNavigate={scrollToSection}
        apiOnline={apiOnline}
        busy={status.running}
        onOpenRun={() => setDrawerOpen(true)}
      />
      <main className="dashboard">
        <section className="page-intro">
          <div>
            <div className="breadcrumb"><span>OPERATIONS</span><b>/</b><span>NETWORK INTELLIGENCE</span></div>
            <h1>SCM command center <span className="title-dot">.</span></h1>
            <p>Self-healing quick-commerce network <i>·</i> Bengaluru</p>
          </div>
          <div className="intro-status">
            <span className={`run-state ${status.running ? "running" : status.error ? "error" : generated.simulation ? "complete" : "idle"}`}><i />{statusLabel(status)}</span>
            <span className="last-updated">{updateText}</span>
          </div>
        </section>

        {error && <div className="error-banner" role="alert"><Icon name="alert" size={16} /><span><strong>Could not reach the simulation service.</strong> {error} Check that the Python API is running on port 8787.</span><button aria-label="Dismiss error" onClick={() => setError("")}><Icon name="close" size={15} /></button></div>}

        {!generated.simulation && !status.running && (
          <section className="setup-banner">
            <div className="setup-icon"><Icon name="pulse" size={19} /></div>
            <div className="setup-copy"><strong>{apiOnline ? "Your command center is ready to initialize" : "Connect the local simulation engine"}</strong><p>{apiOnline ? "No generated scenario is loaded yet. The first run prepares your SKU catalog and Bangalore hub network, then compares both operating modes." : "Start the Python API and Vite frontend to prepare and run simulations using the project data."}</p></div>
            <button className="button button-quiet" onClick={() => setDrawerOpen(true)} disabled={!apiOnline}><Icon name="play" size={14} />Configure run</button>
          </section>
        )}

        <section id="overview" className="stats-grid" aria-label="Key operating metrics">
          <StatCard label="PROMISED SLA MET" value={fmt(onTime, 1)} suffix={onTime === undefined ? "" : "%"} sub={generated.simulation ? `${fmt(metrics.orders)} orders · self-healing` : "Waiting for a simulation"} icon="clock" tone="mint" />
          <StatCard label="AVG DELIVERY" value={fmt(metricValue(dashboard.metrics, "avg_delivery_min"), 1)} suffix={generated.simulation ? " min" : ""} sub={generated.simulation ? `P90 ${fmt(metrics.p90_delivery_min, 1)} min` : "Observed from the simulated window"} icon="arrow" tone="blue" />
          <StatCard label="EXACT ITEM FILL" value={fmt(metricValue(dashboard.metrics, "exact_item_fill_pct"), 1)} suffix={generated.simulation ? "%" : ""} sub={generated.simulation ? `${fmt(metrics.items_substituted)} substitutions · ${fmt(metrics.items_sourced_from_neighbour)} neighbour sourced` : "On-order item availability"} icon="box" tone="amber" />
          <StatCard label="CONTRIBUTION / ORDER" value={fmt(metricValue(dashboard.metrics, "contribution_per_order"), 2)} suffix={generated.simulation ? " Rs" : ""} sub={generated.simulation ? `Net cost ${fmt(metrics.net_cost_per_order, 2)} Rs / order` : "After modeled fulfillment costs"} icon="chart" tone="coral" />
        </section>

        <section id="network" className="primary-grid">
          <NetworkMap hubs={dashboard.hubs} rdcs={dashboard.rdcs} selectedHub={selectedHub} onSelectHub={setSelectedHub} generated={generated} />
          <EventFeed alerts={dashboard.alerts} busy={status.running} />
        </section>

        <section id="service-and-sla" className="secondary-grid">
          <ServicePanel dashboard={dashboard} />
          <HubPanel hubs={dashboard.hubs} selectedHub={selectedHub} onSelectHub={setSelectedHub} generated={generated} />
        </section>

        <section className="feature-grid">
          <TierPanel tiers={dashboard.tiers} generated={generated} />
          <BasketPanel dashboard={dashboard} />
        </section>

        <section className="lower-grid">
          <OrderTable orders={dashboard.orders} generated={generated} />
          <ExperimentPanel experiments={dashboard.experiments} generated={generated} />
        </section>

        <section id="playback" className="playback-grid">
          <PlaybackPanel alerts={dashboard.alerts} generated={generated} />
          <aside className="assumptions-panel">
            <span className="eyebrow">MODEL SCOPE</span>
            <h3>Assumptions, made visible.</h3>
            <p>Distances, rider speeds, fees, margins, customer choices, and starting inventory are model inputs—not measured live data.</p>
            <div className="assumption-pills"><span>20 km/h nominal</span><span>1.4× road circuity</span><span>15 min standard SLA</span></div>
            <a href="https://github.com/skandanapatil/USML_mini_project" target="_blank" rel="noreferrer">Project source <Icon name="arrow" size={13} /></a>
          </aside>
        </section>

        <DataNotes dashboard={dashboard} />
      </main>
      <RunDrawer
        open={drawerOpen}
        onClose={() => !status.running && setDrawerOpen(false)}
        onRun={runScenario}
        busy={status.running}
        apiOnline={apiOnline}
        status={status}
      />
    </div>
  );
}
