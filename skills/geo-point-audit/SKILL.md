---
name: geo-point-audit
description: Verify that the points in a geospatial dataset actually sit where they claim to, then publish a street-level audit artifact with corrected coordinates. Use this whenever someone doubts the placement of a point layer - "are these coordinates right", "the pins look off", "verify this dataset", "check these locations", "QA this shapefile", "some of these markers are in the wrong place" - or hands over a .shp/.geojson/.csv/.kml of facilities, clinics, schools, stores, offices, sites, assets, or stops and wants it checked. Also use for positional accuracy QA, geocoding validation, comparing a point layer against ground truth, or auditing an address-vs-coordinate mismatch. Triggers on shapefiles even when the user only names the folder ("that dataset in my downloads").
license: MIT
metadata:
  version: "1.0"
---

# Geo Point Audit

Point layers rot quietly. Somebody digitises a facility by eye, snaps it to the wrong building, or drops it on the road centreline instead of the site, and the error survives every republication because nothing downstream ever checks. This skill finds those points, proves each call with independent evidence, and ships an artifact the owner of the data can act on.

The whole method rests on one idea: **a coordinate is only as trustworthy as the number of independent sources that agree with it.** One geocoder can be wrong. Three geocoders plus a building outline plus a reverse lookup of what physically stands at the coordinate cannot all be wrong in the same direction. So the pipeline gathers several genuinely independent opinions and lets them convict or acquit each point.

## The deliverable

An Artifact with four things, in this order:

1. **An archipelago/region chart** - every point on real coastlines, coloured by verdict, wrong ones ranked by severity.
2. **An interactive inspector** - step through every point with arrows, arrow keys, or a list; each view shows the street network, the building outline, the recorded point in red, the true position in green, an arrow between them, and a scale bar.
3. **A full audit table** - offset, the footprint test, what physically stands at the recorded coordinate, verdict.
4. **Corrected coordinates** - in WGS 84 and in the layer's own source CRS, so they can be written straight back.

No map tiles anywhere. The Artifact CSP blocks external requests, so Leaflet and Mapbox are not options. Everything is vector geometry embedded in the page and drawn as SVG at view time, which is also why the artifact stays fast and works offline.

## Run order

Each script writes into a work directory and is safe to re-run; finished steps are cached, so a failed network call costs you that step and nothing else. Use `uvx` (never `pip install`), and run from anywhere.

```bash
S=~/.agents/skills/geo-point-audit/scripts
W=./audit-work           # any scratch directory

uvx --with pyshp --with pyproj python $S/load_points.py    <input> --work $W
uvx python $S/verify_points.py                             --work $W     # slow: ~4 s/point
uvx python $S/fetch_reference.py                           --work $W --osm-filter 'amenity=hospital'
uvx --with pyproj python $S/adjudicate.py                  --work $W
uvx python $S/fetch_context.py                             --work $W
# ...write copy.json here (see below)...
uvx python $S/build_report.py                              --work $W --out report.html
```

`load_points.py --help` on any script lists its options. The two you will actually reach for:
`--osm-filter` (the OSM tag identifying this kind of place) and `--name-field` / `--addr-field`
when auto-detection picks the wrong column.

### Between `adjudicate.py` and `build_report.py`, you write the words

`adjudicate.py` writes `findings.md`. Read it. It tells you how many points passed, which failed, what physically stands at each bad coordinate, whether the error bearings scatter or point one way, and what the attribute checks turned up. Then write `$W/copy.json`:

```json
{
  "title": "Hawaii Hospital Point Audit",
  "eyebrow": "Positional accuracy audit · State of Hawaii hospitals",
  "h1": "Eight points are in the wrong place",
  "standfirst": "Every point was checked against four independent references. <b>Nineteen are correct.</b> ...",
  "subject": "hospitals",
  "notes": [
    {"h": "Non-breaking spaces in 23 of 29 names",
     "p": "Most <code>Name</code> values contain <code>U+00A0</code> ..."}
  ]
}
```

Write the headline from what you actually found - "Eight points are in the wrong place" beats "Positional accuracy report". Name real things in the standfirst: if a point landed on a delicatessen, say delicatessen. That specificity is what makes the reader trust the rest, and it costs nothing because `findings.md` already handed you the nouns. Keep the `notes` to defects you can prove from the data.

`copy.json` is optional; without it the report falls back to neutral generated wording. That fallback is a safety net, not a target - a report that says "8 of 29 points require correction" is markedly weaker than one that says a hospital sits on a delicatessen.

## How a point is judged

Read `references/methodology.md` before you defend a verdict or change a threshold. The short version:

| Verdict | Test |
|---|---|
| **Correct** | inside the building outline, or within 50 m of it, or within 150 m of the consensus location |
| **Marginal** | 150-350 m from consensus and clear of the outline |
| **Wrong** | beyond that |

Offset alone never decides it. A hospital campus can be 300 m across, so a point on the correct building can still sit 210 m from the campus centre, while a point 162 m out can be on a neighbour's lot. **The footprint test outranks the distance** - that is why the table shows both columns, and why the report carries a caption explaining the apparent inconsistency before a reader spots it and doubts the whole thing.

### Always check for a systematic shift first

Before reporting individual errors, look at the error bearings in `findings.md`. If every bad point is displaced the same distance in the same direction, this is not sloppy digitising - it is a datum or projection fault, and the finding is "your CRS handling is wrong", not "these 8 points are wrong". Scattered bearings mean per-point human error. `adjudicate.py` computes this for you; say which one it is in the report, because it changes what the owner has to fix.

## Sources, and why these ones

Read `references/data-sources.md` for endpoints, parameters, and quirks. The selection principle matters more than the list:

- **US Census geocoder** - TIGER address-range interpolation. Independent of OpenStreetMap. Puts the point on the street centreline, so it confirms the block, not the building.
- **ArcGIS World Geocoder** - rooftop-level `PointAddress` matches from Esri. The strongest single check, and independent of both others. No key needed for this volume.
- **OpenStreetMap footprints** via Overpass - the building or site polygon, for the point-in-polygon test.
- **Reverse geocoding** - names the feature physically standing at the recorded coordinate. This is the one that turns a distance into evidence a non-GIS reader can check.
- **Census TIGER roads and boundaries** - the street network and coastlines drawn in the artifact.

Draw the street network from the **Census** and the building outline from **OpenStreetMap** on purpose. They are wholly independent, so where they agree that agreement is real corroboration. Drawing both from OSM would only show one source agreeing with itself.

**Outside the US**, the Census sources are unavailable. Fall back to OSM footprints plus Nominatim plus ArcGIS, drop `fetch_context.py`'s road layer, and say plainly in the report that the audit rests on two independent sources rather than four. Never quietly present a weaker evidence base as if it were the full one.

## Verify before you publish

The artifact is the deliverable, so look at it rendered rather than trusting the HTML. Chrome renders a file and screenshots it in one shot without any server:

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=9000 \
  --window-size=1300,7600 --screenshot=shot.png "file://$PWD/report.html"
```

Read the PNG. Then check the interactive parts actually work - append a probe script that clicks the controls and writes the resulting state into a `<div>`, render with `--dump-dom`, and read it back. `references/troubleshooting.md` has the probe pattern and the traps that cost real time on the first build (Overpass outages, curl eating loop stdin, silent buffering, HTTP 406 without a User-Agent).

Publish with the Artifact tool. Load the `artifact-design` skill first, as always.

## Platform

The scripts are pure Python with no shell dependencies and run anywhere `uvx` does. Text I/O
is pinned to UTF-8 at every call site rather than left to the platform default, because place
names carry the ʻokina, macrons, and accents that a legacy Windows code page cannot represent -
so an unpinned run corrupts names on read and dies outright on write. `corrections.csv` is
written UTF-8 with a BOM so Excel on Windows reads those names correctly instead of as mojibake.

Two things in this document are macOS shell conventions, not requirements:

- `S=~/.agents/skills/...` is bash/zsh. In PowerShell use `$S = "$HOME/.agents/skills/..."`.
- The Chrome verification path is `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.
  On Windows it is usually `C:\Program Files\Google\Chrome\Application\chrome.exe`; the
  `--headless=new --screenshot` flags are identical.

## Working notes

- **Reproject, do not assume.** Read the `.prj`. `load_points.py` handles this, and it also keeps the source CRS so corrections come back in the layer's own coordinates - which is what makes them usable rather than merely correct.
- **The slow step is `verify_points.py`.** Nominatim asks for roughly one request per second and will refuse without a User-Agent. Budget about 4 seconds per point, run it in the background, and let the rest of the work continue around it.
- **Ask for small areas, not one big one.** `fetch_reference.py` clusters the points and queries a small box per cluster. Overpass cost scales with the area scanned, so one box around a whole archipelago is mostly ocean: on the 29-point test layer, clustering cut the queried area from 158,000 km² to 741 km², about 0.5% of the original. Small boxes also fail independently, so a bad response costs one cluster rather than the whole run, and re-running retries only what failed. The tradeoff is more requests against a per-IP slot limit, which is why a 429 backs off on the same server rather than stampeding the mirrors.
- **Bad matches are worse than no matches.** Fuzzy name matching against a reference set will happily pair "Ka'u Hospital" with "Kauai Veterans Memorial Hospital" and report a 530 km error. `fetch_reference.py` requires a real match and reports non-matches rather than inventing one; when it cannot match a name, resolve it by hand in `$W/name_map.json`.
- **Report attribute defects too.** The geometry is the headline, but non-breaking spaces in names, an address whose street type is wrong, or two facilities sharing one coordinate are all real and all cheap to detect. `adjudicate.py` looks for them.
