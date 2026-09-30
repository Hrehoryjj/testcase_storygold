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
it is 16–29 s per part, and if COM did not attach you want to know now.

```bat
python tests\task\harness\harness.py reference.SLDPRT
```

This must come back `7.0/7.0`, `passed: true`. **If the reference is not
7.000, stop** — the baseline or a threshold is wrong, not the reference. It is
the one check worth doing every time.

**2. Measure the corpus.** Measuring, not scoring: this is the expensive half
and it only has to happen once.

```bat
python tests\task\harness\harness.py --batch reference.SLDPRT examples --capture-only
```

`examples` is globbed for `*.SLDPRT` recursively and each file is labelled by
its stem. Captures land in `results\captures\`, and the command prints the
line that scores them.

**3. Score.** Milliseconds, and no SolidWorks involved at all:

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
over two dictionaries. Split them and you measure once and re-score as often
as you like — which also turns the stored captures into a regression suite
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
 "score": 7.0,
 "max_score": 7.0,
 "passed": true,
 "subscores": {
  "rebuild health": 1.0,
  "modelling hygiene": 1.0,
  "widened by 15 mm": 1.0,
  "clusters at mirrored positions": 1.0,
  "no new control interference": 1.0,
  "left-handed layout achieved": 1.0,
  "no unrequested changes": 1.0
 },
 "harness_version": "2.1.5"
}
```

#### What the weights mean

`max_score` is 7.0, and the seven components are not equal, because the
instruction is not a list of equals. Weights live in `ALL_CRITERIA` in the
harness, each with its reasoning next to it; `task.toml` carries their sum.

| | Weight | |
|---|---:|---|
| widened by 15 mm | 1.5 | what the task **asks for** — 5.0 of 7.0 |
| clusters at mirrored positions | 1.5 | |
| left-handed layout achieved | 2.0 | heaviest: the demand the last sentence is about |
| no new control interference | 0.5 | what merely **constrains** the edit — 1.0 of 7.0 |
| no unrequested changes | 0.5 | |
| rebuild health | 0.5 | preserving design intent and the feature tree — 1.0 of 7.0 |
| modelling hygiene | 0.5 | |

The split matters because the bottom four are **negative** criteria: a
candidate who never opened the file passes all of them. Their combined weight
is therefore the floor a do-nothing submission collects — 28.6% here, against
the 50% it collected when all five geometry components were equal.

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

Runs anywhere, including Linux and CI. `Grader` consumes two plain
dictionaries and touches no COM object, so its arithmetic is testable in
isolation. The script synthesises candidates by transforming the real baseline
measurements to match each adversarial description, then asserts 13 properties
(the reference scores full marks; a broken feature tree zeroes the geometry
criteria while still emitting all seven subscores; every adversarial is
distinct; doing nothing loses to every real attempt; destroying the evidence
does not pay; and so on).

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
- **A shell widened without its controls moving.** The width criterion
  measures the growth of mirror-pair separation, which is deliberately
  robust to a housing remodel and for that reason blind to an edit that
  widens the shell and leaves the bodies where they were. The X span is
  captured and marked `diagnostic only -- not scored`. This is a limit of
  the metric, not of the files.
- **How much confidence the sidedness verdict deserves.** On genuinely
  edited models the guard rests on ONE readable witness: the port-light
  glyphs, because `housing_side_signature` returns `UNVERIFIABLE` whenever
  the housing is remodelled. A part translated 57.5 mm still scores the
  handedness criterion 1.000 with a note. Widening that evidence base is
  the open item for the next revision.
