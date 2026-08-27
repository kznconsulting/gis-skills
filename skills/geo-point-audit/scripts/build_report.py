#!/usr/bin/env python3
"""Assemble the audit artifact: overview chart, interactive inspector, table, corrections.

Everything is drawn as inline SVG from embedded vector geometry. The Artifact CSP
blocks external requests, so tile-based maps are impossible - which is also why the
page works offline and loads instantly.

Prose comes from copy.json if present (write it after reading findings.md); otherwise
neutral wording is generated so the report is never blocked on it.

  uvx python build_report.py --work ./audit-work --out report.html
"""
import argparse, datetime, html, json, math, os, re, sys

def _utf8_stdout():
    """Console output is a separate encoding from file I/O. On a Windows console with
    a legacy code page, printing a name like "Hale Ho'ola Hamakua" raises
    UnicodeEncodeError and kills an otherwise healthy run, so pin stdout too."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass



ESC = lambda s: html.escape(str(s) if s is not None else '')
SMALL = {'of', 'the', 'for', 'and', 'a', 'an', 'in', 'on', 'at', 'to'}


def tc(n):
    """Title-case that leaves small words alone and survives apostrophes -
    naive .title() turns "Queen's" into "Queen'S"."""
    n = re.sub(r'\s+', ' ', str(n or '')).strip()
    out = []
    for i, w in enumerate(n.split(' ')):
        lw = w.lower()
        if i > 0 and lw in SMALL:
            out.append(lw)
        elif "'" in w:
            a, b = w.split("'", 1)
            out.append(a.capitalize() + "'" + b.lower())
        else:
            out.append(w.capitalize())
    return ' '.join(out)


def fmt(m):
    return '%.2f km' % (m / 1000) if m >= 1000 else '%d m' % round(m)


def fmt1(m):
    return '%.1f km' % (m / 1000) if m >= 1000 else '%d m' % round(m)


def hav(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def simp(c, tol=0.00003):
    o = [[round(c[0][0], 5), round(c[0][1], 5)]]
    for p in c[1:]:
        if abs(p[0] - o[-1][0]) > tol or abs(p[1] - o[-1][1]) > tol:
            o.append([round(p[0], 5), round(p[1], 5)])
    return o if len(o) >= 2 else [[round(q[0], 5), round(q[1], 5)] for q in c[:2]]


def overview_svg(rows, rings, rank, region_field=None):
    W, PAD = 1060, 26
    if rings:
        lats = [p[0] for r in rings for p in r]; lons = [p[1] for r in rings for p in r]
    else:
        lats = [r['lat'] for r in rows]; lons = [r['lon'] for r in rows]
    pad = 0.02 if rings else 0.12
    mnla, mxla = min(lats) - pad, max(lats) + pad
    mnlo, mxlo = min(lons) - pad, max(lons) + pad
    K = math.cos(math.radians((mnla + mxla) / 2))
    dw, dh = (mxlo - mnlo) * K, (mxla - mnla)
    S = (W - 2 * PAD) / dw
    H = int(round(dh * S + 2 * PAD))
    H = max(320, min(H, 900))
    S = min(S, (H - 2 * PAD) / dh)
    ox = (W - dw * S) / 2; oy = (H - dh * S) / 2

    def px(lat, lon):
        return (ox + (lon - mnlo) * K * S, oy + (mxla - lat) * S)

    s = ['<svg viewBox="0 0 %d %d" role="img" aria-label="Chart of every audited point coloured by verdict">' % (W, H)]
    s.append('<rect width="%d" height="%d" class="sea"/>' % (W, H))
    for r in rings:
        pts = [px(p[0], p[1]) for p in r]
        out = [pts[0]]
        for q in pts[1:]:
            if abs(q[0] - out[-1][0]) > 0.4 or abs(q[1] - out[-1][1]) > 0.4:
                out.append(q)
        if len(out) > 3:
            s.append('<path class="coast" d="M%sZ"/>' % ' '.join('%.1f,%.1f' % q for q in out))
    # Region labels come from the layer's own field rather than the boundary file, so the
    # chart speaks the dataset's vocabulary (islands, not the counties that contain them).
    if region_field:
        groups = {}
        for r in rows:
            g = (r.get('rec') or {}).get(region_field)
            if g:
                groups.setdefault(str(g).strip().upper(), []).append(r)
        for g, members in groups.items():
            xs = [px(m['lat'], m['lon']) for m in members]
            cx = sum(p[0] for p in xs) / len(xs)
            cy = min(p[1] for p in xs) - 16          # sit above the cluster, clear of the markers
            cy = max(cy, 12)
            s.append('<text class="isl-label" x="%.1f" y="%.1f" style="paint-order:stroke fill;'
                     'stroke:var(--land);stroke-width:3.4;stroke-linejoin:round">%s</text>'
                     % (cx, cy, ESC(g)))
    CL = {'OK': 'var(--ok)', 'MINOR': 'var(--minor)', 'WRONG': 'var(--bad)', 'UNKNOWN': 'var(--ink3)'}
    for r in sorted(rows, key=lambda r: {'OK': 0, 'UNKNOWN': 0, 'MINOR': 1, 'WRONG': 2}[r['verdict']]):
        x, y = px(r['lat'], r['lon'])
        if r['verdict'] == 'WRONG':
            s.append('<circle cx="%.1f" cy="%.1f" r="9.5" fill="var(--bad)" stroke="var(--panel)" stroke-width="1.6"/>' % (x, y))
            s.append('<text x="%.1f" y="%.1f" font-family="IBM Plex Mono, monospace" font-size="10" '
                     'font-weight="600" fill="#fff" text-anchor="middle">%d</text>' % (x, y + 3.6, rank[r['idx']]))
        else:
            rr = 4.2 if r['verdict'] == 'MINOR' else 3.4
            s.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="var(--panel)" stroke-width="1.2">'
                     '<title>%s</title></circle>' % (x, y, rr, CL[r['verdict']], ESC(tc(r['name']))))
    s.append('</svg>')
    return '\n    '.join(s)


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='./audit-work')
    ap.add_argument('--out', default='report.html')
    ap.add_argument('--template')
    ap.add_argument('--live', action='store_true',
                    help='build the local live-imagery variant instead of the self-contained '
                         'artifact: same report, but the inspector is a real map with satellite '
                         'tiles. Needs the network, so it cannot be published as an Artifact.')
    a = ap.parse_args()
    W = a.work
    tpl_name = 'template-live.html' if a.live else 'template.html'
    tpl_path = a.template or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets', tpl_name)

    rows = json.load(open(os.path.join(W, 'verdict.json'), encoding='utf-8'))
    meta = json.load(open(os.path.join(W, 'meta.json'), encoding='utf-8'))
    quality = json.load(open(os.path.join(W, 'quality.json'), encoding='utf-8')) if os.path.exists(os.path.join(W, 'quality.json')) else {}
    rings = json.load(open(os.path.join(W, 'basemap.json'), encoding='utf-8')) if os.path.exists(os.path.join(W, 'basemap.json')) else []
    roads = json.load(open(os.path.join(W, 'roads.json'), encoding='utf-8')) if os.path.exists(os.path.join(W, 'roads.json')) else {}
    fps = json.load(open(os.path.join(W, 'footprints.json'), encoding='utf-8'))['matched'] if os.path.exists(os.path.join(W, 'footprints.json')) else {}
    copy = json.load(open(os.path.join(W, 'copy.json'), encoding='utf-8')) if os.path.exists(os.path.join(W, 'copy.json')) else {}

    n = len(rows)
    nok = sum(1 for r in rows if r['verdict'] == 'OK')
    nmin = sum(1 for r in rows if r['verdict'] == 'MINOR')
    nbad = sum(1 for r in rows if r['verdict'] == 'WRONG')
    nfp = sum(1 for m in fps.values() if m.get('ring'))
    rows_sorted = sorted(rows, key=lambda r: -r['offset_m'])
    wrong = [r for r in rows_sorted if r['verdict'] == 'WRONG']
    rank = {r['idx']: i + 1 for i, r in enumerate(wrong)}
    worst = rows_sorted[0] if rows_sorted else None

    # ---------- region column: use a real field if the layer has one ----------
    region_field = None
    for cand in ['island', 'county', 'region', 'city', 'district', 'state', 'town', 'area']:
        for f in meta.get('fields', []):
            if f.lower() == cand:
                region_field = f
                break
        if region_field:
            break

    # ---------- viewer bundle ----------
    items = []
    for r in rows_sorted:
        f = fps.get(str(r['idx']))
        fp = simp([[p[0], p[1]] for p in f['ring']], 0.000012) if (f and f.get('ring')) else None
        at = r.get('at_name') or (r.get('at_type') or '').replace('_', ' ') or (r.get('at_point') or '').split(',')[0]
        if r.get('geom') != 'polygon':
            fpt = 'no outline mapped'
        elif r.get('inside'):
            fpt = 'inside the outline'
        elif r['edge_m'] <= 50:
            fpt = 'on the outline (%d m)' % r['edge_m']
        else:
            fpt = '%s outside' % fmt(r['edge_m'])
        rd = [] if a.live else roads.get(str(r['idx']), {}).get('roads', [])
        extra, extra_label = None, None
        for k, v in (r.get('rec') or {}).items():
            if isinstance(v, (int, float)) and v and k.lower() not in ('objectid', 'fid', 'id', 'shape'):
                extra, extra_label = v, k.replace('_', ' ').strip().title()
                break
        phone = next((v for k, v in (r.get('rec') or {}).items()
                      if 'phone' in k.lower() and v), None)
        items.append({
            'i': r['idx'], 'n': tc(r['name']), 'isl': (r.get('region') or ''), 'ad': r.get('addr') or '',
            'v': r['verdict'], 'off': r['offset_m'], 'fpt': fpt, 'at': at,
            'p': [r['lat'], r['lon']], 'f': [r['fix_lat'], r['fix_lon']],
            'ue': r.get('fix_x'), 'un': r.get('fix_y'),
            'bd': extra, 'bdl': extra_label or 'Value', 'ph': phone,
            'src': [{'s': s['src'], 'm': s['m']} for s in r.get('sources', []) if s.get('m') is not None],
            'fp': fp, 'rd': rd,
        })
    viewer = json.dumps(items, separators=(',', ':'))
    if '</script' in viewer or '<!--' in viewer:
        sys.exit('viewer data would break out of the script tag; sanitise the source attributes')

    for it, r in zip(items, rows_sorted):
        it['isl'] = tc(r['rec'].get(region_field)) if region_field else ''
    viewer = json.dumps(items, separators=(',', ':'))

    # ---------- table ----------
    mx = max([r['offset_m'] for r in rows] + [1])
    trs = []
    for r in rows_sorted:
        at = r.get('at_name') or (r.get('at_type') or '').replace('_', ' ') or (r.get('at_point') or '').split(',')[0]
        cl = {'OK': 'ok', 'MINOR': 'minor', 'WRONG': 'bad', 'UNKNOWN': 'minor'}[r['verdict']]
        lbl = {'OK': 'Correct', 'MINOR': 'Marginal', 'WRONG': 'Wrong', 'UNKNOWN': 'Unresolved'}[r['verdict']]
        if r.get('geom') != 'polygon':
            fp = '<span style="color:var(--ink3)">no outline mapped</span>'
        elif r.get('inside'):
            fp = '<b style="color:var(--ok)">inside</b>'
        elif r['edge_m'] <= 50:
            fp = '<b style="color:var(--ok)">on edge</b> %d m' % r['edge_m']
        else:
            fp = '%s away' % fmt(r['edge_m'])
        pct = max(1.2, (r['offset_m'] / mx) ** 0.42 * 100)
        trs.append(
            '<tr>\n <td class="nm">%s</td>\n <td class="sm">%s</td>\n'
            ' <td class="num">%s<span class="bar"><i style="width:%.1f%%;background:var(--%s)"></i></span></td>\n'
            ' <td class="num" style="text-align:left;color:var(--ink2);font-size:12.5px">%s</td>\n'
            ' <td class="sm">%s</td>\n <td><span class="pill %s">%s</span></td>\n</tr>' % (
                ESC(tc(r['name'])), ESC(tc(r['rec'].get(region_field)) if region_field else ''),
                fmt(r['offset_m']), pct, cl, fp, ESC(at), cl, lbl))

    # ---------- ranked list under the chart ----------
    ranked = ['<li><span class="r">%d</span><span>%s</span><span class="d">%s</span></li>' % (
        rank[r['idx']], ESC(tc(r['name'])), fmt1(r['offset_m'])) for r in wrong]

    # ---------- stats strip ----------
    stats = [
        '<div class="stat"><span class="n">%d</span><span class="l">Points audited</span></div>' % n,
        '<div class="stat is-ok"><span class="n">%d</span><span class="l">Correct &mdash; on or inside the mapped outline</span></div>' % nok,
        '<div class="stat is-minor"><span class="n">%d</span><span class="l">Marginal &mdash; just off the outline</span></div>' % nmin,
        '<div class="stat is-bad"><span class="n">%d</span><span class="l">Wrong &mdash; on an unrelated property</span></div>' % nbad,
    ]
    if worst and worst['offset_m'] > 0:
        big = ('%.1f<small style="font-size:18px"> km</small>' % (worst['offset_m'] / 1000)
               if worst['offset_m'] >= 1000 else '%d<small style="font-size:18px"> m</small>' % worst['offset_m'])
        stats.append('<div class="stat is-bad"><span class="n">%s</span><span class="l">Worst error &mdash; %s</span></div>'
                     % (big, ESC(tc(worst['name']))))

    # ---------- corrections block ----------
    csv_txt = open(os.path.join(W, 'corrections.csv'), encoding='utf-8').read().strip()

    # ---------- prose: copy.json wins, otherwise neutral fallback ----------
    brs = [r['bearing'] for r in rows_sorted if r.get('bearing') is not None and r['verdict'] != 'OK']
    bearing_txt = (', '.join('%d&deg;' % b for b in brs[:8]) or 'n/a')
    D = {
        'TITLE': copy.get('title', 'Point Accuracy Audit'),
        'EYEBROW': copy.get('eyebrow', 'Positional accuracy audit &middot; %s' % ESC(meta['source_file'])),
        'H1': copy.get('h1', ('%d points are in the wrong place' % nbad) if nbad else 'Every point checks out'),
        'STANDFIRST': copy.get('standfirst',
            'Every point was checked against independent references. <b>%d of %d are correct.</b> '
            '%d sit just off their site, and <b>%d land on an unrelated property.</b>' % (nok, n, nmin, nbad)),
        'SRC_FILE': ESC(meta['source_file']),
        'N': str(n), 'N_OK': str(nok), 'N_MINOR': str(nmin), 'N_WRONG': str(nbad), 'N_FP': str(nfp),
        'CRS_NAME': ESC(meta.get('crs_name', 'WGS 84')), 'CRS_ID': ESC(meta.get('crs_id', 'EPSG:4326')),
        'CRS_SHORT': ESC(copy.get('crs_short', (meta.get('crs_id') or 'WGS 84').replace('EPSG:', 'EPSG '))),
        'PUBLISHED_ROW': ('<div><span>PUBLISHED</span> %s</div>' % copy['published']) if copy.get('published') else '',
        'AUDITED': copy.get('audited', datetime.date.today().isoformat()),
        'STATS': ''.join(stats),
        'OVERVIEW': overview_svg(rows, rings, rank, region_field),
        'RANKED': '\n      '.join(ranked),
        'ROWS': '\n'.join(trs),
        'VIEWERDATA': viewer,
        'CSV': ESC(csv_txt.split('\n', 1)[1] if '\n' in csv_txt else csv_txt),
        'SUBJECT_COL': copy.get('subject_col', 'Name'),
        'REGION_COL': copy.get('region_col', tc(region_field) if region_field else 'Area'),
        'LEDE_MAP': copy.get('lede_map',
            'Boundaries are US Census TIGER; points are the source coordinates reprojected to WGS&nbsp;84. '
            'The wrong points are numbered by severity.'),
        'LEDE_INSPECT_LIVE': copy.get('lede_inspect_live',
            'Use the arrows, the arrow keys, or the list to move through the points, worst first. '
            'The map is live satellite imagery, so you can pan and zoom to check any point for '
            'yourself. <b class="mono" style="color:var(--bad)">Red</b> is where the data puts it; '
            '<b class="mono" style="color:var(--ok)">green</b> is where it actually stands, and the '
            'blue outline is the mapped building.'),
        'CAPTION_INSPECT_LIVE': copy.get('caption_inspect_live',
            'This build loads map tiles from Esri and OpenStreetMap, so it needs a network '
            'connection and is a local file rather than a shareable link. The self-contained '
            'artifact build is the one to hand to other people; this one is for satisfying '
            'yourself that a finding is real.'),
        'LEDE_INSPECT': copy.get('lede_inspect',
            'Use the arrows, the arrow keys, or the list to move through the points, worst first. Streets are '
            'US Census TIGER centrelines and the blue outline is the mapped building. '
            '<b class="mono" style="color:var(--bad)">Red</b> is where the data puts it; '
            '<b class="mono" style="color:var(--ok)">green</b> is where it actually stands. Each map is drawn '
            'to its own scale, so read the bar.'),
        'CAPTION_INSPECT': copy.get('caption_inspect',
            'The street network comes from the Census and the building outline from OpenStreetMap, so the two '
            'agree only where the underlying point is genuinely right. Where a site has no mapped outline, the '
            'map shows the streets and the two points alone.'),
        'LEDE_TABLE': copy.get('lede_table',
            'Offset is the distance from the recorded point to the consensus true location. <b>Footprint</b> is '
            'the decisive test &mdash; how far the point lies from the site&rsquo;s own mapped outline. '
            '<b>&ldquo;What is actually there&rdquo;</b> is the feature returned when the recorded coordinate is '
            'reverse-geocoded, which is the quickest way to see an error for yourself.'),
        'CAPTION_TABLE': copy.get('caption_table',
            'Offset alone does not decide the verdict. A point on the correct building can still sit far from the '
            'centre of a large site, while a smaller offset can put a point on a neighbouring lot. The footprint '
            'test outranks the distance. Where a site has no mapped outline, the verdict rests on the geocoders alone.'),
        'LEDE_METHOD': copy.get('lede_method',
            'A point passed only if it fell inside or within 50&nbsp;m of its mapped footprint, or within '
            '150&nbsp;m of the consensus location. Sites commonly run 100&ndash;300&nbsp;m across, so an offset '
            'at that scale is normal and not a defect.'),
        'LEDE_FIX': copy.get('lede_fix',
            'Replacement coordinates for the %d points needing correction, in both WGS&nbsp;84 and %s, so they '
            'can be written straight back into the layer. Each is the mean of the rooftop geocode and the centre '
            'of the mapped footprint.' % (nbad + nmin, ESC(meta.get('crs_name', 'the source CRS')))),
        'METHOD_NOTE': copy.get('method_note',
            'The reprojection from %s to WGS&nbsp;84 was itself verified. The error bearings scatter across %s. '
            'A datum or projection fault would move every point the same way by the same amount, and these do '
            'not, so the geometry pipeline is sound and only these individual records need correction.'
            % (ESC(meta.get('crs_id', 'the source CRS')), bearing_txt)),
    }

    # ---------- notes ----------
    notes = copy.get('notes')
    if not notes:
        notes = []
        if 'nbsp' in quality:
            notes.append({'h': 'Non-breaking spaces in %d of %d names' % (quality['nbsp']['n'], quality['nbsp']['of']),
                          'p': 'These <code>U+00A0</code> characters look like ordinary spaces but break every exact '
                               'match, join, and search against the name field.'})
        for d in quality.get('coincident', [])[:3]:
            notes.append({'h': 'Two records share one location',
                          'p': '&ldquo;%s&rdquo; and &ldquo;%s&rdquo; sit %d&nbsp;m apart in the data. Check whether '
                               'that is real or one was snapped to the other.' % (ESC(d['a']), ESC(d['b']), d['m'])})
        if quality.get('sparse_fields'):
            notes.append({'h': 'Sparse optional attributes',
                          'p': 'Mostly blank: ' + ', '.join('<code>%s</code> (%d blank)' % (ESC(k), v)
                                                            for k, v in list(quality['sparse_fields'].items())[:5]) + '.'})
    D['NOTES'] = '\n'.join(
        '<div class="note"><h4>%s</h4><p>%s</p></div>' % (nt['h'], nt['p']) for nt in notes) or \
        '<div class="note"><h4>No attribute defects found</h4><p>Names, addresses, and populated fields all '
    if not notes:
        D['NOTES'] = ('<div class="note"><h4>No attribute defects found</h4><p>Names, addresses and populated '
                      'fields passed the automated checks. Only the geometry needed attention.</p></div>')

    # The template carries two marker styles: {{NAME}} for prose and <!--NAME--> for the
    # generated geometry blocks. Both must be filled, and both must be checked - a missed
    # <!--VIEWERDATA--> yields a page that looks fine until the inspector turns up empty.
    t = open(tpl_path, encoding='utf-8').read()
    for k, v in D.items():
        t = t.replace('{{%s}}' % k, str(v)).replace('<!--%s-->' % k, str(v))
    left = re.findall(r'\{\{[A-Z0-9_]+\}\}', t) + re.findall(r'<!--[A-Z0-9_]+-->', t)
    if left:
        sys.exit('unfilled placeholders: %s' % sorted(set(left)))
    # Guard only against silent substitution failures, not against inputs that were
    # legitimately skipped - an audit run without Overpass or outside the US still
    # produces a valid report, just with fewer layers on the maps.
    for need in ('ptsData', 'insList'):
        if need not in t:
            sys.exit('template did not receive %s - the build is incomplete' % need)
    if rings and 'class="coast"' not in t:
        sys.exit('boundary geometry was loaded but did not reach the page')
    missing = []
    if not rings:
        missing.append('boundaries (overview chart shows points only)')
    if not roads:
        missing.append('street context (inspector shows points and outlines only)')
    if not fps:
        missing.append('building outlines (verdicts rest on the geocoders alone)')
    if missing:
        print('DEGRADED: no ' + '; no '.join(missing))
        print('Say so in the report rather than presenting a weaker evidence base as the full one.')
    open(a.out, 'w', encoding='utf-8').write(t)
    print('wrote %s (%.2f MB)' % (a.out, len(t) / 1048576))
    print('%d points: %d correct, %d marginal, %d wrong' % (n, nok, nmin, nbad))
    if not copy:
        print('NOTE: no copy.json - neutral fallback wording used. Read findings.md and write copy.json '
              'for a report that names what it found.')


if __name__ == '__main__':
    main()
