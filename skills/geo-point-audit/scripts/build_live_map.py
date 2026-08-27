#!/usr/bin/env python3
"""Build a live slippy map of the audit - the companion to the static artifact.

Why both exist. The Artifact CSP blocks every external request, so the published
report draws its own vector geometry as SVG: self-contained, private, instantly
shareable, works offline. What it cannot do is show you aerial imagery, and for
"is this point actually on the hospital?" a satellite photo settles in one second
what a building outline argues for in a paragraph.

So this writes a plain HTML file for local use. Open it in a browser and the
imagery loads. It is a QA instrument, not a deliverable - it needs the network,
it depends on third-party tile servers, and it is a file rather than a link.
Use the artifact to tell people what you found; use this to convince yourself.

  uvx python build_live_map.py --work ./audit-work --out live-map.html
"""
import argparse, json, os, sys


def _utf8_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:14px/1.5 system-ui,-apple-system,sans-serif}
  #map{position:absolute;inset:0}
  .panel{position:absolute;z-index:1000;top:12px;left:12px;width:290px;max-height:calc(100%% - 24px);
         overflow:auto;background:#fff;border-radius:6px;box-shadow:0 2px 16px rgba(0,0,0,.25);padding:12px 14px}
  .panel h1{margin:0 0 2px;font-size:15px}
  .panel .sub{color:#667;font-size:12px;margin-bottom:10px}
  .row{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-top:1px solid #eee;cursor:pointer}
  .row:hover{background:#f4f7f8}
  .row .dot{width:9px;height:9px;border-radius:50%%;flex:none;transform:translateY(3px)}
  .row .off{margin-left:auto;font-variant-numeric:tabular-nums;color:#889;font-size:12px;white-space:nowrap}
  .row.sel{font-weight:600}
  .legend{display:flex;gap:14px;font-size:12px;color:#667;margin:6px 0 4px}
  .legend i{display:inline-block;width:9px;height:9px;border-radius:50%%;margin-right:4px}
  .pop b{display:block;font-size:14px;margin-bottom:2px}
  .pop code{font-size:11px;color:#556}
</style></head><body>
<div id="map"></div>
<div class="panel">
  <h1>%(title)s</h1>
  <div class="sub">%(n)s points &middot; %(nbad)s wrong &middot; %(nmin)s marginal</div>
  <div class="legend">
    <span><i style="background:#c0392b"></i>recorded</span>
    <span><i style="background:#1e8449"></i>actual</span>
  </div>
  <div id="list"></div>
</div>
<script>
const PTS = %(data)s;

const map = L.map('map');
const streets = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' });
const imagery = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics' });
imagery.addTo(map);
L.control.layers({ 'Satellite': imagery, 'Streets': streets }, null, { collapsed: false }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

const COL = { WRONG:'#c0392b', MINOR:'#b9770e', OK:'#1e8449', UNKNOWN:'#7f8c8d' };
const group = L.featureGroup().addTo(map);
const marks = {};

PTS.forEach((p, i) => {
  // the error vector, drawn only when the two positions are meaningfully apart
  if (p.off > 25) {
    L.polyline([p.p, p.f], { color:'#c0392b', weight:2, dashArray:'6 5' }).addTo(group);
  }
  if (p.fp) {
    L.polygon(p.fp, { color:'#1f6f8b', weight:2, fillOpacity:.15 }).addTo(group);
  }
  const actual = L.circleMarker(p.f, { radius:7, color:'#1e8449', weight:3, fillOpacity:0 }).addTo(group);
  const rec = L.circleMarker(p.p, { radius:6, color:'#fff', weight:2,
                                    fillColor: COL[p.v], fillOpacity:1 }).addTo(group);
  const html = `<div class="pop"><b>${p.n}</b>
    <span style="color:${COL[p.v]}">${p.v}</span> &middot; off by ${fmt(p.off)}<br>
    ${p.ad ? p.ad + '<br>' : ''}
    outline: ${p.fpt}<br>at the point: ${p.at || '?'}<br>
    <code>${p.p[0].toFixed(6)}, ${p.p[1].toFixed(6)} recorded<br>
    ${p.f[0].toFixed(6)}, ${p.f[1].toFixed(6)} actual</code></div>`;
  rec.bindPopup(html); actual.bindPopup(html);
  marks[i] = rec;
});

function fmt(m){ return m >= 1000 ? (m/1000).toFixed(2)+' km' : Math.round(m)+' m'; }

const list = document.getElementById('list');
list.innerHTML = PTS.map((p,i) =>
  `<div class="row" data-i="${i}"><span class="dot" style="background:${COL[p.v]}"></span>
   <span>${p.n}</span><span class="off">${fmt(p.off)}</span></div>`).join('');
list.addEventListener('click', ev => {
  const row = ev.target.closest('.row'); if (!row) return;
  const p = PTS[row.dataset.i];
  document.querySelectorAll('.row').forEach(r => r.classList.remove('sel'));
  row.classList.add('sel');
  map.setView(p.p, 18);
  marks[row.dataset.i].openPopup();
});

map.fitBounds(group.getBounds().pad(0.1));
</script></body></html>
"""


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--out', default='live-map.html')
    a = ap.parse_args()

    rows = json.load(open(os.path.join(a.work, 'verdict.json'), encoding='utf-8'))
    fp_path = os.path.join(a.work, 'footprints.json')
    fps = json.load(open(fp_path, encoding='utf-8'))['matched'] if os.path.exists(fp_path) else {}
    copy = {}
    cp = os.path.join(a.work, 'copy.json')
    if os.path.exists(cp):
        copy = json.load(open(cp, encoding='utf-8'))

    pts = []
    for r in sorted(rows, key=lambda r: -r['offset_m']):
        f = fps.get(str(r['idx']))
        at = r.get('at_name') or (r.get('at_type') or '').replace('_', ' ') or ''
        if r.get('geom') != 'polygon':
            fpt = 'none mapped'
        elif r.get('inside'):
            fpt = 'point is inside it'
        elif r['edge_m'] <= 50:
            fpt = 'on the edge (%d m)' % r['edge_m']
        else:
            fpt = '%d m outside' % r['edge_m']
        pts.append({'n': r['name'], 'v': r['verdict'], 'off': r['offset_m'], 'ad': r.get('addr') or '',
                    'p': [r['lat'], r['lon']], 'f': [r['fix_lat'], r['fix_lon']],
                    'fpt': fpt, 'at': at,
                    'fp': (f['ring'] if (f and f.get('ring')) else None)})

    html = HTML % {
        'title': copy.get('title', 'Point Accuracy Audit'),
        'n': len(pts),
        'nbad': sum(1 for p in pts if p['v'] == 'WRONG'),
        'nmin': sum(1 for p in pts if p['v'] == 'MINOR'),
        'data': json.dumps(pts, separators=(',', ':')),
    }
    open(a.out, 'w', encoding='utf-8').write(html)
    print('wrote %s (%.0f KB)' % (a.out, len(html) / 1024))
    print('%d points, %d with an outline' % (len(pts), sum(1 for p in pts if p['fp'])))
    print('\nOpen it in a browser. Satellite imagery is the default layer, because for '
          '"is this point on the building?" a photo settles in a second what an outline argues.')


if __name__ == '__main__':
    main()
