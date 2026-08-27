# Data sources

Every endpoint the pipeline uses, with the parameters that matter and the quirks that cost
time. None needs an API key at audit volumes.

## Contents
- [US Census geocoder](#us-census-geocoder)
- [ArcGIS World Geocoder](#arcgis-world-geocoder)
- [Nominatim](#nominatim)
- [Overpass / OpenStreetMap](#overpass--openstreetmap)
- [Census TIGERweb (roads and boundaries)](#census-tigerweb-roads-and-boundaries)
- [Outside the United States](#outside-the-united-states)

## US Census geocoder

Forward geocoding by address-range interpolation along TIGER street centrelines.

```
https://geocoding.geo.census.gov/geocoder/locations/onelineaddress
  ?address=<urlencoded>&benchmark=Public_AR_Current&format=json
```

Result lands in `result.addressMatches[0].coordinates` as `{x: lon, y: lat}`.

- Interpolated, not rooftop: expect 30-150 m from the building even when correct. It confirms
  the block, so treat a large Census offset as meaningful and a small one as weak confirmation.
- Wants `street, city, ST zip`. Spelled-out state names and ZIP+4 both cause misses;
  `verify_points.py` normalises these before sending.
- No key, no meaningful rate limit at this volume.

## ArcGIS World Geocoder

Esri's commercial reference data, rooftop-accurate for most US addresses. The strongest single
source in the pipeline.

```
https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates
  ?SingleLine=<urlencoded>&f=json&outFields=Match_addr,Addr_type&maxLocations=1&countryCode=USA
```

- **Check `Addr_type`.** `PointAddress` is a rooftop match and is what you want. `StreetAddress`
  is interpolated. `POI`, `StreetName`, `Locality` and `Postal` are progressively vaguer and can
  be kilometres out - the clustering step exists partly to catch these.
- Free for this volume without a token. High-volume or commercial redistribution needs a licence;
  check Esri's terms before running this at scale or republishing the coordinates.

## Nominatim

OpenStreetMap's geocoder, used for both forward lookups and - more importantly - reverse
lookups that name what stands at a coordinate.

```
https://nominatim.openstreetmap.org/search?q=<addr>&format=json&limit=1&countrycodes=us
https://nominatim.openstreetmap.org/reverse?lat=<lat>&lon=<lon>&format=json&zoom=18
```

- **A descriptive `User-Agent` is required.** Without one you get blocked, sometimes as an
  opaque error rather than a clear 403.
- **Roughly one request per second.** Respect it. This is what makes `verify_points.py` slow;
  run it in the background rather than trying to parallelise it.
- `zoom=18` on reverse is the useful level - building or POI rather than street or suburb.
- Not for bulk production use. For a recurring job, run your own Nominatim or use a paid host.

## Overpass / OpenStreetMap

Building and site outlines for the point-in-polygon test.

```
[out:json][timeout:180];
(nwr["amenity"="hospital"](S,W,N,E););
out geom tags;
```

- `out geom` returns full coordinate lists for ways and relation members. `out center` gives only
  a centroid, which is not enough for point-in-polygon.
- **The public instances go down, often.** All three mirrors were unavailable for hours during
  the build of this skill. `fetch_reference.py` tries `overpass-api.de`, `overpass.kumi.systems`,
  then `overpass.private.coffee`, and exits with a clear message rather than a stack trace.
  Use `--from-file` to re-run matching against a saved response.
- **Query by tag AND by name.** Facilities are inconsistently tagged: one hospital in the test
  layer carried `social_facility` rather than `amenity=hospital` and was invisible to a tag-only
  query. Pulling anything whose name matches a point in the layer catches these.
- Useful tags beyond `amenity=hospital`: `amenity=school`, `amenity=clinic`, `amenity=fire_station`,
  `amenity=library`, `shop=supermarket`, `office=government`, `leisure=park`, `healthcare=*`.

## Census TIGERweb (roads and boundaries)

TIGER served as a live, bbox-queryable ArcGIS REST service rather than an annual download. This
is the part people usually miss, and it is what makes the street-level maps possible with no
key and no tile server.

```
# what layers exist
https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer?f=json

# roads in a bbox, as GeoJSON
https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/8/query
  ?geometry=<xmin>,<ymin>,<xmax>,<ymax>
  &geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326
  &spatialRel=esriSpatialRelIntersects&outFields=NAME
  &returnGeometry=true&geometryPrecision=6&f=geojson
```

Transportation layers are split by map scale: **8** local roads, **6** secondary, **2** primary.
Query all three and dedupe - the same road appears in several layers, so key on
`(name, first few coordinates)`.

County boundaries follow the coastline closely enough to serve as a basemap:

```
https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query
```

`geometryPrecision=5` (about 1 m) keeps the payload sane. Draw heaviest road class first so thin
local streets sit on top.

## Outside the United States

Census and TIGER are US-only. Elsewhere:

- Drop `fetch_context.py` (or supply your own road geometry in the same shape).
- Keep OSM footprints, Nominatim, and ArcGIS - ArcGIS covers most countries; drop `countryCode`.
- National mapping agencies often publish an equivalent: Ordnance Survey Open Data in Great
  Britain, BAG in the Netherlands, cadastre extracts in France, Geoscape in Australia.

Say plainly in the report that the audit rests on two independent sources rather than four.
An audit whose evidence base is weaker than it appears is worse than one that admits its limits.
