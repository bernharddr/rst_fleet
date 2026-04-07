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
MAX_SNAPSHOTS = 20

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
</style>
</head>
<body>
<h1>&#x1F69B;&nbsp;RST Fleet Monitor</h1>

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
      <th id="cmp-col-hdr" style="display:none;min-width:160px">LOKASI SEBELUMNYA</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<script>
const SNAPSHOTS=__SNAPSHOTS__;
const FA=__FLEET_ASSIGNMENTS__;
const ORDER=__ASSIGNMENT_ORDER__;

function fmtTs(iso){
  const d=new Date(iso);
  return d.toLocaleString('id-ID',{timeZone:'Asia/Jakarta',
    day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
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
    document.getElementById('tbody').innerHTML='<tr><td colspan="10" style="text-align:center;padding:30px;color:#999">Belum ada data.</td></tr>';
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
    html+=`<tr class="gr-header"><td colspan="10">&#x1F4CC;&nbsp;${grp} &mdash; ${vs.length} unit</td></tr>`;
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
      const detil=v.lokasi_detil||'';
      const detilHtml=detil?`<span style="font-weight:bold;color:#155724">${detil}</span>`:'<span style="color:#ccc">—</span>';
      const speedHtml=v.speed_kmh>0?`<b style="color:#1565c0">${v.speed_kmh} km/h</b>`:`<span style="color:#aaa">0</span>`;
      const odoHtml=v.odo_km>0?v.odo_km.toLocaleString('id-ID',{minimumFractionDigits:1,maximumFractionDigits:1})+' km':'—';
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
window.onload=render;
</script>
</body>
</html>"""


def save_and_report(
    vehicles_data: list[dict],
    timestamp: datetime,
    fleet_assignments: dict[str, str],
) -> None:
    """Save a snapshot and regenerate fleet_report.html."""
    HISTORY_DIR.mkdir(exist_ok=True)

    fname = timestamp.strftime("%Y-%m-%d_%H-%M") + ".json"
    snapshot = {"timestamp": timestamp.isoformat(), "vehicles": vehicles_data}
    with open(HISTORY_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # Load all snapshots newest-first, prune oldest beyond MAX_SNAPSHOTS
    all_files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    while len(all_files) > MAX_SNAPSHOTS:
        all_files[-1].unlink()
        all_files = all_files[:-1]

    snapshots = []
    for fp in all_files:
        try:
            with open(fp, encoding="utf-8") as f:
                snapshots.append(json.load(f))
        except Exception:
            pass

    _write_html(snapshots, fleet_assignments)
    logger.info(f"HTML report saved → {REPORT_FILE}  ({len(snapshots)} snapshots stored)")


def _write_html(snapshots: list[dict], fleet_assignments: dict[str, str]) -> None:
    html = _HTML_TEMPLATE
    html = html.replace("__SNAPSHOTS__", json.dumps(snapshots, ensure_ascii=False))
    html = html.replace("__FLEET_ASSIGNMENTS__", json.dumps(fleet_assignments, ensure_ascii=False))
    html = html.replace("__ASSIGNMENT_ORDER__", json.dumps(ASSIGNMENT_ORDER))
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
