const app = document.getElementById("app");
const connectionDot = document.getElementById("connection-dot");
const connectionText = document.getElementById("connection-text");
const brandTitle = document.getElementById("brand-title");
const toast = document.getElementById("toast");

const runtime = {
  overview: null,
  route: "overview",
  eventSource: null,
  refreshTimer: null,
  clockTimer: null,
  lastVersion: -1,
  lastUpdate: null,
  renderToken: 0,
  mapController: null,
  mapViewState: null,
  economyDays: 30,
  streamConnected: false,
};

function apiUrl(path) {
  const base = window.location.href.split("#")[0];
  return new URL(path.replace(/^\//, ""), base).toString();
}

async function fetchJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = data.error || message;
    } catch (_) {
      // Keep the HTTP status text.
    }
    throw new Error(message);
  }
  return response.json();
}

function escaped(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value, maximumFractionDigits = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return numeric.toLocaleString("en-GB", { maximumFractionDigits });
}

function money(value, symbol = "£", sign = false) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const absolute = Math.abs(numeric).toLocaleString("en-GB", {
    minimumFractionDigits: Math.abs(numeric) < 100 && !Number.isInteger(numeric) ? 2 : 0,
    maximumFractionDigits: 2,
  });
  const prefix = sign ? (numeric > 0 ? "+" : numeric < 0 ? "−" : "") : numeric < 0 ? "−" : "";
  return `${prefix}${symbol}${absolute}`;
}

function duration(seconds, compact = false) {
  const total = Math.max(0, Number(seconds) || 0);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  if (days > 0) return compact ? `${days}d ${hours}h` : `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return compact ? `${hours}h ${minutes}m` : `${hours}h ${minutes}m`;
  if (minutes > 0) return compact ? `${minutes}m` : `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function ageFromIso(value) {
  if (!value) return "Never";
  const then = new Date(value).getTime();
  if (!Number.isFinite(then)) return "Unknown";
  return ageFromSeconds((Date.now() - then) / 1000);
}

function ageFromUnix(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "Unknown";
  return ageFromSeconds(Date.now() / 1000 - numeric);
}

function ageFromSeconds(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function exactDate(value) {
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (!Number.isFinite(date.getTime())) return "Unknown";
  return date.toLocaleString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
  return (parts.map(part => part[0]).join("").slice(0, 2) || "?").toUpperCase();
}

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, char => char.toUpperCase());
}

function percent(value) {
  const numeric = Math.max(0, Math.min(1, Number(value) || 0));
  return `${Math.round(numeric * 100)}%`;
}

function confidencePill(confidence) {
  if (confidence === "inferred") {
    return '<span class="state-pill warning">Inferred</span>';
  }
  if (confidence === "manual") {
    return '<span class="state-pill good">Reviewed</span>';
  }
  return '<span class="state-pill good">Confirmed</span>';
}

function eventIcon(type) {
  const icons = {
    player_join: "↗",
    player_leave: "↙",
    server_online: "✓",
    server_offline: "!",
    contract_started: "▶",
    contract_completed: "✓",
    contract_failed: "×",
    contract_cancelled: "↺",
    contract_payment: "C",
    production_autosale: "A",
    product_sale: "P",
    supply_purchase: "S",
    farm_purchase: "F",
    vehicle_purchase: "V",
    vehicle_sale: "V",
    vehicle_repair: "🔧",
    income: "+",
    expense: "−",
    money_change: "£",
    animal_sale: "🐄",
    animal_purchase: "🐄",
    land_purchase: "◇",
    land_sale: "◇",
    building_purchase: "▦",
    loan_income: "L",
    loan_repayment: "L",
    lease_expense: "R",
    operating_expense: "−",
    other_income: "+",
    other_expense: "−",
    ignored: "×",
  };
  return icons[type] || "•";
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function setConnection(state, text) {
  connectionDot.className = `connection-dot ${state}`;
  connectionText.textContent = text;
}

function currentRoute() {
  const route = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return ["overview", "map", "vehicles", "economy", "mods", "history", "diagnostics"].includes(route)
    ? route
    : "overview";
}

function updateNavigation() {
  document.querySelectorAll("[data-route-link]").forEach(link => {
    link.classList.toggle("active", link.dataset.routeLink === runtime.route);
  });
}

function loadingPage(label = "Loading server data") {
  app.innerHTML = `
    <section class="loading">
      <div>
        <div class="loading-ring"></div>
        <div>${escaped(label)}…</div>
      </div>
    </section>
  `;
}

function errorPage(title, message) {
  preserveMapState();
  app.innerHTML = `
    <section class="page">
      <div class="glass-card empty-state">
        <h2>${escaped(title)}</h2>
        <p>${escaped(message)}</p>
        <button class="action-button" id="retry-button">Try again</button>
      </div>
    </section>
  `;
  document.getElementById("retry-button")?.addEventListener("click", () => renderRoute(true));
}

function pageHeading(eyebrow, title, text, action = "") {
  return `
    <header class="page-heading">
      <div>
        <p class="eyebrow">${escaped(eyebrow)}</p>
        <h1>${escaped(title)}</h1>
        <p>${escaped(text)}</p>
      </div>
      ${action}
    </header>
  `;
}

function miniStat(label, value, extraClass = "") {
  return `
    <div class="mini-stat ${extraClass}">
      <span>${escaped(label)}</span>
      <strong>${value}</strong>
    </div>
  `;
}

function playerCards(players) {
  if (!players?.length) {
    return '<div class="empty-state">The server is quiet. Nobody is connected right now.</div>';
  }
  return players.map(player => `
    <article class="player-card" data-session-player="${escaped(player.name)}">
      <div class="player-avatar">${escaped(initials(player.name))}</div>
      <div class="player-main">
        <strong>${escaped(player.name)} ${player.is_admin ? '<span class="state-pill good">Admin</span>' : ""}</strong>
        <small>${player.vehicle ? `Driving ${escaped(player.vehicle)}` : "Not currently in a vehicle"}</small>
      </div>
      <div class="player-time" data-session-start="${Number(player.session_started) || 0}">
        ${duration(player.session_seconds, true)}
      </div>
    </article>
  `).join("");
}

function activityRows(events, limit = 8) {
  const rows = (events || []).slice(0, limit);
  if (!rows.length) {
    return '<div class="empty-state">Activity starts being recorded from the moment this hub is switched on.</div>';
  }
  return rows.map(event => `
    <article class="activity-row">
      <div class="activity-icon">${escaped(eventIcon(event.event_type))}</div>
      <div class="activity-copy">
        <strong>${escaped(event.title)}</strong>
        <small>${escaped(event.detail || "")} · ${escaped(ageFromUnix(event.ts))}</small>
      </div>
    </article>
  `).join("");
}

function overviewNavigation(data) {
  const symbol = data.currency_symbol || "£";
  const historyPlayers = data.summaries?.history?.players || [];
  const weekSeconds = historyPlayers.reduce((sum, item) => sum + Number(item.seconds || 0), 0);
  return `
    <section class="nav-grid" aria-label="Detailed dashboard pages">
      <a class="nav-tile" href="#/vehicles">
        <div class="nav-tile-icon">🚜</div>
        <h3>Vehicle Fleet</h3>
        <p>${number(data.summaries?.fleet?.owned)} owned · ${number(data.summaries?.fleet?.maintenance)} may need attention</p>
        <span class="nav-tile-arrow">→</span>
      </a>
      <a class="nav-tile" href="#/economy">
        <div class="nav-tile-icon">£</div>
        <h3>Economy</h3>
        <p>${money(data.summaries?.economy?.money, symbol)} balance · ${number(data.summaries?.economy?.inventory_count)} stored products</p>
        <span class="nav-tile-arrow">→</span>
      </a>
      <a class="nav-tile" href="#/mods">
        <div class="nav-tile-icon">🧩</div>
        <h3>Mods</h3>
        <p>${number(data.summaries?.mods?.count)} active mods with version and author search</p>
        <span class="nav-tile-arrow">→</span>
      </a>
      <a class="nav-tile" href="#/history">
        <div class="nav-tile-icon">◷</div>
        <h3>Play History</h3>
        <p>${duration(weekSeconds, true)} recorded this week across ${number(historyPlayers.length)} players</p>
        <span class="nav-tile-arrow">→</span>
      </a>
      <a class="nav-tile" href="#/diagnostics">
        <div class="nav-tile-icon">⚙</div>
        <h3>Diagnostics</h3>
        <p>Feed health, adaptive polling, database size and collector status</p>
        <span class="nav-tile-arrow">→</span>
      </a>
    </section>
  `;
}

function mapCoordinateAvailable(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function mapPercent(value, mapSize) {
  if (!mapCoordinateAvailable(value)) return null;
  const numeric = Number(value);
  const size = Number(mapSize);
  if (!Number.isFinite(numeric) || !Number.isFinite(size) || size <= 0) return null;
  return Math.max(0, Math.min(100, ((numeric + size / 2) / size) * 100));
}

function mapVehicleIcon(vehicle) {
  const text = `${vehicle?.category || ""} ${vehicle?.type || ""} ${vehicle?.name || ""}`.toLowerCase();
  if (text.includes("train") || text.includes("locomotive")) return "🚂";
  if (text.includes("harvest") || text.includes("combine")) return "🌾";
  if (text.includes("trailer") || text.includes("wagon")) return "▰";
  if (text.includes("truck")) return "🚛";
  if (text.includes("forklift") || text.includes("telehandler")) return "🏗";
  return "🚜";
}

function markerDetailAttribute(detail) {
  return escaped(JSON.stringify(detail));
}

function mapPointMarkup({ x, z, mapSize, layer, className, label, icon, detail, title = "" }) {
  const left = mapPercent(x, mapSize);
  const top = mapPercent(z, mapSize);
  if (left === null || top === null) return "";
  return `
    <div class="map-marker ${className}" style="left:${left}%;top:${top}%" data-map-y="${top}">
      <button
        type="button"
        class="map-marker-pin"
        data-map-detail="${markerDetailAttribute(detail)}"
        aria-label="${escaped(title || label)}"
      >
        <span class="map-marker-icon">${icon}</span>
        ${label ? `<span class="map-marker-label">${escaped(label)}</span>` : ""}
      </button>
    </div>
  `;
}

function mapLayersMarkup(data) {
  const server = data.server || {};
  const live = data.live || {};
  const mapSize = Number(server.map_size) || 2048;
  const symbol = data.currency_symbol || "£";
  const players = server.players || [];
  const controlledNames = new Set(players.map(player => player.name));

  const playerMarkers = players.map(player => {
    if (!mapCoordinateAvailable(player.x) || !mapCoordinateAvailable(player.z)) return "";
    return mapPointMarkup({
      x: player.x,
      z: player.z,
      mapSize,
      layer: "players",
      className: "map-marker-player",
      label: player.name,
      icon: player.vehicle ? "🚜" : "●",
      title: `${player.name}${player.vehicle ? ` driving ${player.vehicle}` : ""}`,
      detail: {
        kind: "Player",
        title: player.name,
        subtitle: player.vehicle ? `Driving ${player.vehicle}` : "Online",
        icon: player.vehicle ? "🚜" : "●",
        rows: [
          ["Session", duration(player.session_seconds, true)],
          ["Role", player.is_admin ? "Administrator" : "Player"],
          ["Vehicle", player.vehicle || "On foot / unavailable"],
          ["Coordinates", `${number(player.x, 1)}, ${number(player.z, 1)}`],
        ],
      },
    });
  }).join("");

  const vehicleMarkers = (live.vehicles || []).map((vehicle, index) => {
    if (vehicle.controller && controlledNames.has(vehicle.controller)) return "";
    const fills = (vehicle.fills || [])
      .filter(fill => Number(fill.level) > 0.01)
      .slice(0, 4)
      .map(fill => `${titleCase(fill.fill_type)} ${number(fill.level, 1)}`)
      .join(" · ");
    return mapPointMarkup({
      x: vehicle.x,
      z: vehicle.z,
      mapSize,
      layer: "vehicles",
      className: `map-marker-vehicle${vehicle.is_ai_active ? " ai-active" : ""}`,
      label: vehicle.name,
      icon: mapVehicleIcon(vehicle),
      title: vehicle.name,
      detail: {
        kind: vehicle.is_ai_active ? "AI vehicle" : "Vehicle",
        title: vehicle.name || `Vehicle ${index + 1}`,
        subtitle: titleCase(vehicle.type || vehicle.category || "Vehicle"),
        icon: mapVehicleIcon(vehicle),
        rows: [
          ["Category", titleCase(vehicle.category || "Unknown")],
          ["Type", titleCase(vehicle.type || "Unknown")],
          ["Controller", vehicle.controller || (vehicle.is_ai_active ? "AI worker" : "Unoccupied")],
          ["Contents", fills || "Empty / not reported"],
          ["Coordinates", `${number(vehicle.x, 1)}, ${number(vehicle.z, 1)}`],
        ],
      },
    });
  }).join("");

  const fieldMarkers = (live.fields || []).map(field => mapPointMarkup({
    x: field.x,
    z: field.z,
    mapSize,
    layer: "fields",
    className: `map-marker-field${field.is_owned ? " is-owned" : ""}`,
    label: String(field.id),
    icon: "",
    title: `Field ${field.id}${field.is_owned ? " owned" : ""}`,
    detail: {
      kind: "Field",
      title: `Field ${field.id}`,
      subtitle: field.is_owned ? "Owned by your farm" : "Not owned",
      icon: "#",
      rows: [
        ["Ownership", field.is_owned ? "Owned" : "Not owned"],
        ["Coordinates", `${number(field.x, 1)}, ${number(field.z, 1)}`],
      ],
    },
  })).join("");

  const ownedFieldRings = (live.fields || []).filter(field => field.is_owned).map(field => {
    const left = mapPercent(field.x, mapSize);
    const top = mapPercent(field.z, mapSize);
    if (left === null || top === null) return "";
    return `<span class="owned-field-ring" style="left:${left}%;top:${top}%" data-map-y="${top}" title="Owned field ${escaped(field.id)}"></span>`;
  }).join("");

  const farmlandMarkers = (live.farmlands || []).filter(land => Number(land.owner) > 0).map(land => {
    const left = mapPercent(land.x, mapSize);
    const top = mapPercent(land.z, mapSize);
    if (left === null || top === null) return "";
    const size = Math.max(24, Math.min(74, 22 + Math.sqrt(Math.max(0, Number(land.area) || 0)) * 11));
    const detail = {
      kind: "Owned land parcel",
      title: land.name || `Farmland ${land.id}`,
      subtitle: `Farm ${number(land.owner)}`,
      icon: "◇",
      rows: [
        ["Parcel", number(land.id)],
        ["Area", `${number(land.area, 2)} ha`],
        ["Land value", money(land.price, symbol)],
        ["Coordinates", `${number(land.x, 1)}, ${number(land.z, 1)}`],
      ],
    };
    return `
      <div class="map-farmland-centre" style="left:${left}%;top:${top}%;--land-size:${size}px" data-map-y="${top}">
        <button type="button" class="map-farmland-area" data-map-detail="${markerDetailAttribute(detail)}" aria-label="${escaped(detail.title)}"></button>
      </div>
    `;
  }).join("");

  return `
    <div class="map-layer" data-map-layer="farmland">${farmlandMarkers}</div>
    <div class="map-layer" data-map-layer="owned">${ownedFieldRings}</div>
    <div class="map-layer" data-map-layer="fields">${fieldMarkers}</div>
    <div class="map-layer" data-map-layer="vehicles">${vehicleMarkers}</div>
    <div class="map-layer" data-map-layer="players">${playerMarkers}</div>
  `;
}

function interactiveMapMarkup(data, { id = "interactive-map", compact = false } = {}) {
  const server = data.server || {};
  const live = data.live || {};
  const players = server.players || [];
  const mapStamp = encodeURIComponent(data.collector?.map_updated_at || data.generated_at || Date.now());
  const mapImageUrl = apiUrl(`api/map.jpg?v=${mapStamp}`);
  const mapAge = data.collector?.map_updated_at ? ageFromIso(data.collector.map_updated_at) : "waiting";
  const mapWidth = Number(data.collector?.map_width) || 0;
  const mapHeight = Number(data.collector?.map_height) || mapWidth;
  const mapPixels = Math.max(mapWidth, mapHeight, 512);
  const mapResolution = mapWidth && mapHeight ? `${number(mapWidth)}×${number(mapHeight)}` : "detecting…";
  const mapQualityClass = mapWidth >= 1600 ? "excellent" : mapWidth >= 900 ? "good" : "limited";
  const mapQualityLabel = mapWidth >= 1600 ? "HD" : mapWidth >= 900 ? "Sharp" : "Low-res source";
  const statusClass = server.online ? "online" : "offline";
  const statusText = server.online ? "ONLINE" : "OFFLINE";
  const positionedPlayers = players.filter(player => mapCoordinateAvailable(player.x) && mapCoordinateAvailable(player.z));

  return `
    <article class="interactive-map-shell ${compact ? "compact" : "full"}" id="${escaped(id)}" data-map-size="${Number(server.map_size) || 2048}" data-map-pixels="${mapPixels}">
      <div class="interactive-map-header">
        <div class="map-title">
          <strong>${compact ? "Interactive live map" : escaped(server.map_name || "Live map")}</strong>
          <small>${escaped(server.map_name || "Map")} · ${escaped(mapResolution)} · source updated ${escaped(mapAge)}</small>
        </div>
        <div class="map-header-actions">
          <span class="map-resolution-badge ${mapQualityClass}" title="${escaped(data.collector?.map_source || "GIANTS map feed")}">${mapQualityLabel} · ${escaped(mapResolution)}</span>
          <span class="live-badge"><span class="status-dot ${statusClass}"></span>${statusText}</span>
          ${compact ? '<a class="map-tool-button emphasis" href="#/map" title="Open the full map">Open full map ↗</a>' : '<button class="map-tool-button emphasis" type="button" data-map-action="fullscreen" title="Full screen">Full screen ⛶</button>'}
        </div>
      </div>

      <div class="map-control-deck" aria-label="Map controls">
        <div class="map-layer-controls">
          <button type="button" class="map-layer-toggle active" data-map-layer-toggle="players">Players <strong>${number(players.length)}</strong></button>
          <button type="button" class="map-layer-toggle active" data-map-layer-toggle="vehicles">Vehicles <strong>${number((live.vehicles || []).length)}</strong></button>
          <button type="button" class="map-layer-toggle active" data-map-layer-toggle="owned">Owned fields <strong>${number(live.owned_field_count)}</strong></button>
          <button type="button" class="map-layer-toggle" data-map-layer-toggle="fields">Field numbers <strong>${number((live.fields || []).length)}</strong></button>
          <button type="button" class="map-layer-toggle" data-map-layer-toggle="farmland">Land parcels <strong>${number(live.owned_farmland_count)}</strong></button>
        </div>
        <div class="map-navigation-controls">
          <button type="button" class="map-tool-button" data-map-action="zoom-out" title="Zoom out">−</button>
          <span class="map-zoom-value" data-map-zoom>100%</span>
          <button type="button" class="map-tool-button" data-map-action="zoom-in" title="Zoom in">+</button>
          <button type="button" class="map-tool-button" data-map-action="fit-players" ${positionedPlayers.length ? "" : "disabled"}>Find players</button>
          <button type="button" class="map-tool-button" data-map-action="reset">Reset</button>
          <button type="button" class="map-tool-button" data-map-action="flip-z" title="Use this if markers appear north/south reversed">Flip N/S</button>
        </div>
      </div>

      <div class="map-viewport" data-map-viewport tabindex="0" style="--map-background:url('${escaped(mapImageUrl)}')" aria-label="Interactive map. Drag to pan and use the mouse wheel or buttons to zoom.">
        <div class="map-world" data-map-world>
          <img class="interactive-map-image" src="${mapImageUrl}" alt="${escaped(server.map_name || "Farming Simulator map")}" draggable="false">
          <div class="map-coordinate-grid" aria-hidden="true"></div>
          ${mapLayersMarkup(data)}
        </div>

        <aside class="map-inspector" data-map-inspector hidden>
          <button type="button" class="map-inspector-close" data-map-action="close-inspector" aria-label="Close details">×</button>
          <div data-map-inspector-content></div>
        </aside>

        <div class="map-help">Drag to pan · wheel/pinch to zoom · click a marker for details</div>
        <div class="map-coordinate-readout" data-map-coordinates>Map ${number(server.map_size || 2048)} m</div>
      </div>

      <div class="interactive-map-footer">
        <div class="map-player-strip">
          ${players.length ? players.map(player => {
            const hasPosition = mapCoordinateAvailable(player.x) && mapCoordinateAvailable(player.z);
            return `<button type="button" class="map-player-chip" data-map-focus-player="${escaped(player.name)}" ${hasPosition ? "" : "disabled"}>${escaped(player.name)}<small>${escaped(player.vehicle || "Position unavailable")}</small></button>`;
          }).join("") : '<span class="map-player-chip static">Server empty</span>'}
        </div>
        <span class="map-source-note">${escaped(data.collector?.map_source || "GIANTS live map feed")} · Field and land layers use GIANTS centre coordinates; exact parcel boundaries are not included.</span>
      </div>
    </article>
  `;
}

function preserveMapState() {
  if (!runtime.mapController) return;
  runtime.mapViewState = runtime.mapController.getState();
  runtime.mapController.destroy();
  runtime.mapController = null;
}

function initialiseInteractiveMap(data, id) {
  preserveMapState();
  const root = document.getElementById(id);
  if (!root) return;

  const viewport = root.querySelector("[data-map-viewport]");
  const world = root.querySelector("[data-map-world]");
  const zoomText = root.querySelector("[data-map-zoom]");
  const coordinateText = root.querySelector("[data-map-coordinates]");
  const inspector = root.querySelector("[data-map-inspector]");
  const inspectorContent = root.querySelector("[data-map-inspector-content]");
  const mapSize = Number(root.dataset.mapSize) || Number(data.server?.map_size) || 2048;
  const sourcePixels = Number(root.dataset.mapPixels) || Number(data.collector?.map_width) || 512;
  const pointers = new Map();
  const listeners = [];
  const remembered = runtime.mapViewState || {};
  const state = {
    scale: Math.max(1, Math.min(8, Number(remembered.scale) || 1)),
    centerX: Number.isFinite(Number(remembered.centerX)) ? Number(remembered.centerX) : 0.5,
    centerY: Number.isFinite(Number(remembered.centerY)) ? Number(remembered.centerY) : 0.5,
    tx: 0,
    ty: 0,
    flipZ: Boolean(remembered.flipZ),
    layers: {
      players: remembered.layers?.players ?? true,
      vehicles: remembered.layers?.vehicles ?? true,
      owned: remembered.layers?.owned ?? true,
      fields: remembered.layers?.fields ?? false,
      farmland: remembered.layers?.farmland ?? false,
    },
    dragStart: null,
    pinchStart: null,
  };

  function listen(target, name, handler, options) {
    target.addEventListener(name, handler, options);
    listeners.push(() => target.removeEventListener(name, handler, options));
  }

  function metrics() {
    const width = viewport.clientWidth || 1;
    const height = viewport.clientHeight || 1;
    const size = Math.max(1, Math.min(width, height));
    return {
      width,
      height,
      size,
      originX: (width - size) / 2,
      originY: (height - size) / 2,
    };
  }

  function maximumScale() {
    const view = metrics();
    const nativeScale = sourcePixels / Math.max(1, view.size);
    return Math.max(1.5, Math.min(8, nativeScale * 1.25));
  }

  function clampScale(value) {
    return Math.max(1, Math.min(maximumScale(), Number(value) || 1));
  }

  function layoutWorld() {
    const view = metrics();
    world.style.left = `${view.originX}px`;
    world.style.top = `${view.originY}px`;
    world.style.width = `${view.size}px`;
    world.style.height = `${view.size}px`;
    return view;
  }

  function restoreCentre() {
    const view = layoutWorld();
    state.tx = view.width / 2 - view.originX - state.centerX * view.size * state.scale;
    state.ty = view.height / 2 - view.originY - state.centerY * view.size * state.scale;
  }

  function updateCentre() {
    const view = metrics();
    state.centerX = ((view.width / 2 - view.originX - state.tx) / state.scale) / view.size;
    state.centerY = ((view.height / 2 - view.originY - state.ty) / state.scale) / view.size;
  }

  function constrain() {
    const view = metrics();
    const scaled = view.size * state.scale;
    if (scaled <= view.width) {
      state.tx = (view.width - scaled) / 2 - view.originX;
    } else {
      const minX = view.width - view.originX - scaled;
      const maxX = -view.originX;
      state.tx = Math.max(minX, Math.min(maxX, state.tx));
    }
    if (scaled <= view.height) {
      state.ty = (view.height - scaled) / 2 - view.originY;
    } else {
      const minY = view.height - view.originY - scaled;
      const maxY = -view.originY;
      state.ty = Math.max(minY, Math.min(maxY, state.ty));
    }
    updateCentre();
  }

  function applyTransform(animate = false) {
    constrain();
    world.classList.toggle("animate-map", animate);
    world.style.transform = `translate3d(${state.tx}px, ${state.ty}px, 0) scale(${state.scale})`;
    world.style.setProperty("--map-inverse-scale", String(1 / state.scale));
    if (zoomText) zoomText.textContent = `${Math.round(state.scale * 100)}%`;
    root.classList.toggle("map-flipped", state.flipZ);
    world.querySelectorAll("[data-map-y]").forEach(marker => {
      const normal = Number(marker.dataset.mapY);
      marker.style.top = `${state.flipZ ? 100 - normal : normal}%`;
    });
  }

  function setScale(nextScale, clientX, clientY, animate = false) {
    const rect = viewport.getBoundingClientRect();
    const localX = Number.isFinite(clientX) ? clientX - rect.left : rect.width / 2;
    const localY = Number.isFinite(clientY) ? clientY - rect.top : rect.height / 2;
    const view = metrics();
    const worldX = (localX - view.originX - state.tx) / state.scale;
    const worldY = (localY - view.originY - state.ty) / state.scale;
    state.scale = clampScale(nextScale);
    state.tx = localX - view.originX - worldX * state.scale;
    state.ty = localY - view.originY - worldY * state.scale;
    applyTransform(animate);
  }

  function resetView(animate = true) {
    state.scale = 1;
    state.centerX = 0.5;
    state.centerY = 0.5;
    restoreCentre();
    applyTransform(animate);
  }

  function centreOnPercent(left, top, scale = Math.max(3, state.scale), animate = true) {
    const view = metrics();
    state.scale = clampScale(scale);
    state.centerX = left / 100;
    state.centerY = (state.flipZ ? 100 - top : top) / 100;
    state.tx = view.width / 2 - view.originX - state.centerX * view.size * state.scale;
    state.ty = view.height / 2 - view.originY - state.centerY * view.size * state.scale;
    applyTransform(animate);
  }

  function fitPlayers() {
    const markers = [...world.querySelectorAll('[data-map-layer="players"] .map-marker')];
    if (!markers.length) return;
    const points = markers.map(marker => ({ left: Number.parseFloat(marker.style.left), top: Number(marker.dataset.mapY) }));
    if (points.length === 1) {
      centreOnPercent(points[0].left, points[0].top, Math.min(4, maximumScale()));
      return;
    }
    const xs = points.map(point => point.left);
    const ys = points.map(point => state.flipZ ? 100 - point.top : point.top);
    const minX = Math.min(...xs); const maxX = Math.max(...xs);
    const minY = Math.min(...ys); const maxY = Math.max(...ys);
    const widthFraction = Math.max(0.08, (maxX - minX) / 100);
    const heightFraction = Math.max(0.08, (maxY - minY) / 100);
    const view = metrics();
    const target = Math.min(maximumScale(), Math.max(1, Math.min(0.72 / widthFraction, 0.72 / heightFraction)));
    state.scale = target;
    state.centerX = ((minX + maxX) / 2) / 100;
    state.centerY = ((minY + maxY) / 2) / 100;
    state.tx = view.width / 2 - view.originX - state.centerX * view.size * state.scale;
    state.ty = view.height / 2 - view.originY - state.centerY * view.size * state.scale;
    applyTransform(true);
  }

  function setLayer(layer, visible) {
    state.layers[layer] = visible;
    root.querySelectorAll(`[data-map-layer="${layer}"]`).forEach(element => { element.hidden = !visible; });
    root.querySelectorAll(`[data-map-layer-toggle="${layer}"]`).forEach(button => {
      button.classList.toggle("active", visible);
      button.setAttribute("aria-pressed", String(visible));
    });
  }

  function openInspector(detail) {
    if (!detail || !inspector || !inspectorContent) return;
    const rows = (detail.rows || []).map(row => `
      <div class="map-inspector-row"><span>${escaped(row[0])}</span><strong>${escaped(row[1])}</strong></div>
    `).join("");
    inspectorContent.innerHTML = `
      <div class="map-inspector-kind">${escaped(detail.kind || "Map item")}</div>
      <div class="map-inspector-title"><span>${escaped(detail.icon || "•")}</span><div><strong>${escaped(detail.title || "Map item")}</strong><small>${escaped(detail.subtitle || "")}</small></div></div>
      <div class="map-inspector-rows">${rows}</div>
    `;
    inspector.hidden = false;
  }

  function closeInspector() {
    if (inspector) inspector.hidden = true;
  }

  function localPoint(event) {
    const rect = viewport.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function pointerDistance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function pointerMidpoint(a, b) {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  listen(viewport, "wheel", event => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.18 : 1 / 1.18;
    setScale(state.scale * factor, event.clientX, event.clientY);
  }, { passive: false });

  listen(viewport, "dblclick", event => {
    if (event.target.closest("button, a")) return;
    setScale(state.scale * 1.7, event.clientX, event.clientY, true);
  });

  listen(viewport, "pointerdown", event => {
    if (event.target.closest("button, a, .map-inspector")) return;
    viewport.setPointerCapture?.(event.pointerId);
    pointers.set(event.pointerId, localPoint(event));
    root.classList.add("is-panning");
    if (pointers.size === 1) {
      state.dragStart = { point: localPoint(event), tx: state.tx, ty: state.ty };
      state.pinchStart = null;
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const midpoint = pointerMidpoint(a, b);
      state.pinchStart = {
        distance: Math.max(1, pointerDistance(a, b)),
        scale: state.scale,
        worldX: (midpoint.x - metrics().originX - state.tx) / state.scale,
        worldY: (midpoint.y - metrics().originY - state.ty) / state.scale,
      };
    }
  });

  listen(viewport, "pointermove", event => {
    const point = localPoint(event);
    const view = metrics();
    const worldX = ((point.x - view.originX - state.tx) / state.scale / view.size - 0.5) * mapSize;
    const worldYRaw = ((point.y - view.originY - state.ty) / state.scale / view.size - 0.5) * mapSize;
    const worldZ = state.flipZ ? -worldYRaw : worldYRaw;
    if (coordinateText) coordinateText.textContent = `X ${number(worldX, 0)} · Z ${number(worldZ, 0)} · ${Math.round(state.scale * 100)}%`;

    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, point);
    if (pointers.size >= 2 && state.pinchStart) {
      const [a, b] = [...pointers.values()];
      const midpoint = pointerMidpoint(a, b);
      const nextScale = clampScale(state.pinchStart.scale * pointerDistance(a, b) / state.pinchStart.distance);
      state.scale = nextScale;
      const view = metrics();
      state.tx = midpoint.x - view.originX - state.pinchStart.worldX * state.scale;
      state.ty = midpoint.y - view.originY - state.pinchStart.worldY * state.scale;
      applyTransform();
    } else if (state.dragStart) {
      state.tx = state.dragStart.tx + point.x - state.dragStart.point.x;
      state.ty = state.dragStart.ty + point.y - state.dragStart.point.y;
      applyTransform();
    }
  });

  function endPointer(event) {
    pointers.delete(event.pointerId);
    if (!pointers.size) {
      state.dragStart = null;
      state.pinchStart = null;
      root.classList.remove("is-panning");
    } else if (pointers.size === 1) {
      const point = [...pointers.values()][0];
      state.dragStart = { point, tx: state.tx, ty: state.ty };
      state.pinchStart = null;
    }
  }
  listen(viewport, "pointerup", endPointer);
  listen(viewport, "pointercancel", endPointer);

  listen(root, "click", event => {
    const layerButton = event.target.closest("[data-map-layer-toggle]");
    if (layerButton) {
      const layer = layerButton.dataset.mapLayerToggle;
      setLayer(layer, !state.layers[layer]);
      return;
    }

    const marker = event.target.closest("[data-map-detail]");
    if (marker) {
      try { openInspector(JSON.parse(marker.dataset.mapDetail)); } catch (_) { /* Ignore invalid marker metadata. */ }
      const wrapper = marker.closest("[data-map-y]");
      if (wrapper) centreOnPercent(Number.parseFloat(wrapper.style.left), Number(wrapper.dataset.mapY), Math.max(3, state.scale));
      return;
    }

    const playerButton = event.target.closest("[data-map-focus-player]");
    if (playerButton) {
      const name = playerButton.dataset.mapFocusPlayer;
      const markers = [...world.querySelectorAll('[data-map-layer="players"] .map-marker')];
      const target = markers.find(item => item.querySelector(".map-marker-label")?.textContent === name);
      if (target) {
        setLayer("players", true);
        centreOnPercent(Number.parseFloat(target.style.left), Number(target.dataset.mapY), Math.min(4, maximumScale()));
        target.querySelector("[data-map-detail]")?.click();
      }
      return;
    }

    const actionButton = event.target.closest("[data-map-action]");
    if (!actionButton) return;
    const action = actionButton.dataset.mapAction;
    if (action === "zoom-in") setScale(state.scale * 1.35, undefined, undefined, true);
    if (action === "zoom-out") setScale(state.scale / 1.35, undefined, undefined, true);
    if (action === "reset") resetView();
    if (action === "fit-players") fitPlayers();
    if (action === "flip-z") {
      state.flipZ = !state.flipZ;
      applyTransform(true);
      showToast(`Map marker north/south orientation ${state.flipZ ? "flipped" : "normal"}`);
    }
    if (action === "close-inspector") closeInspector();
    if (action === "fullscreen") {
      if (document.fullscreenElement) {
        document.exitFullscreen?.();
      } else if (root.requestFullscreen) {
        root.requestFullscreen().catch(() => root.classList.toggle("map-expanded"));
      } else {
        root.classList.toggle("map-expanded");
      }
    }
  });

  listen(viewport, "keydown", event => {
    const step = event.shiftKey ? 90 : 35;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "-", "Escape"].includes(event.key)) event.preventDefault();
    if (event.key === "ArrowLeft") state.tx += step;
    if (event.key === "ArrowRight") state.tx -= step;
    if (event.key === "ArrowUp") state.ty += step;
    if (event.key === "ArrowDown") state.ty -= step;
    if (event.key === "+" || event.key === "=") setScale(state.scale * 1.25, undefined, undefined, true);
    if (event.key === "-") setScale(state.scale / 1.25, undefined, undefined, true);
    if (event.key === "Escape") closeInspector();
    applyTransform();
  });

  const resizeObserver = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => {
        restoreCentre();
        applyTransform();
      })
    : null;
  resizeObserver?.observe(viewport);

  state.scale = clampScale(state.scale);
  Object.entries(state.layers).forEach(([layer, visible]) => setLayer(layer, visible));
  restoreCentre();
  applyTransform();

  runtime.mapController = {
    getState() {
      updateCentre();
      return {
        scale: state.scale,
        centerX: state.centerX,
        centerY: state.centerY,
        flipZ: state.flipZ,
        layers: { ...state.layers },
      };
    },
    destroy() {
      resizeObserver?.disconnect();
      listeners.forEach(remove => remove());
    },
  };
}

async function renderMap(force = false) {
  const token = ++runtime.renderToken;
  if (!runtime.overview || force) loadingPage("Loading the interactive map");
  const data = await fetchJson("api/overview");
  if (token !== runtime.renderToken) return;
  runtime.overview = data;
  runtime.lastVersion = Number(data.version) || runtime.lastVersion;
  runtime.lastUpdate = new Date();
  brandTitle.textContent = data.site_title || data.server?.name || "FS25 Server Hub";
  preserveMapState();
  const players = data.server?.players || [];
  const live = data.live || {};
  app.innerHTML = `
    <section class="page map-page">
      ${pageHeading(
        "Live operations",
        "Interactive Map",
        "Track connected players and vehicles, inspect fields and owned land, then zoom or pan around the complete map.",
        `<div class="page-heading-pills"><span class="pill">${number(players.length)} players</span><span class="pill">${number((live.vehicles || []).length)} live vehicles</span></div>`
      )}
      ${interactiveMapMarkup(data, { id: "full-interactive-map", compact: false })}
    </section>
  `;
  initialiseInteractiveMap(data, "full-interactive-map");
  setConnection(data.server?.online ? "online" : "offline", data.server?.online ? "Server online" : "Server offline");
}


async function renderOverview(force = false) {
  const token = ++runtime.renderToken;
  if (!runtime.overview || force) loadingPage("Loading the farming control room");
  const data = await fetchJson("api/overview");
  if (token !== runtime.renderToken) return;
  runtime.overview = data;
  runtime.lastVersion = Number(data.version) || runtime.lastVersion;
  runtime.lastUpdate = new Date();
  brandTitle.textContent = data.site_title || data.server?.name || "FS25 Server Hub";

  const server = data.server || {};
  const live = data.live || {};
  const career = data.career || {};
  const symbol = data.currency_symbol || "£";
  const mapStamp = encodeURIComponent(data.collector?.map_updated_at || data.generated_at || Date.now());
  const players = server.players || [];
  const statusClass = server.online ? "online" : "offline";
  const statusText = server.online ? "ONLINE" : "OFFLINE";

  preserveMapState();
  app.innerHTML = `
    <section class="page">
      <article class="hero-card">
        <div class="hero-copy">
          <p class="eyebrow">Private multiplayer server</p>
          <h1>${escaped(server.name || "FS25 Server")}</h1>
          <p>${escaped(server.map_name || career.map_title || "Unknown map")} · ${escaped(server.game || "Farming Simulator 25")} ${escaped(server.version || "")}</p>
          <div class="hero-pills">
            <span class="pill"><span class="status-dot ${statusClass}"></span>${statusText}</span>
            <span class="pill">${number(server.players_used)} / ${number(server.capacity || 4)} players</span>
            <span class="pill">In-game time ${escaped(server.game_time || "—")}</span>
            <span class="pill">Feed ${server.last_success ? ageFromIso(server.last_success) : "waiting"}</span>
          </div>
        </div>
        <div class="hero-stats">
          <div class="hero-stat"><span>Farm balance</span><strong>${money(career.money, symbol)}</strong></div>
          <div class="hero-stat"><span>Owned fields</span><strong>${number(live.owned_field_count)}</strong></div>
          <div class="hero-stat"><span>Fleet assets</span><strong>${number(data.fleet?.owned_count)}</strong></div>
          <div class="hero-stat"><span>Active mods</span><strong>${number(live.mod_count || career.mod_count)}</strong></div>
        </div>
      </article>

      <section class="overview-grid">
        ${interactiveMapMarkup(data, { id: "overview-interactive-map", compact: true })}

        <aside class="side-stack">
          <article class="glass-card">
            <div class="card-heading">
              <div><h2>Players online</h2><p>Sessions update live</p></div>
              <span class="state-pill ${players.length ? "good" : ""}">${number(players.length)} connected</span>
            </div>
            <div class="player-list">${playerCards(players)}</div>
          </article>

          <article class="glass-card">
            <div class="card-heading">
              <div><h2>Farm overview</h2><p>Latest saved career data</p></div>
              <span class="state-pill">${career.last_success ? ageFromIso(career.last_success) : "Waiting"}</span>
            </div>
            <div class="farm-stats">
              <div class="stat-card"><span>Balance</span><strong>${money(career.money, symbol)}</strong></div>
              <div class="stat-card"><span>Playtime</span><strong>${duration(career.play_time_seconds, true)}</strong></div>
              <div class="stat-card"><span>Land area</span><strong>${number(live.owned_area, 2)} ha</strong></div>
              <div class="stat-card"><span>Slot usage</span><strong>${number(career.slot_usage)}</strong></div>
            </div>
          </article>

          <article class="glass-card">
            <div class="card-heading">
              <div><h2>Recent activity</h2><p>Players, money and fleet changes</p></div>
              <a href="#/history" class="state-pill good">View all</a>
            </div>
            <div class="activity-list">${activityRows(data.recent_events, 7)}</div>
          </article>
        </aside>
      </section>

      ${overviewNavigation(data)}
    </section>
  `;
  initialiseInteractiveMap(data, "overview-interactive-map");
  updateLiveTimers();
  setConnection(server.online ? "online" : "offline", server.online ? "Server online" : "Server offline");
}

function vehicleMaintenanceState(vehicle) {
  const condition = vehicle.condition;
  const service = vehicle.service;
  if (vehicle.damage >= 0.55 || (condition !== null && condition < 0.4) || (service !== null && service < 0.4)) {
    return { label: "Needs attention", className: "error" };
  }
  if (vehicle.damage >= 0.3 || (condition !== null && condition < 0.55) || (service !== null && service < 0.55)) {
    return { label: "Watch", className: "warning" };
  }
  return { label: "Good", className: "good" };
}

function progress(label, value, invert = false) {
  const raw = Math.max(0, Math.min(1, Number(value) || 0));
  const displayed = invert ? 1 - raw : raw;
  const className = displayed < 0.4 ? "error" : displayed < 0.65 ? "warning" : "";
  return `
    <div class="progress-row">
      <div class="progress-label"><span>${escaped(label)}</span><strong>${percent(displayed)}</strong></div>
      <div class="progress-track"><div class="progress-bar ${className}" style="width:${Math.round(displayed * 100)}%"></div></div>
    </div>
  `;
}

function vehicleCard(vehicle, symbol) {
  const state = vehicleMaintenanceState(vehicle);
  const condition = vehicle.condition ?? (1 - Number(vehicle.damage || 0));
  const service = vehicle.service ?? (1 - Number(vehicle.damage || 0));
  const fuel = (vehicle.fills || []).find(fill => fill.fill_type === "DIESEL" || fill.fill_type === "ELECTRICCHARGE" || fill.fill_type === "METHANE");
  return `
    <article class="vehicle-card">
      <div class="vehicle-top">
        <div class="vehicle-name">
          <strong title="${escaped(vehicle.name)}">${escaped(vehicle.name)}</strong>
          <small>${escaped(vehicle.property_state)} · Farm ${number(vehicle.farm_id)}</small>
        </div>
        <span class="state-pill ${state.className}">${escaped(state.label)}</span>
      </div>
      <div class="vehicle-metrics">
        <div class="metric"><span>Value</span><strong>${money(vehicle.price, symbol)}</strong></div>
        <div class="metric"><span>Hours</span><strong>${number(Number(vehicle.operating_time_seconds || 0) / 3600, 1)} h</strong></div>
        <div class="metric"><span>Age</span><strong>${number(vehicle.age_months, 0)} months</strong></div>
        <div class="metric"><span>Distance</span><strong>${vehicle.odometer_km === null ? "—" : `${number(vehicle.odometer_km, 1)} km`}</strong></div>
      </div>
      ${progress("Condition", condition)}
      ${progress("Service state", service)}
      ${progress("Cleanliness", vehicle.dirt, true)}
      ${fuel ? `<div class="detail-line" style="margin-top:13px"><span>${escaped(titleCase(fuel.fill_type))}</span><strong>${number(fuel.level, 1)} L</strong></div>` : ""}
    </article>
  `;
}

async function renderVehicles() {
  const token = ++runtime.renderToken;
  loadingPage("Loading the vehicle fleet");
  const data = await fetchJson("api/vehicles?farm_only=true");
  if (token !== runtime.renderToken) return;
  const symbol = runtime.overview?.currency_symbol || "£";
  app.innerHTML = `
    <section class="page">
      ${pageHeading("Fleet control", "Vehicle Fleet", "Search every owned and leased asset, compare operating hours, and spot machinery that may need maintenance.")}
      <section class="fleet-summary">
        ${miniStat("Owned assets", number(data.owned_count))}
        ${miniStat("Leased assets", number(data.leased_count))}
        ${miniStat("Fleet value", money(data.total_value, symbol))}
        ${miniStat("Needs attention", number(data.maintenance_count))}
      </section>
      <article class="glass-card">
        <div class="toolbar">
          <div class="filters">
            <input class="field-control" id="vehicle-search" type="search" placeholder="Search tractor, harvester, mod…" autocomplete="off">
            <select class="select-control" id="vehicle-state">
              <option value="">Owned and leased</option>
              <option value="OWNED">Owned only</option>
              <option value="LEASED">Leased only</option>
            </select>
            <label class="action-button"><input id="maintenance-only" type="checkbox"> Maintenance watch</label>
          </div>
          <span class="state-pill"><span id="vehicle-count">${number(data.returned_count)}</span> shown</span>
        </div>
        <div class="vehicle-grid" id="vehicle-grid">
          ${(data.vehicles || []).map(vehicle => vehicleCard(vehicle, symbol)).join("") || '<div class="empty-state">No farm vehicles found.</div>'}
        </div>
      </article>
    </section>
  `;

  let timer;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const search = document.getElementById("vehicle-search")?.value || "";
      const state = document.getElementById("vehicle-state")?.value || "";
      const maintenance = document.getElementById("maintenance-only")?.checked || false;
      try {
        const filtered = await fetchJson(`api/vehicles?farm_only=true&q=${encodeURIComponent(search)}&state=${encodeURIComponent(state)}&maintenance=${maintenance}`);
        const grid = document.getElementById("vehicle-grid");
        const count = document.getElementById("vehicle-count");
        if (grid) grid.innerHTML = (filtered.vehicles || []).map(vehicle => vehicleCard(vehicle, symbol)).join("") || '<div class="empty-state">No vehicles match those filters.</div>';
        if (count) count.textContent = number(filtered.returned_count);
      } catch (error) {
        showToast(`Fleet filter failed: ${error.message}`);
      }
    }, 180);
  };

  document.getElementById("vehicle-search")?.addEventListener("input", refresh);
  document.getElementById("vehicle-state")?.addEventListener("change", refresh);
  document.getElementById("maintenance-only")?.addEventListener("change", refresh);
}

function economyCategory(type) {
  const categories = {
    contract_payment: { label: "Contract payment", tone: "contract" },
    production_autosale: { label: "Production autosale", tone: "income" },
    product_sale: { label: "Product sale", tone: "income" },
    vehicle_sale: { label: "Vehicle sale", tone: "income" },
    income: { label: "Other income", tone: "income" },
    supply_purchase: { label: "Farm supplies", tone: "spending" },
    vehicle_purchase: { label: "Vehicle purchase", tone: "spending" },
    vehicle_repair: { label: "Vehicle repairs", tone: "spending" },
    farm_purchase: { label: "Mixed purchase", tone: "spending" },
    expense: { label: "Operating expense", tone: "spending" },
    money_change: { label: "Balance adjustment", tone: "neutral" },
    animal_sale: { label: "Animal sale", tone: "income" },
    animal_purchase: { label: "Animal purchase", tone: "spending" },
    land_purchase: { label: "Land purchase", tone: "spending" },
    land_sale: { label: "Land sale", tone: "income" },
    building_purchase: { label: "Building / construction", tone: "spending" },
    loan_income: { label: "Loan received", tone: "income" },
    loan_repayment: { label: "Loan repayment", tone: "spending" },
    lease_expense: { label: "Lease / rental", tone: "spending" },
    operating_expense: { label: "Operating expense", tone: "spending" },
    other_income: { label: "Other income", tone: "income" },
    other_expense: { label: "Other expense", tone: "spending" },
    ignored: { label: "Ignored", tone: "neutral" },
  };
  return categories[type] || { label: titleCase(type || "transaction"), tone: "neutral" };
}

function supplyGroupLabel(value) {
  const groups = {
    animal_feed: "Animal feed",
    crop_input: "Crop input",
    fuel_utility: "Fuel / utility",
    other_supply: "Farm supply",
  };
  return groups[value] || titleCase(value || "Saved object");
}

function sourcePills(sources, compact = false) {
  if (!(sources || []).length) return "";
  return `<span class="source-pills ${compact ? "compact" : ""}">${sources.map(source => `<span>${escaped(source)}</span>`).join("")}</span>`;
}

function objectEvidenceRows(items, symbol, verb) {
  return (items || []).map(item => {
    const count = Number(item.count || 0);
    const quantity = Number(item.fill_amount || 0);
    const price = Number(item.price_total || 0);
    const facts = [
      count > 1 ? `${number(count)} objects` : "1 object",
      quantity > 0 ? `${number(quantity, 1)} units` : null,
      price > 0 ? `${money(price, symbol)} saved value` : null,
      item.group ? supplyGroupLabel(item.group) : null,
    ].filter(Boolean).join(" · ");
    return `
      <div class="audit-line">
        <span>${escaped(verb)}</span>
        <strong>${escaped(item.name || "Saved object")}</strong>
        <small>${escaped(facts)}</small>
      </div>
    `;
  }).join("");
}

function missionAuditCard(mission, match, symbol) {
  const reward = Number(mission.reward || 0);
  const reimbursement = Number(mission.reimbursement || 0);
  const expected = Number(mission.expected_payout || reward + reimbursement || 0);
  const facts = [
    mission.field_id !== null && mission.field_id !== undefined ? `Field ${number(mission.field_id)}` : null,
    mission.progress_detail || `${Math.round((Number(mission.completion) || 0) * 100)}% complete`,
    mission.borrowed_vehicles ? "Borrowed machinery" : "Own machinery",
    mission.finish_state && mission.finish_state !== "NONE" ? titleCase(mission.finish_state) : titleCase(mission.status || mission.state),
  ].filter(Boolean);
  return `
    <article class="mission-audit-card">
      <div>
        <strong>${escaped(mission.title || mission.label || "Contract")}</strong>
        <small>${escaped(facts.join(" · "))}</small>
      </div>
      <div class="mission-money-grid">
        <span><small>Reward</small><strong>${reward > 0 ? money(reward, symbol) : "Calculated in game"}</strong></span>
        <span><small>Reimbursement</small><strong>${reimbursement > 0 ? money(reimbursement, symbol) : money(0, symbol)}</strong></span>
        <span><small>Listed payout</small><strong>${expected > 0 ? money(expected, symbol) : "Not stored"}</strong></span>
      </div>
      ${match?.quality ? `<span class="match-quality">${escaped(titleCase(match.quality))}</span>` : ""}
    </article>
  `;
}

function transactionEvidence(event, symbol) {
  const meta = event.meta || {};
  const match = meta.contract_match || {};
  const production = meta.production_autosale || {};
  const productionOutputs = production.outputs || [];
  const balanceAvailable = Number.isFinite(Number(meta.old_balance)) && Number.isFinite(Number(meta.new_balance));
  const objects = [
    objectEvidenceRows(meta.added_assets, symbol, "Fleet added"),
    objectEvidenceRows(meta.removed_assets, symbol, "Fleet removed"),
    objectEvidenceRows(meta.added_supplies, symbol, "Supply added"),
    objectEvidenceRows(meta.removed_products, symbol, "Product removed"),
  ].join("");
  const inventory = [
    ...(meta.inventory_decreases || []).slice(0, 5).map(item => ({ ...item, direction: "decreased" })),
    ...(meta.inventory_increases || []).slice(0, 5).map(item => ({ ...item, direction: "increased" })),
  ];
  const evidence = (meta.evidence || []).map(item => `<span class="evidence-chip">${escaped(item)}</span>`).join("");
  const missions = (meta.missions || []).map(item => missionAuditCard(item, match, symbol)).join("");
  const hasAnything = balanceAvailable || objects || inventory.length || evidence || missions || productionOutputs.length || meta.confidence_reason || (meta.sources || []).length;
  if (!hasAnything) return "";
  return `
    <details class="transaction-evidence audit-evidence">
      <summary>Open audit trail ${balanceAvailable ? `<span class="balance-path">${money(meta.old_balance, symbol)} <b>→</b> ${money(meta.new_balance, symbol)}</span>` : ""}</summary>
      <div class="audit-panel">
        <div class="audit-header">
          <div>
            <span>Why this classification?</span>
            <strong>${escaped(meta.confidence_reason || "The saved files support this transaction classification")}</strong>
          </div>
          ${sourcePills(meta.sources)}
        </div>
        ${balanceAvailable ? `
          <section class="audit-section">
            <h4>Money movement</h4>
            <div class="money-audit-grid">
              <span><small>Before</small><strong>${money(meta.old_balance, symbol)}</strong></span>
              <span><small>Change</small><strong class="${Number(event.amount) >= 0 ? "positive" : "negative"}">${money(event.amount, symbol, true)}</strong></span>
              <span><small>After</small><strong>${money(meta.new_balance, symbol)}</strong></span>
              ${Number(match.expected_payout || 0) > 0 ? `<span><small>Listed contract payout</small><strong>${money(match.expected_payout, symbol)}</strong></span>` : ""}
              ${match.variance !== null && match.variance !== undefined ? `<span><small>Difference</small><strong>${money(match.variance, symbol, true)}</strong></span>` : ""}
            </div>
          </section>
        ` : ""}
        ${missions ? `<section class="audit-section"><h4>Matched contract</h4><div class="mission-audit-list">${missions}</div></section>` : ""}
        ${productionOutputs.length ? `
          <section class="audit-section">
            <h4>Automatic production sales</h4>
            <div class="audit-lines">
              ${productionOutputs.map(item => `<div class="audit-line"><span>Direct sell</span><strong>${escaped(item.label || titleCase(item.fill_type))}</strong><small>${escaped((item.sites || []).join(" · ") || "Owned production building")}</small></div>`).join("")}
            </div>
          </section>
        ` : ""}
        ${objects ? `<section class="audit-section"><h4>Saved objects</h4><div class="audit-lines">${objects}</div></section>` : ""}
        ${inventory.length ? `
          <section class="audit-section">
            <h4>Inventory movement</h4>
            <div class="audit-lines">
              ${inventory.map(item => `<div class="audit-line"><span>${escaped(titleCase(item.direction))}</span><strong>${escaped(item.label || titleCase(item.fill_type))}</strong><small>${number(item.amount, 1)} units</small></div>`).join("")}
            </div>
          </section>
        ` : ""}
        ${evidence ? `<section class="audit-section"><h4>Collector notes</h4><div class="evidence-list">${evidence}</div></section>` : ""}
      </div>
    </details>
  `;
}

function transactionRow(event, symbol) {
  const amountValue = Number(event.amount);
  const amountClass = amountValue > 0 ? "positive" : amountValue < 0 ? "negative" : "neutral";
  const category = economyCategory(event.event_type);
  const sources = event.meta?.sources || [];
  return `
    <article class="transaction-row enhanced" data-transaction-type="${escaped(event.event_type)}" data-transaction-confidence="${escaped(event.confidence || "confirmed")}">
      <div class="transaction-icon ${category.tone}">${escaped(eventIcon(event.event_type))}</div>
      <div class="transaction-main">
        <div class="transaction-kicker">
          <span class="transaction-category ${category.tone}">${escaped(category.label)}</span>
          ${confidencePill(event.confidence)}
          ${sourcePills(sources, true)}
        </div>
        <strong>${escaped(event.title)}</strong>
        <small>${escaped(event.detail || "Balance change")} · ${escaped(exactDate(event.ts))}</small>
        ${transactionEvidence(event, symbol)}
      </div>
      <div class="amount ${amountClass}">${money(amountValue, symbol, true)}</div>
    </article>
  `;
}

function inventoryCard(item) {
  const history = item.history || [];
  const max = Math.max(...history.map(point => Number(point.value) || 0), 1);
  return `
    <article class="inventory-card">
      <div class="inventory-top">
        <div class="inventory-name">
          <strong>${escaped(item.label)}</strong>
          <small>${escaped(item.name)}</small>
        </div>
        <span class="state-pill ${item.total_amount > 0 ? "good" : ""}">${number(item.total_amount)} units</span>
      </div>
      <div class="price-history" title="Seasonal price pattern">
        ${history.map(point => `<span class="price-bar" style="height:${Math.max(4, (Number(point.value) / max) * 100)}%" title="${escaped(titleCase(point.period))}: ${number(point.value)}"></span>`).join("")}
      </div>
      <div class="inventory-meta">
        <div class="detail-line"><span>Peak period</span><strong>${escaped(titleCase(item.peak_period || "Unknown"))}</strong></div>
        <div class="detail-line"><span>Peak index</span><strong>${number(item.peak_value)}</strong></div>
      </div>
    </article>
  `;
}

function drawMoneyChart(canvas, daily, symbol) {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  context.clearRect(0, 0, width, height);

  const rows = daily || [];
  if (!rows.length) {
    context.fillStyle = "rgba(190, 211, 196, .7)";
    context.font = "14px system-ui";
    context.textAlign = "center";
    context.fillText("Money history will build after saved balance changes", width / 2, height / 2);
    return;
  }

  const padding = { top: 20, right: 18, bottom: 34, left: 52 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const max = Math.max(...rows.map(row => Math.max(Number(row.income || 0), Number(row.spending || 0))), 1);
  const groupWidth = chartWidth / rows.length;
  const barWidth = Math.max(4, groupWidth * 0.28);

  context.strokeStyle = "rgba(169, 255, 190, .10)";
  context.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + chartHeight * (i / 4);
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
  }

  rows.forEach((row, index) => {
    const center = padding.left + groupWidth * index + groupWidth / 2;
    const incomeHeight = chartHeight * (Number(row.income || 0) / max);
    const spendHeight = chartHeight * (Number(row.spending || 0) / max);
    context.fillStyle = "rgba(101, 229, 131, .88)";
    context.fillRect(center - barWidth - 2, padding.top + chartHeight - incomeHeight, barWidth, incomeHeight);
    context.fillStyle = "rgba(255, 126, 120, .82)";
    context.fillRect(center + 2, padding.top + chartHeight - spendHeight, barWidth, spendHeight);
    if (rows.length <= 14 || index % Math.ceil(rows.length / 10) === 0) {
      context.fillStyle = "rgba(190, 211, 196, .72)";
      context.font = "10px system-ui";
      context.textAlign = "center";
      context.fillText(String(row.day || "").slice(5), center, height - 12);
    }
  });

  context.fillStyle = "rgba(190, 211, 196, .72)";
  context.font = "10px system-ui";
  context.textAlign = "right";
  context.fillText(`${symbol}${number(max)}`, padding.left - 7, padding.top + 4);
  context.fillText(`${symbol}0`, padding.left - 7, padding.top + chartHeight + 4);
}

function categoryBreakdownRows(categories, symbol, direction) {
  const rows = (categories || [])
    .map(item => ({ ...item, value: Number(direction === "income" ? item.income : item.spending) || 0 }))
    .filter(item => item.value > 0)
    .sort((a, b) => b.value - a.value);
  const max = Math.max(...rows.map(item => item.value), 1);
  if (!rows.length) return '<div class="empty-state compact">No entries in this period.</div>';
  return rows.map(item => {
    const category = economyCategory(item.event_type);
    return `
      <div class="breakdown-row">
        <div class="breakdown-label"><span>${escaped(category.label)}</span><strong>${money(item.value, symbol)}</strong></div>
        <div class="breakdown-track"><span class="${direction}" style="width:${Math.max(4, item.value / max * 100)}%"></span></div>
        <small>${number(item.entries)} entr${Number(item.entries) === 1 ? "y" : "ies"} · ${number(item.confirmed_entries)} confirmed</small>
      </div>
    `;
  }).join("");
}

function missionCard(mission, symbol) {
  const completion = Math.max(0, Math.min(1, Number(mission.completion) || 0));
  const reward = Number(mission.reward || 0);
  const reimbursement = Number(mission.reimbursement || 0);
  const expected = Number(mission.expected_payout || reward + reimbursement || 0);
  const stateClass = mission.finish_state === "SUCCESS" ? "good" : mission.state === "active" ? "active" : "";
  const details = [
    mission.field_id !== null && mission.field_id !== undefined ? `Field ${number(mission.field_id)}` : null,
    Number(mission.farm_id) > 0 ? `Farm ${number(mission.farm_id)}` : null,
    mission.borrowed_vehicles ? "Borrowed machinery" : "Own machinery",
    mission.end_day !== null && mission.end_day !== undefined ? `Ends day ${number(mission.end_day)} ${mission.end_time || ""}`.trim() : null,
  ].filter(Boolean).join(" · ");
  return `
    <article class="mission-card detailed">
      <div class="mission-top">
        <div>
          <strong>${escaped(mission.title || mission.label || "Contract")}</strong>
          <small>${escaped(details || titleCase(mission.status))}</small>
        </div>
        <span class="state-pill ${stateClass}">${escaped(mission.finish_state === "SUCCESS" ? "Complete" : titleCase(mission.status || mission.state))}</span>
      </div>
      <div class="mission-progress"><span style="width:${Math.round(completion * 100)}%"></span></div>
      <div class="mission-progress-copy">
        <span>${escaped(mission.progress_detail || `${Math.round(completion * 100)}% complete`)}</span>
        <strong>${Math.round(completion * 100)}%</strong>
      </div>
      <div class="mission-payout-strip">
        <span><small>Reward</small><strong>${reward > 0 ? money(reward, symbol) : "In-game"}</strong></span>
        <span><small>Reimbursement</small><strong>${reimbursement > 0 ? money(reimbursement, symbol) : money(0, symbol)}</strong></span>
        <span><small>Listed payout</small><strong>${expected > 0 ? money(expected, symbol) : "Calculated in game"}</strong></span>
      </div>
    </article>
  `;
}

function availableMissionRow(mission, symbol) {
  const expected = Number(mission.expected_payout || 0);
  const meta = [
    mission.field_id !== null && mission.field_id !== undefined ? `Field ${number(mission.field_id)}` : null,
    mission.end_day !== null && mission.end_day !== undefined ? `Day ${number(mission.end_day)} at ${mission.end_time || "—"}` : null,
  ].filter(Boolean).join(" · ");
  return `
    <article class="available-mission-row">
      <div>
        <strong>${escaped(mission.title || mission.label || "Contract")}</strong>
        <small>${escaped(meta || "Available contract")}</small>
      </div>
      <span>${expected > 0 ? money(expected, symbol) : "Calculated in game"}</span>
    </article>
  `;
}

function contractActivityRows(events, symbol) {
  if (!(events || []).length) return '<div class="empty-state compact">Contract activity will appear after missions.xml changes.</div>';
  return events.slice(0, 8).map(event => `
    <article class="activity-row contract-activity">
      <div class="activity-icon">${escaped(eventIcon(event.event_type))}</div>
      <div class="activity-copy">
        <strong>${escaped(event.title)}</strong>
        <small>${escaped(event.detail || "Contract state changed")} · ${escaped(ageFromUnix(event.ts))}${event.amount !== null && event.amount !== undefined ? ` · ${money(event.amount, symbol, true)}` : ""}</small>
      </div>
      ${confidencePill(event.confidence)}
    </article>
  `).join("");
}

function contractTypeRows(types, symbol) {
  if (!(types || []).length) return '<div class="empty-state compact">Paid contract types will appear here.</div>';
  const max = Math.max(...types.map(item => Number(item.income || 0)), 1);
  return types.slice(0, 6).map(item => `
    <div class="contract-type-row">
      <div><span>${escaped(item.label || titleCase(item.type))}</span><strong>${money(item.income, symbol)}</strong></div>
      <div class="breakdown-track"><span class="contract" style="width:${Math.max(4, Number(item.income || 0) / max * 100)}%"></span></div>
      <small>${number(item.count)} paid contract${Number(item.count) === 1 ? "" : "s"}</small>
    </div>
  `).join("");
}

function reviewQueueItem(item, categories, symbol) {
  const amount = Number(item.amount || 0);
  return `
    <article class="review-item" data-review-id="${number(item.id)}">
      <div class="review-item-main">
        <div class="transaction-kicker">
          <span class="transaction-category ${amount >= 0 ? "income" : "spending"}">${amount >= 0 ? "Unclassified income" : "Unclassified spending"}</span>
          <span class="state-pill warning">Needs review</span>
        </div>
        <strong>${escaped(item.title || "Unclassified transaction")}</strong>
        <small>${escaped(item.detail || "No matching savegame evidence")} · ${escaped(exactDate(item.ts))}</small>
      </div>
      <strong class="amount ${amount >= 0 ? "positive" : "negative"}">${money(amount, symbol, true)}</strong>
      <div class="review-controls">
        <select class="select-control review-category" aria-label="Transaction category">
          <option value="">Choose category…</option>
          ${(categories || []).map(category => `<option value="${escaped(category.value)}">${escaped(category.label)}</option>`).join("")}
        </select>
        <input class="field-control review-label" type="text" maxlength="80" placeholder="Optional custom title, e.g. Mushroom autosale">
        <label class="review-rule-option"><input type="checkbox" class="review-remember"> Remember similar amounts</label>
        <button class="action-button review-apply" type="button">Apply</button>
      </div>
    </article>
  `;
}

function classificationRuleRow(rule, symbol) {
  const direction = Number(rule.direction) >= 0 ? "Income" : "Spending";
  return `
    <article class="rule-row" data-rule-id="${number(rule.id)}">
      <div>
        <strong>${escaped(rule.label || economyCategory(rule.category).label)}</strong>
        <small>${escaped(direction)} · ${money(rule.min_amount, symbol)}–${money(rule.max_amount, symbol)} · used ${number(rule.use_count)} times</small>
      </div>
      <button class="text-button rule-delete" type="button">Delete</button>
    </article>
  `;
}

async function renderEconomy() {
  const token = ++runtime.renderToken;
  const days = Number(runtime.economyDays) || 30;
  loadingPage("Loading the farm economy");
  const data = await fetchJson(`api/economy?days=${days}`);
  if (token !== runtime.renderToken) return;
  const symbol = data.currency_symbol || "£";
  const transactions = data.history?.transactions || [];
  const activity = data.history?.activity || [];
  const daily = data.history?.daily || [];
  const categories = data.history?.categories || [];
  const ledger = data.history?.ledger_summary || {};
  const contractStats = data.history?.contract_stats || {};
  const income = daily.reduce((sum, row) => sum + Number(row.income || 0), 0);
  const spending = daily.reduce((sum, row) => sum + Number(row.spending || 0), 0);
  const net = daily.reduce((sum, row) => sum + Number(row.net || 0), 0);
  const contractIncome = Number(contractStats.income || 0);
  const confirmedValueRate = Number(ledger.confirmed_value_rate || 0);
  const inventories = (data.economy?.fill_types || []).filter(item => item.total_amount > 0).sort((a, b) => b.total_amount - a.total_amount);
  const demands = data.economy?.great_demands || [];
  const missions = data.missions || {};
  const productions = data.productions || {};
  const directSellOutputs = productions.direct_sell_outputs || [];
  const activeMissions = missions.active || [];
  const availableMissions = [...(missions.available_contracts || [])].sort((a, b) => Number(b.expected_payout || 0) - Number(a.expected_payout || 0));
  const categoryOptions = [...new Set(transactions.map(item => item.event_type))].sort();
  const review = data.review || {};
  const reviewQueue = review.queue || [];
  const reviewRules = review.rules || [];
  const reviewCategories = review.categories || [];

  app.innerHTML = `
    <section class="page economy-page economy-v4">
      ${pageHeading("Farm finances", "Economy", "An audited farm ledger that correlates balances with contracts, automatic production sales, products, supplies and fleet records.", `
        <div class="heading-actions">
          <select class="select-control" id="economy-days" aria-label="Economy period">
            ${[7, 30, 90, 365].map(value => `<option value="${value}" ${value === days ? "selected" : ""}>Last ${value} days</option>`).join("")}
          </select>
          <a class="action-button" href="${apiUrl("api/events.csv")}">Download audit CSV</a>
        </div>
      `)}
      <section class="economy-summary economy-summary-wide">
        ${miniStat("Current balance", money(data.career?.money, symbol))}
        ${miniStat(`${days}-day income`, money(income, symbol), "positive-stat")}
        ${miniStat(`${days}-day spending`, money(spending, symbol), "negative-stat")}
        ${miniStat("Net movement", money(net, symbol, true), net >= 0 ? "positive-stat" : "negative-stat")}
        ${miniStat("Contract income", money(contractIncome, symbol), "contract-stat")}
        ${miniStat("Confirmed by value", `${number(confirmedValueRate, 1)}%`, confirmedValueRate >= 75 ? "positive-stat" : "contract-stat")}
      </section>

      <section class="audit-summary-strip">
        <div><span>Confirmed entries</span><strong>${number(ledger.confirmed_entries || 0)} / ${number(ledger.entry_count || 0)}</strong></div>
        <div><span>Confirmed movement</span><strong>${money(ledger.confirmed_value || 0, symbol)}</strong></div>
        <div><span>Unclassified income</span><strong>${money(ledger.unclassified_income || 0, symbol)}</strong></div>
        <div><span>Unclassified spending</span><strong>${money(ledger.unclassified_spending || 0, symbol)}</strong></div>
      </section>

      ${!missions.available ? `
        <div class="feed-callout warning">
          <strong>Mission matching is waiting for missions.xml</strong>
          <span>Connect <code>missions.xml</code> using either <code>missions_url</code> or the GPORTAL FTP fields. Contract payments remain inferred until it is connected.</span>
        </div>
      ` : `
        <div class="feed-callout good">
          <strong>Mission audit connected</strong>
          <span>${number(missions.active_count)} active · ${number(missions.available_count)} available · ${number(missions.completed_count)} finished in latest save · checked ${missions.last_success ? ageFromIso(missions.last_success) : "recently"}</span>
          ${sourcePills(["missions.xml", "careerSavegame.xml", "vehicles.xml", "economy.xml", ...(productions.available ? ["placeables.xml"] : [])])}
        </div>
      `}

      <section class="section-grid economy-primary-grid">
        <article class="glass-card chart-card">
          <div class="card-heading">
            <div><h2>Money movement</h2><p>Income and spending captured from save-to-save farm balance changes</p></div>
            <span class="state-pill">Last save ${data.career?.last_success ? ageFromIso(data.career.last_success) : "waiting"}</span>
          </div>
          <div class="chart-legend"><span class="income">Income</span><span class="spending">Spending</span></div>
          <div class="chart-wrap"><canvas id="money-chart"></canvas></div>
        </article>

        <article class="glass-card mission-panel">
          <div class="card-heading">
            <div><h2>Active contracts</h2><p>Live mission progress, equipment and listed payout</p></div>
            <span class="state-pill ${activeMissions.length ? "active" : ""}">${number(activeMissions.length)} active</span>
          </div>
          <div class="mission-summary-strip three">
            <div><span>Listed active payouts</span><strong>${money(missions.active_reward_total || 0, symbol)}</strong></div>
            <div><span>Available jobs</span><strong>${number(missions.available_count || 0)}</strong></div>
            <div><span>Paid this period</span><strong>${number(contractStats.paid_count || 0)}</strong></div>
          </div>
          <div class="mission-list">
            ${activeMissions.length ? activeMissions.slice(0, 5).map(item => missionCard(item, symbol)).join("") : '<div class="empty-state compact">No accepted contracts are present in the latest save.</div>'}
          </div>
          <div class="contract-activity-list">${contractActivityRows(activity, symbol)}</div>
        </article>
      </section>

      <section class="section-grid contract-insight-grid" style="margin-top:18px">
        <article class="glass-card contract-performance-card">
          <div class="card-heading"><div><h2>Contract performance</h2><p>Payments and mission outcomes in the selected period</p></div><strong class="amount positive">${money(contractIncome, symbol)}</strong></div>
          <div class="contract-kpi-grid">
            ${miniStat("Paid contracts", number(contractStats.paid_count || 0))}
            ${miniStat("Average payment", money(contractStats.average_payment || 0, symbol))}
            ${miniStat("Largest payment", money(contractStats.largest_payment || 0, symbol), "contract-stat")}
            ${miniStat("Completed states", number(contractStats.completed || 0), "positive-stat")}
            ${miniStat("Failed", number(contractStats.failed || 0), "negative-stat")}
            ${miniStat("Cancelled / lost", number(contractStats.cancelled || 0))}
          </div>
          <div class="contract-type-list">${contractTypeRows(contractStats.types, symbol)}</div>
        </article>

        <article class="glass-card available-contract-card">
          <div class="card-heading">
            <div><h2>Available contract board</h2><p>The best visible offers from the latest missions.xml save</p></div>
            <span class="state-pill">${number(availableMissions.length)} jobs</span>
          </div>
          <div class="available-mission-list">
            ${availableMissions.length ? availableMissions.slice(0, 8).map(item => availableMissionRow(item, symbol)).join("") : '<div class="empty-state compact">No available jobs were reported in the latest save.</div>'}
          </div>
          <small class="panel-note">Some fieldwork rewards are calculated by the game and remain zero in missions.xml until the game resolves them.</small>
        </article>
      </section>

      <article class="glass-card production-autosale-card" style="margin-top:18px">
        <div class="card-heading">
          <div><h2>Automatic production sales</h2><p>Outputs set to Selling in owned production buildings</p></div>
          <span class="state-pill ${directSellOutputs.length ? "good" : ""}">${number(directSellOutputs.length)} products</span>
        </div>
        ${productions.available ? `
          <div class="production-autosale-grid">
            ${directSellOutputs.length ? directSellOutputs.map(item => `
              <article class="production-autosale-item">
                <div class="activity-icon">A</div>
                <div>
                  <strong>${escaped(item.label || titleCase(item.fill_type))}</strong>
                  <small>${escaped((item.sites || []).join(" · ") || "Owned production building")}</small>
                </div>
                <span class="state-pill good">Direct sell</span>
              </article>
            `).join("") : '<div class="empty-state compact">No owned production outputs are currently set to direct selling.</div>'}
          </div>
          <small class="panel-note">Income is labelled as inferred because the savegame records the direct-sell setting and balance change, but not a separate receipt with the exact product allocation.</small>
        ` : `
          <div class="empty-state compact">Waiting for <code>placeables.xml</code>. With GPORTAL FTP configured, the hub automatically reads it from the same savegame folder as <code>missions.xml</code>.</div>
        `}
      </article>

      <article class="glass-card review-card" style="margin-top:18px">
        <div class="card-heading">
          <div><h2>Needs review</h2><p>Classify balance changes the savegame could not explain, then optionally remember a safe amount-range rule</p></div>
          <span class="state-pill ${reviewQueue.length ? "warning" : "good"}">${number(reviewQueue.length)} waiting</span>
        </div>
        <div class="review-list" id="review-list">
          ${reviewQueue.length ? reviewQueue.map(item => reviewQueueItem(item, reviewCategories, symbol)).join("") : '<div class="empty-state compact">Nothing needs review. All captured balance changes have a useful classification.</div>'}
        </div>
        <details class="saved-rules">
          <summary>Saved classification rules <span class="state-pill">${number(reviewRules.length)}</span></summary>
          <div class="rule-list" id="rule-list">
            ${reviewRules.length ? reviewRules.map(rule => classificationRuleRow(rule, symbol)).join("") : '<div class="empty-state compact">No remembered rules yet.</div>'}
          </div>
          <small class="panel-note">Rules only apply to future unclassified entries with the same direction and a similar amount. They never override a contract, product, production or fleet match.</small>
        </details>
      </article>

      <section class="section-grid economy-breakdown-grid" style="margin-top:18px">
        <article class="glass-card">
          <div class="card-heading"><div><h2>Income breakdown</h2><p>Where money entered the farm</p></div><strong class="amount positive">${money(income, symbol)}</strong></div>
          <div class="breakdown-list">${categoryBreakdownRows(categories, symbol, "income")}</div>
        </article>
        <article class="glass-card">
          <div class="card-heading"><div><h2>Spending breakdown</h2><p>Where money left the farm</p></div><strong class="amount negative">${money(-spending, symbol)}</strong></div>
          <div class="breakdown-list">${categoryBreakdownRows(categories, symbol, "spending")}</div>
        </article>
      </section>

      <article class="glass-card transaction-ledger" style="margin-top:18px">
        <div class="card-heading">
          <div><h2>Farm transaction ledger</h2><p>Open any row to see its balance path, source files, matched contract and saved-object evidence</p></div>
          <span class="state-pill"><span id="transaction-count">${number(transactions.length)}</span> entries</span>
        </div>
        <div class="toolbar economy-toolbar">
          <div class="filters">
            <input class="field-control" id="transaction-search" type="search" placeholder="Search contracts, products, feed, supplies…" autocomplete="off">
            <select class="select-control" id="transaction-category">
              <option value="">All categories</option>
              ${categoryOptions.map(type => `<option value="${escaped(type)}">${escaped(economyCategory(type).label)}</option>`).join("")}
            </select>
            <select class="select-control" id="transaction-confidence">
              <option value="">Confirmed and inferred</option>
              <option value="confirmed">Confirmed only</option>
              <option value="inferred">Inferred only</option>
              <option value="manual">Reviewed only</option>
            </select>
          </div>
        </div>
        <div class="transaction-list" id="transaction-list">
          ${transactions.length ? transactions.map(event => transactionRow(event, symbol)).join("") : '<div class="empty-state">Complete a sale, contract or purchase after the collector starts and it will appear here after the next save.</div>'}
        </div>
      </article>

      <section class="section-grid" style="margin-top:18px">
        <article class="glass-card">
          <div class="card-heading">
            <div><h2>Great demands</h2><p>Scheduled and active selling bonuses</p></div>
            <span class="state-pill ${demands.some(item => item.is_running) ? "good" : ""}">${demands.filter(item => item.is_running).length} active</span>
          </div>
          <div class="activity-list">
            ${demands.length ? demands.map(item => `
              <article class="activity-row">
                <div class="activity-icon">${item.is_running ? "!" : "◷"}</div>
                <div class="activity-copy">
                  <strong>${escaped(item.label)} · ${number(item.multiplier, 2)}×</strong>
                  <small>Day ${number(item.start_day)} at ${String(item.start_hour).padStart(2, "0")}:00 · ${number(item.duration_hours)} hours</small>
                </div>
              </article>
            `).join("") : '<div class="empty-state compact">No great-demand records available.</div>'}
          </div>
        </article>

        <article class="glass-card">
          <div class="card-heading"><div><h2>Savegame settings</h2><p>Economy and simulation controls</p></div></div>
          <div class="stats-grid">
            ${miniStat("Economy difficulty", escaped(titleCase(data.career?.settings?.economicDifficulty || "Unknown")))}
            ${miniStat("Days per month", number(data.career?.settings?.plannedDaysPerPeriod))}
            ${miniStat("Time scale", `${number(data.career?.settings?.timeScale, 1)}×`)}
            ${miniStat("Autosave", `${number(data.career?.settings?.autoSaveInterval, 0)} min`)}
            ${miniStat("Supply objects", number(data.fleet_summary?.supply_count || 0))}
            ${miniStat("Product pallets", number(data.fleet_summary?.product_object_count || 0))}
          </div>
        </article>
      </section>

      <article class="glass-card" style="margin-top:18px">
        <div class="card-heading">
          <div><h2>Stored products</h2><p>Current quantities with seasonal price shape</p></div>
          <span class="state-pill good">${number(inventories.length)} stocked</span>
        </div>
        <div class="inventory-grid">
          ${inventories.length ? inventories.map(inventoryCard).join("") : '<div class="empty-state">No stored products were reported by the economy feed.</div>'}
        </div>
      </article>
    </section>
  `;

  const applyTransactionFilters = () => {
    const search = (document.getElementById("transaction-search")?.value || "").trim().toLowerCase();
    const category = document.getElementById("transaction-category")?.value || "";
    const confidence = document.getElementById("transaction-confidence")?.value || "";
    const filtered = transactions.filter(item => {
      const haystack = `${item.title || ""} ${item.detail || ""} ${economyCategory(item.event_type).label} ${JSON.stringify(item.meta || {})}`.toLowerCase();
      return (!search || haystack.includes(search))
        && (!category || item.event_type === category)
        && (!confidence || (item.confidence || "confirmed") === confidence);
    });
    const list = document.getElementById("transaction-list");
    const count = document.getElementById("transaction-count");
    if (list) list.innerHTML = filtered.length ? filtered.map(item => transactionRow(item, symbol)).join("") : '<div class="empty-state">No ledger entries match those filters.</div>';
    if (count) count.textContent = number(filtered.length);
  };

  document.getElementById("economy-days")?.addEventListener("change", event => {
    runtime.economyDays = Number(event.target.value) || 30;
    renderEconomy().catch(error => errorPage("Economy unavailable", error.message));
  });
  document.getElementById("transaction-search")?.addEventListener("input", applyTransactionFilters);
  document.getElementById("transaction-category")?.addEventListener("change", applyTransactionFilters);
  document.getElementById("transaction-confidence")?.addEventListener("change", applyTransactionFilters);

  document.querySelectorAll(".review-apply").forEach(button => {
    button.addEventListener("click", async () => {
      const row = button.closest(".review-item");
      const eventId = Number(row?.dataset.reviewId || 0);
      const category = row?.querySelector(".review-category")?.value || "";
      const label = row?.querySelector(".review-label")?.value || "";
      const rememberRule = Boolean(row?.querySelector(".review-remember")?.checked);
      if (!category) {
        showToast("Choose a category first");
        return;
      }
      button.disabled = true;
      button.textContent = "Saving…";
      try {
        await fetchJson("api/review/classify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ event_id: eventId, category, label, remember_rule: rememberRule }),
        });
        showToast(rememberRule ? "Transaction classified and rule saved" : "Transaction classified");
        await renderEconomy();
      } catch (error) {
        button.disabled = false;
        button.textContent = "Apply";
        showToast(`Review failed: ${error.message}`);
      }
    });
  });

  document.querySelectorAll(".rule-delete").forEach(button => {
    button.addEventListener("click", async () => {
      const row = button.closest(".rule-row");
      const ruleId = Number(row?.dataset.ruleId || 0);
      button.disabled = true;
      try {
        await fetchJson("api/review/rules/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rule_id: ruleId }),
        });
        showToast("Saved rule deleted");
        await renderEconomy();
      } catch (error) {
        button.disabled = false;
        showToast(`Could not delete rule: ${error.message}`);
      }
    });
  });

  requestAnimationFrame(() => drawMoneyChart(document.getElementById("money-chart"), daily, symbol));
}

function modCard(mod) {
  return `
    <article class="mod-card">
      <div class="mod-top">
        <div class="mod-name">
          <strong title="${escaped(mod.name)}">${escaped(mod.name)}</strong>
          <small>${escaped(mod.internal_name || "No internal name")}</small>
        </div>
        <span class="state-pill good">v${escaped(mod.version || "—")}</span>
      </div>
      <div class="mod-meta">
        <div class="detail-line"><span>Author</span><strong>${escaped(mod.author || "Unknown")}</strong></div>
        <div class="detail-line"><span>Hash</span><strong title="${escaped(mod.hash)}">${escaped((mod.hash || "—").slice(0, 10))}</strong></div>
      </div>
    </article>
  `;
}

async function renderMods() {
  const token = ++runtime.renderToken;
  loadingPage("Loading active mods");
  const data = await fetchJson("api/mods");
  if (token !== runtime.renderToken) return;
  app.innerHTML = `
    <section class="page">
      ${pageHeading("Server content", "Active Mods", "Search the exact mod name, internal package name, author or installed version currently exposed by the live server feed.")}
      <article class="glass-card">
        <div class="toolbar">
          <div class="filters">
            <input class="field-control" id="mod-search" type="search" placeholder="Search mods, authors or versions…" autocomplete="off">
          </div>
          <span class="state-pill good"><span id="mod-count">${number(data.count)}</span> active</span>
        </div>
        <div class="author-cloud" id="author-cloud">
          ${(data.authors || []).slice(0, 18).map(item => `<button class="author-chip" data-author="${escaped(item.author)}">${escaped(item.author)} <strong>${number(item.count)}</strong></button>`).join("")}
        </div>
        <div class="mod-grid" id="mod-grid" style="margin-top:18px">
          ${(data.mods || []).map(modCard).join("") || '<div class="empty-state">No active mods were returned.</div>'}
        </div>
      </article>
    </section>
  `;

  let timer;
  const searchInput = document.getElementById("mod-search");
  const refresh = value => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const filtered = await fetchJson(`api/mods?q=${encodeURIComponent(value)}`);
        const grid = document.getElementById("mod-grid");
        const count = document.getElementById("mod-count");
        if (grid) grid.innerHTML = (filtered.mods || []).map(modCard).join("") || '<div class="empty-state">No mods match that search.</div>';
        if (count) count.textContent = number(filtered.count);
      } catch (error) {
        showToast(`Mod search failed: ${error.message}`);
      }
    }, 160);
  };
  searchInput?.addEventListener("input", event => refresh(event.target.value));
  document.querySelectorAll("[data-author]").forEach(button => {
    button.addEventListener("click", () => {
      if (searchInput) searchInput.value = button.dataset.author || "";
      refresh(button.dataset.author || "");
    });
  });
}

function drawPlayChart(canvas, daily) {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  const rows = daily || [];
  context.clearRect(0, 0, width, height);
  if (!rows.length) {
    context.fillStyle = "rgba(190, 211, 196, .7)";
    context.font = "14px system-ui";
    context.textAlign = "center";
    context.fillText("Play history begins from the hub installation", width / 2, height / 2);
    return;
  }
  const padding = { top: 18, right: 16, bottom: 36, left: 42 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const max = Math.max(...rows.map(row => Number(row.seconds || 0)), 1);
  const step = chartWidth / Math.max(rows.length - 1, 1);

  context.strokeStyle = "rgba(169, 255, 190, .10)";
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + chartHeight * (i / 4);
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
  }

  const points = rows.map((row, index) => ({
    x: padding.left + step * index,
    y: padding.top + chartHeight - chartHeight * (Number(row.seconds || 0) / max),
  }));
  const gradient = context.createLinearGradient(0, padding.top, 0, padding.top + chartHeight);
  gradient.addColorStop(0, "rgba(101, 229, 131, .36)");
  gradient.addColorStop(1, "rgba(101, 229, 131, 0)");
  context.beginPath();
  context.moveTo(points[0].x, padding.top + chartHeight);
  points.forEach(point => context.lineTo(point.x, point.y));
  context.lineTo(points.at(-1).x, padding.top + chartHeight);
  context.closePath();
  context.fillStyle = gradient;
  context.fill();

  context.beginPath();
  points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
  context.strokeStyle = "rgba(101, 229, 131, .95)";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.stroke();

  context.fillStyle = "rgba(190, 211, 196, .72)";
  context.font = "10px system-ui";
  context.textAlign = "center";
  rows.forEach((row, index) => {
    if (rows.length <= 14 || index % Math.ceil(rows.length / 10) === 0) {
      context.fillText(String(row.day || "").slice(5), points[index].x, height - 13);
    }
  });
  context.textAlign = "right";
  context.fillText(duration(max, true), padding.left - 6, padding.top + 4);
  context.fillText("0m", padding.left - 6, padding.top + chartHeight + 4);
}

function sessionRow(session) {
  const active = session.left_at === null || session.left_at === undefined;
  return `
    <article class="session-row">
      <div class="session-main">
        <div class="session-top">
          <strong>${escaped(session.player)} ${session.is_admin ? '<span class="state-pill good">Admin</span>' : ""}</strong>
          <span class="state-pill ${active ? "good" : ""}">${active ? "Online" : "Completed"}</span>
        </div>
        <small>Joined ${escaped(exactDate(session.joined_at))}${active ? "" : ` · left ${escaped(exactDate(session.left_at))}`}</small>
      </div>
      <div class="amount ${active ? "positive" : "neutral"}">${duration(session.duration_seconds, true)}</div>
    </article>
  `;
}

async function renderHistory() {
  const token = ++runtime.renderToken;
  loadingPage("Loading player history");
  const data = await fetchJson("api/history?days=30");
  if (token !== runtime.renderToken) return;
  const history = data.history || {};
  const players = history.players || [];
  const sessions = history.recent_sessions || [];
  const totalSeconds = players.reduce((sum, player) => sum + Number(player.seconds || 0), 0);
  const totalSessions = players.reduce((sum, player) => sum + Number(player.sessions || 0), 0);
  const topPlayer = players[0];

  app.innerHTML = `
    <section class="page">
      ${pageHeading("Multiplayer records", "Play History", "Player sessions, total time, latest joins and the server activity timeline recorded by the hub.")}
      <section class="history-summary">
        ${miniStat("Recorded playtime", duration(totalSeconds, true))}
        ${miniStat("Player sessions", number(totalSessions))}
        ${miniStat("Players tracked", number(players.length))}
        ${miniStat("Top player", topPlayer ? escaped(topPlayer.player) : "—")}
      </section>

      <section class="section-grid">
        <article class="glass-card chart-card">
          <div class="card-heading">
            <div><h2>Daily server activity</h2><p>Combined recorded playtime by day</p></div>
            <span class="state-pill">30 days</span>
          </div>
          <div class="chart-wrap"><canvas id="play-chart"></canvas></div>
        </article>
        <article class="glass-card">
          <div class="card-heading">
            <div><h2>Player leaderboard</h2><p>Time recorded since installation</p></div>
          </div>
          <div class="activity-list">
            ${players.length ? players.map((player, index) => `
              <article class="activity-row">
                <div class="activity-icon">${index + 1}</div>
                <div class="activity-copy">
                  <strong>${escaped(player.player)} · ${duration(player.seconds, true)}</strong>
                  <small>${number(player.sessions)} sessions · last seen ${ageFromUnix(player.last_seen)}</small>
                </div>
              </article>
            `).join("") : '<div class="empty-state">No completed player sessions yet.</div>'}
          </div>
        </article>
      </section>

      <section class="section-grid" style="margin-top:18px">
        <article class="glass-card">
          <div class="card-heading">
            <div><h2>Recent sessions</h2><p>Join, leave and session duration records</p></div>
            <span class="state-pill">${number(sessions.length)} listed</span>
          </div>
          <div class="session-list">${sessions.length ? sessions.map(sessionRow).join("") : '<div class="empty-state">Waiting for the first player session.</div>'}</div>
        </article>
        <article class="glass-card">
          <div class="card-heading">
            <div><h2>Full activity log</h2><p>Server, player and economy events</p></div>
            <a class="state-pill good" href="${apiUrl("api/events.csv")}">CSV</a>
          </div>
          <div class="activity-list">${activityRows(data.events, 30)}</div>
        </article>
      </section>
    </section>
  `;
  requestAnimationFrame(() => drawPlayChart(document.getElementById("play-chart"), history.daily || []));
}

function sourceDiagnosticCard(name, source) {
  const healthy = Boolean(source?.last_success) && !source?.last_error;
  return `
    <article class="source-diagnostic ${healthy ? "healthy" : source?.last_error ? "failed" : "waiting"}">
      <div class="source-diagnostic-head">
        <strong>${escaped(titleCase(name))}</strong>
        <span class="state-pill ${healthy ? "good" : source?.last_error ? "warning" : ""}">${healthy ? "Healthy" : source?.last_error ? "Error" : "Waiting"}</span>
      </div>
      <div class="detail-list compact">
        <div class="detail-line"><span>Last success</span><strong>${source?.last_success ? ageFromIso(source.last_success) : "Never"}</strong></div>
        <div class="detail-line"><span>Latency</span><strong>${Number.isFinite(Number(source?.latency_ms)) ? `${number(source.latency_ms, 1)} ms` : "—"}</strong></div>
        <div class="detail-line"><span>Payload</span><strong>${number(source?.bytes || 0)} bytes</strong></div>
        <div class="detail-line"><span>Changed / unchanged</span><strong>${number(source?.changes || 0)} / ${number(source?.unchanged || 0)}</strong></div>
        <div class="detail-line"><span>Current interval</span><strong>${source?.current_interval_seconds ? duration(source.current_interval_seconds, true) : "—"}</strong></div>
      </div>
      ${source?.last_error ? `<small class="diagnostic-error">${escaped(source.last_error)}</small>` : ""}
    </article>
  `;
}

async function renderDiagnostics() {
  loadingPage("Loading collector diagnostics");
  const data = await fetchJson("api/diagnostics");
  const sources = data.sources || {};
  const database = data.database || {};
  app.innerHTML = `
    <section class="page diagnostics-page">
      ${pageHeading("Collector health", "Diagnostics", "A read-only view of feed health, adaptive polling and database housekeeping. No Home Assistant notifications or alarms are used.")}
      <section class="economy-summary">
        ${miniStat("Database size", `${number((database.size_bytes || 0) / 1024 / 1024, 2)} MB`)}
        ${miniStat("Transactions", number(database.events || 0))}
        ${miniStat("Needs review", number(database.unclassified || 0), Number(database.unclassified || 0) ? "contract-stat" : "positive-stat")}
        ${miniStat("Saved rules", number(database.classification_rules || 0))}
        ${miniStat("Live balance samples", number(database.balance_samples || 0))}
        ${miniStat("Daily summaries", number(database.daily_balance_summaries || 0))}
      </section>
      <article class="glass-card" style="margin-top:18px">
        <div class="card-heading"><div><h2>Adaptive polling</h2><p>Fast while people are playing; quieter while the server is empty</p></div><span class="state-pill ${data.adaptive_polling ? "good" : ""}">${data.adaptive_polling ? "Enabled" : "Fixed"}</span></div>
        <div class="stats-grid">
          ${miniStat("Stats interval", duration(data.stats_poll_seconds || 0, true))}
          ${miniStat("Current save interval", duration(data.current_save_poll_seconds || data.save_poll_seconds || 0, true))}
          ${miniStat("Empty-server save interval", duration(data.empty_server_save_poll_seconds || 0, true))}
          ${miniStat("Current map interval", duration(data.current_map_poll_seconds || data.map_poll_seconds || 0, true))}
          ${miniStat("Empty-server map interval", duration(data.empty_server_map_poll_seconds || 0, true))}
          ${miniStat("Balance detail retained", `${number(data.retention?.balance_sample_days || 0)} days`)}
        </div>
      </article>
      <article class="glass-card" style="margin-top:18px">
        <div class="card-heading"><div><h2>Data sources</h2><p>Requests are hashed so unchanged files are reused rather than parsed again</p></div></div>
        <div class="source-diagnostic-grid">
          ${Object.entries(sources).map(([name, source]) => sourceDiagnosticCard(name, source)).join("")}
        </div>
      </article>
      <article class="glass-card" style="margin-top:18px">
        <div class="card-heading"><div><h2>Database housekeeping</h2><p>Transaction and play history are kept; only old high-frequency balance samples are compressed</p></div></div>
        <div class="detail-list">
          <div class="detail-line"><span>Transaction retention</span><strong>${escaped(data.retention?.transactions || "Kept indefinitely")}</strong></div>
          <div class="detail-line"><span>Play-session retention</span><strong>${escaped(data.retention?.sessions || "Kept indefinitely")}</strong></div>
          <div class="detail-line"><span>Balance sample policy</span><strong>${number(data.retention?.balance_sample_days || 0)} days full detail, daily summaries after that</strong></div>
        </div>
      </article>
    </section>
  `;
}

function updateLiveTimers() {
  document.querySelectorAll("[data-session-start]").forEach(element => {
    const started = Number(element.dataset.sessionStart);
    if (started > 0) {
      element.textContent = duration(Date.now() / 1000 - started, true);
    }
  });
}

function updateTopbarClock() {
  updateLiveTimers();
  if (runtime.lastUpdate && connectionText.textContent.includes("Live")) {
    connectionText.textContent = `Live · ${ageFromSeconds((Date.now() - runtime.lastUpdate.getTime()) / 1000)}`;
  }
}

async function renderRoute(force = false) {
  runtime.route = currentRoute();
  updateNavigation();
  if (!["overview", "map"].includes(runtime.route)) preserveMapState();
  try {
    if (runtime.route === "overview") await renderOverview(force);
    if (runtime.route === "map") await renderMap(force);
    if (runtime.route === "vehicles") await renderVehicles();
    if (runtime.route === "economy") await renderEconomy();
    if (runtime.route === "mods") await renderMods();
    if (runtime.route === "history") await renderHistory();
    if (runtime.route === "diagnostics") await renderDiagnostics();
  } catch (error) {
    console.error(error);
    setConnection("offline", "Data unavailable");
    errorPage("Dashboard data unavailable", error.message);
  }
}

function connectStream() {
  if (runtime.eventSource) runtime.eventSource.close();
  const stream = new EventSource(apiUrl("api/stream"));
  runtime.eventSource = stream;
  stream.addEventListener("open", () => {
    runtime.streamConnected = true;
    setConnection("online", "Live connection");
  });
  stream.addEventListener("update", async event => {
    try {
      const message = JSON.parse(event.data);
      const version = Number(message.version);
      if (version === runtime.lastVersion) return;
      runtime.lastVersion = version;
      runtime.lastUpdate = new Date();
      setConnection("online", "Live · just now");
      if (runtime.route === "overview") {
        await renderOverview(false);
      } else if (runtime.route === "map") {
        await renderMap(false);
      } else if (["vehicles", "economy", "mods", "history", "diagnostics"].includes(runtime.route)) {
        // Detailed pages refresh without requiring the user to reload.
        await renderRoute(false);
      }
    } catch (error) {
      console.warn("Live refresh failed", error);
    }
  });
  stream.addEventListener("error", () => {
    runtime.streamConnected = false;
    setConnection("offline", "Reconnecting…");
  });
}

window.addEventListener("hashchange", () => renderRoute(false));
window.addEventListener("resize", () => {
  if (runtime.route === "economy") renderRoute(false);
  if (runtime.route === "history") renderRoute(false);
});

runtime.clockTimer = setInterval(updateTopbarClock, 1000);
runtime.refreshTimer = setInterval(() => {
  if (!runtime.streamConnected && runtime.route === "overview") renderOverview(false).catch(() => {});
}, 120000);

if (!window.location.hash) window.location.hash = "#/overview";
renderRoute(true);
connectStream();
