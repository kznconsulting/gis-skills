#!/usr/bin/env python3
"""Load any point layer into the audit work directory.

Accepts an ESRI shapefile (dir or .shp), GeoJSON, or CSV. Reprojects to WGS 84
while keeping the source CRS, so corrections can be handed back in the
coordinates the layer actually uses.

  uvx --with pyshp --with pyproj python load_points.py <input> --work ./audit-work
"""
import argparse, csv, json, os, re, sys, unicodedata

def _utf8_stdout():
    """Console output is a separate encoding from file I/O. On a Windows console with
    a legacy code page, printing a name like "Hale Ho'ola Hamakua" raises
    UnicodeEncodeError and kills an otherwise healthy run, so pin stdout too."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass



NAME_HINTS = ['name', 'facility', 'title', 'label', 'site', 'place', 'location_name']
ADDR_HINTS = ['physical_a', 'physical', 'address', 'addr', 'street', 'location', 'site_addr']


def clean(s):
    """Normalise whitespace but REMEMBER that we did - NBSP in source names is a
    finding worth reporting, so the raw value is kept alongside."""
    if s is None:
        return None
    s = str(s)
    s = s.replace(' ', ' ').replace('’', "'")
    return re.sub(r'\s+', ' ', s).strip()


def pick(fields, hints):
    low = {f.lower(): f for f in fields}
    for h in hints:
        if h in low:
            return low[h]
    for h in hints:
        for f in fields:
            if h in f.lower():
                return f
    return None


def read_shapefile(path):
    import shapefile
    base = path[:-4] if path.lower().endswith('.shp') else path
    if os.path.isdir(base):
        cands = [f for f in os.listdir(base) if f.lower().endswith('.shp')]
        if not cands:
            sys.exit('no .shp found in %s' % base)
        base = os.path.join(base, cands[0])[:-4]
    r = shapefile.Reader(base)
    fields = [f[0] for f in r.fields[1:]]
    rows, geom = [], []
    for sr in r.iterShapeRecords():
        if not sr.shape.points:
            continue
        geom.append(tuple(sr.shape.points[0]))
        rows.append(dict(zip(fields, sr.record)))
    prj = base + '.prj'
    wkt = open(prj, encoding='utf-8').read().strip() if os.path.exists(prj) else None
    return fields, rows, geom, wkt, os.path.basename(base) + '.shp'


def read_geojson(path):
    d = json.load(open(path, encoding='utf-8'))
    feats = d.get('features', d if isinstance(d, list) else [])
    rows, geom, fields = [], [], []
    for f in feats:
        g = f.get('geometry') or {}
        if g.get('type') != 'Point':
            continue
        p = f.get('properties', {})
        for k in p:
            if k not in fields:
                fields.append(k)
        rows.append(p)
        geom.append((g['coordinates'][0], g['coordinates'][1]))
    return fields, rows, geom, 'EPSG:4326', os.path.basename(path)


def read_csv(path, latf=None, lonf=None):
    rows = list(csv.DictReader(open(path, newline='', encoding='utf-8-sig')))
    if not rows:
        sys.exit('empty csv')
    fields = list(rows[0].keys())
    latf = latf or pick(fields, ['latitude', 'lat', 'y'])
    lonf = lonf or pick(fields, ['longitude', 'lon', 'lng', 'long', 'x'])
    if not latf or not lonf:
        sys.exit('could not find lat/lon columns; pass --lat-field/--lon-field')
    geom = [(float(r[lonf]), float(r[latf])) for r in rows]
    return fields, rows, geom, 'EPSG:4326', os.path.basename(path)


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--name-field')
    ap.add_argument('--addr-field')
    ap.add_argument('--lat-field')
    ap.add_argument('--lon-field')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)

    p = a.input.rstrip('/')
    if p.lower().endswith(('.geojson', '.json')):
        fields, rows, geom, crs, src = read_geojson(p)
    elif p.lower().endswith(('.csv', '.tsv')):
        fields, rows, geom, crs, src = read_csv(p, a.lat_field, a.lon_field)
    else:
        fields, rows, geom, crs, src = read_shapefile(p)

    namef = a.name_field or pick(fields, NAME_HINTS)
    addrf = a.addr_field or pick(fields, ADDR_HINTS)
    if not namef:
        sys.exit('could not identify a name column in %s; pass --name-field' % fields)

    # reproject to WGS 84, keeping the source CRS for the correction output
    if crs and crs != 'EPSG:4326':
        from pyproj import CRS, Transformer
        src_crs = CRS.from_wkt(crs) if crs.strip().upper().startswith(('PROJCS', 'GEOGCS', 'PROJCRS')) else CRS.from_user_input(crs)
        t = Transformer.from_crs(src_crs, CRS.from_epsg(4326), always_xy=True)
        lonlat = [t.transform(x, y) for x, y in geom]
        try:
            epsg = src_crs.to_epsg()
        except Exception:
            epsg = None
        crs_name = src_crs.name
        crs_id = 'EPSG:%d' % epsg if epsg else crs_name
    else:
        lonlat = geom
        crs_name, crs_id = 'WGS 84', 'EPSG:4326'

    pts = []
    for i, (rec, (lon, lat), (sx, sy)) in enumerate(zip(rows, lonlat, geom)):
        raw_name = rec.get(namef)
        pts.append({
            'idx': i,
            'name': clean(raw_name) or '(unnamed %d)' % i,
            'name_raw': str(raw_name) if raw_name is not None else '',
            'addr': clean(rec.get(addrf)) if addrf else None,
            'lat': round(lat, 6), 'lon': round(lon, 6),
            'src_x': sx, 'src_y': sy,
            'rec': {k: (v if isinstance(v, (int, float, type(None))) else str(v)) for k, v in rec.items()},
        })

    meta = {'source_file': src, 'crs_name': crs_name, 'crs_id': crs_id, 'crs_wkt': crs,
            'count': len(pts), 'name_field': namef, 'addr_field': addrf, 'fields': fields}
    json.dump(pts, open(os.path.join(a.work, 'points.json'), 'w', encoding='utf-8'), indent=1)
    json.dump(meta, open(os.path.join(a.work, 'meta.json'), 'w', encoding='utf-8'), indent=1)

    print('%d points -> %s' % (len(pts), os.path.join(a.work, 'points.json')))
    print('source CRS : %s (%s)' % (crs_name, crs_id))
    print('name field : %s' % namef)
    print('addr field : %s' % (addrf or 'NONE - geocoding will be skipped, verdicts rest on footprints alone'))
    nbsp = sum(1 for p in pts if ' ' in p['name_raw'])
    if nbsp:
        print('note       : %d of %d names contain U+00A0 (report this)' % (nbsp, len(pts)))
    for p in pts[:3]:
        print('  ', p['lat'], p['lon'], '|', p['name'], '|', p['addr'])


if __name__ == '__main__':
    main()
