#!/usr/bin/env python3
"""Test each point against parcel (property) boundaries, and propose a point inside one.

Why this exists, in the words of the person who hit it: a hospital point was marked
correct at ~110 m because it sat close to the building - but it was in the road
right-of-way, not ON the parcel. Any downstream process that joins points to parcels
drops that facility entirely, and the audit said it was fine.

That is a different question from the one the building-footprint test answers. "Near
the right building" is enough to draw a dot on a map; "inside the property boundary"
is what a parcel join, a tax roll, a service-area analysis, or a permit lookup needs.
On the 29-point reference layer, 7 of the 19 points that passed reverse-geocode to a
street rather than a site, so this is the common failure, not the exotic one.

Parcels are county data with no national source, so bring your own file - a shapefile
or GeoJSON of polygons. Everything else in the pipeline still works without it; this
adds a stricter test when you have the boundaries to apply it.

  uvx --with pyshp --with pyproj python check_parcels.py --work ./audit-work \\
      --parcels ~/data/parcels.shp
"""
import argparse, json, math, os, sys


def _utf8_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def hav(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def inpoly(pt, poly):
    x, y, ins, n = pt[1], pt[0], False, len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if ((xi > x) != (xj > x)) and (y < (yj - yi) * (x - xi) / ((xj - xi) or 1e-12) + yi):
            ins = not ins
        j = i
    return ins


def edge_m(pt, poly):
    best, k = 1e12, math.cos(math.radians(pt[0]))
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ax, ay = (a[1] - pt[1]) * k * 111320, (a[0] - pt[0]) * 110540
        bx, by = (b[1] - pt[1]) * k * 111320, (b[0] - pt[0]) * 110540
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def interior_point(poly):
    """A point guaranteed to be inside the parcel, as far from the edge as practical.

    The centroid of an L-shaped or crescent parcel can fall outside it, and a point
    barely inside the boundary is fragile - a later reprojection or rounding can push
    it back out. So: use the centroid when it is safely inside, otherwise grid-search
    for the interior point furthest from any edge (a coarse pole of inaccessibility).
    """
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    c = (sum(lats) / len(lats), sum(lons) / len(lons))
    if inpoly(c, poly) and edge_m(c, poly) > 5:
        return c, round(edge_m(c, poly))
    best, best_d = None, -1.0
    for i in range(1, 24):
        for j in range(1, 24):
            p = (min(lats) + (max(lats) - min(lats)) * i / 24.0,
                 min(lons) + (max(lons) - min(lons)) * j / 24.0)
            if not inpoly(p, poly):
                continue
            d = edge_m(p, poly)
            if d > best_d:
                best, best_d = p, d
    return (best, round(best_d)) if best else (c, 0)


def load_parcels(path, bbox, id_field=None):
    """Load polygons, keeping only those near our points - a county parcel file can hold
    millions, and we only care about the handful around each audited site."""
    s, n, w, e = bbox
    out = []

    def keep(ring, props):
        rl = [p[0] for p in ring]
        ro = [p[1] for p in ring]
        if max(rl) < s or min(rl) > n or max(ro) < w or min(ro) > e:
            return
        pid = None
        if id_field and props.get(id_field) is not None:
            pid = str(props[id_field])
        else:
            for k in ('APN', 'apn', 'PARCELID', 'PARCEL_ID', 'TMK', 'tmk', 'PIN', 'OBJECTID'):
                if props.get(k) is not None:
                    pid = str(props[k])
                    break
        out.append({'id': pid, 'ring': ring})

    if path.lower().endswith(('.geojson', '.json')):
        d = json.load(open(path, encoding='utf-8'))
        for f in d.get('features', []):
            g = f.get('geometry') or {}
            props = f.get('properties') or {}
            polys = g.get('coordinates') or []
            if g.get('type') == 'Polygon':
                polys = [polys]
            elif g.get('type') != 'MultiPolygon':
                continue
            for p in polys:
                if p and p[0]:
                    keep([[y, x] for x, y in p[0]], props)
    else:
        import shapefile
        base = path[:-4] if path.lower().endswith('.shp') else path
        r = shapefile.Reader(base)
        fields = [f[0] for f in r.fields[1:]]
        # reproject if the parcel file is not already lon/lat
        tr = None
        prj = base + '.prj'
        if os.path.exists(prj):
            wkt = open(prj, encoding='utf-8').read().strip()
            if 'PROJCS' in wkt.upper():
                from pyproj import CRS, Transformer
                tr = Transformer.from_crs(CRS.from_wkt(wkt), CRS.from_epsg(4326), always_xy=True)
        for sr in r.iterShapeRecords():
            sh = sr.shape
            if not sh.points:
                continue
            props = dict(zip(fields, sr.record))
            parts = list(sh.parts) + [len(sh.points)]
            for i in range(len(parts) - 1):
                seg = sh.points[parts[i]:parts[i + 1]]
                if len(seg) < 4:
                    continue
                ring = [tr.transform(x, y) for x, y in seg] if tr else seg
                keep([[p[1], p[0]] for p in ring], props)
    return out


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--parcels', required=True, help='shapefile or GeoJSON of parcel polygons')
    ap.add_argument('--id-field', help='attribute holding the parcel id (APN/TMK/PIN); auto-detected otherwise')
    ap.add_argument('--pad', type=float, default=0.01, help='degrees of parcels to load around the points')
    a = ap.parse_args()

    W = a.work
    rows = json.load(open(os.path.join(W, 'verdict.json'), encoding='utf-8'))
    lats = [r['lat'] for r in rows] + [r['fix_lat'] for r in rows]
    lons = [r['lon'] for r in rows] + [r['fix_lon'] for r in rows]
    bbox = (min(lats) - a.pad, max(lats) + a.pad, min(lons) - a.pad, max(lons) + a.pad)

    print('loading parcels from %s ...' % a.parcels)
    parcels = load_parcels(a.parcels, bbox, a.id_field)
    print('  %d parcel polygon(s) in range' % len(parcels))
    if not parcels:
        sys.exit('No parcels overlap the audited points. Check the file covers this area.')

    out = {}
    for r in rows:
        rec = (r['lat'], r['lon'])
        tru = (r['fix_lat'], r['fix_lon'])
        # The facility's parcel is the one containing its TRUE location. Anchoring on the
        # recorded point would be circular: a point in the road belongs to no parcel, and
        # we would learn nothing.
        site = next((p for p in parcels if inpoly(tru, p['ring'])), None)
        e = {'parcel_id': site['id'] if site else None,
             'site_parcel_found': bool(site),
             'recorded_inside': False, 'recorded_in_any': False,
             'edge_m': None, 'suggest': None, 'clearance_m': None}
        e['recorded_in_any'] = any(inpoly(rec, p['ring']) for p in parcels)
        if site:
            e['recorded_inside'] = inpoly(rec, site['ring'])
            e['edge_m'] = round(edge_m(rec, site['ring']))
            if not e['recorded_inside']:
                pt, clear = interior_point(site['ring'])
                e['suggest'] = [round(pt[0], 6), round(pt[1], 6)]
                e['clearance_m'] = clear
                e['move_m'] = round(hav(rec, pt))
        out[str(r['idx'])] = e

    json.dump(out, open(os.path.join(W, 'parcels.json'), 'w', encoding='utf-8'), indent=1)

    known = [r for r in rows if out[str(r['idx'])]['site_parcel_found']]
    outside = [r for r in known if not out[str(r['idx'])]['recorded_inside']]
    passed_but_outside = [r for r in outside if r['verdict'] == 'OK']

    print('\n%d of %d points have an identifiable site parcel' % (len(known), len(rows)))
    print('%d of those sit OUTSIDE it' % len(outside))
    if passed_but_outside:
        print('\n%d point(s) passed the footprint test but fall outside the parcel.\n'
              'These are the ones a parcel join would silently drop:' % len(passed_but_outside))
        for r in sorted(passed_but_outside, key=lambda r: -r['offset_m']):
            e = out[str(r['idx'])]
            print('  %-42s %4d m from the building, %4d m outside the parcel; move %d m to get inside'
                  % (r['name'][:42], r['offset_m'], e['edge_m'], e.get('move_m', 0)))
    print('\nwrote %s - re-run adjudicate.py to fold this into the verdicts' % os.path.join(W, 'parcels.json'))


if __name__ == '__main__':
    main()
