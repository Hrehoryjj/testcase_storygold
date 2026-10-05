# After submission: where the grader does not look, and a tighter version

The submitted grader is unchanged on `main` and in `harness/`. Everything
below is new and sits beside it on this branch.

## In short

A grader that a model is trained against teaches the model whatever the
grader cannot see. So after submitting I measured where the
"no unrequested changes" check is blind, on the part itself.

- **The Blind-Spot Map** puts small edits nobody asked for at 408 spots
  over the reference's outer housing and grades each one. A 3D page shows
  where the edits are caught, where they are missed and where the check
  does not look at all.
- **What it found.** The check skips about 38% of the outer housing: the
  widening strip and a box with an 8 mm margin around each group of
  controls. An edit there goes unseen, whatever its size.
- **Tight Zones** is a proposed version of the check that narrows the
  skipped area to about 15%. It keeps every current check and adds tighter
  ones, and the lower of the two scores stands, so it can catch more than
  the submitted grader and never less.
- **The Blind-Spot Audit** is how the change was proved: the same spots,
  before and after, with gates that the change must pass. It is meant for
  any later change to the grader as well.

| Measured on the same 408 spots | Submitted grader | Tight Zones |
|---|---:|---:|
| Outer housing skipped, by area | 38% | 15% |
| Spots the check looks at | 258 | 326 |
| 3 mm boss caught | 256 | 315 |
| 0.5 mm boss caught | 231 | 290 |
| Through hole caught | 244 | 298 |
| 0.15 mm step caught | 223 | 258 |
| All four edits caught at one spot | 187 | 218 |
| False alarms on a split line or a finer mesh | 1 of 516 | 1 of 652 |

Every edit is 8 mm across. The reference still scores 8.0, no example
scores higher than before, no harmless edit is newly flagged, and nothing
caught before is missed now.

## How I approached it

1. **Measure before changing.** The random-edit sweep shipped with the
   grader tests 20 spots. That is enough to show the checks work, not
   enough to show where they do not. The map tests 408 spots spread evenly
   over the outer skin, four real edits and two harmless ones at each, and
   draws the result on the part, so a blind area shows up as a patch
   rather than a number.
2. **Find the cause.** The map showed the misses were not spread out: they
   were the zones the check skips on purpose. The zones exist because the
   reference rebuilds its button wells, stick rings and the widened middle,
   so the original part cannot say what the surface there should be. But
   the zones were much wider than those rebuilds.
3. **Set the bar before the change.** The audit's gates were fixed first:
   the reference keeps full marks, no broken example scores higher, no
   harmless edit is newly flagged, and nothing caught before is missed.
4. **Change, measure, repeat.** Each version of Tight Zones was audited on
   the same spots. Rules that charged the reference or lost a catch were
   reworked until every gate passed.
5. **Keep it reversible.** The change is a separate grader file, so the
   submitted grader can be compared with it at any time, and adopting it
   is a one-line choice of which file to run.

## What Tight Zones changes

- **A zone per control, not per group.** A box around each control body,
  1.5 mm wider (4 mm for sticks, bumpers and triggers, whose rings and
  housings the reference opens slightly), at its new place and at its
  original place.
- **Button pads compared across the plane.** The task moves the D-pad and
  the face buttons to the other side, so the pad around them is compared
  with the original pad mirrored to its new side instead of being skipped.
- **The middle is checked.** The original's section at the mirror plane is
  drawn across the added width, and the candidate's middle must lie on it.
  Only a narrow band where each half meets the middle is skipped.
- **Small centred features may stay centred.** A feature that sits across
  the plane, within 11 mm of it, may be split with the halves or kept in
  the middle, as the reference does.
- **Where the original part is itself not symmetric**, the mirror check
  does not charge the candidate for it.
- **A rebuild cut into pieces by the zones is judged as one**, so a seat
  the reference rebuilt does not read as several edits.

Scores of the shipped models (out of 8.0), from the shipped captures:

| Model | Submitted grader | Tight Zones |
|---|---:|---:|
| solution (reference) | 8.000 | 8.000 |
| adversarial_unrequested_change_elsewhere | 7.726 | 7.712 |
| adversarial_missing_glyphs | 7.000 | 7.000 |
| adversarial_widened_by_30mm | 6.500 | 6.500 |
| adversarial_text_mirrored_incorrectly | 6.300 | 6.300 |
| adversarial_only_one_button_cluster_mirrored | 6.059 | 6.059 |
| adversarial_widened_15mm_clusters_at_original_spacing | 3.211 | 3.193 |
| adversarial_feature_tree_with_errors | 2.780 | 2.761 |
| adversarial_unwidened_shell_with_correct_clusters | 0.442 | 0.433 |
| input (untouched seed) | 3.000 | 2.982 |

All drops are on `no unrequested changes`. On the unrequested-change
example the tighter check sees more of its emboss band, and on the two
models with broken feature trees it finds more changed skin. The untouched
seed and the shell widened without its controls each lose 0.018 for a
different reason, described under "What is still weak".

## What is still weak

- **The top of the moved D-pad.** The reference rebuilt the seats there,
  so edits are judged by size only, and at 7 spots none of the four edits
  is caught. The map shows them in red.
- **One reference.** Every rule and threshold was set and checked on the
  one reference solution. A second correct solution, modelled a different
  way, is the next test.
- **Controls that were not moved.** The untouched seed and the shell
  widened without its controls each get one flagged patch of about
  23 mm² on the left underside, next to the left bumper, which the old
  8 mm margin covered. It costs 0.018 points and changes no ranking. It
  appears only in the two models whose controls were not moved, so it is
  most likely the zones being placed for a move that did not happen; the
  exact cause is not traced yet. Not moving the controls is already
  charged by the cluster criteria, so this is the same mistake counted a
  second time, and it is the next thing to fix, through the audit.
- **Grading takes longer**, because both versions of the check run.
  Scoring the reference from its capture takes about 54 s instead of
  22 s, and the most broken example (34 broken features) about 8 minutes.
  Once Tight Zones is checked on a second solution, the old check can be
  dropped, and the slow case is worth profiling.
- **The shipped random-edit sweep.** Building the map showed two things
  about `sweep_synthetic.py`. Its edit code refines only mesh triangles
  with a corner near the edit, so on a flat face meshed with large
  triangles the edit can silently not be made; its own 20 spots were not
  affected in practice. And it picks spots on the whole skin, so 9 of its
  20 lie on the inside of the hollow housing. The map's own edits handle
  both. The shipped sweep is left as submitted, so its numbers in
  `NOTES.md` stay comparable; carrying the fix into it is a next step.

## Files on this branch

| Path (under `test_example/`) | What it is |
|---|---|
| `SolidWorks/1_playstation_controller/tests/task/harness_tight_zones/` | The Tight Zones grader, its README and a diff against the submitted grader |
| `tools/blindspot_map.py`, `tools/build_blindspot_page.py`, `tools/blindspot_page_template.html` | The Blind-Spot Map |
| `tools/blindspot_audit.py`, `tools/BLINDSPOT_AUDIT.md` | The Blind-Spot Audit and how to use it |
| `SolidWorks/1_playstation_controller/evidence/blindspot/` | The 3D map (open it in a browser and switch between the two graders), the before and after report, and two pictures |

## Run it

Grade a part with Tight Zones, exactly like the submitted grader:

```bat
cd test_example\SolidWorks\1_playstation_controller
python tests\task\harness_tight_zones\harness.py solution\solution.SLDPRT
```

Without SolidWorks, from a shipped capture:

```bash
cd test_example/SolidWorks/1_playstation_controller
gunzip -c evidence/captures/solution.json.gz > /tmp/solution.json
python3 tests/task/harness_tight_zones/harness.py --score-from /tmp/solution.json
```

Audit the submitted grader and Tight Zones on the same spots (no
SolidWorks, Python 3 only):

```bash
cd test_example
python3 tools/blindspot_audit.py run audit/before
BLINDSPOT_HARNESS=SolidWorks/1_playstation_controller/tests/task/harness_tight_zones/harness.py \
  python3 tools/blindspot_audit.py run audit/after --spots-from audit/before
python3 tools/blindspot_audit.py compare audit/before audit/after
```
