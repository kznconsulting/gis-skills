# Methodology

Read this before defending a verdict, changing a threshold, or explaining the audit to
whoever owns the data.

## Contents
- [Why several sources](#why-several-sources)
- [The consensus location](#the-consensus-location)
- [The verdict rule](#the-verdict-rule)
- [Why offset alone is a bad test](#why-offset-alone-is-a-bad-test)
- [Systematic shift versus per-point error](#systematic-shift-versus-per-point-error)
- [Reverse geocoding as evidence](#reverse-geocoding-as-evidence)
- [What this audit cannot tell you](#what-this-audit-cannot-tell-you)

## Why several sources

A single geocoder is not ground truth. Each has a different reference database, a different
interpolation model, and different failure modes, and each will occasionally return something
confidently wrong. The audit is only trustworthy because the sources fail *independently*:
Census TIGER interpolates along address ranges, Esri matches against a commercial rooftop
database, and OpenStreetMap footprints are hand-drawn from imagery. Nothing links them, so
their agreement is meaningful.

This is also why the street network in the artifact is drawn from the Census while the
building outline comes from OpenStreetMap. If both came from OSM, a reader would see a
building sitting neatly on a road and conclude the position was corroborated, when in fact
one dataset was agreeing with itself.

## The consensus location

`adjudicate.py` clusters the candidate locations and keeps the largest cluster, discarding
the rest. It does not average them.

That distinction matters more than it looks. Geocoders fail badly rather than gradually: three
sources will sit within 40 m of each other and a fourth will land 3 km away on a similarly
named street. An average is dragged by that outlier, the offset is inflated, and the audit
then reports a fabricated error with complete confidence - the worst failure this tool can
have, because a false accusation costs the data owner more than a missed error.

Ties are broken toward the cluster containing a mapped footprint, then toward the one holding
the rooftop geocode. Every candidate is recorded in `verdict.json` with a `used` flag, so a
rejected outlier stays visible rather than silently disappearing.

## The verdict rule

| Verdict | Condition |
|---|---|
| **Correct** | inside the footprint, OR within 50 m of its edge, OR within 150 m of consensus |
| **Marginal** | 150-350 m from consensus and clear of the footprint |
| **Wrong** | beyond 350 m from consensus |

Thresholds are `--ok-edge`, `--ok-consensus`, `--marginal`. They suit sites of roughly
building-to-campus scale. Tighten them for dense urban point layers where 150 m spans several
blocks; loosen them for rural facilities on large parcels.

## Why offset alone is a bad test

Offset is measured to the consensus centre. A hospital campus can be 300 m across, so a point
correctly placed on the emergency entrance can sit 200 m from that centre and look worse than
a point 160 m away on a neighbour's lot.

That is why the footprint test outranks distance, and why the report shows both columns and
carries a caption explaining the apparent inconsistency. A reader who spots two rows where the
smaller offset was flagged and the larger passed, with no explanation, stops trusting every
other row on the page. Pre-empt it.

## Systematic shift versus per-point error

Before reporting anything, look at the bearings from each bad point to its true position.

- **Bearings scatter** - independent human errors. Each point was digitised or transcribed
  wrongly on its own. Report them individually.
- **Bearings cluster and the magnitudes match** - a datum or projection fault. The whole layer
  is shifted. The finding is "the CRS handling is wrong", the fix is one reprojection, and
  listing individual points would send the owner chasing symptoms.

`adjudicate.py` computes this and states its conclusion at the top of `findings.md`. Carry that
conclusion into the report; it changes what the owner has to do.

## Reverse geocoding as evidence

Reverse-geocoding each recorded coordinate and naming what stands there is the cheapest step in
the pipeline and the most persuasive output. "Kohala Hospital is 1.65 km from its true position"
is a claim the reader has to take on faith. "The Kohala Hospital point sits on Island Short-Stop
& Deli" is a claim they can check in ten seconds, and once they check one they believe the rest.

Put these in the table and the standfirst. Name the actual thing - delicatessen, farm track,
apartment block, playing field.

## What this audit cannot tell you

Be honest about the limits, in the report as well as to yourself.

- **The reference sources can be wrong too.** OSM footprints are volunteer-drawn and sometimes
  cover one building of a larger campus. Where sources disagree widely (`spread_m` is large in
  `verdict.json`), the consensus is weak - say so rather than asserting a precise correction.
- **A point can be "wrong" on purpose.** Some layers deliberately use a mailing address, a site
  entrance, or a parcel centroid. If the offsets are consistent and all land on entrances or
  driveways, the convention may be intentional. Ask before calling it a defect.
- **An unmatched name is not a passing grade.** A point with no footprint and no geocode is
  unresolved, not correct. It shows as `UNKNOWN`; do not let it quietly count as a pass.
- **Currency.** OSM and the geocoders reflect the present. A facility that genuinely moved makes
  the old coordinate wrong, but the finding is "this record is stale", not "this was mis-digitised".
