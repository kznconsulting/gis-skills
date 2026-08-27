# Troubleshooting

Traps that cost real time on the first build of this pipeline. Most are silent failures - the
command appears to succeed and produces nothing, or produces something confidently wrong.

## Contents
- [Verifying the artifact renders](#verifying-the-artifact-renders)
- [Testing the interactive inspector](#testing-the-interactive-inspector)
- [Network and shell traps](#network-and-shell-traps)
- [Data traps](#data-traps)
- [Artifact traps](#artifact-traps)

## Verifying the artifact renders

The Browser pane cannot screenshot files outside the project directory, and the published
artifact URL is not reachable from the in-app browser. Headless Chrome renders a local file and
screenshots it in one shot, with no server:

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=9000 \
  --window-size=1300,7600 --screenshot=shot.png "file://$PWD/report.html"
```

Then read the PNG. Crop tall pages with Pillow rather than squinting at a 7600 px image:

```bash
uvx --with pillow python -c "
from PIL import Image
im = Image.open('shot.png'); im.crop((0, 2000, 1300, 3400)).save('mid.png')"
```

Dark mode: add `--force-dark-mode`. Chrome prints unrelated `task_policy_set` and certificate
warnings to stderr on macOS - they are noise, not page errors.

## Testing the interactive inspector

A screenshot only proves the first frame. To prove the controls work, append a probe script to a
copy of the page that drives them and writes the resulting state into a `<div>`, then read it
back with `--dump-dom`:

```html
<div id="PROBE"></div>
<script>
window.addEventListener("error", function(e){ (window.__err = window.__err || []).push(e.message); });
setTimeout(function(){
  var el = function(s){ return document.querySelector(s); };
  var o = { err: window.__err || [] };
  o.first = el("#insName").textContent + " | " + el("#insCount").textContent;
  o.items = document.querySelectorAll(".ins-item").length;
  o.roads = document.querySelectorAll("#insMap path.rd").length;
  el("#insNext").click();
  o.next = el("#insName").textContent;
  document.getElementById("PROBE").textContent = JSON.stringify(o);
}, 400);
</script>
```

```bash
"$CHROME" --headless=new --disable-gpu --virtual-time-budget=9000 --dump-dom "file://$PWD/probe.html" \
  | grep -o '<div id="PROBE">.*</div>'
```

An empty `err` array plus plausible counts means the page genuinely works. Zero roads or zero
items means the data never reached the page - check the marker substitution first.

## Network and shell traps

**`curl` eats the loop's stdin.** A `while read ... done < list.txt` loop containing `curl` will
run once and then silently stop creating files, because curl consumed the rest of the input.
Add `< /dev/null` to the curl call.

**Overpass returns HTTP 406 without a User-Agent.** It looks like a malformed query. Send a
descriptive `User-Agent` on every Overpass and Nominatim request.

**Overpass mirrors fail differently.** `overpass-api.de` refuses connections outright when
overloaded; the others return HTTP 502, or a 200 with an HTML error body that breaks
`json.load` with "Expecting value: line 1 column 1". Always inspect the first bytes of an
unexpected response before assuming your query was wrong.

**Buffered output vanishes when a background job is killed.** Run Python with `-u` when
redirecting to a log, or a timeout will leave you with an empty file and no idea how far it got.

**Long jobs need backgrounding, not a longer timeout.** `verify_points.py` on 30 points takes
about two minutes. Start it in the background, checkpoint after every point (it does), and do
other work meanwhile.

## Data traps

**Non-breaking spaces in source names.** `U+00A0` renders identically to a space and breaks every
exact match, join, and dictionary lookup. 23 of 29 names in the test layer carried them. Normalise
for matching, keep the raw value, and report it as a defect.

**Fuzzy name matching invents errors.** Stripping common words and testing substring containment
paired "Ka'u Hospital" with "Kauai Veterans Memorial Hospital" and produced a confident 530 km
error. Match on folded full names first (case, accents, apostrophes, plurals removed); only accept
a reduced-token match when exactly one candidate qualifies; otherwise report it unmatched and let
a human decide in `name_map.json`.

**`.title()` mangles names.** It produces "Queen'S Medical Center" and "Rehabilitation Hospital Of
The Pacific". Use the `tc()` helper in `build_report.py`, which leaves small words lowercase and
handles apostrophes.

**`@db.Date`-style midnight-UTC dates** and other timezone artefacts are not an issue here, but
coordinate precision is: round to 5-6 decimal places when embedding. Six decimals is ~0.1 m, which
is far beyond what any of these sources can support, and it triples the payload.

## Artifact traps

**No external requests.** The Artifact CSP blocks tiles, CDN scripts, and remote images. Google
Fonts is the sole exception. Any map must be vector geometry embedded in the page.

**Two marker styles in the template.** Prose uses `{{NAME}}`; generated geometry blocks use
`<!--NAME-->`. `build_report.py` fills both and fails loudly if either is left over - a missed
`<!--VIEWERDATA-->` produces a page that looks perfect until the inspector turns up empty.

**Embedded JSON must not contain `</script`.** `build_report.py` refuses to write if it does.
Source attributes are the likely culprit.

**Payload size.** A 30-point audit with street context lands around 2.5 MB, comfortably inside the
16 MB limit. If a larger layer approaches it, raise the simplification tolerance in
`fetch_context.py` and drop road layer 8 outside the immediate area before dropping detail that
carries evidence.

**Define every colour token at `:root`.** A colour whose only definition sits inside a
`@media (prefers-color-scheme: dark)` or `[data-theme]` block will not apply for viewers on the
default "system" setting, and the page renders one theme's text on the other theme's background.
