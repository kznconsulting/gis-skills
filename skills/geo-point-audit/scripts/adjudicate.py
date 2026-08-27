#!/usr/bin/env python3
"""Turn evidence into verdicts, and write the findings brief a human reads.

Two things happen here that carry most of the audit's weight:

1. The footprint test outranks raw distance. A point on the correct building can
   still sit 210 m from the campus centre; a point 162 m out can be on a
   neighbour's lot. Distance alone would rank those backwards.
2. Error bearings are checked for a systematic shift. If every bad point moves
   the same way by the same amount, the finding is "your CRS handling is wrong",
   not "these points are wrong" - a completely different repair.

  uvx --with pyproj python adjudicate.py --work ./audit-work
"""
import argparse, csv, json, math, os, re, statistics, sys
from collections import Counter

def _utf8_stdout():
    """Console output is a separate encoding from file I/O. On a Windows console with
    a legacy code page, printing a name like "Hale Ho'ola Hamakua" raises
    UnicodeEncodeError and kills an otherwise healthy run, so pin stdout too."""
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


def bearing(a, b):
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = (math.cos(math.radians(a[0])) * math.sin(math.radians(b[0])) -
         math.sin(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(math.radians(b[1] - a[1])))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def consensus(cands, radius=250.0):
    """Agree by majority, not by average.

    Geocoders fail independently and occasionally badly - one will return a rooftop
    3 km away while the other three sit within 50 m of each other. Averaging lets
    that single bad answer drag the result, and the audit then reports a fake error
    with total confidence, which is the worst possible failure for this tool.

    So: cluster the candidates, keep the largest cluster, and ignore the rest.
    A footprint in the winning cluster breaks ties, because a building outline for
    the named facility is stronger evidence than any address interpolation.
    Returns (lat, lon, spread, kept, rejected).
    """
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][1][0], cands[0][1][1], 0.0, list(cands), []

    best = None
    for seed in cands:
        near = [c for c in cands if hav(seed[1], c[1]) <= radius]
        has_fp = any(c[0].startswith('OSM') for c in near)
        has_roof = any(c[0] == 'ArcGIS rooftop' for c in near)
        score = (len(near), has_fp, has_roof)
        if best is None or score > best[0]:
            best = (score, near)
    keep = best[1]
    rej = [c for c in cands if c not in keep]
    lat = statistics.mean([c[1][0] for c in keep])
    lon = statistics.mean([c[1][1] for c in keep])
    spread = max([hav((lat, lon), c[1]) for c in keep]) if len(keep) > 1 else 0.0
    return lat, lon, spread, keep, rej


def inpoly(pt, poly):
    x, y, ins, n = pt[1], pt[0], False, len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]; yj, xj = poly[j]
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


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--ok-consensus', type=float, default=150)
    ap.add_argument('--ok-edge', type=float, default=50)
    ap.add_argument('--marginal', type=float, default=350)
    a = ap.parse_args()

    W = a.work
    pts = json.load(open(os.path.join(W, 'points.json'), encoding='utf-8'))
    meta = json.load(open(os.path.join(W, 'meta.json'), encoding='utf-8'))
    ev = {e['idx']: e for e in json.load(open(os.path.join(W, 'evidence.json'), encoding='utf-8'))}
    fp_path = os.path.join(W, 'footprints.json')
    fps = json.load(open(fp_path, encoding='utf-8'))['matched'] if os.path.exists(fp_path) else {}

    rows = []
    for p in pts:
        e = ev.get(p['idx'], {})
        pt = (p['lat'], p['lon'])
        srcs = []
        for label, k in [('ArcGIS rooftop', 'arcgis'), ('Census TIGER', 'census'), ('Nominatim', 'nominatim')]:
            d = e.get(k)
            if d and (k != 'arcgis' or d.get('type') in (None, 'PointAddress', 'StreetAddress')):
                srcs.append((label, (d['lat'], d['lon']), d['m']))

        f = fps.get(str(p['idx']))
        geom, inside, edge, ref = None, None, None, None
        if f:
            if f.get('ring'):
                geom = 'polygon'
                inside = inpoly(pt, f['ring'])
                edge = round(edge_m(pt, f['ring']))
                c = (sum(q[0] for q in f['ring']) / len(f['ring']), sum(q[1] for q in f['ring']) / len(f['ring']))
            else:
                geom = 'node'
                c = (f['lat'], f['lon'])
            ref = c
            srcs.append(('OSM footprint' if geom == 'polygon' else 'OSM node', c, round(hav(pt, c))))

        if not srcs:
            rows.append(dict(idx=p['idx'], name=p['name'], name_raw=p['name_raw'], addr=p['addr'],
                             lat=p['lat'], lon=p['lon'], verdict='UNKNOWN', offset_m=0, spread_m=0,
                             fix_lat=p['lat'], fix_lon=p['lon'], inside=None, edge_m=None, geom=None,
                             at_point=None, at_name=None, at_type=None, sources=[], bearing=None,
                             rec=p['rec'], src_x=p['src_x'], src_y=p['src_y']))
            continue

        rlat, rlon, spread, kept, rejected = consensus(srcs)
        off = hav(pt, (rlat, rlon))
        keptset = {s[0] for s in kept}

        if inside or (edge is not None and edge <= a.ok_edge) or off < a.ok_consensus:
            v = 'OK'
        elif off < a.marginal:
            v = 'MINOR'
        else:
            v = 'WRONG'

        rv = e.get('reverse') or {}
        rows.append(dict(
            idx=p['idx'], name=p['name'], name_raw=p['name_raw'], addr=p['addr'],
            lat=p['lat'], lon=p['lon'], verdict=v, offset_m=round(off), spread_m=round(spread),
            fix_lat=round(rlat, 6), fix_lon=round(rlon, 6),
            inside=inside, edge_m=edge, geom=geom,
            at_point=rv.get('display'), at_name=rv.get('name'), at_type=rv.get('type'),
            sources=[{'src': s[0], 'lat': round(s[1][0], 6), 'lon': round(s[1][1], 6), 'm': s[2],
                      'used': s[0] in keptset} for s in srcs],
            n_agree=len(kept), n_reject=len(rejected),
            bearing=round(bearing(pt, (rlat, rlon))) if off > 20 else None,
            rec=p['rec'], src_x=p['src_x'], src_y=p['src_y']))

    # corrections back into the layer's own CRS
    if meta.get('crs_id') and meta['crs_id'] != 'EPSG:4326' and meta.get('crs_wkt'):
        try:
            from pyproj import CRS, Transformer
            dst = CRS.from_wkt(meta['crs_wkt']) if meta['crs_wkt'].strip().upper().startswith(
                ('PROJCS', 'GEOGCS', 'PROJCRS')) else CRS.from_user_input(meta['crs_wkt'])
            t = Transformer.from_crs(CRS.from_epsg(4326), dst, always_xy=True)
            for r in rows:
                x, y = t.transform(r['fix_lon'], r['fix_lat'])
                r['fix_x'], r['fix_y'] = round(x, 1), round(y, 1)
        except Exception as ex:
            print('CRS back-transform failed (%s); corrections are WGS 84 only' % str(ex)[:60])

    # ---- attribute defects: cheap to find, real, and worth reporting ----
    q = {}
    nbsp = [r['name'] for r in rows if ' ' in (r['name_raw'] or '')]
    if nbsp:
        q['nbsp'] = {'n': len(nbsp), 'of': len(rows), 'examples': nbsp[:4]}
    dup = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            d = hav((rows[i]['lat'], rows[i]['lon']), (rows[j]['lat'], rows[j]['lon']))
            if d < 120:
                dup.append({'a': rows[i]['name'], 'b': rows[j]['name'], 'm': round(d)})
    if dup:
        q['coincident'] = dup
    blanks = {}
    for fld in meta.get('fields', []):
        miss = [r['name'] for r in rows if r['rec'].get(fld) in (None, '', ' ', 0)]
        if len(miss) > len(rows) * 0.25:
            blanks[fld] = len(miss)
    if blanks:
        q['sparse_fields'] = blanks

    json.dump(rows, open(os.path.join(W, 'verdict.json'), 'w', encoding='utf-8'), indent=1)
    json.dump(q, open(os.path.join(W, 'quality.json'), 'w', encoding='utf-8'), indent=1)

    # utf-8-sig so Excel on Windows reads accented names correctly rather than as mojibake
    with open(os.path.join(W, 'corrections.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
        wr = csv.writer(fh)
        head = ['name', 'current_lat', 'current_lon', 'corrected_lat', 'corrected_lon']
        if 'fix_x' in rows[0]:
            head += ['corrected_x', 'corrected_y']
        head += ['offset_m', 'verdict']
        wr.writerow(head)
        for r in sorted([x for x in rows if x['verdict'] not in ('OK', 'UNKNOWN')], key=lambda r: -r['offset_m']):
            line = [r['name'], r['lat'], r['lon'], r['fix_lat'], r['fix_lon']]
            if 'fix_x' in r:
                line += [r['fix_x'], r['fix_y']]
            wr.writerow(line + [r['offset_m'], r['verdict']])

    # ---- findings brief ----
    c = Counter(r['verdict'] for r in rows)
    bad = sorted([r for r in rows if r['verdict'] in ('WRONG', 'MINOR')], key=lambda r: -r['offset_m'])
    brs = [r['bearing'] for r in bad if r['bearing'] is not None]
    systematic = False
    if len(brs) >= 4:
        mx = statistics.mean([math.cos(math.radians(b)) for b in brs])
        my = statistics.mean([math.sin(math.radians(b)) for b in brs])
        offs = [r['offset_m'] for r in bad]
        systematic = math.hypot(mx, my) > 0.75 and (max(offs) - min(offs)) < 0.35 * statistics.mean(offs)

    L = []
    L.append('# Findings: %s\n' % meta['source_file'])
    L.append('- %d points | source CRS %s (%s)' % (len(rows), meta['crs_name'], meta['crs_id']))
    L.append('- **%d correct, %d marginal, %d wrong**%s\n' % (
        c['OK'], c['MINOR'], c['WRONG'], ', %d unresolved' % c['UNKNOWN'] if c['UNKNOWN'] else ''))
    if systematic:
        L.append('> **SYSTEMATIC SHIFT.** Error bearings cluster and magnitudes are similar. Treat this as a\n'
                 '> datum/projection fault in the layer, not per-point digitising error. Report it that way.\n')
    else:
        L.append('- Error bearings scatter (%s) -> per-point digitising error, not a datum fault.\n'
                 '  The reprojection is sound; only these records need correcting.\n' %
                 ', '.join('%d deg' % b for b in brs[:8]))
    if bad:
        L.append('## Points needing correction\n')
        L.append('| Offset | Name | Verdict | Physically at the recorded point | Footprint |')
        L.append('|---|---|---|---|---|')
        for r in bad:
            at = r['at_name'] or (r['at_type'] or '').replace('_', ' ') or '?'
            fpt = ('inside' if r['inside'] else ('%d m outside' % r['edge_m'] if r['edge_m'] is not None
                                                 else 'no outline'))
            off = '%.2f km' % (r['offset_m'] / 1000) if r['offset_m'] >= 1000 else '%d m' % r['offset_m']
            L.append('| %s | %s | %s | %s | %s |' % (off, r['name'], r['verdict'], at, fpt))
    near = [r for r in rows if r['verdict'] == 'OK' and r['offset_m'] > a.ok_consensus]
    if near:
        L.append('\n## Passed on the footprint test despite a large offset\n')
        L.append('These are why the report needs its caption: the offset looks bad, the point is on the building.\n')
        for r in near:
            L.append('- %s: %d m offset, but %s' % (
                r['name'], r['offset_m'], 'inside the outline' if r['inside'] else '%d m from the outline' % r['edge_m']))
    if q:
        L.append('\n## Attribute defects\n')
        if 'nbsp' in q:
            L.append('- **U+00A0 (non-breaking space) in %d of %d names** - breaks every exact match and join. e.g. %s'
                     % (q['nbsp']['n'], q['nbsp']['of'], ', '.join(q['nbsp']['examples'][:2])))
        for d in q.get('coincident', []):
            L.append('- **Coincident points**: "%s" and "%s" are %d m apart in the data - check whether that is real.'
                     % (d['a'], d['b'], d['m']))
        if 'sparse_fields' in q:
            L.append('- Sparse fields: ' + ', '.join('`%s` blank on %d' % (k, v)
                                                     for k, v in q['sparse_fields'].items()))
    L.append('\n## Next\n')
    L.append('Write `%s/copy.json` (title, eyebrow, h1, standfirst, subject, notes[]) using the specifics above,\n'
             'then run `build_report.py`. Name real things: "sits on a delicatessen" persuades, "478 m offset" does not.'
             % W)
    open(os.path.join(W, 'findings.md'), 'w', encoding='utf-8').write('\n'.join(L))

    print('\n'.join(L[:40]))
    print('\nwrote verdict.json, quality.json, corrections.csv, findings.md')


if __name__ == '__main__':
    main()
