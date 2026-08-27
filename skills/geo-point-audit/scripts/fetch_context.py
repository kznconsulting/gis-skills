#!/usr/bin/env python3
"""Fetch the basemap geometry the artifact draws: coastlines/boundaries for the
overview chart, and a street network around every point for the inspector.

Both come from the Census TIGERweb ArcGIS REST service, which serves TIGER as a
live bbox-queryable API rather than an annual download. That matters twice over:
it needs no key, and it is independent of OpenStreetMap - so where the Census
street grid agrees with the OSM building outline, that agreement is genuine
corroboration rather than one source repeating itself.

US only. Outside the US, skip this step; the report degrades to points and
outlines without street context.

  uvx python fetch_context.py --work ./audit-work
"""
import argparse, json, math, os, sys, time, urllib.parse, urllib.request

def _utf8_stdout():
    """Console output is a separate encoding from file I/O. On a Windows console with
    a legacy code page, printing a name like "Hale Ho'ola Hamakua" raises
    UnicodeEncodeError and kills an otherwise healthy run, so pin stdout too."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass



TIGER = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb'
BOUND = TIGER + '/State_County/MapServer/1/query'        # county polygons follow the coast
ROADS = TIGER + '/Transportation/MapServer/%d/query'     # 8 local, 6 secondary, 2 primary
ROAD_LAYERS = (8, 6, 2)


def get(url, tries=3):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=60))
        except Exception as e:
            if i == tries - 1:
                print('  ! %s' % str(e)[:70], file=sys.stderr)
                return None
            time.sleep(2)


def simp(coords, tol=0.00003):
    """Drop vertices closer than tol to the previous one, round to ~1 m.
    Cuts the embedded bundle by roughly a third with no visible difference."""
    o = [[round(coords[0][0], 5), round(coords[0][1], 5)]]
    for p in coords[1:]:
        if abs(p[0] - o[-1][0]) > tol or abs(p[1] - o[-1][1]) > tol:
            o.append([round(p[0], 5), round(p[1], 5)])
    if len(o) < 2:
        o = [[round(q[0], 5), round(q[1], 5)] for q in coords[:2]]
    return o


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--road-pad-m', type=float, default=450, help='minimum context around each point')
    ap.add_argument('--skip-boundaries', action='store_true')
    ap.add_argument('--skip-roads', action='store_true')
    a = ap.parse_args()

    W = a.work
    rows = json.load(open(os.path.join(W, 'verdict.json'), encoding='utf-8'))

    # ---------- boundaries for the overview chart ----------
    if not a.skip_boundaries:
        lats = [r['lat'] for r in rows]; lons = [r['lon'] for r in rows]
        pad = 0.35
        q = urllib.parse.urlencode({
            'geometry': '%f,%f,%f,%f' % (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad),
            'geometryType': 'esriGeometryEnvelope', 'inSR': 4326, 'outSR': 4326,
            'spatialRel': 'esriSpatialRelIntersects', 'outFields': 'NAME',
            'returnGeometry': 'true', 'geometryPrecision': 5, 'f': 'geojson'})
        print('fetching boundaries ...')
        d = get(BOUND + '?' + q)
        rings = []
        if d:
            for f in d.get('features', []):
                g = f.get('geometry') or {}
                polys = g.get('coordinates', []) if g.get('type') == 'MultiPolygon' else [g.get('coordinates', [])]
                for p in polys:
                    if not p:
                        continue
                    r = [[round(y, 5), round(x, 5)] for x, y in p[0]]
                    if len(r) > 8:
                        rings.append(r)
        # keep only rings that actually contain or neighbour our points
        keep = []
        for r in rings:
            rl = [q[0] for q in r]; ro = [q[1] for q in r]
            if any(min(rl) - 0.25 <= p['lat'] <= max(rl) + 0.25 and
                   min(ro) - 0.25 <= p['lon'] <= max(ro) + 0.25 for p in rows):
                keep.append(r)
        json.dump(keep, open(os.path.join(W, 'basemap.json'), 'w', encoding='utf-8'), separators=(',', ':'))
        print('  %d boundary rings (%d vertices)' % (len(keep), sum(len(r) for r in keep)))

    # ---------- street network per point ----------
    if not a.skip_roads:
        out_path = os.path.join(W, 'roads.json')
        out = json.load(open(out_path, encoding='utf-8')) if os.path.exists(out_path) else {}
        for r in rows:
            if str(r['idx']) in out:
                continue
            la = [r['lat'], r['fix_lat']]; lo = [r['lon'], r['fix_lon']]
            clat = sum(la) / 2
            k = math.cos(math.radians(clat)) or 1e-6
            padlat = max(a.road_pad_m / 110540.0, (max(la) - min(la)) * 0.55)
            padlon = max(a.road_pad_m / (111320.0 * k), (max(lo) - min(lo)) * 0.55)
            xmin, xmax = min(lo) - padlon, max(lo) + padlon
            ymin, ymax = min(la) - padlat, max(la) + padlat
            feats, seen = [], set()
            for layer in ROAD_LAYERS:
                q = urllib.parse.urlencode({
                    'geometry': '%f,%f,%f,%f' % (xmin, ymin, xmax, ymax),
                    'geometryType': 'esriGeometryEnvelope', 'inSR': 4326, 'outSR': 4326,
                    'spatialRel': 'esriSpatialRelIntersects', 'outFields': 'NAME',
                    'returnGeometry': 'true', 'geometryPrecision': 6, 'f': 'geojson'})
                d = get(ROADS % layer + '?' + q)
                for f in (d or {}).get('features', []):
                    g = f.get('geometry') or {}
                    if g.get('type') != 'LineString' or len(g['coordinates']) < 2:
                        continue
                    c = simp([[y, x] for x, y in g['coordinates']])
                    nm = f['properties'].get('NAME')
                    key = (nm, tuple(map(tuple, c[:3])))
                    if key in seen:        # the same road appears in several scale layers
                        continue
                    seen.add(key)
                    feats.append({'n': nm, 'c': c, 'l': layer})
                time.sleep(0.15)
            out[str(r['idx'])] = {'bbox': [ymin, ymax, xmin, xmax], 'roads': feats}
            json.dump(out, open(out_path, 'w', encoding='utf-8'), separators=(',', ':'))
            print('%3d %-42s %d segments' % (r['idx'], r['name'][:42], len(feats)), flush=True)
        print('\n%d road segments total -> %s' % (sum(len(v['roads']) for v in out.values()), out_path))


if __name__ == '__main__':
    main()
