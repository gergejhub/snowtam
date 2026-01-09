/* WIZZ SNOWTAM Watch (Notamify-backed)
   Static GitHub Pages frontend that loads:
   - data/airports.json
   - data/snowtam_status.json
   and renders a Leaflet map with clickable markers + list.
*/

const DATA_AIRPORTS = "data/airports.json";
const DATA_STATUS   = "data/snowtam_status.json";

const COLORS = {
  gray:   "#9aa0a6",
  green:  "#2ecc71",
  yellow: "#f1c40f",
  orange: "#f39c12",
  red:    "#e74c3c"
};

const map = L.map("map", {
  preferCanvas: false,
  zoomControl: true,
  attributionControl: true
}).setView([47.0, 14.0], 5);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

const markersByIcao = new Map();
let airports = [];
let statusData = null;

function escHtml(s){
  return (s ?? "").toString()
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

function badgeClass(sev){
  return ["gray","green","yellow","orange","red"].includes(sev) ? sev : "gray";
}

function sevLabel(sev){
  switch(sev){
    case "green": return "OK";
    case "yellow": return "MILD";
    case "orange": return "MODERATE";
    case "red": return "SEVERE";
    default: return "UNKNOWN";
  }
}

function markerStyleFor(sev){
  const fill = COLORS[sev] ?? COLORS.gray;
  return {
    radius: 6,
    weight: 1,
    color: "rgba(0,0,0,.65)",
    fillColor: fill,
    fillOpacity: 0.92
  };
}

function getAirportStatus(icao){
  if(!statusData || !statusData.airports || !statusData.airports[icao]){
    return { severity: "gray", has_snowtam: false, changed: false, error: null, items: [] };
  }
  return statusData.airports[icao];
}

function buildPopupHtml(icao){
  const st = getAirportStatus(icao);
  const sev = st.severity || "gray";
  const changed = !!st.changed;
  const lastChange = st.last_change_utc || null;
  const generated = statusData?.generated_at_utc || null;

  const header = `<div class="popup-title">${escHtml(icao)} — ${sevLabel(sev)}${changed ? " (CHANGED)" : ""}</div>`;
  const meta = `<div class="popup-meta">Data: ${escHtml(generated || "—")}${lastChange ? " • Last change: " + escHtml(lastChange) : ""}</div>`;

  if(st.error){
    return header + meta + `<div class="popup-block"><strong>Error</strong><pre>${escHtml(st.error)}</pre></div>`;
  }

  if(!st.has_snowtam || !st.items || st.items.length === 0){
    return header + meta + `<div class="popup-block"><strong>No SNOWTAM-like NOTAMs detected</strong></div>`;
  }

  let blocks = "";
  st.items.slice(0, 6).forEach((it, idx) => {
    const raw = it.raw || "";
    const excerpt = it.excerpt || "";
    const description = it.description || "";
    blocks += `
      <div class="popup-block">
        <strong>Item ${idx+1}</strong>
        ${excerpt ? `<div class="popup-meta">${escHtml(excerpt)}</div>` : ""}
        <pre>${escHtml(raw)}</pre>
        ${description ? `<div class="popup-meta"><strong>Decode</strong>: ${escHtml(description)}</div>` : ""}
      </div>`;
  });

  if(st.items.length > 6){
    blocks += `<div class="popup-meta">Showing 6 of ${st.items.length} items</div>`;
  }

  return header + meta + blocks;
}

function updateMarker(icao){
  const marker = markersByIcao.get(icao);
  if(!marker) return;

  const st = getAirportStatus(icao);
  const sev = st.severity || (st.loaded ? "green" : "gray");

  marker.setStyle(markerStyleFor(sev));

  const el = marker.getElement();
  if(el){
    if(st.changed){
      el.classList.add("blinking");
    } else {
      el.classList.remove("blinking");
    }
  }

  marker.unbindPopup();
  marker.bindPopup(buildPopupHtml(icao), { maxWidth: 460, autoPan: true, closeButton: true });
}

function rebuildList(filter=""){
  const list = document.getElementById("airport-list");
  list.innerHTML = "";

  const q = filter.trim().toUpperCase();
  const filtered = airports.filter(a => !q || a.icao.includes(q));

  filtered.forEach(a => {
    const st = getAirportStatus(a.icao);
    const sev = st.severity || (st.loaded ? "green" : "gray");

    const row = document.createElement("div");
    row.className = "airport-item";
    row.innerHTML = `
      <div><strong>${escHtml(a.icao)}</strong>${a.name ? `<div class="popup-meta">${escHtml(a.name)}</div>` : ""}</div>
      <div class="badge ${badgeClass(sev)}">${sevLabel(sev)}</div>
    `;

    row.addEventListener("click", () => {
      const marker = markersByIcao.get(a.icao);
      if(marker){
        map.setView(marker.getLatLng(), Math.max(map.getZoom(), 8), { animate: true });
        marker.openPopup();
      }
    });

    list.appendChild(row);
  });
}

async function fetchJson(url){
  const bust = url.includes("?") ? "&" : "?";
  const res = await fetch(url + bust + "t=" + Date.now(), { cache: "no-store" });
  if(!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.json();
}

async function loadAirports(){
  const data = await fetchJson(DATA_AIRPORTS);
  airports = (data.airports || []).filter(a => a && a.icao && typeof a.lat === "number" && typeof a.lon === "number");
  return data;
}

function initMarkers(){
  markersByIcao.clear();

  airports.forEach(a => {
    const marker = L.circleMarker([a.lat, a.lon], markerStyleFor("gray"))
      .addTo(map)
      .on("click", () => {
        updateMarker(a.icao);
      });

    marker.bindPopup(buildPopupHtml(a.icao), { maxWidth: 460, autoPan: true });
    markersByIcao.set(a.icao, marker);
  });

  rebuildList(document.getElementById("search").value || "");
}

async function refreshStatus(){
  try{
    statusData = await fetchJson(DATA_STATUS);

    const lu = document.getElementById("last-updated");
    lu.textContent = "Last update: " + (statusData.generated_at_utc || "—");

    for(const a of airports){
      updateMarker(a.icao);
    }
    rebuildList(document.getElementById("search").value || "");
  } catch(err){
    console.error(err);
    const lu = document.getElementById("last-updated");
    lu.textContent = "Last update: error loading status JSON";
  }
}

(async function main(){
  const btn = document.getElementById("refresh-btn");
  btn.addEventListener("click", () => refreshStatus());

  const search = document.getElementById("search");
  search.addEventListener("input", () => rebuildList(search.value || ""));

  try{
    await loadAirports();
    initMarkers();
  } catch(err){
    console.error(err);
    const lu = document.getElementById("last-updated");
    lu.textContent = "Last update: airports.json missing — run GitHub Action once";
  }

  await refreshStatus();
  setInterval(refreshStatus, 60 * 1000);
})();
