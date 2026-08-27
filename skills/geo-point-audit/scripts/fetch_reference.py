#!/usr/bin/env python3
"""Fetch building/site outlines from OpenStreetMap for the point-in-polygon test.

The footprint is the strongest evidence in the whole audit: a point either falls
on the building or it does not, and that beats any distance measurement.

Two things keep this cheap enough that the public Overpass servers will actually
serve it:

1. Points are clustered and each cluster gets its own small bounding box. Overpass
   cost scales with the area scanned, so one box around a whole archipelago is
   mostly ocean and enormously expensive, while a dozen small boxes are trivial.
   Small queries also fail independently, so one bad response costs one cluster.
2. The name search runs only for points the tag query missed, inside that point's
   own box. A case-insensitive regex over every named object in a large area is one
   of the most expensive things you can ask Overpass to do; running it upfront for
   everything, everywhere, is what makes a query get rejected.

Name matching is deliberately strict. Loose fuzzy matching once paired
"Ka'u Hospital" with "Kauai Veterans Memorial Hospital" and produced a confident
530 km error, so anything short of a real match is reported as unmatched and left
for a human to resolve in name_map.json rather than guessed at.

  uvx python fetch_reference.py --work ./audit-work --osm-filter 'amenity=hospital'
"""
import argparse, json, math, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

def _utf8_stdout():
    """Console output is a separate encoding from file I/O. On a Windows console with
    a legacy code page, printing a name like "Hale Ho'ola Hamakua" raises
    UnicodeEncodeError and kills an otherwise healthy run, so pin stdout too."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass



MIRRORS = ['https://overpass-api.de/api/interpreter',
           'https://overpass.kumi.systems/api/interpreter',
           'https://overpass.private.coffee/api/interpreter']
UA = {'User-Agent': 'geo-point-audit/1.0 (positional accuracy QA)'}

STOP = {'hospital', 'medical', 'center', 'centre', 'memorial', 'community', 'clinic', 'the', 'of',
        'and', 'for', 'school', 'elementary', 'high', 'middle', 'library', 'station', 'department',
        'health', 'services', 'inc', 'llc', 'co', 'company', 'general'}


def fold(s):
    """Normalise away the differences that are never meaningful: case, the ʻokina and
    other apostrophes, accents, punctuation, and trailing plurals. "KAU HOSPITAL" and
    "Ka'u Hospital" are the same place; "Shriners Hospital" and "Shriners Hospitals"
    are too. Comparing folded full names is safe because nothing is discarded except
    typography - unlike key() below, which drops whole words."""
    s = unicodedata.normalize('NFKD', (s or '').replace('’', "'").replace('ʻ', "'").replace('`', "'"))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['‘’]", '', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    return ' '.join(re.sub(r's$', '', t) for t in s.split() if t)


def key(s):
    """Aggressive reduction to the distinctive words only. Powerful but dangerous:
    it can collapse two different places to the same string, so results are used
    only when exactly one candidate matches."""
    return ' '.join(t for t in fold(s).split() if t not in STOP)


def overpass(query, quiet=False):
    """Query Overpass, backing off politely rather than hammering.

    Splitting one huge query into many small ones trades area for request count, and
    Overpass rate-limits on concurrent slots per IP, not on area. So a 429 means
    "wait your turn" - the right response is to wait longer on the SAME server, not
    to stampede the mirrors, which just spends everyone's goodwill faster. Only a
    real failure (5xx, refused, malformed) is worth moving on for."""
    msg = 'no attempt'
    for ep in MIRRORS:
        delay = 5
        for attempt in range(4):
            try:
                req = urllib.request.Request(ep, data=urllib.parse.urlencode({'data': query}).encode(),
                                             headers=UA)
                body = urllib.request.urlopen(req, timeout=120).read()
                if body[:1] not in (b'{', b'['):
                    # Overpass answers overload with an HTML error page under HTTP 200,
                    # which json.load reports as "Expecting value: line 1 column 1"
                    raise ValueError('non-JSON response (server busy)')
                return json.loads(body.decode())
            except urllib.error.HTTPError as e:
                msg = 'HTTP %s' % e.code
                if e.code in (429, 504):          # slot exhausted or timed out: wait, do not switch
                    if not quiet:
                        print('    rate limited, waiting %ds' % delay, file=sys.stderr, flush=True)
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                break                              # any other HTTP error: try the next mirror
            except Exception as e:
                msg = str(e)[:70]
                time.sleep(3)
        if not quiet:
            print('  mirror failed: %s (%s)' % (ep.split('/')[2], msg), file=sys.stderr)
    return None


def ring(e):
    if e['type'] == 'way' and 'geometry' in e:
        return [[round(p['lat'], 6), round(p['lon'], 6)] for p in e['geometry']]
    if e['type'] == 'relation':
        pts = []
        for m in e.get('members', []):
            if m.get('role') in ('outer', '') and 'geometry' in m:
                pts += [[round(p['lat'], 6), round(p['lon'], 6)] for p in m['geometry']]
        return pts or None
    return None


def cluster_points(pts, link, max_span):
    """Group points that sit near each other so they can share one small query.

    Single-linkage against each cluster's growing bbox, capped so a chain of points
    can never grow back into one continent-sized box - which is the failure this
    whole function exists to prevent."""
    clusters = []
    for p in sorted(pts, key=lambda p: (p['lat'], p['lon'])):
        for c in clusters:
            if (c['lat0'] - link <= p['lat'] <= c['lat1'] + link and
                    c['lon0'] - link <= p['lon'] <= c['lon1'] + link):
                la0, la1 = min(c['lat0'], p['lat']), max(c['lat1'], p['lat'])
                lo0, lo1 = min(c['lon0'], p['lon']), max(c['lon1'], p['lon'])
                if (la1 - la0) <= max_span and (lo1 - lo0) <= max_span:
                    c.update(lat0=la0, lat1=la1, lon0=lo0, lon1=lo1)
                    c['members'].append(p)
                    break
        else:
            clusters.append({'lat0': p['lat'], 'lat1': p['lat'],
                             'lon0': p['lon'], 'lon1': p['lon'], 'members': [p]})
    return clusters


def collect(d, into):
    """Fold an Overpass response into the name -> feature table, preferring a real
    polygon over a bare node when the same name turns up twice."""
    for e in (d or {}).get('elements', []):
        t = e.get('tags', {}) or {}
        n = t.get('name')
        if not n:
            continue
        r = ring(e)
        if r and len(r) > 2:
            lat = sum(p[0] for p in r) / len(r)
            lon = sum(p[1] for p in r) / len(r)
        else:
            r = None
            lat = e.get('lat') or (e.get('center') or {}).get('lat')
            lon = e.get('lon') or (e.get('center') or {}).get('lon')
        if lat is None:
            continue
        f = {'name': n, 'osm': '%s/%s' % (e['type'], e['id']), 'ring': r,
             'lat': round(lat, 6), 'lon': round(lon, 6),
             'street': t.get('addr:street'), 'hn': t.get('addr:housenumber')}
        prev = into.get(n)
        if prev is None or (prev['ring'] is None and r is not None):
            into[n] = f


def resolve(p, best, manual):
    """Find the one reference feature that is genuinely this point's, or nothing."""
    target = manual.get(p['name'])
    if target and best.get(target):
        return best[target]
    if best.get(p['name']):
        return best[p['name']]
    fp = fold(p['name'])
    hits = [f for f in best.values() if fp and fold(f['name']) == fp]
    if len(hits) == 1:
        return hits[0]
    kp = key(p['name'])
    hits = [f for f in best.values() if kp and key(f['name']) == kp]
    if len(hits) == 1:
        return hits[0]
    # containment only when unambiguous AND the shorter side is substantial,
    # which is what stops "ka u" matching "kauai veterans"
    hits = [f for f in best.values()
            if kp and len(kp) >= 5 and (kp in key(f['name']) or key(f['name']) in kp)]
    return hits[0] if len(hits) == 1 else None


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--osm-filter', default='amenity=hospital',
                    help="OSM tag for this kind of place, e.g. amenity=school, shop=supermarket")
    ap.add_argument('--pad', type=float, default=0.02,
                    help='degrees of context around each cluster (default ~2 km)')
    ap.add_argument('--link', type=float, default=0.05,
                    help='degrees within which points share a cluster (default ~5 km)')
    ap.add_argument('--max-span', type=float, default=0.5,
                    help='largest cluster bbox side in degrees, to cap query cost')
    ap.add_argument('--from-file', help='raw Overpass JSON to match against instead of querying '
                                        '(re-run name matching offline, or supply your own reference layer)')
    a = ap.parse_args()

    pts = json.load(open(os.path.join(a.work, 'points.json'), encoding='utf-8'))
    k, v = a.osm_filter.split('=', 1)
    map_path = os.path.join(a.work, 'name_map.json')
    manual = json.load(open(map_path, encoding='utf-8')) if os.path.exists(map_path) else {}
    best, failed = {}, []

    if a.from_file:
        print('reading reference features from %s' % a.from_file)
        collect(json.load(open(a.from_file, encoding='utf-8')), best)
    else:
        clusters = cluster_points(pts, a.link, a.max_span)
        print('%d points -> %d cluster(s); querying %s=%s per cluster' % (len(pts), len(clusters), k, v))
        for i, c in enumerate(clusters, 1):
            bb = '%f,%f,%f,%f' % (c['lat0'] - a.pad, c['lon0'] - a.pad,
                                  c['lat1'] + a.pad, c['lon1'] + a.pad)
            q = '[out:json][timeout:90];nwr["%s"="%s"](%s);out geom tags;' % (k, v, bb)
            d = overpass(q)
            if d is None:
                failed.append(c)
                print('  %2d/%d  %2d point(s)  QUERY FAILED' % (i, len(clusters), len(c['members'])))
                continue
            before = len(best)
            collect(d, best)
            print('  %2d/%d  %2d point(s)  %d feature(s)' % (
                i, len(clusters), len(c['members']), len(best) - before), flush=True)
            time.sleep(2.5)

    matched, unmatched = {}, []
    for p in pts:
        f = resolve(p, best, manual)
        if f:
            matched[str(p['idx'])] = f
        else:
            unmatched.append(p)

    # Second pass: only the points the tag query missed, and only in their own small box.
    # Some facilities carry healthcare=/social_facility= instead of the expected amenity tag,
    # so they are invisible to a tag query but findable by name.
    if unmatched and not a.from_file:
        print('\n%d point(s) unmatched - searching by name in a small box around each' % len(unmatched))
        still = []
        for p in unmatched:
            nm = re.sub(r'["\\]', '', p['name'].split(',')[0])[:48].strip()
            if not nm:
                still.append(p)
                continue
            bb = '%f,%f,%f,%f' % (p['lat'] - a.pad, p['lon'] - a.pad, p['lat'] + a.pad, p['lon'] + a.pad)
            q = '[out:json][timeout:60];nwr["name"~"%s",i](%s);out geom tags;' % (re.escape(nm), bb)
            d = overpass(q, quiet=True)
            collect(d, best)
            f = resolve(p, best, manual)
            if f:
                matched[str(p['idx'])] = f
                print('  found: %s -> %s' % (p['name'][:40], f['name']))
            else:
                still.append(p)
            time.sleep(2.5)
        unmatched = still

    json.dump({'features': list(best.values()), 'matched': matched},
              open(os.path.join(a.work, 'footprints.json'), 'w', encoding='utf-8'), indent=1)
    npoly = sum(1 for m in matched.values() if m['ring'])
    print('\nmatched %d of %d points (%d with a real outline)' % (len(matched), len(pts), npoly))

    if failed:
        n = sum(len(c['members']) for c in failed)
        print('\n%d cluster(s) covering %d point(s) failed. Re-run to retry just those - '
              'matched points are already saved.' % (len(failed), n))
    if unmatched:
        print('\nUNMATCHED - add these to %s as {"<point name>": "<osm name>"} and re-run:' % map_path)
        for p in unmatched:
            print('   ', p['name'])
        near = sorted({f['name'] for f in best.values()})
        print('\nreference names available (%d):' % len(near))
        for n in near[:60]:
            print('   ', n)
        if not os.path.exists(map_path):
            json.dump({p['name']: '' for p in unmatched}, open(map_path, 'w', encoding='utf-8'), indent=1)
            print('\nstub written to %s' % map_path)


if __name__ == '__main__':
    main()
