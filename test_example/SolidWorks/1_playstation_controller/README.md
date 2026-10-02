# Harness — continuous-scoring harness for the PS3 controller task

## Running

Every command below is run from `task 1`, with SolidWorks **already open**
and your work **saved** — each run closes the session's open documents before
opening the next candidate.

The script is `tests\task\harness\harness.py`. Handing Python the directory
instead of the file gets you `can't find '__main__' module in
'...\tests\task\harness'`; that is a missing filename, not a broken harness.

### The usual run, in order

Say you have a reference part and a folder of candidates.

**0. Check the yardstick is the right one.** Nothing here is graded in
absolute terms — every criterion is a delta against the seed part, frozen as
measurements in `tests/task/prompt/input.json`. If the candidates were edited
from the seed that baseline was taken from, leave it alone. If they started
from a different part, re-freeze first — and keep a copy of the current
`input.json`, because the command overwrites it:

```bat
python tests\task\harness\harness.py --capture-baseline path\to\seed.SLDPRT
```

**1. Prove the connection with one part.** Do not open with the whole corpus:
it is about 40 to 50 s per part (measuring plus scoring), and if COM did
not attach you want to know now.

```bat
python tests\task\harness\harness.py reference.SLDPRT
```

This must come back `8.0/8.0`, `passed: true`. **If the reference is not
8.000, stop** — the baseline or a threshold is wrong, not the reference. It is
the one check worth doing every time.

**2. Measure the corpus.** Measuring, not scoring: this is the expensive half
and it only has to happen once.

```bat
python tests\task\harness\harness.py --batch reference.SLDPRT examples --capture-only
```

`examples` is globbed for `*.SLDPRT` recursively and each file is labelled by
its stem. Captures land in `results\captures\`, and the command prints the
line that scores them.

**3. Score.** About 20 s per part on one core (the skin and clearance checks
walk the full meshes), and no SolidWorks involved at all:

```bat
python tests\task\harness\harness.py --batch --score-from results\captures
```

You get a table on stdout, `results\summary.md` and `.csv`, and per model
`results\<model>.envelope.json` (the score envelope), `.log` (the readable
breakdown with per-criterion reasons) and `results\full\<model>.report.json`
(every measurement taken).

A `.log` reading `UNGRADABLE` means the part was **not measured** — it would
not open, has no bodies, or has no determinable symmetry plane. That is not
the same verdict as "measured and wrong", and the reason is printed with it.

Useful additions:

```bat
... --batch DIR --only solution     REM filter labels by substring
... --batch DIR --timeout 1800      REM raise the 900 s per-part cap
... --batch DIR --out somewhere     REM default is results\
python tests\task\harness\harness.py --help
```

### Why steps 2 and 3 are separate

`--batch reference.SLDPRT examples` with no flags measures and scores in one
pass, and that is fine for a one-off. But then every threshold or formula
change costs another trip through SolidWorks.

Measuring needs SolidWorks and takes 15–30 s per part. Scoring is arithmetic
over two dictionaries, about 20 s per part since the mesh checks (2.5.0).
Split them and you measure once and re-score as often as you like — which also turns the stored captures into a regression suite
that needs no CAD: change the harness, re-run step 3, see immediately which
models moved.

The same split works for a single part:

```bat
python tests\task\harness\harness.py --capture-only part.SLDPRT -o cap.json
python tests\task\harness\harness.py --score-from cap.json
```

A capture carries its schema version, so `--score-from` warns rather than
silently scoring against fields that are not there. A measurement a stored
capture predates is reported UNVERIFIABLE and drops out of its weighted mean —
scores stay comparable, they just rest on less evidence. To pick up a new
measurement, re-freeze the baseline and re-capture.

### Grading a single part

```bat
python tests\task\harness\harness.py path\to\candidate.SLDPRT
```

With no argument it grades whatever document is currently active. It prints a
human-readable summary to **stderr** and exactly one JSON envelope to
**stdout** — that envelope is the contract the evaluation pipeline reads, and
batch mode never touches its shape:

```json
{
 "task_id": "solidworks-0001-playstation-controller",
 "score": 8.0,
 "max_score": 8.0,
 "passed": true,
 "subscores": {
  "rebuild health": 1.0,
  "modelling hygiene": 1.0,
  "widened by 15 mm": 1.0,
  "clusters at mirrored positions": 1.0,
  "no new control interference": 1.0,
  "left-handed layout achieved": 1.0,
  "no unrequested changes": 1.0,
  "markings preserved": 1.0
 },
 "harness_version": "2.5.0"
}
```

#### What the weights mean

`max_score` is 8.0, and the eight components are not equal, because the
instruction is not a list of equals. Weights live in `ALL_CRITERIA` in the
harness, each with its reasoning next to it; `task.toml` carries their sum.

| | Weight | |
|---|---:|---|
| widened by 15 mm | 1.5 | what the task **asks for**: 5.0 of 8.0 |
| clusters at mirrored positions | 1.5 | |
| left-handed layout achieved | 2.0 | heaviest: the demand the last sentence is about |
| no new control interference | 0.5 | what merely **constrains** the edit: 1.0 of 8.0 |
| no unrequested changes | 0.5 | |
| rebuild health | 0.5 | preserving design intent and the feature tree: 1.0 of 8.0 |
| modelling hygiene | 0.5 | |
| markings preserved | 1.0 | the face-button symbols must **remain** (instruction.md, last sentence) |

The split matters because five of them (interference, unrequested changes,
rebuild health, hygiene, markings) are **negative** criteria: a candidate who
never opened the file passes all of them. Their combined weight is therefore
the floor a do-nothing submission collects: 3.0 of 8.0 (37.5%), which the
untouched seed scores exactly.

#### A tree that rebuilds with errors is discounted, not zeroed

Every geometry criterion is multiplied by the `rebuild health` score, and the
unscaled value is kept as `measured_score`. Before 2.4.0 any newly broken
feature zeroed all geometry, so one broken feature and a tree in ruins scored
the same, and what the candidate actually got wrong was never measured.
Health reaches 0 at 13.3% of the tree newly broken (1.5 times stricter than
the 20% it used when it priced only its own 0.5 point), because it now also
scales 7.0 points of geometry. The factor was chosen so that a part that does
not rebuild does not outrank a clean part with a geometry mistake: on this
corpus that ordering holds from a factor of 1.37 up, and 1.5 is the nearest
round value above it. That threshold comes from this corpus, not from the
task, and is stated as such. A rebuild that could not run at all still zeroes
the geometry.

#### Controls stay seated in their openings

`no new control interference` takes the weakest of three readings. The first is
the boolean-intersect volume among the controls and against the housing. The
second (2.5.0) is that every control whose centre sits on the axis of a round
housing opening in the seed (the sticks, the face buttons, the PS button)
still sits on the axis of one in the candidate: free up to 1 mm off, zero at
5 mm, averaged over those controls. The bore it sits in (the smallest
opening on its axis) must also keep the seed's radius: free within 0.2 mm,
zero at 2 mm. Drafted (conical) bores count as openings, without a radius
comparison. It reads only body centroids and the housing's own cylinders and
cones, so it asks the candidate to be consistent with
itself, not to match the reference. A control can clear the housing and still
sit beside the hole it belongs in; this sees that. On the shipped corpus it
changes no total: the models it fires on (sticks 7.5 mm off their openings in
`adversarial_widened_15mm_clusters_at_original_spacing` and in the two broken
models) already lose this criterion through interference.

#### Every control keeps its gap to its own opening

Coaxiality covers round openings only, and the interference volume forgives
up to 5 mm3. The third reading (2.5.0) covers every control, round or not.
From the meshes in the capture it takes horizontal sections through the
control every 0.5 mm, casts 16 rays around it, and keeps on each ray the
tightest gap between the control and the housing. The seed's gaps are
compared with the candidate's, each inside its own part, so neither the frame
nor the widening enters. Only narrowing is charged: free up to 0.15 mm, zero
at 0.6 mm, averaged over the controls. Widening is allowed because the
reference itself opens up its stick rings (about 1.2 mm) and its face-button
wells (up to 6 mm). Each control is compared with the closest seed pattern of
its own kind, in either handedness, so a matcher that pairs two look-alike
buttons the other way round is not mistaken for a moved button.

On the reference no gap narrows by more than 0.008 mm. A control pushed
0.3 mm sideways in its opening loses this criterion and nothing else. On the
shipped corpus it found one defect the examples do not advertise: in
`adversarial_only_one_button_cluster_mirrored` a face button sits 0.19 mm
closer to the wall of its well than any seed button, 0.09 mm into it, under
the interference allowance. That costs the model 0.003 points and changes no
ranking. Captures older than schema /7 carry no meshes; for them this reading
is skipped with a note.

#### `passed` means flawless, not "good enough"

`finalize()` sets `passed` to `all(subscore >= 1.0)` — every criterion
perfect, not a pass mark. A candidate at 80% comes back `passed: false`, and
that is the intended reading: **`score` says how much of the task was done,
`passed` says whether the answer is fully correct.** In this corpus only two
parts pass, the reference and its byte-identical copy, which doubles as the
determinism check.

Turning `passed` into a threshold would put the binary verdict back that the
continuous scoring exists to remove, just at a different cut point — and the
cut point would have to be invented, since nothing in the data suggests one.
Where a pipeline needs an accept/reject decision, it belongs to whoever
consumes the envelope and is taken from `score / max_score`.

To keep every measurement rather than just the scores from a single run, set
`HARNESS_REPORT_JSON` to an output path (batch mode does this for you):

```bat
set HARNESS_REPORT_JSON=out\solution.report.json
python tests\task\harness\harness.py ..\SolidWorks\1_playstation_controller\solution\solution.SLDPRT
```

### What `--batch` accepts

A list of parts, a directory, or both:

```bat
python tests\task\harness\harness.py --batch ..\SolidWorks\1_playstation_controller
python tests\task\harness\harness.py --batch a.SLDPRT b.SLDPRT c.SLDPRT
python tests\task\harness\harness.py --batch
```

With no argument at all it takes the shipped task directory.

A directory laid out like the shipped task (`solution/`, `examples/`,
`environment/`) is expanded with the usual labels, so the reference, the
adversarials and the untouched seed keep the names the results are indexed by.
Any other directory is globbed recursively and each part is labelled by its
file stem.

Each part is graded in its own child process, so one wedged COM call can be
timed out without ending the batch.

### Re-freeze the baseline

`tests/task/prompt/input.json` is the seed part frozen as measurements —
bodies, roles, the mirror plane, the interference budget, the modelling census
and the seed's own feature errors. It is the yardstick every criterion is
measured against, so it has to describe the part candidates actually started
from, measured the way this harness measures.

```bat
python tests\task\harness\harness.py --capture-baseline ..\SolidWorks\1_playstation_controller\environment\input.SLDPRT
```

The shipped baseline is the upstream one plus three blocks taken from a fresh
schema /7 capture of the same seed (body ids mapped to the baseline's by
centroid): `housing_halves` (for the width criterion), `housing_faces` (every
housing face with its full tessellation and its cylinder or cone axis, for the
skin checks and the opening checks) and `control_meshes` (the tessellation of
every control, for the clearance check). The meshes make the file about 3.4 MB; captures and the baseline keep each list of numbers on one line.

Re-measures the seed and rewrites the whole baseline. Run it when the seed
part changes, when the harness starts recording something it did not record
before, or when porting the rubric to another part. Everything downstream
shifts, so re-score the corpus afterwards and check the reference still lands
on full marks.

```bat
python tests\task\harness\harness.py --capture-seed-rebuild ..\SolidWorks\1_playstation_controller\environment\input.SLDPRT
```

Refreshes **only** the `rebuild` block — the per-feature error census of the
untouched seed — and leaves every geometric measurement alone.

That census is the one part of the baseline that describes the *machine* as
much as the part. Mass properties and centroids are the same wherever you
measure them; which features a SolidWorks build reports as errored or warning
is not. `rebuild health` grades newly broken features *relative to* this
census, precisely so a seed that already carries faults does not charge them
to every candidate — but that only works if the census was taken where the
grading happens. On this seed it is currently empty (199 features, no errors,
no warnings), so any feature the grading machine flags counts against whoever
is being graded.

So: re-run this one on the machine that will do the grading, before a session,
and if it comes back non-empty, the seed is reporting faults there that it did
not report when it was frozen. Re-run `--capture-baseline` only when the
geometry itself needs re-freezing — it is the heavier operation and moves
every criterion at once.

### Validate the scoring logic without SolidWorks

```bash
python3 tools/selftest_synthetic.py
```

Run from `test_example/`. Runs anywhere, including Linux and CI. `Grader`
consumes two plain dictionaries and touches no COM object, so its arithmetic is
testable in isolation. The script scores the shipped SolidWorks captures in
`evidence/captures/` (one per model, gzipped) and synthetic variants built from
the reference capture, and asserts:

- the reference scores 8.0 and the untouched seed the 3.0 floor;
- every example loses points, and only on the criteria it gets wrong (the two
  models with rebuild errors may lose anywhere, since health scales them);
- the reference cut at the mirror plane into left and right pieces, another
  valid way to widen, still scores 8.0;
- on faces the skin checks cover, a 0.15 to 0.25 mm step (raised or
  sunk), and a boss, pocket or through hole 16 mm across, cost
  `no unrequested changes` and nothing else, and a zero-height split line on
  the same face costs nothing;
- a control pushed 0.3 mm sideways in its opening costs
  `no new control interference` and nothing else.

```bash
python3 tools/sweep_synthetic.py 20
```

Measures what `no unrequested changes` catches: boss, pocket and hole edits
at random spots on the skin the task did not ask to change. Slow: each grade
takes about 25 s, so 320 grades take about two hours on one core. Results are in
NOTES.md at the repository root.

This validates the **scoring logic**, not the measurement layer — `capture()`,
`assign_roles()` and the port-light face matching all read live geometry.

---

## Not machine-graded

- **Whether the logos are correct rather than flipped.** The instruction
  asks for exactly this, and it is **unverifiable by construction, not by
  accident**: the PS triangle, circle, cross and square are left-right
  symmetric split-faces, so a mirror of them is geometrically undetectable.
  The harness records the reading as `UNVERIFIABLE` and gives it no weight
  rather than scoring a proxy for it. Handedness is witnessed instead by
  cluster sides, body inertia signs and the housing side signature.
- **Whether standard parts are correct.** The second half of the same
  sentence. Nothing in this grader reads a BOM, a configuration or a
  library reference, and grading is name-blind by contract, so a
  Toolbox screw mirrored into a left-hand thread reads the same as one
  left alone.
- **Whether engraved text is legible or correctly oriented.** What is read
  is which SIDE of the symmetry plane engraving-scale faces sit on,
  weighted by area. `adversarial_text_mirrored_incorrectly` is caught by
  the port-light witness — that the indicator glyphs moved across the
  plane — not by anything about the text itself.
- **The exact width of the shell itself.** Since 2.4.0 `widened by 15 mm`
  is half mirror-pair growth against +15 mm and half the growth of the
  housing halves (area-weighted face X either side of the plane): full
  credit from +15 mm to +30 mm, rising from 0 below, falling to 0 at +45 mm.
  The shell is not sized against exactly 15 mm because the shipped reference
  grows its housing halves +20.6 mm (bbox +21.9) while its controls move
  +15.0. For the same reason "controls follow the grips" is reported in the
  cluster criterion but not scored. The halves witness is a proxy: hollowing
  the shell or adding large faces also moves it.
- **Unrequested edits the skin checks cannot see.** `no unrequested
  changes` looks at the part of the housing the task did not ask to change:
  the seed skin moved onto the candidate, minus the widening strip and an
  8 mm margin around every control. Three checks share it. `skin_splits`
  (2.4.0) finds a new face cut into an old one that stays on the old
  surface: a shallow insert 0.1 to 0.3 mm proud or sunk, which is what
  `adversarial_unrequested_change_elsewhere` is (a 0.15 mm band).
  `skin_holes` (2.5.0) finds a patch of an otherwise intact old face that is
  no longer there: a boss, pocket or hole of any height. From schema /7 the
  seed mesh is resampled every 1.5 mm and each point asks the candidate's
  own triangles for same-facing surface within 0.3 mm (no sideways
  allowance, patches judged by size and grouped across face edges). The
  seed skin is moved by the controls' widening and by the shell's own move,
  registered per side, so a candidate that widens the shell more than its
  controls (as the reference does) is not charged. On a face the candidate
  rebuilt (less than 80% of it in place; the reference reshapes its grips,
  about 8,000 mm2) a patch of 60 to 500 mm2 still counts, and `skin_mirror`
  (2.5.0) compares the candidate there with its own mirror image, since any
  honest rebuild stays symmetric. What none sees: an edit inside a control
  margin or the strip, an edit under 0.3 mm or made on both halves alike on
  a rebuilt face, a 0.5 mm offset on a face along the widening axis, and a
  fillet on an edge. `tools/sweep_synthetic.py` measures this on random
  spots (258 of 320 edits caught). Samples come from SolidWorks tessellation, so a
  much coarser image-quality setting can hide an edit; it cannot invent one.
- **How much confidence the sidedness verdict deserves.** On genuinely
  edited models the guard rests on ONE readable witness: the port-light
  glyphs, because `housing_side_signature` returns `UNVERIFIABLE` whenever
  the housing is remodelled. A part translated 57.5 mm still scores the
  handedness criterion 1.000 with a note. Widening that evidence base is
  the open item for the next revision.
