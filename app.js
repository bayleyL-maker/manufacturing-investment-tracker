// =============================================================================
// US Manufacturing Investment Tracker - frontend
// =============================================================================
// Loads investment records from a JSON file and renders them as pins on a
// Leaflet map. Supports filtering by industry, type, dollar bucket, and date.
// Clicking a pin opens the right-side sidebar with full details.
// =============================================================================

// Set to true to load demo data instead of real data. Flip to false once the
// ingestion pipeline is producing real records.
const USE_DEMO_DATA = false;

const DATA_URL = USE_DEMO_DATA
  ? "data/demo-investments.json"
  : "data/investments.json";

// -----------------------------------------------------------------------------
// Display labels for the enum values used internally
// -----------------------------------------------------------------------------
const INDUSTRY_LABELS = {
  agriculture_machinery: "Agriculture Machinery",
  heavy_equipment: "Heavy Equipment",
  food_beverage_machinery: "Food & Beverage Machinery",
  food_and_beverage: "Food & Beverage Products",
  automotive: "Automotive",
  ev_battery: "EV / Battery",
  non_auto_transportation: "Non-Auto Transportation",
  pharma_biotech: "Pharma & Biotech",
  metals_and_primary_materials: "Metals & Primary Materials",
  fabricated_metal_products: "Fabricated Metal Products",
  electrical_equipment_and_grid: "Electrical Equipment & Grid",
  consumer_packaged_goods: "Consumer Packaged Goods"
};

const TYPE_LABELS = {
  new_facility: "New facility",
  expansion: "Expansion",
  equipment_upgrade: "Equipment upgrade",
  onshoring: "Onshoring",
  automation: "Automation",
  retooling: "Retooling",
  reopening: "Reopening"
};

// -----------------------------------------------------------------------------
// Map setup
// -----------------------------------------------------------------------------
const map = L.map("map", {
  center: [39.5, -98.35], // approx. geographic center of contiguous US
  zoom: 4,
  minZoom: 3,
  maxZoom: 10
});

// CartoDB Dark Matter - dark, minimal-label, no-roads aesthetic.
// Other free options to swap in:
//   Positron (light minimal): https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png
//   Voyager (color minimal):  https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  subdomains: "abcd",
  maxZoom: 19
}).addTo(map);

// Marker cluster group - collapses dense pins when zoomed out
const clusterGroup = L.markerClusterGroup({
  showCoverageOnHover: false,
  spiderfyOnMaxZoom: true,
  maxClusterRadius: 50
});
map.addLayer(clusterGroup);

// -----------------------------------------------------------------------------
// State
// -----------------------------------------------------------------------------
let allInvestments = []; // full set loaded from JSON
let currentFilters = {
  industry: "",
  type: "",
  amount: "",
  date: ""
};

// -----------------------------------------------------------------------------
// Data loading
// -----------------------------------------------------------------------------
async function loadData() {
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // Only approved records are shown
    allInvestments = data.filter(r => r.review && r.review.status === "approved");
    render();
  } catch (err) {
    console.error("Failed to load investment data:", err);
    document.getElementById("record-count").textContent = "(failed to load data)";
  }
}

// -----------------------------------------------------------------------------
// Filtering
// -----------------------------------------------------------------------------
function passesFilters(inv) {
  const f = currentFilters;

  if (f.industry && inv.industry !== f.industry) return false;
  if (f.type && inv.investment_type !== f.type) return false;

  if (f.amount) {
    if (f.amount === "undisclosed") {
      if (inv.amount_disclosed) return false;
    } else {
      if (!inv.amount_disclosed || inv.amount_usd == null) return false;
      const millions = inv.amount_usd / 1_000_000;
      const [lo, hi] = f.amount.split("-").map(s => s === "" ? Infinity : Number(s));
      if (millions < lo) return false;
      if (hi !== Infinity && millions >= hi) return false;
    }
  }

  if (f.date) {
    const days = Number(f.date);
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    const announced = new Date(inv.dates.announced);
    if (announced < cutoff) return false;
  }

  return true;
}

// -----------------------------------------------------------------------------
// Rendering
// -----------------------------------------------------------------------------
function render() {
  clusterGroup.clearLayers();

  const visible = allInvestments.filter(passesFilters);

  visible.forEach(inv => {
    const { lat, lon } = getCoords(inv);
    if (lat == null || lon == null) return;

    const precise = inv.location.precision === "city";
    const icon = L.divIcon({
      className: "",
      html: `<div class="pin ${precise ? "pin-precise" : "pin-approx"}"></div>`,
      iconSize: [18, 18],
      iconAnchor: [9, 9]
    });

    const marker = L.marker([lat, lon], { icon });
    const tooltipText = buildTooltip(inv);
    marker.bindTooltip(tooltipText, {
      direction: "top",
      offset: [0, -10],
      className: "pin-tooltip",
      opacity: 1
    });
    marker.on("click", () => openSidebar(inv));
    clusterGroup.addLayer(marker);
  });

  // Record count + empty state
  const total = allInvestments.length;
  const shown = visible.length;
  const countEl = document.getElementById("record-count");
  if (total === 0) {
    countEl.textContent = "";
  } else if (shown === total) {
    countEl.textContent = `${total} investment${total === 1 ? "" : "s"}`;
  } else {
    countEl.textContent = `${shown} of ${total} shown`;
  }

  document.getElementById("empty-state").hidden = total !== 0;
}

function getCoords(inv) {
  if (inv.location.lat != null && inv.location.lon != null) {
    return { lat: inv.location.lat, lon: inv.location.lon };
  }
  // Fallback: state center
  const sc = STATE_CENTERS[inv.location.state];
  if (sc) return { lat: sc.lat, lon: sc.lon };
  return { lat: null, lon: null };
}

// -----------------------------------------------------------------------------
// Sidebar
// -----------------------------------------------------------------------------
function openSidebar(inv) {
  const content = document.getElementById("sidebar-content");
  content.innerHTML = renderSidebar(inv);
  const sb = document.getElementById("sidebar");
  sb.classList.remove("sidebar-hidden");
  sb.setAttribute("aria-hidden", "false");
}

function closeSidebar() {
  const sb = document.getElementById("sidebar");
  sb.classList.add("sidebar-hidden");
  sb.setAttribute("aria-hidden", "true");
}

function renderSidebar(inv) {
  const locText = inv.location.precision === "city"
    ? `${escapeHtml(inv.location.city)}, ${escapeHtml(inv.location.state)}`
    : `${stateName(inv.location.state)} (state-level)`;

  const amountText = inv.amount_disclosed && inv.amount_usd != null
    ? formatAmount(inv.amount_usd)
    : "Undisclosed";

  // Suppliers section disabled. The data may still exist on records but is no
  // longer rendered. To re-enable: restore the suppliersHtml block and the
  // matching <details> block below.
  const suppliersHtml = "";

  const sourcesHtml = (inv.sources || []).map(s => `
    <div><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.publication || s.url)}</a></div>
  `).join("");

  return `
    <h2 class="sb-company">${escapeHtml(inv.company.name)}</h2>
    <div class="sb-hq">${inv.company.hq_country ? "HQ: " + escapeHtml(inv.company.hq_country) : ""}</div>

    <div class="sb-field">
      <div class="sb-field-label">Investment</div>
      <div class="sb-amount">${amountText}</div>
    </div>

    <div class="sb-field">
      <div class="sb-field-label">Location</div>
      <div class="sb-field-value">${locText}</div>
    </div>

    <div class="sb-field">
      <div class="sb-field-label">Industry</div>
      <div class="sb-field-value">${escapeHtml(INDUSTRY_LABELS[inv.industry] || inv.industry)}</div>
    </div>

    <div class="sb-field">
      <div class="sb-field-label">Type</div>
      <div class="sb-field-value">${escapeHtml(TYPE_LABELS[inv.investment_type] || inv.investment_type)}</div>
    </div>

    <div class="sb-field">
      <div class="sb-field-label">Announced</div>
      <div class="sb-field-value">${escapeHtml(inv.dates.announced)}</div>
    </div>

    ${inv.dates.expected_start || inv.dates.expected_completion ? `
      <div class="sb-field">
        <div class="sb-field-label">Expected</div>
        <div class="sb-field-value">
          ${inv.dates.expected_start ? "Start: " + escapeHtml(inv.dates.expected_start) : ""}
          ${inv.dates.expected_start && inv.dates.expected_completion ? " &middot; " : ""}
          ${inv.dates.expected_completion ? "Complete: " + escapeHtml(inv.dates.expected_completion) : ""}
        </div>
      </div>
    ` : ""}

    ${inv.description ? `<p class="sb-description">${escapeHtml(inv.description)}</p>` : ""}

    ${suppliersHtml ? `
      <details class="sb-suppliers">
        <summary>Likely suppliers</summary>
        ${suppliersHtml}
      </details>
    ` : ""}

    ${sourcesHtml ? `
      <div class="sb-sources">
        <div class="sb-field-label" style="margin-bottom:0.35rem;">Sources</div>
        ${sourcesHtml}
      </div>
    ` : ""}
  `;
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------
function buildTooltip(inv) {
  const company = escapeHtml(inv.company.name);
  const amount = inv.amount_disclosed && inv.amount_usd != null
    ? formatAmount(inv.amount_usd)
    : "Undisclosed";
  const type = TYPE_LABELS[inv.investment_type] || inv.investment_type;
  return `
    <div class="tt-company">${company}</div>
    <div class="tt-meta">${escapeHtml(type)} &middot; ${amount}</div>
  `;
}

function formatAmount(usd) {
  if (usd >= 1_000_000_000) return `$${(usd / 1_000_000_000).toFixed(usd % 1_000_000_000 === 0 ? 0 : 1)}B`;
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(0)}M`;
  return `$${usd.toLocaleString()}`;
}

function humanize(snake) {
  return snake.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function stateName(code) {
  return (STATE_CENTERS[code] && STATE_CENTERS[code].name) || code;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// -----------------------------------------------------------------------------
// Event wiring
// -----------------------------------------------------------------------------
function bindFilters() {
  const map = {
    "filter-industry": "industry",
    "filter-type": "type",
    "filter-amount": "amount",
    "filter-date": "date"
  };
  Object.entries(map).forEach(([elId, key]) => {
    document.getElementById(elId).addEventListener("change", e => {
      currentFilters[key] = e.target.value;
      render();
    });
  });

  document.getElementById("clear-filters").addEventListener("click", () => {
    currentFilters = { industry: "", type: "", amount: "", date: "" };
    Object.keys(map).forEach(id => { document.getElementById(id).value = ""; });
    render();
  });
}

document.getElementById("sidebar-close").addEventListener("click", closeSidebar);

bindFilters();
loadData();
