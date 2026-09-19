/*
 * Demo client for django-osm-locations.
 *
 * Talks to the API on the same origin, so there is no CORS to configure. The
 * index stores geometry as GeoJSON, which is exactly what L.geoJSON consumes --
 * MultiPolygon borders and MultiLineString streets both draw without any
 * coordinate juggling.
 */
const TYPES = ['countries', 'country-subareas', 'places', 'place-subareas', 'streets'];

const STYLE = {
  'countries':        { color: '#f0a05a', weight: 2, fillOpacity: 0.05 },
  'country-subareas': { color: '#c08cf0', weight: 2, fillOpacity: 0.07 },
  'places':           { color: '#5fb3f5', weight: 2, fillOpacity: 0.12 },
  'place-subareas':   { color: '#5fe0c0', weight: 2, fillOpacity: 0.12 },
  'streets':          { color: '#f5d75f', weight: 4, fillOpacity: 0 },
};

const el = (id) => document.getElementById(id);
const active = new Set();
let selected = null;
let layer = null;
let sequence = 0;

const map = L.map('map', { zoomControl: true }).setView([52.1, 19.4], 6);
// Plain OSM tiles: no API key, no sign-up. They are light, so the CSS inverts
// them to match the panel rather than pulling in a keyed dark basemap.
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors',
  maxZoom: 19,
}).addTo(map);

/* ---------------------------------------------------------------- helpers */

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

function setStatus(text) {
  el('status').textContent = text;
}

/* ------------------------------------------------------------ type chips */

function buildChips() {
  const host = el('types');
  TYPES.forEach((type) => {
    const chip = document.createElement('button');
    chip.className = 'chip';
    chip.textContent = type.replace('-', ' ');
    chip.setAttribute('aria-pressed', 'false');
    chip.addEventListener('click', () => {
      if (active.has(type)) { active.delete(type); } else { active.add(type); }
      chip.setAttribute('aria-pressed', active.has(type) ? 'true' : 'false');
      search();
    });
    host.appendChild(chip);
  });
}

/* ---------------------------------------------------------------- search */

function searchParams() {
  const params = new URLSearchParams();
  const q = el('q').value.trim();
  if (q) params.set('q', q);
  if (active.size) params.set('type', [...active].join(','));
  if (el('use-bbox').checked) {
    const b = map.getBounds();
    params.set('bbox', [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
      .map((v) => v.toFixed(5)).join(','));
  }
  params.set('page_size', '40');
  return params;
}

async function search() {
  const mine = ++sequence;              // ignore responses from stale keystrokes
  const params = searchParams();
  setStatus('searching…');
  try {
    const response = await fetch('/api/search/?' + params);
    const body = await response.json();
    if (mine !== sequence) return;
    if (!response.ok) { setStatus(body.detail || 'search failed'); return; }
    render(body);
  } catch (error) {
    if (mine === sequence) setStatus('could not reach the API: ' + error.message);
  }
}

function render(body) {
  const list = el('results');
  list.innerHTML = '';
  setStatus(`${formatNumber(body.total)} match${body.total === 1 ? '' : 'es'}`
            + (body.total > body.results.length ? ` — showing ${body.results.length}` : ''));

  body.results.forEach((hit) => {
    const item = document.createElement('li');
    item.setAttribute('aria-selected', 'false');
    item.innerHTML = `<span class="name"></span>
                      <span class="badge"></span>
                      <span class="score"></span>`;
    item.querySelector('.name').textContent = hit.name;
    item.querySelector('.badge').textContent = hit.type.replace('-', ' ');
    item.querySelector('.score').textContent = hit.score ? hit.score.toFixed(1) : '';
    item.addEventListener('click', () => select(hit, item));
    list.appendChild(item);
  });
}

/* ----------------------------------------------------------- selection */

async function select(hit, item) {
  document.querySelectorAll('#results li').forEach((n) => n.setAttribute('aria-selected', 'false'));
  item.setAttribute('aria-selected', 'true');
  selected = hit;

  setStatus('loading geometry…');
  const response = await fetch(`/api/${hit.type}/${encodeURIComponent(hit.slug)}/`);
  if (!response.ok) { setStatus('could not load that one'); return; }
  const full = await response.json();

  draw(full);
  describe(full);
  history.replaceState(null, '', `#${hit.type}/${encodeURIComponent(hit.slug)}`);
  setStatus(`${full.name} selected`);
}

function draw(location) {
  if (layer) { map.removeLayer(layer); layer = null; }
  if (!location.geometry) { setStatus('this one has no geometry'); return; }

  layer = L.geoJSON(location.geometry, { style: STYLE[location.type] }).addTo(map);
  layer.bindPopup(`<strong>${location.name}</strong><br>${location.type.replace('-', ' ')}`);
  try {
    map.fitBounds(layer.getBounds(), { padding: [30, 30], maxZoom: 15 });
  } catch (error) {
    if (location.center) map.setView([location.center.lat, location.center.lon], 13);
  }
}

/** Ancestry without the repeats the denormalised arrays can contain. */
function crumbNames(location) {
  const seen = new Set();
  return (location.ancestry || [])
    .map((a) => a.name)
    .filter((name) => name && !seen.has(name) && seen.add(name));
}

function describe(location) {
  const box = el('detail');
  box.hidden = false;

  const rows = [];
  // anything this small is the pre-fix value that predates the km² correction
  if (location.area > 0.001) rows.push(['area', `${formatNumber(location.area, 2)} km²`]);
  else if (location.area) rows.push(['area', 'stale — needs a recalculate']);
  if (location.length) rows.push(['length', `${formatNumber(location.length, 0)} m`]);
  if (location.population) rows.push(['population', formatNumber(location.population)]);
  if (location.center) {
    rows.push(['centre', `${location.center.lat.toFixed(4)}, ${location.center.lon.toFixed(4)}`]);
  }
  if (location.bbox && location.bbox.length === 4) {
    rows.push(['bbox', location.bbox.map((v) => v.toFixed(3)).join(', ')]);
  }
  if (location.geometry) rows.push(['geometry', location.geometry.type]);

  const osm = location.osm || {};
  const osmId = osm.relation_id || osm.way_id || osm.node_id;
  if (osmId) {
    const kind = osm.relation_id ? 'relation' : (osm.way_id ? 'way' : 'node');
    rows.push(['osm', `<a href="https://www.openstreetmap.org/${kind}/${osmId}"
                          target="_blank" rel="noreferrer">${kind} ${osmId}</a>`]);
  }

  box.innerHTML = `
    <h2></h2>
    <p class="sub"></p>
    <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${crumbNames(location).length
      ? `<div class="crumbs">${crumbNames(location).map(() => '<span></span>').join('')}</div>`
      : ''}
  `;
  box.querySelector('h2').textContent = location.name;
  box.querySelector('.sub').textContent = `${location.type.replace('-', ' ')} · ${location.slug}`;
  const crumbs = box.querySelectorAll('.crumbs span');
  crumbNames(location).forEach((name, i) => { if (crumbs[i]) crumbs[i].textContent = name; });
}

/* ------------------------------------------------------------- wiring */

let debounce = null;
el('q').addEventListener('input', () => {
  clearTimeout(debounce);
  debounce = setTimeout(search, 180);
});
el('use-bbox').addEventListener('change', search);
map.on('moveend', () => { if (el('use-bbox').checked) search(); });

async function announceDataset() {
  try {
    const response = await fetch('/api/countries/?page_size=10');
    const body = await response.json();
    // a country row can exist from the fixture with nothing imported for it,
    // so only announce the ones that actually have an extent
    const withData = body.results.filter((c) => c.bbox && c.bbox.length === 4);
    el('dataset').textContent = withData.length
      ? `indexed: ${withData.map((c) => c.name).join(', ')}`
      : 'nothing imported yet';
    // do not steal the view from a deep link that is already loading
    if (withData.length && !window.location.hash) {
      const b = withData[0].bbox;
      map.fitBounds([[b[1], b[0]], [b[3], b[2]]]);
    }
  } catch (error) {
    el('dataset').textContent = 'API unreachable';
  }
}

/** #<type>/<slug> selects a location directly, so a view can be linked to. */
async function openFromHash() {
  const match = /^#([a-z-]+)\/(.+)$/.exec(window.location.hash);
  if (!match) return false;
  const [, type, slug] = match;
  const response = await fetch(`/api/${type}/${encodeURIComponent(slug)}/`);
  if (!response.ok) { setStatus(`no ${type} called ${decodeURIComponent(slug)}`); return true; }
  const full = await response.json();
  draw(full);
  describe(full);
  setStatus(`${full.name} — linked directly`);
  return true;
}

window.addEventListener('hashchange', openFromHash);

buildChips();
announceDataset();
openFromHash().then((handled) => { if (!handled) search(); });
