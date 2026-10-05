# Tight Zones

A proposed change to the "no unrequested changes" check, kept as a separate
file so the grader in `../harness/` is not touched.

## In short

The check skips the parts of the housing the task is allowed to change.
Today that skip is wide: the widening strip and a box around each group of
controls with an 8 mm margin, about 38% of the outer skin, and an edit there
goes unseen whatever its size. Tight Zones narrows it to about 15%.

It keeps every current check and adds tighter ones. The criterion is graded
both ways and the lower score stands, so it can catch more than today, never
less.

Measured with the Blind-Spot Audit (`tools/BLINDSPOT_AUDIT.md`) on the same
408 spots of the reference's outer skin:

| | Today | Tight Zones |
|---|---|---|
| Outer skin skipped, by area | 38% | 15% |
| Spots checked | 258 | 326 |
| 3 mm boss caught | 256 | 315 |
| 0.5 mm boss caught | 231 | 290 |
| Hole caught | 244 | 298 |
| 0.15 mm step caught | 223 | 258 |
| False alarms (split line, re-mesh) | 1 | 1 |

The reference keeps 8.0, no shipped example scores higher, no harmless edit
is newly flagged, and nothing caught today is missed.

## What changes

- **A zone per control, not per group.** A box around each control body,
  1.5 mm wider (4 mm for sticks, bumpers and triggers, whose rings and
  housings the reference opens slightly), at its new place and at its
  original place moved by the task.
- **Button pads compared across.** The task moves the D-pad and the face
  buttons to the other side. A seed face that lies on one of those pads is
  compared with the candidate mirrored to its new side, so the pad around
  the buttons is checked instead of skipped.
- **The middle is checked.** The original's section at the plane is drawn
  across the added width and the candidate's middle must lie on it. Only a
  band at each join of a half and the middle is skipped.
- **Small centred features may stay centred.** A seed face that sits across
  the plane and ends within 11 mm of it on both sides may be split with the
  halves or kept in the middle, as the reference does.
- **Where the original itself is not symmetric**, the mirror check does not
  charge the candidate for it.
- **A rebuild cut into pieces by the zones is judged as one**, so a seat the
  reference rebuilt does not leave mid-size patches that read as edits. A
  patch on intact faces keeps the intact-face size rule.

## Limits

- Every rule and threshold was set on one reference solution. A second
  valid solution built differently is the next test.
- The top of the D-pad after the move is still weak: the reference rebuilt
  the seats there, so edits are judged by size only (7 spots catch none of
  the four edits).
- The untouched seed and the shell widened without its controls each lose
  0.018 on this criterion: one patch of about 23 mm² next to the left
  bumper, seen only where the controls were not moved. Not moving them is
  already charged elsewhere, so this counts it twice; the cause is not
  traced yet.
- Grading takes longer, since both versions of the criterion run: about
  54 s instead of 22 s for the reference from its capture, and about
  8 minutes for the example with 34 broken features.

## Run it

From `test_example`:

```
BLINDSPOT_HARNESS=SolidWorks/1_playstation_controller/tests/task/harness_tight_zones/harness.py \
  python3 tools/blindspot_audit.py run audit/tight --spots-from audit/before
python3 tools/blindspot_audit.py compare audit/before audit/tight
```

`tight_zones.diff` shows every change against `../harness/harness.py`.
