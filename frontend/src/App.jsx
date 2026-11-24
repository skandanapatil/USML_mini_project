import { useEffect, useMemo, useState } from "react";
import Papa from "papaparse";
import { MapContainer, TileLayer, CircleMarker, Rectangle, Polygon, Polyline, Tooltip, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

const DATA = "/data/";
const COLORS = {
  bg: "#0B1220",
  panel: "#111A2B",
  teal: "#14B8A6",
  purple: "#8B5CF6",
  amber: "#F59E0B",
  green: "#22C55E",
  red: "#EF4444",
  blue: "#3B82F6",
};

function csv(path) {
  return fetch(DATA + path).then((response) => {
    if (!response.ok) throw new Error(`Unable to load ${path}`);
    return response.text();
  }).then((text) => Papa.parse(text, { header: true, dynamicTyping: true, skipEmptyLines: true }).data);
}

function timeToMinutes(value) {
  if (typeof value === "number") return value;
  if (!value) return 0;
  const [h, m, s = 0] = value.split(":").map(Number);
  return h * 60 + m + s / 60;
}

function formatTime(minutes) {
  const total = Math.max(0, Math.round(minutes));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function formatNumber(value, digits = 1) {
  return Number(value ?? 0).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function FitBounds({ points }) {
  const map = useMap();
  useEffect(() => {
    if (!points?.length) return;
    const bounds = points.map(([lat, lon]) => [lat, lon]);
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  }, [map, points]);
  return null;
}

function Icon({ name }) {
  const icons = { map: "⌖", chart: "▥", clock: "◷", shield: "◆", grid: "▦", info: "i" };
  return <span className="icon" aria-hidden="true">{icons[name] || "·"}</span>;
}

function Header({ page, setPage }) {
  const pages = [
    ["map", "Control map", "map"],
    ["consolidation", "Consolidation", "shield"],
    ["inventory", "RDC inventory", "grid"],
    ["stress", "Stress tests", "chart"],
    ["playback", "Playback", "clock"],
  ];
  return (
    <header className="topbar">
      <button className="brand" onClick={() => setPage("map")}>
        <span className="brand-mark"><Icon name="shield" /></span>
        <span><strong>SCM<span>.</span></strong><small>COMMAND CENTER</small></span>
      </button>
      <nav>
        {pages.map(([id, label, icon]) => (
          <button key={id} className={page === id ? "active" : ""} onClick={() => setPage(id)}>
            <Icon name={icon} />{label}
          </button>
        ))}
      </nav>
      <div className="live-state"><i /> STATIC SCENARIO</div>
    </header>
  );
}

function SectionHeading({ eyebrow, title, aside }) {
  return (
    <div className="section-heading">
      <div><span>{eyebrow}</span><h2>{title}</h2></div>
      {aside}
    </div>
  );
}

function ControlMap({ data, clock, setClock }) {
  const [rdcId, setRdcId] = useState(null);
  const [hubId, setHubId] = useState(null);
  const [activeStage, setActiveStage] = useState("detect");
  const [showHulls, setShowHulls] = useState(false);
  const hubs = data.hubs || [];
  const rdcs = data.rdcs || [];
  const geo = data.geojson;
  const hubLookup = Object.fromEntries(hubs.map((hub) => [hub.hub_id, hub]));
  const features = geo?.features || [];
  const microFeatures = features.filter((feature) => feature.properties?.kind === "micro_hub");
  const rdcFeatures = features.filter((feature) => feature.properties?.kind === "rdc");
  const hullFeatures = features.filter((feature) => feature.properties?.kind === "demand_hull");
  const circles = features.filter((feature) => feature.properties?.kind === "coverage_circle");
  const visibleHubs = useMemo(() => rdcId ? hubs.filter((hub) => hub.rdc_id === rdcId) : hubs, [hubs, rdcId]);
  const points = useMemo(() => visibleHubs.map((hub) => [Number(hub.lat), Number(hub.lon)]), [visibleHubs]);
  const visibleHubIds = new Set(visibleHubs.map((hub) => hub.hub_id));
  const visibleMicroFeatures = microFeatures.filter((feature) => visibleHubIds.has(feature.properties.hub_id));
  const visibleHulls = hullFeatures.filter((feature) => !rdcId || hubLookup[feature.properties.hub_id]?.rdc_id === rdcId);
  const visibleCircles = circles.filter((feature) => !rdcId || hubLookup[feature.properties.hub_id]?.rdc_id === rdcId);
  const reroutes = (data.orders || []).filter((order) =>
    order.mode === "self_healing" &&
    order.reroute === "rerouted_gridlock" &&
    Number(order.t) <= clock &&
    (!rdcId || visibleHubIds.has(order.hub) || visibleHubIds.has(order.home_hub))
  );
  const occurredAlerts = (data.alerts || [])
    .filter((alert) => timeToMinutes(alert.time) <= clock)
    .sort((a, b) => timeToMinutes(b.time) - timeToMinutes(a.time));
  const occurredTrucks = (data.trucks || []).filter((truck) =>
    truck.mode === "self_healing" &&
    timeToMinutes(truck.departs) <= clock &&
    (!rdcId || visibleHubIds.has(truck.hub_id))
  );
  const occurredTransfers = (data.transfers || []).filter((transfer) =>
    timeToMinutes(transfer.triggered_at) <= clock &&
    (!rdcId || visibleHubIds.has(transfer.donor_hub) || visibleHubIds.has(transfer.receiver_hub))
  );
  const activeGridlock = clock >= 1155 && clock <= 1185;
  const rdcColors = ["#8B5CF6", "#60A5FA", "#F472B6", "#F59E0B", "#22C55E"];
  const selectRdc = (id) => {
    setRdcId(rdcId === id ? null : id);
    setHubId(null);
  };
  const stageContent = {
    detect: occurredAlerts.slice(0, 3).map((alert) => `${alert.time} · ${alert.message || alert.type}`),
    decide: [
      ...reroutes.slice(0, 2).map((order) => `${order.order_id} · gridlock reroute to ${order.hub}`),
      ...(data.alerts || [])
        .filter((alert) =>
          timeToMinutes(alert.time) <= clock &&
          ["STOCKOUT_SUBSTITUTE", "STOCKOUT_SOURCING", "ITEM_DROPPED_BY_PREFERENCE"].includes(alert.type)
        )
        .slice(0, 2)
        .map((alert) => `${alert.time} · ${alert.message}`),
    ],
    act: [
      ...occurredTransfers.slice(-2).map((transfer) => `${transfer.transfer_id} · ${transfer.donor_hub} → ${transfer.receiver_hub}`),
      ...occurredTrucks.slice(-2).map((truck) => `${truck.wave_id} · RDC → ${truck.hub_id}`),
    ],
    measure: [
      `Promised SLA met · ${formatNumber(data.summary?.sla_met_pct?.self_healing)}%`,
      `P90 delivery · ${formatNumber(data.summary?.p90_delivery_min?.self_healing)} min`,
      `Cancelled orders · ${formatNumber(data.summary?.orders_cancelled_pct?.self_healing)}%`,
    ],
  };
  const stageEmpty = {
    detect: "No alerts have occurred by this time. Advance the clock in Playback to see events.",
    decide: "No reroutes or stock-out decisions have occurred by this time.",
    act: "No transfers or trucks have departed by this time.",
    measure: "Simulation metrics are unavailable.",
  };

  return (
    <section className="control-layout">
      <div className="panel map-panel">
        <SectionHeading
          eyebrow="BENGALURU · NETWORK OVERVIEW"
          title="Micro-hub control map"
          aside={
            <div className="map-controls">
              <label className="rdc-filter">RDC
                <select value={rdcId || ""} onChange={(event) => selectRdc(event.target.value || null)}>
                  <option value="">All</option>
                  {rdcs.map((rdc) => <option key={rdc.rdc_id} value={rdc.rdc_id}>{rdc.rdc_id}</option>)}
                </select>
              </label>
              <label className="toggle"><input type="checkbox" checked={showHulls} onChange={(event) => setShowHulls(event.target.checked)} />Demand hulls</label>
            </div>
          }
        />
        <div className="network-chips">
          <span><b>{data.cluster?.k_rdc ?? rdcs.length}</b> RDCs</span>
          <span><b>{data.cluster?.n_micro_hubs ?? hubs.length}</b> Hubs</span>
          <span><b>{formatNumber(data.cluster?.pct_orders_served, 1)}%</b> within 2 km</span>
          {rdcId && <button className="filter-chip" onClick={() => selectRdc(rdcId)}>{rdcId} · Show all</button>}
        </div>
        <div className="map-canvas">
          <MapContainer center={[12.95, 77.62]} zoom={11} zoomControl={false}>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {points.length > 0 && <FitBounds points={points} />}
            {showHulls && visibleHulls.map((feature, index) => (
              <Polygon
                key={`hull-${index}`}
                positions={feature.geometry.coordinates[0].map(([lon, lat]) => [lat, lon])}
                pathOptions={{ color: "#64748B", weight: 1, dashArray: "5 5", fillOpacity: 0.04 }}
              />
            ))}
            {visibleMicroFeatures.map((feature) => {
              const [lon, lat] = feature.geometry.coordinates;
              const hub = hubLookup[feature.properties.hub_id] || {};
              const rdcIndex = rdcs.findIndex((rdc) => rdc.rdc_id === hub.rdc_id);
              const selected = hubId === feature.properties.hub_id;
              return (
                <CircleMarker
                  key={feature.properties.hub_id}
                  center={[lat, lon]}
                  radius={selected ? 8 : 5}
                  pathOptions={{ color: rdcIndex >= 0 ? rdcColors[rdcIndex % rdcColors.length] : COLORS.teal, fillColor: COLORS.teal, fillOpacity: 0.9, weight: 2 }}
                  eventHandlers={{ click: () => setHubId(feature.properties.hub_id) }}
                >
                  <Tooltip direction="top" offset={[0, -5]}>
                    <strong>{feature.properties.hub_id}</strong><br />
                    {feature.properties.nearest_area}<br />
                    {feature.properties.orders_served} orders
                  </Tooltip>
                </CircleMarker>
              );
            })}
            {rdcFeatures.map((feature) => {
              const [lon, lat] = feature.geometry.coordinates;
              return (
                <Rectangle
                  key={feature.properties.rdc_id}
                  bounds={[[lat - 0.008, lon - 0.008], [lat + 0.008, lon + 0.008]]}
                  pathOptions={{
                    color: COLORS.purple,
                    fillColor: COLORS.purple,
                    fillOpacity: rdcId === feature.properties.rdc_id ? 1 : 0.9,
                    weight: rdcId === feature.properties.rdc_id ? 4 : 2,
                  }}
                  eventHandlers={{ click: () => selectRdc(feature.properties.rdc_id) }}
                >
                  <Tooltip>{feature.properties.rdc_id}<br />{feature.properties.nearest_area}<br />{feature.properties.n_orders} orders</Tooltip>
                </Rectangle>
              );
            })}
            {visibleCircles.map((feature) => {
              const [lon, lat] = feature.geometry.coordinates?.[0]?.[0] ? [feature.geometry.coordinates[0][0][0], feature.geometry.coordinates[0][0][1]] : [0, 0];
              if (!lon || !lat) return null;
              const hubIdForCircle = feature.properties.hub_id;
              return <Polygon key={`circle-${hubIdForCircle}`} positions={feature.geometry.coordinates[0].map(([circleLon, circleLat]) => [circleLat, circleLon])} pathOptions={{ color: COLORS.teal, weight: 1, fillOpacity: 0.025 }} />;
            })}
            {occurredTrucks.slice(0, 62).map((truck) => {
              const from = hubLookup[truck.hub_id];
              const rdc = rdcs.find((item) => item.rdc_id === truck.rdc_id);
              if (!from || !rdc) return null;
              return <Polyline key={truck.wave_id} positions={[[rdc.lat, rdc.lon], [from.lat, from.lon]]} pathOptions={{ color: COLORS.blue, weight: 1.5, dashArray: "7 5", opacity: 0.75 }} />;
            })}
            {occurredTransfers.map((transfer) => {
              const from = hubLookup[transfer.donor_hub];
              const to = hubLookup[transfer.receiver_hub];
              if (!from || !to) return null;
              return <Polyline key={transfer.transfer_id} positions={[[from.lat, from.lon], [to.lat, to.lon]]} pathOptions={{ color: COLORS.purple, weight: 2, dashArray: "4 7", opacity: 0.85 }} />;
            })}
            {reroutes.map((order) => {
              const from = hubLookup[order.hub];
              const receiver = hubLookup[order.home_hub];
              if (!from || !receiver) return null;
              return <Polyline key={order.order_id} positions={[[from.lat, from.lon], [receiver.lat, receiver.lon]]} pathOptions={{ color: COLORS.red, weight: 3, dashArray: "8 5" }} />;
            })}
            {activeGridlock && hubLookup["MH-05"] && (
              <CircleMarker center={[hubLookup["MH-05"].lat, hubLookup["MH-05"].lon]} radius={15} pathOptions={{ color: COLORS.red, fillColor: COLORS.red, fillOpacity: 0.25, weight: 3 }} />
            )}
          </MapContainer>
        </div>
        {activeGridlock && <div className="gridlock-banner"><b>GRIDLOCK</b> MH-05 · rerouting to MH-03 and MH-17</div>}
        {hubId && hubLookup[hubId] && (
          <div className="hub-card">
            <div><b>{hubLookup[hubId].hub_id}</b><span>{hubLookup[hubId].nearest_area}</span></div>
            <span>{hubLookup[hubId].orders_served} orders served</span>
            <span>{formatNumber(hubLookup[hubId].dist_to_rdc_km)} km to RDC</span>
            <button aria-label="Clear selected hub" onClick={() => setHubId(null)}>×</button>
          </div>
        )}
      </div>
      <aside className="panel side-panel">
        <SectionHeading eyebrow={`NETWORK SIGNALS · ${formatTime(clock)}`} title="Control loop" />
        <div className="loop">
          {["detect", "decide", "act", "measure"].map((step, index) => (
            <button
              className={activeStage === step ? "active" : ""}
              key={step}
              onClick={() => setActiveStage(step)}
              aria-pressed={activeStage === step}
            >
              <span>{index + 1}</span><b>{step}</b>
            </button>
          ))}
        </div>
        <div className="stage-details" aria-live="polite">
          <strong>{activeStage} · {activeStage === "measure" ? "scenario results" : "events by selected time"}</strong>
          {stageContent[activeStage].length ? (
            <ul>{stageContent[activeStage].map((item, index) => <li key={`${activeStage}-${index}`}>{item}</li>)}</ul>
          ) : <p>{stageEmpty[activeStage]}</p>}
        </div>
        <div className="legend">
          <span><i style={{ background: COLORS.teal }} />Micro-hub</span>
          <span><i style={{ background: COLORS.purple }} />RDC / P2P</span>
          <span><i style={{ background: COLORS.blue }} />Truck</span>
          <span><i style={{ background: COLORS.red }} />Reroute / gridlock</span>
        </div>
      </aside>
    </section>
  );
}

function Consolidation({ data }) {
  const [mode, setMode] = useState("self_healing");
  const summary = data.summary || {};
  const metric = (name) => summary[name]?.[mode] ?? summary[name]?.[mode === "self_healing" ? "self_healing" : "baseline"];
  const tiers = data.tiers || [];
  const tierPerformance = tiers.map((tier) => {
    const orders = (data.orders || []).filter((order) => order.mode === mode && order.tier === tier.tier);
    const onTime = orders.filter((order) => order.sla_met === true || String(order.sla_met).toLowerCase() === "true").length;
    const average = orders.length
      ? orders.reduce((total, order) => total + Number(order.total_min || 0), 0) / orders.length
      : mode === "baseline" ? tier.avg_min_baseline : tier.avg_min_healing;
    const slaMetPct = orders.length
      ? (onTime / orders.length) * 100
      : tier.sla_met_pct_healing;
    return {
      ...tier,
      orderCount: orders.length || tier.orders,
      average,
      slaMetPct,
    };
  });
  const sweep = data.sweep || [];
  const pairs = (data.colocation || []).filter((row) => row.colocate === true).slice(0, 9);

  return (
    <section>
      <div className="page-toolbar">
        <SectionHeading eyebrow="BASE SCENARIO" title="Before and after consolidation" />
        <div className="segmented">
          <button className={mode === "baseline" ? "active" : ""} onClick={() => setMode("baseline")}>Baseline</button>
          <button className={mode === "self_healing" ? "active" : ""} onClick={() => setMode("self_healing")}>Self-healing</button>
        </div>
      </div>
      <div className="kpi-grid">
        {[
          ["Promised SLA met", metric("sla_met_pct"), "%"],
          ["Avg delivery", metric("avg_delivery_min"), " min"],
          ["P90 delivery", metric("p90_delivery_min"), " min"],
          ["Rider trips", metric("rider_trips"), ""],
          ["Cancelled", metric("orders_cancelled_pct"), "%"],
          ["Contribution / order", metric("contribution_per_order"), " Rs"],
        ].map(([label, value, suffix]) => (
          <article className="kpi" key={label}><span>{label}</span><strong>{formatNumber(value)}{suffix}</strong></article>
        ))}
      </div>
      <div className="content-grid">
        <article className="panel">
          <SectionHeading eyebrow={`DELIVERY TIERS · ${mode === "baseline" ? "BASELINE" : "SELF-HEALING"}`} title="Tier performance" />
          <div className="tier-table">
            {tierPerformance.map((tier) => (
              <div className="tier-row" key={tier.tier}>
                <span className={`tier ${tier.tier}`}>{tier.tier}</span>
                <span><b>{formatNumber(tier.orderCount, 0)}</b><small>orders</small></span>
                <span><b>{formatNumber(tier.average)}</b><small>avg minutes</small></span>
                <span><b>{formatNumber(tier.slaMetPct)}%</b><small>SLA met</small></span>
              </div>
            ))}
          </div>
          <p className="note">Green average batch size: {tiers.find((tier) => tier.tier === "green")?.avg_batch_size ?? "--"}.</p>
        </article>
        <article className="panel">
          <SectionHeading eyebrow="SELF-HEALING ACTIONS" title="Add-ons & co-location" />
          <div className="action-stats">
            <div><span>Recipe add-ons</span><b>{formatNumber(metric("addon_accepted"), 0)} accepted</b><small>{formatNumber(metric("addon_offers"), 0)} offered · Rs {formatNumber(metric("addon_revenue_rs"), 0)}</small></div>
            <div><span>Co-location badge</span><b>{formatNumber(metric("coloc_badge_orders"), 0)} orders</b><small>{formatNumber(metric("coloc_badge_filled_at_one_hub_pct"))}% filled at one hub</small></div>
          </div>
          <div className="pair-list">{pairs.map((pair) => <span key={`${pair.category_a}-${pair.category_b}`}>{pair.category_a} + {pair.category_b}</span>)}</div>
        </article>
        <article className="panel incentive-card">
          <SectionHeading eyebrow="GREEN ADOPTION" title="Incentive cost sweep" />
          {sweep.length ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={sweep} margin={{ top: 12, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#202C38" vertical={false} />
                <XAxis dataKey="green_share" tickFormatter={(value) => `${value}%`} stroke="#718096" />
                <YAxis stroke="#718096" />
                <ChartTooltip cursor={{ fill: "rgba(20,184,166,.06)" }} />
                <Bar dataKey="net_cost_cashback" name="Cashback" fill="#3B82F6" />
                <Bar dataKey="net_cost_conditional" name="Conditional" fill="#14B8A6" />
                <Bar dataKey="net_cost_nudge" name="Nudge" fill="#F59E0B" />
                <Bar dataKey="net_cost_hybrid" name="Hybrid" fill="#8B5CF6" />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="empty-small">Green adoption sweep unavailable.</div>}
        </article>
      </div>
    </section>
  );
}

function Inventory({ data }) {
  const [hubId, setHubId] = useState("MH-05");
  const hub = (data.hubs || []).find((item) => item.hub_id === hubId);
  const stockAlerts = (data.alerts || []).filter((alert) =>
    alert.hub === hubId &&
    ["STOCKOUT_SUBSTITUTE", "STOCKOUT_SOURCING", "ITEM_DROPPED_BY_PREFERENCE"].includes(alert.type)
  ).sort((a, b) => timeToMinutes(b.time) - timeToMinutes(a.time));
  const responseCounts = [
    { action: "Substituted", events: stockAlerts.filter((alert) => alert.type === "STOCKOUT_SUBSTITUTE").length },
    { action: "Neighbor sourced", events: stockAlerts.filter((alert) => alert.type === "STOCKOUT_SOURCING").length },
    { action: "Dropped", events: stockAlerts.filter((alert) => alert.type === "ITEM_DROPPED_BY_PREFERENCE").length },
  ];
  const trucks = (data.trucks || []).filter((truck) => truck.hub_id === hubId);
  const transfers = (data.transfers || []).filter((transfer) => transfer.donor_hub === hubId || transfer.receiver_hub === hubId);
  const hasResponses = responseCounts.some((row) => row.events > 0);

  return (
    <section className="content-grid inventory-layout">
      <article className="panel hub-picker">
        <SectionHeading eyebrow="RDC MICRO VIEW" title="Inventory node" />
        <label>Micro-hub
          <select value={hubId} onChange={(event) => setHubId(event.target.value)}>
            {(data.hubs || []).map((item) => <option value={item.hub_id} key={item.hub_id}>{item.hub_id} · {item.nearest_area}</option>)}
          </select>
        </label>
        {hub && <div className="hub-details">
          <span><b>{hub.orders_served}</b><small>orders served</small></span>
          <span><b>{formatNumber(hub.coverage_radius_p90_km)} km</b><small>P90 coverage</small></span>
          <span><b>{formatNumber(hub.dist_to_rdc_km)} km</b><small>to RDC</small></span>
        </div>}
      </article>
      <article className="panel">
        <SectionHeading eyebrow="STOCK-OUT RESPONSES" title={`Actions at ${hubId}`} />
        {hasResponses ? (
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={responseCounts} layout="vertical" margin={{ top: 8, right: 12, left: 2, bottom: 0 }}>
              <CartesianGrid stroke="#202C38" horizontal={false} />
              <XAxis type="number" allowDecimals={false} stroke="#718096" />
              <YAxis type="category" dataKey="action" width={104} stroke="#9AA8B6" tick={{ fontSize: 9 }} />
              <ChartTooltip cursor={{ fill: "rgba(20,184,166,.06)" }} />
              <Bar dataKey="events" name="Events" fill={COLORS.teal} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : <div className="empty-small">No stock-out responses recorded for this hub.</div>}
      </article>
      <article className="panel">
        <SectionHeading eyebrow="STOCK-OUT FEED" title="Newest first" />
        <div className="alert-list">{stockAlerts.length ? stockAlerts.slice(0, 8).map((alert, index) => (
          <div key={index}><time>{alert.time}</time><p>{alert.message}</p></div>
        )) : <p className="empty-small">No stock-out events at this hub in the current feed.</p>}</div>
      </article>
      <article className="panel">
        <SectionHeading eyebrow="REBALANCE STATUS" title="Truck / P2P" />
        {trucks.slice(0, 4).map((truck) => <div className="status-row" key={truck.wave_id}><b>Truck · {truck.wave_id}</b><span>{truck.departs} → {truck.arrives}</span><small>{truck.units} units · {truck.road_km} km</small></div>)}
        {transfers.slice(0, 4).map((transfer) => <div className="status-row purple" key={transfer.transfer_id}><b>P2P · {transfer.transfer_id}</b><span>{transfer.donor_hub} → {transfer.receiver_hub}</span><small>{transfer.units} units · arrives {transfer.arrives}</small></div>)}
        {!trucks.length && !transfers.length && <p className="empty-small">No active truck or P2P record for this hub.</p>}
      </article>
    </section>
  );
}

function StressTests({ data }) {
  const [metric, setMetric] = useState("sla_met_pct");
  const [strict, setStrict] = useState(false);
  const rows = (data.experiments || []).filter((row) => row.metric === (strict ? "met_standard_sla_pct" : metric));
  const chartRows = rows.map((row) => ({
    name: row.scenario,
    baseline: row.baseline_mean,
    healing: row.healing_mean,
  }));

  return (
    <section>
      <div className="page-toolbar">
        <SectionHeading
          eyebrow="MONTE CARLO"
          title="Stress-test results"
          aside={<span className="seed-label">{rows.length ? `Mean of ${rows[0].reps} seeds` : "No experiment data"}</span>}
        />
        <div className="stress-controls">
          <select value={metric} onChange={(event) => setMetric(event.target.value)}>
            <option value="sla_met_pct">Promised SLA</option>
            <option value="avg_delivery_min">Delivery time</option>
            <option value="orders_cancelled_pct">Cancelled %</option>
            <option value="contribution_per_order">Contribution</option>
          </select>
          <label className="toggle"><input type="checkbox" checked={strict} onChange={(event) => setStrict(event.target.checked)} />Strict 15-minute SLA</label>
        </div>
      </div>
      <article className="panel stress-chart">
        {chartRows.length ? (
          <ResponsiveContainer width="100%" height={390}>
            <BarChart data={chartRows} margin={{ top: 20, right: 20, left: 0, bottom: 50 }}>
              <CartesianGrid stroke="#202C38" vertical={false} />
              <XAxis dataKey="name" stroke="#718096" angle={-18} textAnchor="end" interval={0} />
              <YAxis stroke="#718096" />
              <ChartTooltip />
              <Bar dataKey="baseline" name="Baseline" fill={COLORS.blue} radius={[4, 4, 0, 0]} />
              <Bar dataKey="healing" name="Self-healing" fill={COLORS.teal} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : <div className="empty-small">Experiment summary file is not available in the current static package.</div>}
      </article>
    </section>
  );
}

function Playback({ data, clock, setClock, playing, setPlaying, speed, setSpeed }) {
  const mode = "self_healing";
  const orders = (data.orders || []).filter((order) => order.mode === mode);
  const activeOrders = orders.filter((order) => Number(order.t) <= clock && Number(order.delivered) >= clock).slice(0, 14);
  const firedAlerts = (data.alerts || []).filter((alert) => timeToMinutes(alert.time) <= clock).sort((a, b) => timeToMinutes(b.time) - timeToMinutes(a.time));
  const latestToasts = firedAlerts.slice(0, 3);
  const rerouteCount = firedAlerts.filter((alert) => alert.type === "GRIDLOCK").length ? 2 : 0;
  const p2pCount = firedAlerts.filter((alert) => alert.type === "P2P_REBALANCE").length;
  const truckCount = firedAlerts.filter((alert) => alert.type === "REPLENISHMENT").length;
  const addonCount = firedAlerts.filter((alert) => alert.type === "ADDON_ACCEPTED").length;
  const hubLookup = Object.fromEntries((data.hubs || []).map((hub) => [hub.hub_id, hub]));

  useEffect(() => {
    if (!playing) return undefined;
    const interval = window.setInterval(() => {
      setClock((value) => value >= 1273 ? 1080 : value + 1 / speed);
    }, 1000 / speed);
    return () => window.clearInterval(interval);
  }, [playing, speed, setClock]);

  function timerColor(order) {
    const elapsed = Math.max(0, clock - Number(order.t));
    const limit = Number(order.sla_limit) || 15;
    if (elapsed > limit) return "red";
    if (elapsed >= limit * 0.8) return "amber";
    return "green";
  }

  return (
    <section>
      <div className="playback-toolbar panel">
        <button className="play-button" onClick={() => setPlaying((value) => !value)}>{playing ? "Ⅱ" : "▶"}</button>
        <strong>{formatTime(clock)}</strong>
        <input aria-label="Playback time" type="range" min="1080" max="1273" step="0.25" value={clock} onChange={(event) => setClock(Number(event.target.value))} />
      </div>
      {clock >= 1155 && clock <= 1185 && <div className="gridlock-banner playback-gridlock"><b>GRIDLOCK</b> MH-05 · 19:15–19:45 · reroutes to MH-03 and MH-17</div>}
      <div className="counter-row">
        <span><b>{rerouteCount}</b> reroutes</span>
        <span><b>{p2pCount}</b> P2P</span>
        <span><b>{truckCount}</b> trucks</span>
        <span><b>{addonCount}</b> add-ons</span>
      </div>
      <div className="playback-layout">
        <article className="panel order-list">
          <SectionHeading eyebrow="LIVE ORDER LEDGER" title="Active orders" aside={<span className="ledger-count">{activeOrders.length} ACTIVE</span>} />
          <div className="order-table">
            {activeOrders.map((order) => {
              const elapsed = Math.max(0, clock - Number(order.t));
              const color = timerColor(order);
              return (
                <button className="order-row" key={order.order_id} onClick={() => alert(data.timeline || "Order journey data unavailable.")}>
                  <span className={`tier ${order.tier}`}>{order.tier}</span>
                  <span><b>{order.order_id}</b><small>{order.hub} · batch {order.batch_size}</small></span>
                  <span className={`timer ${color}`}>{elapsed >= (order.sla_limit || 15) ? "+" : ""}{Math.floor(elapsed)}:{String(Math.floor((elapsed % 1) * 60)).padStart(2, "0")}</span>
                  {Number(order.incentive_rs) > 0 && <span className="incentive">Rs {order.incentive_rs} credited</span>}
                </button>
              );
            })}
            {!activeOrders.length && <p className="empty-small">No active orders at this clock position.</p>}
          </div>
        </article>
        <aside className="panel toast-feed">
          <SectionHeading eyebrow="EVENT FEED" title="Alert stream" aside={<span>{firedAlerts.length} fired</span>} />
          <div className="toasts">
            {latestToasts.map((alert, index) => <div className={`toast ${alert.type.toLowerCase()}`} key={`${alert.time}-${alert.type}-${index}`}><b>{alert.type.replaceAll("_", " ")}</b><p>{alert.message}</p><time>{alert.time}</time></div>)}
          </div>
        </aside>
      </div>
    </section>
  );
}

export default function App() {
  const [page, setPage] = useState("map");
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(1080);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    Promise.all([
      fetch(DATA + "hubs.geojson").then((response) => response.json()),
      csv("hubs_micro.csv"),
      csv("hubs_macro.csv"),
      fetch(DATA + "cluster_summary.json").then((response) => response.json()),
      csv("sla_sim_summary.csv"),
      csv("sla_tier_summary.csv"),
      csv("sla_alerts.csv"),
      csv("sla_sim_orders.csv"),
      csv("replenishment_alerts.csv"),
      csv("p2p_transfers.csv"),
      csv("green_adoption_sweep.csv").catch(() => []),
      csv("colocation_pairs.csv"),
      csv("experiments/experiment_summary.csv").catch(() => []),
      fetch(DATA + "sla_timeline_example.txt").then((response) => response.ok ? response.text() : "").catch(() => ""),
    ]).then(([
      geojson, hubs, rdcs, cluster, summaryRows, tiers, alerts, orders, trucks, transfers, sweep, colocation, experiments, timeline,
    ]) => {
      const summary = Object.fromEntries(summaryRows.map((row) => [
        row[""],
        { baseline: row.baseline, healing_reactive_only: row.healing_reactive_only, self_healing: row.self_healing },
      ]));
      setData({
        geojson, hubs, rdcs, cluster, summary, tiers, alerts, orders, trucks, transfers, sweep, colocation, experiments, timeline,
      });
      setLoading(false);
    }).catch((loadError) => {
      setError(loadError.message);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    document.documentElement.style.background = COLORS.bg;
  }, []);

  if (loading) return <div className="loading-screen"><div className="brand-mark">◆</div><p>Loading static command data</p></div>;
  if (error) return <div className="loading-screen"><p className="error-text">{error}</p></div>;

  return (
    <div className="app-shell">
      <Header page={page} setPage={setPage} />
      <main>
        {page === "map" && <ControlMap data={data} clock={clock} setClock={setClock} />}
        {page === "consolidation" && <Consolidation data={data} />}
        {page === "inventory" && <Inventory data={data} />}
        {page === "stress" && <StressTests data={data} />}
        {page === "playback" && <Playback data={data} clock={clock} setClock={setClock} playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed} />}
      </main>
      <footer>
        <span>SCM · STATIC RESULTS</span>
        <button onClick={() => setPage("map")}>Assumptions</button>
        <span>{formatTime(clock)}</span>
      </footer>
    </div>
  );
}
