# GIS Skills

Agent skills for geospatial work, built for [Claude Code](https://claude.com/claude-code) and
[Codex](https://developers.openai.com/codex). Each skill is a self-contained folder: instructions
an agent reads, plus scripts it runs.

Maintained by [KZN Consulting](https://kznconsulting.com). MIT licensed — use them, fork them,
send patches.

## Skills

### [`geo-point-audit`](skills/geo-point-audit) — is this point layer actually correct?

Point layers rot quietly. Somebody digitises a facility by eye, snaps it to the wrong building,
or drops it on the road centreline instead of the site, and the error survives every
republication because nothing downstream ever checks.

Give this skill a `.shp`, `.geojson`, or `.csv` of facilities and it checks every point against
four independent references, then publishes a street-level audit with corrected coordinates.

![Overview chart](docs/example-overview.png)

On a 29-point state hospital layer it found **8 points on the wrong property** — one 7 km out on a
farm track, another sitting on a delicatessen, a third on a college playing field.

**How it decides.** A coordinate is only as trustworthy as the number of independent sources that
agree with it. One geocoder can be wrong; three geocoders plus a building outline plus a reverse
lookup of what physically stands there cannot all be wrong the same way. Candidate locations are
clustered and the largest cluster wins, so a single bad geocode cannot drag the result — averaging
would let one 3 km outlier manufacture a fake error with total confidence.

**The footprint test outranks distance.** A point on the correct building can still sit 210 m from
the centre of a large campus, while a point 162 m out can be on a neighbour's lot. Distance alone
ranks those backwards.

![Inspector](docs/example-inspector.png)

The report's inspector steps through every point: street network, building outline, recorded
position in red, true position in green, and an arrow between them. Streets come from the US
Census and buildings from OpenStreetMap — deliberately independent, so where they agree that
agreement means something.

**It also checks for a systematic shift first.** If every bad point moves the same way by the same
amount, the finding is "your CRS handling is wrong", not "these points are wrong" — a completely
different repair.

## Install

Requires [`uv`](https://docs.astral.sh/uv/) (`brew install uv`). No other dependencies; the
scripts fetch what they need per run.

```bash
git clone https://github.com/kznconsulting/gis-skills.git ~/Code/gis-skills
```

Then link the skills into your agent. Claude Code:

```bash
ln -sfn ~/Code/gis-skills/skills/geo-point-audit ~/.claude/skills/geo-point-audit
```

Codex:

```bash
ln -sfn ~/Code/gis-skills/skills/geo-point-audit ~/.codex/skills/geo-point-audit
```

Symlinking rather than copying means `git pull` updates the skill in place.

Verify it registered by asking your agent to list its skills, or just hand it a dataset — the
skill is written to trigger on phrases like *"are these coordinates right"*, *"the pins look
off"*, or *"QA this shapefile"*.

## Using it

Point your agent at a dataset in plain language:

> These hospital locations look wrong to me — can you verify them? `~/Downloads/hospitals`

The agent runs the pipeline, reads the findings, writes the report copy, and publishes the
artifact. Roughly four seconds per point, most of it waiting on rate-limited geocoders.

To drive it yourself, see [`SKILL.md`](skills/geo-point-audit/SKILL.md) for the run order and
[`references/`](skills/geo-point-audit/references) for methodology, endpoints, and the traps that
cost real time.

## Data sources

All free, no API keys at audit volumes.

| Source | Role |
|---|---|
| [US Census Geocoder](https://geocoding.geo.census.gov/) | Address-range interpolation |
| [ArcGIS World Geocoder](https://developers.arcgis.com/rest/geocode/) | Rooftop-level matches |
| [Nominatim](https://nominatim.org/) | Forward and reverse geocoding (OpenStreetMap) |
| [Overpass](https://overpass-api.de/) | Building outlines (OpenStreetMap) |
| [Census TIGERweb](https://tigerweb.geo.census.gov/) | Street network and boundaries |

These are volunteer-funded shared services. The skill clusters queries into small bounding boxes
(on the test layer, 741 km² instead of 158,000 km²), paces requests, and backs off on HTTP 429
rather than stampeding the mirrors. Please keep it that way if you fork it.

Coverage outside the United States is reduced: the Census sources are US-only, so the audit falls
back to OpenStreetMap and ArcGIS. The report says so rather than presenting a weaker evidence base
as the full one.

## Platform

Pure Python, no shell dependencies, text I/O pinned to UTF-8 throughout — place names carry
ʻokina, macrons, and accents that a legacy Windows code page cannot represent. Runs anywhere
`uvx` does. Two conventions in the docs are macOS shell (`$HOME` expansion and the Chrome path);
PowerShell equivalents are noted in `SKILL.md`.

## Contributing

Issues and pull requests welcome. If you add a skill, keep the shape: a `SKILL.md` that explains
*why* as well as *what*, scripts that are resumable and fail loudly, and reference docs an agent
can load only when it needs them.

## License

[MIT](LICENSE)
