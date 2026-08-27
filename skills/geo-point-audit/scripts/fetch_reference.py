#!/usr/bin/env python3
"""Fetch building/site outlines from OpenStreetMap for the point-in-polygon test.

The footprint is the strongest evidence in the whole audit: a point either falls
on the building or it does not, and that beats any distance measurement.

Three things keep this cheap enough that the public Overpass servers will actually
serve it, without sacrificing recall:

1. Points are clustered and each cluster gets its own small bounding box. Overpass
   cost scales with the area scanned, so one box around a whole archipelago is
   mostly ocean and enormously expensive, while a dozen small boxes are trivial.
   Small queries also fail independently, so one bad response costs one cluster.
2. A cluster's box covers both the recorded coordinates and their geocoded addresses,
   because a badly misplaced point's real building sits near the address, not near
   the bad coordinate. Boxes drawn around recorded positions alone would miss the
   reference geometry for exactly the points the audit most needs to judge.
3. An empty result widens the box and retries, up to --max-pad. Most clusters answer
   on the first try, so the extra cost is paid only where it buys something.

The name search runs only for points the tag query missed. A case-insensitive regex
over every named object in a large area is one of the most expensive things you can
ask Overpass to do; running it upfront for everything is what gets a query rejected.

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



# Global instances only. Never add a regional one (overpass.osm.ch, and most national
# instances): they answer HTTP 200 with zero results for anything outside their extract,
# which is indistinguishable from "nothing is mapped here" and would silently report
# every point as having no outline. A wrong answer delivered confidently is far worse
# than an outage. Note kumi.systems announces private.coffee as its endpoint - they are
# one backend, so the real redundancy here is two instances, not three.
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


# Once every mirror has failed, they are down for everyone, not just for that one
# bounding box. Re-discovering that per cluster costs ~20s each and hammers servers
# that are already struggling, so the first total failure trips this and the rest of
# the run goes straight to the fallback.
_OVERPASS_DOWN = [False]


def overpass(query, quiet=False):
    """Query Overpass, backing off politely rather than hammering.

    Splitting one huge query into many small ones trades area for request count, and
    Overpass rate-limits on concurrent slots per IP, not on area. So a 429 means
    "wait your turn" - the right response is to wait longer on the SAME server, not
    to stampede the mirrors, which just spends everyone's goodwill faster. Only a
    real failure (5xx, refused, malformed) is worth moving on for."""
    if _OVERPASS_DOWN[0]:
        return None
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
    _OVERPASS_DOWN[0] = True
    return None


MAX_SPLIT_DEPTH = 3          # up to 64 sub-boxes; ample for a dense city centre


def _subdivide(south, west, north, east, quiet, depth, why):
    """Quarter a box and merge the results. The API's own advice for an overflow."""
    if not quiet and depth == 0:
        print('    box %s - splitting into quarters' % why, file=sys.stderr, flush=True)
    mid_lat, mid_lon = (south + north) / 2.0, (west + east) / 2.0
    quads = [(south, west, mid_lat, mid_lon), (south, mid_lon, mid_lat, east),
             (mid_lat, west, north, mid_lon), (mid_lat, mid_lon, north, east)]
    merged = []
    for q in quads:
        sub = osm_api('%f,%f,%f,%f' % q, quiet=quiet, depth=depth + 1)
        if sub:
            merged.extend(sub['elements'])
        time.sleep(3)
    return {'elements': merged} if merged else None


def osm_api(bb, quiet=False, depth=0, tries=3):
    """Fall back to the OSM core API - the service behind openstreetmap.org itself.

    This is separate infrastructure from Overpass and markedly more reliable, because it
    is core OSM rather than a volunteer-run query engine. Overpass was unavailable across
    every mirror for hours while this skill was written; the core API served the same data
    throughout.

    The tradeoff is that it cannot filter server-side, so it returns everything in the box
    and we filter here. That is only affordable because the boxes are already small - which
    is another reason the clustering matters. API limits: 0.25 square degrees and 50k nodes,
    and a dense city centre will breach the node cap even in a small box, so an overflow
    subdivides rather than giving up.
    """
    south, west, north, east = [float(x) for x in bb.split(',')]
    if (north - south) * (east - west) > 0.24 and depth < MAX_SPLIT_DEPTH:
        return _subdivide(south, west, north, east, quiet, depth, 'over the 0.25 sq deg limit')
    url = 'https://api.openstreetmap.org/api/0.6/map?bbox=%f,%f,%f,%f' % (west, south, east, north)
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
    except urllib.error.HTTPError as ex:
        # 400 here is nearly always "You requested too many nodes (limit is 50000)" - a
        # dense area rather than a malformed request. Quartering is the documented remedy.
        if ex.code == 400 and depth < MAX_SPLIT_DEPTH:
            return _subdivide(south, west, north, east, quiet, depth, 'too dense')
        # 509 is Bandwidth Limit Exceeded and 429 is rate limiting. Both are transient
        # quotas that refill, so waiting recovers where giving up would strand every
        # remaining cluster - which is exactly what happened before this branch existed.
        if ex.code in (429, 509) and tries > 0:
            wait = 30 * (4 - tries)
            if not quiet:
                print('    OSM API quota hit (HTTP %s) - waiting %ds' % (ex.code, wait),
                      file=sys.stderr, flush=True)
            time.sleep(wait)
            return osm_api(bb, quiet=quiet, depth=depth, tries=tries - 1)
        if not quiet:
            print('    OSM API failed (HTTP %s)' % ex.code, file=sys.stderr)
        return None
    except Exception as ex:
        if not quiet:
            print('    OSM API failed (%s)' % str(ex)[:60], file=sys.stderr)
        return None

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as ex:
        if not quiet:
            print('    OSM API returned unparseable XML (%s)' % str(ex)[:50], file=sys.stderr)
        return None

    coords, els = {}, []
    for nd in root.findall('node'):
        coords[nd.get('id')] = (float(nd.get('lat')), float(nd.get('lon')))
    for nd in root.findall('node'):
        tags = {t.get('k'): t.get('v') for t in nd.findall('tag')}
        if tags.get('name'):
            els.append({'type': 'node', 'id': nd.get('id'), 'tags': tags,
                        'lat': float(nd.get('lat')), 'lon': float(nd.get('lon'))})
    for wy in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in wy.findall('tag')}
        if not tags.get('name'):
            continue
        geom = [{'lat': coords[r][0], 'lon': coords[r][1]}
                for r in (x.get('ref') for x in wy.findall('nd')) if r in coords]
        if geom:
            els.append({'type': 'way', 'id': wy.get('id'), 'tags': tags, 'geometry': geom})
    return {'elements': els}


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


def extents(pts, ev):
    """Each point's search region: where it claims to be, PLUS wherever the geocoders
    put it.

    This matters more than it looks. A badly misplaced point's real building sits near
    the geocoded address, not near the recorded coordinate - Ka'u Hospital's record is
    7 km from the building. A box drawn only around recorded positions therefore misses
    the reference geometry for precisely the points the audit most needs to judge, and
    they come back "no outline mapped" instead of "wrong". Since verify_points.py runs
    first, the geocodes are already on disk; use them."""
    out = []
    for p in pts:
        locs = [(p['lat'], p['lon'])]
        e = ev.get(p['idx'], {})
        for k in ('arcgis', 'census', 'nominatim'):
            d = e.get(k)
            if d and d.get('lat') is not None:
                locs.append((d['lat'], d['lon']))
        out.append({'idx': p['idx'], 'name': p['name'], 'locs': locs,
                    'lat0': min(l[0] for l in locs), 'lat1': max(l[0] for l in locs),
                    'lon0': min(l[1] for l in locs), 'lon1': max(l[1] for l in locs)})
    return out


def cluster_points(exts, link, max_span):
    """Group search regions that overlap or nearly touch, so they share one query.

    Single-linkage on bounding boxes, capped so a chain of points can never grow back
    into one continent-sized box - which is the failure this whole function exists to
    prevent."""
    clusters = []
    for x in sorted(exts, key=lambda x: (x['lat0'], x['lon0'])):
        for c in clusters:
            if (c['lat0'] - link <= x['lat1'] and x['lat0'] <= c['lat1'] + link and
                    c['lon0'] - link <= x['lon1'] and x['lon0'] <= c['lon1'] + link):
                la0, la1 = min(c['lat0'], x['lat0']), max(c['lat1'], x['lat1'])
                lo0, lo1 = min(c['lon0'], x['lon0']), max(c['lon1'], x['lon1'])
                if (la1 - la0) <= max_span and (lo1 - lo0) <= max_span:
                    c.update(lat0=la0, lat1=la1, lon0=lo0, lon1=lo1)
                    c['members'].append(x)
                    break
        else:
            clusters.append({'lat0': x['lat0'], 'lat1': x['lat1'],
                             'lon0': x['lon0'], 'lon1': x['lon1'], 'members': [x]})
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
    ap.add_argument('--max-pad', type=float, default=0.25,
                    help='widen an empty cluster search up to this many degrees (~25 km)')
    ap.add_argument('--max-span', type=float, default=0.5,
                    help='largest cluster bbox side in degrees, to cap query cost')
    ap.add_argument('--from-file', help='raw Overpass JSON to match against instead of querying '
                                        '(re-run name matching offline, or supply your own reference layer)')
    a = ap.parse_args()

    pts = json.load(open(os.path.join(a.work, 'points.json'), encoding='utf-8'))
    ev_path = os.path.join(a.work, 'evidence.json')
    ev = {}
    if os.path.exists(ev_path):
        ev = {e['idx']: e for e in json.load(open(ev_path, encoding='utf-8'))}
    else:
        print('note: evidence.json not found - run verify_points.py first, or search boxes\n'
              '      will be drawn around the recorded coordinates alone and will miss the\n'
              '      reference geometry for badly misplaced points.')
    k, v = a.osm_filter.split('=', 1)
    map_path = os.path.join(a.work, 'name_map.json')
    manual = json.load(open(map_path, encoding='utf-8')) if os.path.exists(map_path) else {}
    best, failed, used_fallback = {}, [], [False]

    # Resume support. Without this, "re-run to retry the failures" was not true - every
    # cluster was re-queried, which on a bandwidth-limited API is the difference between
    # finishing and tripping the quota all over again.
    fp_path = os.path.join(a.work, 'footprints.json')
    prior = {}
    if os.path.exists(fp_path):
        cached = json.load(open(fp_path, encoding='utf-8'))
        for f in cached.get('features', []):
            best.setdefault(f['name'], f)
        prior = cached.get('matched', {})
        if prior:
            print('resuming: %d point(s) already matched from a previous run' % len(prior))

    if a.from_file:
        print('reading reference features from %s' % a.from_file)
        collect(json.load(open(a.from_file, encoding='utf-8')), best)
    else:
        clusters = cluster_points(extents(pts, ev), a.link, a.max_span)
        print('%d points -> %d cluster(s); querying %s=%s per cluster' % (len(pts), len(clusters), k, v))
        for i, c in enumerate(clusters, 1):
            if prior and all(str(m['idx']) in prior for m in c['members']):
                print('  %2d/%d  %2d point(s)  already matched, skipping' % (
                    i, len(clusters), len(c['members'])))
                continue
            # Start tight and widen only on an empty result. Most clusters answer on the
            # first try, so the extra cost is paid exactly where it buys something: a
            # facility mapped further from its address than expected, or a point whose
            # address never geocoded and whose extent is therefore just the bad coordinate.
            pad, d, n = a.pad, None, 0
            while True:
                bb = '%f,%f,%f,%f' % (c['lat0'] - pad, c['lon0'] - pad,
                                      c['lat1'] + pad, c['lon1'] + pad)
                q = '[out:json][timeout:90];nwr["%s"="%s"](%s);out geom tags;' % (k, v, bb)
                d = overpass(q)
                if d is None:
                    d = osm_api(bb)             # Overpass unavailable: same data, sturdier service
                    if d is not None and not used_fallback[0]:
                        used_fallback[0] = True
                        print('  (Overpass unavailable - using the OSM core API instead)', flush=True)
                if d is None:
                    break                       # a failure is not an empty result; do not widen
                n = sum(1 for e in d['elements'] if (e.get('tags') or {}).get('name'))
                if n or pad >= a.max_pad:
                    break
                pad = min(pad * 3, a.max_pad)
                time.sleep(2.5)
            if d is None:
                failed.append(c)
                print('  %2d/%d  %2d point(s)  QUERY FAILED' % (i, len(clusters), len(c['members'])))
                time.sleep(2.5)
                continue
            before = len(best)
            collect(d, best)
            widened = '' if pad == a.pad else '  (widened to %.2f deg)' % pad
            print('  %2d/%d  %2d point(s)  %d feature(s)%s' % (
                i, len(clusters), len(c['members']), len(best) - before, widened), flush=True)
            time.sleep(2.5)

    matched, unmatched = dict(prior), []
    for p in pts:
        f = resolve(p, best, manual) or prior.get(str(p['idx']))
        if f:
            matched[str(p['idx'])] = f
        else:
            unmatched.append(p)

    # Second pass: only the points the tag query missed, and only in their own small box.
    # Some facilities carry healthcare=/social_facility= instead of the expected amenity tag,
    # so they are invisible to a tag query but findable by name.
    if unmatched and not a.from_file:
        print('\n%d point(s) unmatched - searching by name around each' % len(unmatched))
        ext_by_idx = {x['idx']: x for x in extents(pts, ev)}
        still = []
        for p in unmatched:
            nm = re.sub(r'["\\]', '', p['name'].split(',')[0])[:48].strip()
            if not nm:
                still.append(p)
                continue
            x = ext_by_idx[p['idx']]
            bb = '%f,%f,%f,%f' % (x['lat0'] - a.pad, x['lon0'] - a.pad,
                                  x['lat1'] + a.pad, x['lon1'] + a.pad)
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
