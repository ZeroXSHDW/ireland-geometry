#!/usr/bin/env python3
"""Build a standalone, data-driven Leaflet research dashboard."""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path

try:
    from geometry import geometry_from_geojson, iter_polygons
    from runtime import atomic_write_text, project_path
except ImportError:
    from scripts.geometry import geometry_from_geojson, iter_polygons
    from scripts.runtime import atomic_write_text, project_path


TOP_N_MARKERS = 800
TOP_N_POLYGONS = 100
PAGE_SIZE = 50


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def integer(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def json_safe(value):
    """Prevent data values from terminating the inline script tag."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def polygon_rings(feature: dict) -> list[list[list[float]]]:
    geom = geometry_from_geojson(feature.get("geometry"))
    rings = []
    for polygon in iter_polygons(geom):
        rings.append([[round(y, 5), round(x, 5)] for x, y in polygon.exterior.coords])
    return rings


def normalize_row(row: dict, niah_by_id: dict[str, dict]) -> dict:
    niah = niah_by_id.get(row["osm_id"], {})
    flags = [flag for flag in row.get("flags", "").split(",") if flag]
    return {
        "osm_id": row["osm_id"],
        "name": row.get("name", ""),
        "group": row.get("group", "other"),
        "subtype": row.get("subtype", ""),
        "lat": number(row.get("lat")),
        "lon": number(row.get("lon")),
        "score": number(row.get("score")),
        "area_m2": number(row.get("area_m2")),
        "perimeter_m": number(row.get("perimeter_m")),
        "length_m": number(row.get("length_m")),
        "width_m": number(row.get("width_m")),
        "aspect_ratio": number(row.get("aspect_ratio")),
        "n_vertices": integer(row.get("n_vertices")),
        "convexity": number(row.get("convexity")),
        "circularity": number(row.get("circularity")),
        "has_golden_angle": integer(row.get("has_golden_angle")),
        "has_golden_ratio": int(number(row.get("golden_ratio_err_pct"), 999) <= 3),
        "golden_ratio_err_pct": number(row.get("golden_ratio_err_pct"), 999),
        "fib_ratio_err_pct": number(row.get("fib_ratio_err_pct"), 999),
        "valid": integer(row.get("valid"), 1),
        "repaired": integer(row.get("repaired")),
        "multipart": integer(row.get("multipart")),
        "hole_count": integer(row.get("hole_count")),
        "geometry_warning": row.get("geometry_warning", ""),
        "flags": flags,
        "niah": {
            "reg_no": niah.get("reg_no", ""),
            "name": niah.get("niah_name", ""),
            "county": niah.get("county", ""),
            "source_region": niah.get("source_region", ""),
            "rating": niah.get("rating", ""),
            "type": niah.get("niah_type", ""),
            "century": niah.get("century", ""),
            "century50": niah.get("century50", ""),
            "date_mid": number(niah.get("date_mid"), 0),
            "match_mode": niah.get("match_mode", ""),
            "dist_m": number(niah.get("dist_m"), 0),
        },
        "osm_url": f"https://www.openstreetmap.org/{html.escape(row['osm_id'])}",
    }


def load_manifest(out: Path) -> dict:
    path = out / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_report(out: Path) -> str:
    rows = read_csv(out / "analysis_results.csv")
    if not rows:
        raise SystemExit(f"Missing {out / 'analysis_results.csv'}. Run analyze.py first.")
    niah_rows = read_csv(out / "niah_join.csv")
    niah_by_id = {row["osm_id"]: row for row in niah_rows}
    targets = [normalize_row(row, niah_by_id) for row in rows if row.get("group") != "control"]
    controls = [row for row in rows if row.get("group") == "control"]
    targets.sort(key=lambda row: (-row["score"], row["osm_id"]))

    gj_path = out / "ireland_buildings.geojson"
    geojson = (
        json.loads(gj_path.read_text(encoding="utf-8"))
        if gj_path.exists()
        else {"type": "FeatureCollection", "features": []}
    )
    features_by_id = {
        feature.get("properties", {}).get("osm_id"): feature
        for feature in geojson.get("features", [])
    }
    outlines = []
    for row in targets:
        feature = features_by_id.get(row["osm_id"])
        if not feature:
            continue
        rings = polygon_rings(feature)
        if rings:
            outlines.append(
                {
                    "rings": rings,
                    "osm_id": row["osm_id"],
                    "score": row["score"],
                    "name": row["name"],
                    "group": row["group"],
                    "flags": row["flags"],
                }
            )
        if len(outlines) >= TOP_N_POLYGONS:
            break

    modes = Counter(row.get("match_mode", "") for row in niah_rows)
    manifest = load_manifest(out)
    summary = {
        "targets": len(targets),
        "controls": len(controls),
        "golden_angle": sum(row["has_golden_angle"] for row in targets),
        "golden_ratio": sum(row["has_golden_ratio"] for row in targets),
        "niah_matches": len(niah_rows),
        "niah_contained": modes.get("contained", 0),
        "niah_near": modes.get("near", 0),
        "groups": dict(Counter(row["group"] for row in targets)),
        "generated_at": manifest.get("generated_at", ""),
    }
    data = {
        "targets": targets,
        "outlines": outlines,
        "summary": summary,
        "significance": read_csv(out / "significance.csv"),
        "niah_significance": read_csv(out / "niah_significance.csv"),
        "decades": read_csv(out / "niah_decades.csv"),
        "point_pattern": read_csv(out / "point_pattern.csv"),
        "architects": read_csv(out / "architects.csv"),
        "architects_binary": read_csv(out / "architects_binary.csv"),
        "manifest": manifest,
        "geojson": geojson,
    }
    return TEMPLATE.replace("__MARKER_LIMIT__", str(TOP_N_MARKERS)).replace(
        "__DATA__", json_safe(data)
    )


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Ireland Geometric Pattern Scan</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<style>
:root { color-scheme: light; --ink:#17202a; --muted:#667085; --line:#e5e7eb;
        --blue:#2563eb; --red:#c2413b; --green:#18805c; --panel:rgba(255,255,255,.97); }
* { box-sizing:border-box; }
html,body { margin:0; height:100%; color:var(--ink); font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
#map { position:fixed; inset:0; background:#dfe8ee; }
#panel { position:fixed; z-index:1000; top:10px; right:10px; bottom:10px; width:min(560px,calc(100vw - 20px));
         display:flex; flex-direction:column; overflow:hidden; border:1px solid #d9dee5; border-radius:14px;
         background:var(--panel); box-shadow:0 8px 32px rgba(15,23,42,.22); }
header { padding:15px 17px 10px; border-bottom:1px solid var(--line); }
h1 { margin:0; font-size:18px; letter-spacing:-.02em; }
.subtitle { margin-top:4px; color:var(--muted); font-size:12px; }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:7px; padding:10px 12px; border-bottom:1px solid var(--line); }
.kpi { min-width:0; padding:8px 9px; border:1px solid var(--line); border-radius:9px; background:#fff; }
.kpi b { display:block; font-size:18px; line-height:1.1; }
.kpi span { color:var(--muted); font-size:10px; }
.filters { display:grid; grid-template-columns:1.6fr 1fr 1fr; gap:7px; padding:10px 12px 8px; border-bottom:1px solid var(--line); }
input,select,button { min-height:31px; border:1px solid #cfd5dd; border-radius:7px; background:#fff; color:var(--ink); padding:5px 8px; font:inherit; }
input[type=range] { padding:0; accent-color:var(--blue); }
button { cursor:pointer; font-weight:600; }
button:hover { border-color:var(--blue); color:var(--blue); }
.filter-wide { grid-column:1 / -1; display:flex; align-items:center; gap:8px; color:var(--muted); font-size:11px; }
.filter-wide input { flex:1; }
.checks { display:flex; flex-wrap:wrap; gap:8px; grid-column:1 / -1; color:var(--muted); font-size:11px; }
.checks label { display:flex; align-items:center; gap:3px; }
.checks input { min-height:auto; }
.toolbar { display:flex; justify-content:space-between; align-items:center; gap:6px; padding:8px 12px; border-bottom:1px solid var(--line); }
.toolbar small { color:var(--muted); }
.toolbar .actions { display:flex; gap:5px; }
.section { padding:10px 12px; border-bottom:1px solid var(--line); }
.section h2 { margin:0 0 7px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:#465467; }
.bars { display:grid; gap:5px; }
.bar-row { display:grid; grid-template-columns:100px 1fr 58px; align-items:center; gap:6px; font-size:11px; }
.bar-track { height:10px; border-radius:20px; background:#eef1f5; overflow:hidden; }
.bar-fill { height:100%; border-radius:20px; background:var(--blue); }
.bar-fill.control { background:#98a2b3; }
.table-wrap { flex:1; overflow:auto; }
table { width:100%; border-collapse:collapse; font-size:11px; }
th { position:sticky; top:0; z-index:2; padding:7px 6px; background:#f7f8fa; color:#526071; text-align:left; cursor:pointer; }
td { padding:6px; border-top:1px solid #eef0f3; vertical-align:top; }
tr:hover td { background:#f8fbff; }
tr[data-id] { cursor:pointer; }
.score { color:var(--red); font-weight:700; }
.flag { display:inline-block; margin:1px 2px 1px 0; padding:1px 4px; border-radius:4px; background:#eef4ff; color:#2456a6; font-size:10px; }
.verdict-SIGNAL { color:#a5322e; font-weight:700; }
.verdict-suggestive { color:#a05a00; font-weight:700; }
.pagination { display:flex; justify-content:center; gap:7px; padding:8px; border-top:1px solid var(--line); }
.pagination button { min-width:74px; }
.empty { padding:18px; color:var(--muted); text-align:center; }
.footnote { color:var(--muted); font-size:11px; }
@media (max-width:720px) {
  #panel { top:auto; right:0; bottom:0; left:0; width:100%; max-height:72vh; border-radius:14px 14px 0 0; }
  .kpis { grid-template-columns:repeat(4,1fr); }
  .kpi b { font-size:15px; }
  .filters { grid-template-columns:1fr 1fr; }
  .filters input[type=text] { grid-column:1 / -1; }
}
</style>
</head>
<body>
<div id="map" aria-label="Map of analysed Irish buildings"></div>
<div id="panel">
  <header>
    <h1>Ireland Geometric Pattern Scan</h1>
    <div class="subtitle">Interactive, significance-tested footprint survey. Scores are search heuristics, not proof of design intent.</div>
  </header>
  <div class="kpis" aria-live="polite">
    <div class="kpi"><b id="kTargets">—</b><span>visible targets</span></div>
    <div class="kpi"><b id="kControls">—</b><span>controls</span></div>
    <div class="kpi"><b id="kGolden">—</b><span>golden-angle</span></div>
    <div class="kpi"><b id="kNiah">—</b><span>NIAH matched</span></div>
  </div>
  <div class="filters">
    <input id="query" type="text" placeholder="Search name, OSM id, flags, county…" aria-label="Search"/>
    <select id="group"><option value="">All groups</option></select>
    <select id="century"><option value="">All centuries</option></select>
    <select id="rating"><option value="">All ratings</option></select>
    <select id="niahType"><option value="">All NIAH classes</option></select>
    <div class="filter-wide"><span>Minimum score <b id="scoreValue">0</b></span><input id="score" type="range" min="0" max="100" value="0"/></div>
    <div class="checks">
      <label><input id="onlyAngle" type="checkbox"/> golden angle</label>
      <label><input id="onlyRatio" type="checkbox"/> golden ratio</label>
      <label><input id="onlyCircular" type="checkbox"/> circular</label>
      <label><input id="onlyMulti" type="checkbox"/> multipart/repaired</label>
    </div>
  </div>
  <div class="toolbar"><small id="count">Loading…</small><div class="actions"><button id="downloadCsv">CSV</button><button id="downloadGeo">GeoJSON</button></div></div>
  <div class="section"><h2>Observed target vs control rates</h2><div id="groupBars" class="bars"></div></div>
  <div class="section"><h2>Construction-era golden-angle rates</h2><div id="eraBars" class="bars"></div></div>
  <div class="table-wrap"><table><thead><tr>
    <th data-sort="name">Name</th><th data-sort="group">Group</th><th data-sort="area_m2">Area</th><th data-sort="score">Score</th><th data-sort="flags">Evidence</th>
  </tr></thead><tbody id="tbody"></tbody></table><div id="empty" class="empty" hidden>No buildings match these filters.</div></div>
  <div class="pagination"><button id="prev">Previous</button><span id="page">1 / 1</span><button id="next">Next</button></div>
  <div class="section"><h2>Method and provenance</h2><div id="method" class="footnote"></div></div>
  <div class="section"><h2>Statistical results</h2><div id="statsTable"></div></div>
</div>
<script>
const PACK = __DATA__;
const DATA = PACK.targets || [];
const OUTLINES = PACK.outlines || [];
const SUMMARY = PACK.summary || {};
const SIG = PACK.significance || [];
const NIAH_SIG = PACK.niah_significance || [];
const DECADES = PACK.decades || [];
const GEOJSON = PACK.geojson || {type:'FeatureCollection',features:[]};
const PAGE_SIZE = 50;
let filtered = DATA.slice();
let page = 1;
let sortKey = 'score';
let sortDesc = true;
let map = null;
let markerLayer = null;
const markerById = new Map();

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value, digits=1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
const pFmt = value => { const p=Number(value); if (!Number.isFinite(p)) return 'n/a'; if (p<1e-4) return '&lt;0.0001'; if (p<.001) return '&lt;0.001'; return p.toFixed(4).replace(/0+$/,'').replace(/\.$/,''); };
const flagsText = row => row.flags.join(', ');
const hasFlag = (row, flag) => row.flags.includes(flag);
const unique = key => [...new Set(DATA.map(row => key(row)).filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));

function fillSelect(id, values) { for (const value of values) { const option=document.createElement('option'); option.value=value; option.textContent=value; $(id).appendChild(option); } }
fillSelect('group', unique(row=>row.group));
fillSelect('century', unique(row=>row.niah.century));
fillSelect('rating', unique(row=>row.niah.rating));
fillSelect('niahType', unique(row=>row.niah.type));

function color(score) { return score >= 60 ? '#b42318' : score >= 35 ? '#d97706' : score >= 15 ? '#2563eb' : '#3f8f65'; }
function matches(row) {
  const q=$('query').value.trim().toLowerCase();
  const hay=[row.name,row.osm_id,row.group,row.subtype,flagsText(row),row.niah.name,row.niah.county,row.niah.type].join(' ').toLowerCase();
  return (!q || hay.includes(q)) && (!$('group').value || row.group===$('group').value) &&
    (!$('century').value || row.niah.century===$('century').value) && (!$('rating').value || row.niah.rating===$('rating').value) &&
    (!$('niahType').value || row.niah.type===$('niahType').value) && row.score >= Number($('score').value) &&
    (!$('onlyAngle').checked || row.has_golden_angle) && (!$('onlyRatio').checked || row.has_golden_ratio) &&
    (!$('onlyCircular').checked || hasFlag(row,'circular')) && (!$('onlyMulti').checked || row.multipart || row.repaired);
}
function sortRows(rows) {
  return rows.sort((a,b) => { let av=a[sortKey], bv=b[sortKey]; if(sortKey==='flags') { av=flagsText(a); bv=flagsText(b); } if(sortKey==='name'||sortKey==='group') return (String(av||'').localeCompare(String(bv||'')))*(sortDesc?-1:1); return ((Number(bv)||0)-(Number(av)||0))*(sortDesc?1:-1); });
}
function applyFilters() { filtered=sortRows(DATA.filter(matches)); page=1; renderAll(); }

function renderSummary() {
  $('kTargets').textContent=filtered.length.toLocaleString();
  $('kControls').textContent=Number(SUMMARY.controls||0).toLocaleString();
  $('kGolden').textContent=filtered.filter(row=>row.has_golden_angle).length.toLocaleString();
  $('kNiah').textContent=filtered.filter(row=>row.niah.reg_no).length.toLocaleString();
  $('count').textContent=`${filtered.length.toLocaleString()} matching targets · showing up to ${PAGE_SIZE} per page`;
}
function flagHtml(row) { return row.flags.slice(0,5).map(flag=>`<span class="flag">${esc(flag.replaceAll('_',' '))}</span>`).join('') || '<span class="footnote">none</span>'; }
function renderTable() {
  const start=(page-1)*PAGE_SIZE, visible=filtered.slice(start,start+PAGE_SIZE);
  $('tbody').innerHTML=visible.map(row=>`<tr data-id="${esc(row.osm_id)}"><td><b>${esc(row.name||'Unnamed')}</b><br><span class="footnote">${esc(row.osm_id)}${row.niah.name?' · '+esc(row.niah.name):''}</span></td><td>${esc(row.group)}${row.niah.century?`<br><span class="footnote">${esc(row.niah.century)}</span>`:''}</td><td>${fmt(row.area_m2,0)} m²</td><td class="score">${fmt(row.score)}</td><td>${flagHtml(row)}</td></tr>`).join('');
  $('empty').hidden=visible.length>0;
  const pages=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE)); $('page').textContent=`${Math.min(page,pages)} / ${pages}`; $('prev').disabled=page<=1; $('next').disabled=page>=pages;
  document.querySelectorAll('#tbody tr[data-id]').forEach(tr=>tr.addEventListener('click',()=>focusRow(tr.dataset.id)));
}
function popup(row) { return `<b>${esc(row.name||'Unnamed')}</b><br>${esc(row.group)} · ${fmt(row.area_m2,0)} m²<br>Score <b>${fmt(row.score)}</b> · aspect ${fmt(row.aspect_ratio,3)}<br>Convexity ${fmt(row.convexity,3)} · ${row.n_vertices} vertices${row.multipart?' · multipart':''}${row.repaired?' · repaired':''}<br>${flagHtml(row)}${row.niah.name?`<br><span>${esc(row.niah.name)} · ${esc(row.niah.rating)} · ${esc(row.niah.century)}</span>`:''}<br><a href="${row.osm_url}" target="_blank" rel="noopener">OpenStreetMap</a>`; }
function renderMap() {
  if (!map || !markerLayer) return;
  markerLayer.clearLayers(); markerById.clear();
  filtered.slice(0,__MARKER_LIMIT__).forEach(row=>{ const marker=L.circleMarker([row.lat,row.lon],{radius:5,color:'#17324d',weight:1,fillColor:color(row.score),fillOpacity:.86}); marker.bindPopup(popup(row)); markerLayer.addLayer(marker); markerById.set(row.osm_id,marker); });
}
function focusRow(id) { const row=DATA.find(item=>item.osm_id===id); if(!row) return; if(map){ map.setView([row.lat,row.lon],17); const marker=markerById.get(id); if(marker) marker.openPopup(); } }
function renderBars() {
  const groups=SIG.filter(row=>row.signal==='golden_angle' && row.group);
  $('groupBars').innerHTML=groups.length?groups.map(row=>`<div class="bar-row"><span>${esc(row.group)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,Number(row.observed_rate)||0)}%"></div></div><span>${fmt(row.observed_rate,2)}% / ${fmt(row.control_rate,2)}%</span></div>`).join(''):'<span class="footnote">No global significance file available.</span>';
  const eras=NIAH_SIG.filter(row=>row.signal==='golden_angle'&&row.group==='worship'&&String(row.reference).includes('era-matched')).sort((a,b)=>String(a.stratum).localeCompare(String(b.stratum),undefined,{numeric:true}));
  $('eraBars').innerHTML=eras.length?eras.map(row=>`<div class="bar-row"><span>${esc(row.stratum)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,Number(row.observed_rate)||0)}%"></div></div><span>${fmt(row.observed_rate,2)}% · H ${pFmt(row.p_adjusted)}</span></div>`).join(''):'<span class="footnote">No NIAH era results available.</span>';
}
function renderStats() {
  const rows=[...SIG.map(row=>({...row,family:'global'})),...NIAH_SIG.slice(0,30).map(row=>({...row,family:'NIAH'}))];
  $('statsTable').innerHTML=rows.length?`<table><thead><tr><th>Test</th><th>Observed</th><th>Reference</th><th>p / Holm</th><th>Result</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.signal)} · ${esc(row.group||row.stratum||'')}</td><td>${esc(row.observed_rate||'—')}%</td><td>${esc(row.control_rate||row.reference_rate||'—')}%</td><td>${pFmt(row.p_value)} / ${pFmt(row.p_adjusted)}</td><td class="verdict-${esc(row.verdict)}">${esc(row.verdict||'—')}</td></tr>`).join('')}</tbody></table>`:'<span class="footnote">No statistical results available.</span>';
}
function download(name, content, type) { const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([content],{type})); a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),500); }
function downloadCsv() { const cols=['osm_id','name','group','lat','lon','score','area_m2','aspect_ratio','convexity','circularity','flags','niah_name','niah_rating','niah_century']; const escCsv=v=>`"${String(v??'').replaceAll('"','""')}"`; const lines=[cols.join(',')]; filtered.forEach(row=>lines.push(cols.map(key=>{ if(key==='flags')return escCsv(flagsText(row)); if(key.startsWith('niah_'))return escCsv(row.niah[key.slice(5)]); return escCsv(row[key]); }).join(','))); download('ireland-geometry-filtered.csv',lines.join('\n'),'text/csv'); }
function downloadGeo() { const ids=new Set(filtered.map(row=>row.osm_id)); const copy={...GEOJSON,features:(GEOJSON.features||[]).filter(feature=>ids.has(feature.properties?.osm_id))}; download('ireland-geometry-filtered.geojson',JSON.stringify(copy),'application/geo+json'); }
function initMap() { if(typeof L==='undefined'){ $('map').innerHTML='<div style="padding:20px">Map libraries could not be loaded. The table and exports remain available.</div>'; return; } map=L.map('map').setView([53.35,-8.05],7); const osm=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; OpenStreetMap contributors',maxZoom:19}).addTo(map); const esri=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Esri World Imagery',maxZoom:18}); markerLayer=(L.markerClusterGroup?L.markerClusterGroup({maxClusterRadius:45,disableClusteringAtZoom:14}):L.layerGroup()).addTo(map); const outlineLayer=L.layerGroup().addTo(map); OUTLINES.forEach(item=>{ const shapes=item.rings.length===1?item.rings[0]:item.rings; L.polygon(shapes,{color:'#1f2937',weight:2,fillColor:color(item.score),fillOpacity:.2}).bindPopup(`<b>${esc(item.name||'Unnamed')}</b><br>${esc(item.group)} · score ${fmt(item.score)}<br>${flagHtml({flags:item.flags||[]})}`).addTo(outlineLayer); }); L.control.layers({'OSM':osm,'Satellite':esri},{'Top outlines':outlineLayer,'Markers':markerLayer}).addTo(map); renderMap(); }
function init() { $('score').addEventListener('input',()=>{$('scoreValue').textContent=$('score').value;applyFilters();}); ['query','group','century','rating','niahType','onlyAngle','onlyRatio','onlyCircular','onlyMulti'].forEach(id=>$(id).addEventListener(id==='query'?'input':'change',applyFilters)); $('prev').addEventListener('click',()=>{if(page>1){page--;renderTable();}}); $('next').addEventListener('click',()=>{if(page<Math.ceil(filtered.length/PAGE_SIZE)){page++;renderTable();}}); document.querySelectorAll('th[data-sort]').forEach(th=>th.addEventListener('click',()=>{const key=th.dataset.sort; sortDesc=sortKey===key?!sortDesc:key==='score';sortKey=key;applyFilters();})); $('downloadCsv').addEventListener('click',downloadCsv); $('downloadGeo').addEventListener('click',downloadGeo); $('method').innerHTML=`<p>Target rows: <b>${Number(SUMMARY.targets||0).toLocaleString()}</b>; controls: <b>${Number(SUMMARY.controls||0).toLocaleString()}</b>; NIAH joins: <b>${Number(SUMMARY.niah_matches||0).toLocaleString()}</b> (${Number(SUMMARY.niah_contained||0).toLocaleString()} contained, ${Number(SUMMARY.niah_near||0).toLocaleString()} near).</p><p>Pattern scores are prioritisation heuristics. Primary rates use building-level target/control comparisons with confidence intervals and Holm-adjusted p-values. Construction dates and ratings cover the NIAH dataset, not all of Ireland. Generated ${esc(SUMMARY.generated_at||'unknown')}.</p><p>Sources: OpenStreetMap contributors (ODbL), National Inventory of Architectural Heritage (CC BY 4.0), and Esri World Imagery for visual reference.</p>`; renderSummary();renderTable();renderBars();renderStats();initMap(); }
init();
</script>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default=None, help="output directory; defaults to project output/"
    )
    args = parser.parse_args()
    out = project_path(args.out_dir, "output")
    out.mkdir(parents=True, exist_ok=True)
    html_text = build_report(out)
    report_path = out / "report.html"
    atomic_write_text(report_path, html_text)
    print(f"[report] wrote {report_path} ({len(html_text):,} bytes)")


if __name__ == "__main__":
    main()
