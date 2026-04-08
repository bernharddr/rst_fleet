"""
HTML report generator.

Saves each run as a JSON snapshot in history/ (keeps last 20),
then generates a self-contained fleet_report.html with a comparison UI.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_DIR = Path("history")
REPORT_FILE = Path("fleet_report.html")
MAX_SNAPSHOTS_IN_HTML = 100   # how many snapshots to embed in the comparison dropdown
MAX_SNAPSHOT_DAYS = 90        # delete snapshot files older than this many days

ASSIGNMENT_ORDER = [
    # TEK & TEJ groups first — Oncall Trailer on top
    "Oncall Trailer",
    "Dedicated Tjiwi",
    "Dedicated Internusa",
    "Dedicated J&T",
    "Dedicated IKK",
    # TEZ groups
    "Tjiwi Kimia",
    "Aliansi",
    "Oncall TWB",
    "JNE & SPX",
    # Other
    "Breakdown",
    "Other",
]

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RST Fleet Monitor</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-polylinedecorator@1.6.0/dist/leaflet.polylineDecorator.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:Arial,sans-serif;font-size:13px;background:#f0f2f5;padding:14px}
  h1{font-size:17px;color:#1a1a2e;margin-bottom:12px}
  .toolbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;background:#fff;
    border-radius:8px;padding:10px 14px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:10px}
  .toolbar select{padding:5px 8px;border:1px solid #ccc;border-radius:5px;font-size:13px}
  .ts{color:#666;font-size:12px}
  .compare-ts{color:#d84315;font-size:12px;font-weight:bold}
  .stats{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .stat{padding:7px 14px;border-radius:20px;font-weight:bold;font-size:12px}
  .s-jalan{background:#d4edda;color:#155724}
  .s-idle{background:#fff3cd;color:#856404}
  .s-berhenti{background:#f8d7da;color:#721c24}
  .s-gps{background:#e2e3e5;color:#383d41}
  table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
    overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:16px}
  th{background:#2c3e50;color:#fff;padding:8px 10px;text-align:left;font-size:12px;
    position:sticky;top:0;z-index:1}
  td{padding:6px 10px;border-bottom:1px solid #f0f0f0;vertical-align:top}
  tr:last-child td{border-bottom:none}
  tr.gr-header td{background:#495057;color:#fff;font-weight:bold;font-size:12px;
    padding:5px 10px;letter-spacing:.3px}
  tr.r-jalan td:nth-child(2){border-left:3px solid #28a745}
  tr.r-idle td:nth-child(2){border-left:3px solid #ffc107}
  tr.r-berhenti td:nth-child(2){border-left:3px solid #dc3545}
  tr.r-gps td:nth-child(2){border-left:3px solid #adb5bd}
  tr.changed{background:#fff8e1!important}
  tr.changed td:nth-child(2){border-left:3px solid #ff6d00!important}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:bold}
  .b-jalan{background:#d4edda;color:#155724}
  .b-idle{background:#fff3cd;color:#856404}
  .b-berhenti{background:#f8d7da;color:#721c24}
  .b-gps{background:#e2e3e5;color:#383d41}
  .prev{display:block;font-size:11px;color:#e65100;margin-top:2px}
  .prev-lokasi{font-size:11px;color:#888}
  #no-cmp{display:none}
  .tabs{display:flex;gap:8px;margin-bottom:10px}
  .tab-btn{padding:8px 20px;border:2px solid #2c3e50;border-radius:6px;background:#fff;
    color:#2c3e50;font-weight:bold;font-size:13px;cursor:pointer;transition:all .15s}
  .tab-btn.active{background:#2c3e50;color:#fff}
  .tab-btn:hover:not(.active){background:#ecf0f1}
  #map-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
    background:#fff;border-radius:8px;padding:10px 14px;
    box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:10px}
  #map-toolbar input,#map-toolbar select{padding:5px 8px;border:1px solid #ccc;
    border-radius:5px;font-size:13px}
  #map-count{font-size:12px;color:#666}
  #map-view{height:calc(100vh - 200px);min-height:500px;border-radius:8px;
    border:1px solid #ddd}
  #trail-panel{background:#fff;border-radius:8px;padding:12px 16px;
    box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:10px;display:none}
  #trail-panel h3{font-size:13px;margin-bottom:8px;color:#2c3e50}
  #trail-panel select,#trail-panel input{padding:5px 8px;border:1px solid #ccc;
    border-radius:5px;font-size:13px;margin-right:6px}
  #trail-panel button{padding:6px 14px;background:#2c3e50;color:#fff;border:none;
    border-radius:5px;cursor:pointer;font-size:13px}
  #trail-panel button:hover{background:#34495e}
  #trail-status{font-size:12px;color:#666;margin-left:8px}
  #live-badge{display:none;align-items:center;gap:5px;font-size:12px;
    color:#155724;background:#d4edda;padding:4px 10px;border-radius:12px}
  .live-dot{width:8px;height:8px;background:#28a745;border-radius:50%;
    animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .dwell{display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;
    font-weight:bold;background:#e8f5e9;color:#1b5e20;white-space:nowrap}
</style>
</head>
<body>
<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
  <h1 style="margin:0">&#x1F69B;&nbsp;RST Fleet Monitor</h1>
  <div id="live-badge"><span class="live-dot"></span> LIVE</div>
</div>

<div class="tabs">
  <button class="tab-btn active" data-tab="table" onclick="switchTab('table')">&#x1F4CB;&nbsp;Tabel</button>
  <button class="tab-btn" data-tab="map" onclick="switchTab('map')">&#x1F5FA;&nbsp;Peta</button>
</div>

<div id="tab-table">
<div class="toolbar">
  <span>&#x1F550;&nbsp;<strong id="cur-ts" class="ts"></strong></span>
  <label style="font-weight:bold;font-size:12px">Bandingkan dengan:&nbsp;
    <select id="cmp-sel" onchange="render()">
      <option value="-1">&#x2014; pilih snapshot &#x2014;</option>
    </select>
  </label>
  <span id="cmp-ts" class="compare-ts"></span>
  <span id="change-count" style="font-size:12px;color:#555"></span>
</div>

<div class="stats" id="stats"></div>

<table>
  <thead>
    <tr>
      <th style="width:30px">#</th>
      <th>NOPOL</th>
      <th>STATUS</th>
      <th>ENGINE</th>
      <th>VOLT</th>
      <th>SPEED</th>
      <th>ODO (km)</th>
      <th>LOKASI</th>
      <th style="background:#1a6b3a">LOKASI DETIL</th>
      <th style="background:#0d5c32;white-space:nowrap">&#x23F1;&nbsp;DURASI</th>
      <th id="cmp-col-hdr" style="display:none;min-width:160px">LOKASI SEBELUMNYA</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
</div><!-- #tab-table -->

<div id="tab-map" style="display:none">
  <div id="map-toolbar">
    <label style="font-weight:bold;font-size:12px">&#x1F50D;&nbsp;Cari NOPOL:&nbsp;
      <input id="map-search" type="text" placeholder="B 9973 TEJ..." style="width:160px"
        oninput="renderMap()">
    </label>
    <label style="font-weight:bold;font-size:12px">Grup:&nbsp;
      <select id="map-grp-filter" onchange="renderMap()">
        <option value="ALL">Semua Grup</option>
      </select>
    </label>
    <span id="map-count" class="ts"></span>
    <button onclick="document.getElementById('trail-panel').style.display=document.getElementById('trail-panel').style.display==='none'?'block':'none'"
      style="padding:5px 12px;background:#1a6b3a;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:12px">
      &#x1F4CD;&nbsp;Jejak Rute
    </button>
  </div>

  <div id="trail-panel">
    <h3>&#x1F4CD; Tampilkan Jejak Rute</h3>
    <div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px">
      <select id="trail-nopol"><option value="">— pilih unit —</option></select>
      <label style="font-size:12px;cursor:pointer">
        <input type="radio" name="trail-mode" value="hours" checked onchange="toggleTrailMode()"> Terakhir
      </label>
      <select id="trail-hours">
        <option value="1">1 jam</option>
        <option value="6">6 jam</option>
        <option value="24" selected>24 jam</option>
        <option value="72">3 hari</option>
        <option value="168">7 hari</option>
        <option value="720">30 hari</option>
        <option value="2160">3 bulan</option>
      </select>
      <label style="font-size:12px;cursor:pointer">
        <input type="radio" name="trail-mode" value="range" onchange="toggleTrailMode()"> Rentang Tanggal
      </label>
      <span id="trail-date-range" style="display:none;align-items:center;gap:4px">
        <input type="date" id="trail-from" style="padding:4px 6px;border:1px solid #ccc;border-radius:5px;font-size:12px">
        <span style="font-size:12px">s/d</span>
        <input type="date" id="trail-to" style="padding:4px 6px;border:1px solid #ccc;border-radius:5px;font-size:12px">
      </span>
      <button onclick="loadTrail()">Tampilkan</button>
      <button onclick="clearTrail()" style="background:#666">Hapus</button>
      <span id="trail-status"></span>
    </div>
    <div id="trail-scrubber-wrap" style="display:none;margin-top:10px">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:4px">
        <span style="font-size:11px;color:#666">Legenda:</span>
        <span style="background:#28a745;width:24px;height:6px;display:inline-block;border-radius:3px"></span><span style="font-size:11px">Cepat (&ge;40)</span>
        <span style="background:#ffc107;width:24px;height:6px;display:inline-block;border-radius:3px"></span><span style="font-size:11px">Pelan (5-40)</span>
        <span style="background:#dc3545;width:24px;height:6px;display:inline-block;border-radius:3px"></span><span style="font-size:11px">Berhenti (&lt;5)</span>
      </div>
      <input type="range" id="trail-scrubber" style="width:100%;accent-color:#1a6b3a"
        min="0" max="0" value="0" oninput="moveCursor(parseInt(this.value))">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:3px">
        <span id="trail-time-start" style="font-size:11px;color:#888"></span>
        <span id="trail-cursor-info" style="font-size:12px;font-weight:bold;color:#1a6b3a;
          background:#e8f5e9;padding:2px 8px;border-radius:8px"></span>
        <span id="trail-time-end" style="font-size:11px;color:#888"></span>
      </div>
      <button id="play-btn" onclick="playTrail()"
        style="margin-top:6px;padding:4px 14px;background:#1a6b3a;color:#fff;
        border:none;border-radius:5px;cursor:pointer;font-size:12px">&#x25B6; Putar</button>
    </div>
  </div>

  <div id="map-view"></div>
</div><!-- #tab-map -->

<script>
const SNAPSHOTS=__SNAPSHOTS__;
const FA=__FLEET_ASSIGNMENTS__;
const ORDER=__ASSIGNMENT_ORDER__;

function fmtTs(iso){
  const d=new Date(iso);
  return d.toLocaleString('id-ID',{timeZone:'Asia/Jakarta',
    day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
}
function fmtDur(enteredAt){
  if(!enteredAt)return'';
  const mins=Math.round((Date.now()-new Date(enteredAt).getTime())/60000);
  if(mins<1)return'<1m';
  if(mins<60)return mins+'m';
  const h=Math.floor(mins/60),m=mins%60;
  return m?h+'j '+m+'m':h+'j';
}
function stripDist(s){return(s||'').replace(/\s*\([\d.]+km[^)]*\)/,'').trim()}
function bc(st){
  if(!st)return'b-gps';
  const s=st.toLowerCase();
  return s==='jalan'?'b-jalan':s==='idle'?'b-idle':s==='berhenti'?'b-berhenti':'b-gps';
}
function rc(st){
  if(!st)return'r-gps';
  const s=st.toLowerCase();
  return s==='jalan'?'r-jalan':s==='idle'?'r-idle':s==='berhenti'?'r-berhenti':'r-gps';
}

function render(){
  if(!SNAPSHOTS.length){
    document.getElementById('tbody').innerHTML='<tr><td colspan="11" style="text-align:center;padding:30px;color:#999">Belum ada data.</td></tr>';
    return;
  }
  const cur=SNAPSHOTS[0];
  document.getElementById('cur-ts').textContent=fmtTs(cur.timestamp);

  const sel=document.getElementById('cmp-sel');
  if(sel.options.length===1){
    SNAPSHOTS.slice(1).forEach((s,i)=>{
      const o=document.createElement('option');
      o.value=i+1;
      o.textContent=fmtTs(s.timestamp);
      sel.appendChild(o);
    });
  }
  const ci=parseInt(sel.value);
  const prev=(ci>=1&&ci<SNAPSHOTS.length)?SNAPSHOTS[ci]:null;
  const pm={};
  if(prev){
    document.getElementById('cmp-ts').textContent='← '+fmtTs(prev.timestamp);
    prev.vehicles.forEach(v=>pm[v.nopol]=v);
  } else {
    document.getElementById('cmp-ts').textContent='';
  }
  document.getElementById('cmp-col-hdr').style.display=prev?'':'none';

  // stats
  const cnt={Jalan:0,Idle:0,Berhenti:0,other:0};
  cur.vehicles.forEach(v=>{
    if(v.status==='Jalan')cnt.Jalan++;
    else if(v.status==='Idle')cnt.Idle++;
    else if(v.status==='Berhenti')cnt.Berhenti++;
    else cnt.other++;
  });
  document.getElementById('stats').innerHTML=
    `<div class="stat s-jalan">&#x1F7E2; Jalan: ${cnt.Jalan}</div>`+
    `<div class="stat s-idle">&#x1F7E1; Idle: ${cnt.Idle}</div>`+
    `<div class="stat s-berhenti">&#x1F534; Berhenti: ${cnt.Berhenti}</div>`+
    `<div class="stat s-gps">&#x26AA; GPS Missing / Lainnya: ${cnt.other}</div>`;

  // group vehicles
  const grps={};
  ORDER.forEach(g=>grps[g]=[]);
  cur.vehicles.forEach(v=>{
    const g=FA[v.nopol]||'Other';
    if(!grps[g])grps[g]=[];
    grps[g].push(v);
  });

  let html='';
  let totalChanged=0;
  ORDER.forEach(grp=>{
    const vs=grps[grp];
    if(!vs||!vs.length)return;
    vs.sort((a,b)=>a.nopol.localeCompare(b.nopol));
    html+=`<tr class="gr-header"><td colspan="11">&#x1F4CC;&nbsp;${grp} &mdash; ${vs.length} unit</td></tr>`;
    vs.forEach((v,i)=>{
      const pv=pm[v.nopol];
      const locChanged=pv&&stripDist(v.lokasi)!==stripDist(pv.lokasi);
      const stChanged=pv&&v.status!==pv.status;
      const anyChanged=locChanged||stChanged;
      if(anyChanged)totalChanged++;
      const rowCls=rc(v.status)+(anyChanged?' changed':'');
      let stHtml=`<span class="badge ${bc(v.status)}">${v.status||'GPS Missing'}</span>`;
      if(stChanged)stHtml+=`<span class="prev">was: ${pv.status}</span>`;
      let locHtml=v.lokasi||'-';
      let cmpLoc='';
      if(prev){cmpLoc=pv?(pv.lokasi||'-'):'<i style="color:#aaa">tidak ada data</i>';}
      const detil=v.lokasi_detil||(v.at_place?'Di lokasi':'');
      const detilHtml=detil?`<span style="font-weight:bold;color:#155724">${detil}</span>`:'<span style="color:#ccc">—</span>';
      const speedHtml=v.speed_kmh>0?`<b style="color:#1565c0">${v.speed_kmh} km/h</b>`:`<span style="color:#aaa">0</span>`;
      const odoHtml=v.odo_km>0?v.odo_km.toLocaleString('id-ID',{minimumFractionDigits:1,maximumFractionDigits:1})+' km':'—';
      const dur=fmtDur(v.place_entered_at);
      const durHtml=dur?`<span class="dwell">&#x23F1;&nbsp;${dur}</span>`:'<span style="color:#ccc">—</span>';
      html+=`<tr class="${rowCls}">
        <td style="color:#999;font-size:11px">${i+1}</td>
        <td><strong>${v.nopol}</strong></td>
        <td>${stHtml}</td>
        <td>${v.engine_on?'<b style="color:#28a745">ON</b>':'<span style="color:#aaa">OFF</span>'}</td>
        <td style="white-space:nowrap">${v.voltage_v!=null?v.voltage_v.toFixed(2)+'V':'-'}</td>
        <td style="white-space:nowrap">${speedHtml}</td>
        <td style="white-space:nowrap;font-size:12px">${odoHtml}</td>
        <td>${locHtml}</td>
        <td>${detilHtml}</td>
        <td>${durHtml}</td>
        ${prev?`<td class="prev-lokasi">${cmpLoc}</td>`:''}
      </tr>`;
    });
  });

  document.getElementById('tbody').innerHTML=html;
  if(prev&&totalChanged>0){
    document.getElementById('change-count').textContent=`⚠ ${totalChanged} perubahan terdeteksi`;
  } else {
    document.getElementById('change-count').textContent='';
  }
}

// ── LIVE WebSocket ────────────────────────────────────────
let ws=null, liveMarkers={};

function connectWS(){
  if(window.location.protocol==='file:')return; // offline mode
  const url='ws://'+window.location.host+'/ws';
  try{
    ws=new WebSocket(url);
    ws.onopen=()=>{
      document.getElementById('live-badge').style.display='flex';
      console.log('WS connected');
    };
    ws.onmessage=(evt)=>{
      const data=JSON.parse(evt.data);
      if(data.type==='positions')updateLiveMarkers(data.vehicles);
    };
    ws.onclose=()=>{
      document.getElementById('live-badge').style.display='none';
      setTimeout(connectWS,5000);
    };
    ws.onerror=()=>ws.close();
  }catch(e){}
}

function updateLiveMarkers(vehicles){
  if(!map||!markersLayer)return;
  vehicles.forEach(v=>{
    if(!v.lat||!v.lng||v.lat===0&&v.lng===0)return;
    if(liveMarkers[v.nopol]){
      liveMarkers[v.nopol].setLatLng([v.lat,v.lng]);
      // Refresh popup with updated dwell time
      const grp=FA[v.nopol]||'Other';
      liveMarkers[v.nopol].setPopupContent(_popupHtml(v,grp));
    }
  });
}

// ── MAP VIEW ──────────────────────────────────────────────
function _popupHtml(v,grp){
  const detilTxt=v.lokasi_detil||(v.at_place?'Di lokasi':'');
  const detil=detilTxt?`<br><b style="color:#155724">${detilTxt}</b>`:'';
  const dur=fmtDur(v.place_entered_at);
  const dwellHtml=dur?`<br><span style="background:#e8f5e9;color:#1b5e20;padding:1px 6px;border-radius:8px;font-size:11px;font-weight:bold">&#x23F1; Di sini: ${dur}</span>`:'';
  return `<b>${v.nopol}</b>
    <span style="display:inline-block;margin-left:6px;padding:1px 7px;border-radius:10px;
      font-size:10px;background:#2c3e50;color:#fff">${grp}</span><br>
    Status: <b>${v.status||'?'}</b><br>
    Engine: ${v.engine_on?'<b style="color:green">ON</b>':'OFF'}<br>
    Volt: ${v.voltage_v}V &nbsp; Speed: ${v.speed_kmh} km/h<br>
    ODO: ${(v.odo_km||0).toLocaleString('id-ID')} km<br>
    ${v.lokasi||''}${detil}${dwellHtml}<br>
    <button onclick="selectTrailUnit('${v.nopol}')"
      style="margin-top:6px;padding:3px 10px;background:#1a6b3a;color:#fff;
      border:none;border-radius:4px;cursor:pointer;font-size:11px">
      &#x1F4CD; Lihat Jejak
    </button>`;
}

let map=null, markersLayer=null;
let trailLayer=null, trailDecorator=null;
const STATUS_EMOJI={'Jalan':'🚛','Idle':'🚚','Berhenti':'🅿️','GPS Missing':'❓'};
const STATUS_COLOR={'Jalan':'#28a745','Idle':'#ffc107','Berhenti':'#dc3545','GPS Missing':'#aaa'};

function initMap(){
  if(map)return;
  map=L.map('map-view').setView([-6.2,107.0],8);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',{
    attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/">CARTO</a>',
    subdomains:'abcd',maxZoom:19
  }).addTo(map);
  markersLayer=L.layerGroup().addTo(map);
}

function renderMap(){
  if(!SNAPSHOTS.length||!map)return;
  markersLayer.clearLayers();
  const cur=SNAPSHOTS[0];
  const filterGrp=document.getElementById('map-grp-filter').value;
  const search=document.getElementById('map-search').value.trim().toUpperCase();

  const visible=[];
  cur.vehicles.forEach(v=>{
    if(!v.lat||!v.lng||v.lat===0&&v.lng===0)return;
    if(filterGrp&&filterGrp!=='ALL'&&(FA[v.nopol]||'Other')!==filterGrp)return;
    if(search&&!v.nopol.toUpperCase().includes(search))return;
    visible.push(v);
  });

  liveMarkers={};
  visible.forEach(v=>{
    const emoji=STATUS_EMOJI[v.status]||'❓';
    const color=STATUS_COLOR[v.status]||'#aaa';
    const icon=L.divIcon({
      className:'',
      html:`<div style="text-align:center;line-height:1">
        <div style="font-size:22px">${emoji}</div>
        <div style="background:rgba(255,255,255,0.92);border:1px solid ${color};border-radius:4px;
          padding:1px 4px;font-size:10px;font-weight:bold;white-space:nowrap;margin-top:1px">${v.nopol}</div>
      </div>`,
      iconAnchor:[28,32], iconSize:[56,40],
    });
    const m=L.marker([v.lat,v.lng],{icon});
    liveMarkers[v.nopol]=m;
    const grp=FA[v.nopol]||'Other';
    m.bindPopup(_popupHtml(v,grp));
    markersLayer.addLayer(m);
  });

  // Populate trail NOPOL selector
  const sel=document.getElementById('trail-nopol');
  const prev=sel.value;
  sel.innerHTML='<option value="">— pilih unit —</option>';
  visible.forEach(v=>{
    const o=document.createElement('option');
    o.value=v.nopol; o.textContent=v.nopol;
    sel.appendChild(o);
  });
  if(prev)sel.value=prev;

  // Auto-fit to visible markers
  if(visible.length>0&&!search&&filterGrp==='ALL'){
    const lats=visible.map(v=>v.lat),lngs=visible.map(v=>v.lng);
    map.fitBounds([[Math.min(...lats),Math.min(...lngs)],[Math.max(...lats),Math.max(...lngs)]],{padding:[30,30]});
  }

  // If search matches exactly 1, center on it
  if(visible.length===1){
    map.setView([visible[0].lat,visible[0].lng],15);
    markersLayer.getLayers()[0]?.openPopup();
  }
  document.getElementById('map-count').textContent=`${visible.length} unit ditampilkan`;
}

function switchTab(tab){
  document.getElementById('tab-table').style.display=tab==='table'?'block':'none';
  document.getElementById('tab-map').style.display=tab==='map'?'block':'none';
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
  if(tab==='map'){
    initMap();
    setTimeout(()=>{map.invalidateSize();renderMap();},100);
  }
}

// ── ROUTE TRAIL ──────────────────────────────────────────
let trailPoints=[], trailCursor=null, _playInterval=null;

function fmtTime(t){
  try{return new Date(t).toLocaleString('id-ID',{timeZone:'Asia/Jakarta',
    day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});}catch(e){return t;}
}

function selectTrailUnit(nopol){
  document.getElementById('trail-nopol').value=nopol;
  document.getElementById('trail-panel').style.display='block';
  switchTab('map');
  loadTrail();
}

function clearTrail(){
  if(trailLayer){map.removeLayer(trailLayer);trailLayer=null;}
  if(trailDecorator){map.removeLayer(trailDecorator);trailDecorator=null;}
  if(trailCursor){map.removeLayer(trailCursor);trailCursor=null;}
  if(_playInterval){clearInterval(_playInterval);_playInterval=null;}
  trailPoints=[];
  document.getElementById('trail-status').textContent='';
  document.getElementById('trail-scrubber-wrap').style.display='none';
  const pb=document.getElementById('play-btn');if(pb)pb.textContent='\u25B6 Putar';
}

function moveCursor(idx){
  if(!trailPoints.length)return;
  idx=Math.max(0,Math.min(idx,trailPoints.length-1));
  const pt=trailPoints[idx];
  if(!trailCursor){
    trailCursor=L.circleMarker([pt.lat,pt.lng],{
      radius:9,color:'#ff6600',fillColor:'#ff6600',fillOpacity:0.95,weight:2
    }).addTo(map);
  }else{
    trailCursor.setLatLng([pt.lat,pt.lng]);
  }
  document.getElementById('trail-scrubber').value=idx;
  document.getElementById('trail-cursor-info').textContent=
    `\u23F1 ${fmtTime(pt.gps_time)}  \u2022  ${pt.speed} km/h`;
}

function playTrail(){
  const btn=document.getElementById('play-btn');
  if(_playInterval){
    clearInterval(_playInterval);_playInterval=null;
    btn.textContent='\u25B6 Putar';return;
  }
  let idx=parseInt(document.getElementById('trail-scrubber').value)||0;
  if(idx>=trailPoints.length-1)idx=0;
  btn.textContent='\u23F8 Pause';
  _playInterval=setInterval(()=>{
    if(idx>=trailPoints.length-1){
      clearInterval(_playInterval);_playInterval=null;
      btn.textContent='\u25B6 Putar';return;
    }
    moveCursor(idx++);
    map.panTo(trailCursor.getLatLng(),{animate:true,duration:0.3});
  },300);
}

function toggleTrailMode(){
  const mode=document.querySelector('input[name="trail-mode"]:checked').value;
  const rangeEl=document.getElementById('trail-date-range');
  const hoursEl=document.getElementById('trail-hours');
  if(mode==='range'){
    rangeEl.style.display='inline-flex';
    hoursEl.style.display='none';
    // Default to today if empty
    const today=new Date().toISOString().slice(0,10);
    if(!document.getElementById('trail-to').value) document.getElementById('trail-to').value=today;
    if(!document.getElementById('trail-from').value) document.getElementById('trail-from').value=today;
  } else {
    rangeEl.style.display='none';
    hoursEl.style.display='';
  }
}

async function loadTrail(){
  const nopol=document.getElementById('trail-nopol').value;
  if(!nopol)return;
  if(window.location.protocol==='file:'){
    document.getElementById('trail-status').textContent='\u26A0 Jejak hanya tersedia di mode server';
    return;
  }
  const mode=document.querySelector('input[name="trail-mode"]:checked').value;
  document.getElementById('trail-status').textContent='Memuat...';
  clearTrail();
  let url;
  if(mode==='range'){
    const from=document.getElementById('trail-from').value;
    const to=document.getElementById('trail-to').value;
    if(!from||!to){document.getElementById('trail-status').textContent='Pilih tanggal dari dan sampai';return;}
    url=`/api/trail/${encodeURIComponent(nopol)}?from_date=${from}&to_date=${to}`;
  } else {
    const hours=document.getElementById('trail-hours').value;
    url=`/api/trail/${encodeURIComponent(nopol)}?hours=${hours}`;
  }
  try{
    const res=await fetch(url);
    const data=await res.json();
    const pts=data.points||[];
    if(pts.length<2){
      document.getElementById('trail-status').textContent=`Tidak ada data jejak (${pts.length} titik)`;
      return;
    }
    trailPoints=pts;
    trailLayer=L.layerGroup().addTo(map);

    // Speed-colored polyline segments
    for(let i=0;i<pts.length-1;i++){
      const spd=pts[i].speed||0;
      const color=spd>=40?'#28a745':spd>=5?'#ffc107':'#dc3545';
      L.polyline([[pts[i].lat,pts[i].lng],[pts[i+1].lat,pts[i+1].lng]],
        {color,weight:4,opacity:0.85}).addTo(trailLayer);
    }

    // Stopped-point markers (red dot) with tooltip
    pts.forEach((pt,i)=>{
      if((pt.speed||0)<5){
        L.circleMarker([pt.lat,pt.lng],{radius:5,color:'#dc3545',fillColor:'#dc3545',
          fillOpacity:0.8,weight:1})
          .bindTooltip(`${fmtTime(pt.gps_time)}<br>Berhenti`,{direction:'top',sticky:true})
          .on('click',()=>moveCursor(i))
          .addTo(trailLayer);
      }
    });

    // Start (blue) / End (dark red) markers
    const first=pts[0],last=pts[pts.length-1];
    L.circleMarker([first.lat,first.lng],{radius:8,color:'#0066cc',fillColor:'#0066cc',fillOpacity:1})
      .bindPopup(`<b>Start</b><br>${fmtTime(first.gps_time)}<br>Speed: ${first.speed} km/h`)
      .addTo(trailLayer);
    L.circleMarker([last.lat,last.lng],{radius:8,color:'#880000',fillColor:'#880000',fillOpacity:1})
      .bindPopup(`<b>Terakhir</b><br>${fmtTime(last.gps_time)}<br>Speed: ${last.speed} km/h`)
      .addTo(trailLayer);

    // Fit map
    const lats=pts.map(p=>p.lat),lngs=pts.map(p=>p.lng);
    map.fitBounds([[Math.min(...lats),Math.min(...lngs)],[Math.max(...lats),Math.max(...lngs)]],{padding:[40,40]});

    // Setup scrubber
    const sc=document.getElementById('trail-scrubber');
    sc.max=pts.length-1; sc.value=pts.length-1;
    document.getElementById('trail-time-start').textContent=fmtTime(first.gps_time);
    document.getElementById('trail-time-end').textContent=fmtTime(last.gps_time);
    document.getElementById('trail-scrubber-wrap').style.display='block';
    moveCursor(pts.length-1);

    document.getElementById('trail-status').textContent=
      `${pts.length} titik GPS \u00B7 ${fmtTime(first.gps_time)} \u2192 ${fmtTime(last.gps_time)}`;
  }catch(e){
    document.getElementById('trail-status').textContent='Error: '+e.message;
  }
}

window.onload=function(){
  // Populate map group filter
  const sel=document.getElementById('map-grp-filter');
  ORDER.forEach(g=>{const o=document.createElement('option');o.value=g;o.textContent=g;sel.appendChild(o);});
  render();
  connectWS();
};
</script>
</body>
</html>"""


def save_and_report(
    vehicles_data: list[dict],
    timestamp: datetime,
    fleet_assignments: dict[str, str],
) -> None:
    """Save a snapshot and regenerate fleet_report.html."""
    from datetime import timedelta
    HISTORY_DIR.mkdir(exist_ok=True)

    fname = timestamp.strftime("%Y-%m-%d_%H-%M") + ".json"
    snapshot = {"timestamp": timestamp.isoformat(), "vehicles": vehicles_data}
    with open(HISTORY_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # Prune files older than MAX_SNAPSHOT_DAYS (filename starts with YYYY-MM-DD)
    cutoff = (datetime.now() - timedelta(days=MAX_SNAPSHOT_DAYS)).strftime("%Y-%m-%d")
    all_files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    for fp in all_files:
        if fp.stem < cutoff:
            fp.unlink()

    # Reload file list after pruning
    all_files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)

    # Only embed the latest MAX_SNAPSHOTS_IN_HTML into the HTML (keeps page size small)
    snapshots = []
    for fp in all_files[:MAX_SNAPSHOTS_IN_HTML]:
        try:
            with open(fp, encoding="utf-8") as f:
                snapshots.append(json.load(f))
        except Exception:
            pass

    _write_html(snapshots, fleet_assignments)
    logger.info(
        f"HTML report saved → {REPORT_FILE}  "
        f"({len(all_files)} snapshots on disk, {len(snapshots)} in HTML)"
    )


def _write_html(snapshots: list[dict], fleet_assignments: dict[str, str]) -> None:
    html = _HTML_TEMPLATE
    html = html.replace("__SNAPSHOTS__", json.dumps(snapshots, ensure_ascii=False))
    html = html.replace("__FLEET_ASSIGNMENTS__", json.dumps(fleet_assignments, ensure_ascii=False))
    html = html.replace("__ASSIGNMENT_ORDER__", json.dumps(ASSIGNMENT_ORDER))
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
