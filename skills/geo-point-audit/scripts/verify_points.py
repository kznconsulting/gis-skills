#!/usr/bin/env python3
"""Gather independent opinions on where each point should be.

Three forward geocoders (Census TIGER, Nominatim, ArcGIS World) plus a reverse
lookup naming whatever physically stands at the recorded coordinate. The reverse
lookup is the one that turns "478 m off" into "sits on a private house", which is
the form a non-GIS reader can check for themselves.

Slow on purpose: Nominatim asks for ~1 req/s. Budget ~4 s per point and run it
in the background. Results are cached per point, so re-running resumes.

  uvx python verify_points.py --work ./audit-work
"""
import argparse, json, math, os, re, sys, time, urllib.parse, urllib.request

def _utf8_stdout():
    """Console output is a separate encoding from file I/O. On a Windows console with
    a legacy code page, printing a name like "Hale Ho'ola Hamakua" raises
    UnicodeEncodeError and kills an otherwise healthy run, so pin stdout too."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass



UA = {'User-Agent': 'geo-point-audit/1.0 (positional accuracy QA)'}
CENSUS = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress'
NOMI = 'https://nominatim.openstreetmap.org'
ARC = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates'


def get(url, headers=None, tries=3, pause=2.0):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            return json.load(urllib.request.urlopen(req, timeout=45))
        except Exception as e:
            if i == tries - 1:
                return {'_error': str(e)[:120]}
            time.sleep(pause)


def hav(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def norm_addr(a, state_hint=None):
    """US geocoders want 'street, city, ST zip'. Source layers often spell the
    state out and append a zip+4, which some of them choke on."""
    if not a:
        return None
    a = re.sub(r'\s+', ' ', a.replace(' ', ' ').replace('’', "'")).strip()
    a = re.sub(r',\s*Hawaii\s+(\d{5})', r', HI \1', a, flags=re.I)
    for full, ab in [('California', 'CA'), ('New York', 'NY'), ('Texas', 'TX'), ('Florida', 'FL'),
                     ('Washington', 'WA'), ('Oregon', 'OR'), ('Colorado', 'CO'), ('Arizona', 'AZ'),
                     ('Massachusetts', 'MA'), ('Illinois', 'IL'), ('Georgia', 'GA'), ('Virginia', 'VA'),
                     ('Maryland', 'MD'), ('Pennsylvania', 'PA'), ('Michigan', 'MI'), ('Ohio', 'OH')]:
        a = re.sub(r',\s*%s\s+(\d{5})' % full, r', %s \1' % ab, a, flags=re.I)
    a = re.sub(r'-\d{4}\b', '', a)          # zip+4 confuses TIGER
    return a


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--sleep', type=float, default=1.1, help='pause between Nominatim calls')
    ap.add_argument('--skip', default='', help='comma list of sources to skip: census,nominatim,arcgis,reverse')
    a = ap.parse_args()
    skip = {s.strip() for s in a.skip.split(',') if s.strip()}

    pts = json.load(open(os.path.join(a.work, 'points.json'), encoding='utf-8'))
    out_path = os.path.join(a.work, 'evidence.json')
    done = {}
    if os.path.exists(out_path):
        done = {e['idx']: e for e in json.load(open(out_path, encoding='utf-8'))}
        print('resuming: %d of %d already verified' % (len(done), len(pts)))

    out = []
    for p in pts:
        if p['idx'] in done:
            out.append(done[p['idx']])
            continue
        addr = norm_addr(p['addr'])
        pt = (p['lat'], p['lon'])
        e = {'idx': p['idx'], 'name': p['name'], 'addr': addr,
             'census': None, 'nominatim': None, 'arcgis': None, 'reverse': None}

        if addr and 'census' not in skip:
            d = get('%s?address=%s&benchmark=Public_AR_Current&format=json' % (CENSUS, urllib.parse.quote(addr)))
            m = ((d.get('result') or {}).get('addressMatches') or [])
            if m:
                c = (m[0]['coordinates']['y'], m[0]['coordinates']['x'])
                e['census'] = {'lat': round(c[0], 6), 'lon': round(c[1], 6),
                               'm': round(hav(pt, c)), 'match': m[0].get('matchedAddress')}

        if addr and 'nominatim' not in skip:
            d = get('%s/search?%s' % (NOMI, urllib.parse.urlencode(
                {'q': addr, 'format': 'json', 'limit': 1, 'countrycodes': 'us'})), UA)
            time.sleep(a.sleep)
            if isinstance(d, list) and d:
                c = (float(d[0]['lat']), float(d[0]['lon']))
                e['nominatim'] = {'lat': round(c[0], 6), 'lon': round(c[1], 6),
                                  'm': round(hav(pt, c)), 'match': d[0].get('display_name'),
                                  'type': d[0].get('type')}

        if addr and 'arcgis' not in skip:
            d = get('%s?%s' % (ARC, urllib.parse.urlencode(
                {'SingleLine': addr, 'f': 'json', 'outFields': 'Match_addr,Addr_type',
                 'maxLocations': 1, 'countryCode': 'USA'})))
            c = (d.get('candidates') or [None])[0] if isinstance(d, dict) else None
            if c:
                q = (c['location']['y'], c['location']['x'])
                at = (c.get('attributes') or {})
                e['arcgis'] = {'lat': round(q[0], 6), 'lon': round(q[1], 6), 'm': round(hav(pt, q)),
                               'match': at.get('Match_addr'), 'type': at.get('Addr_type')}
            time.sleep(0.25)

        if 'reverse' not in skip:
            d = get('%s/reverse?%s' % (NOMI, urllib.parse.urlencode(
                {'lat': p['lat'], 'lon': p['lon'], 'format': 'json', 'zoom': 18})), UA)
            time.sleep(a.sleep)
            if isinstance(d, dict) and 'display_name' in d:
                ad = d.get('address', {})
                e['reverse'] = {'display': d['display_name'], 'name': d.get('name'),
                                'class': d.get('class'), 'type': d.get('type'),
                                'road': ad.get('road'),
                                'city': ad.get('city') or ad.get('town') or ad.get('village'),
                                'county': ad.get('county'), 'state': ad.get('state')}

        out.append(e)
        json.dump(out, open(out_path, 'w', encoding='utf-8'), indent=1)   # checkpoint every point
        print('%3d %-40s census %-7s nomi %-7s arcgis %-7s | at point: %s' % (
            p['idx'], p['name'][:40],
            (e['census'] or {}).get('m', '-'), (e['nominatim'] or {}).get('m', '-'),
            (e['arcgis'] or {}).get('m', '-'),
            ((e['reverse'] or {}).get('name') or (e['reverse'] or {}).get('type') or '?')[:32]), flush=True)

    print('\nwrote %s' % out_path)
    miss = [e['name'] for e in out if not any([e['census'], e['nominatim'], e['arcgis']])]
    if miss:
        print('NO geocode matched for %d point(s) - check the address field:' % len(miss))
        for m in miss[:10]:
            print('   ', m)


if __name__ == '__main__':
    main()
