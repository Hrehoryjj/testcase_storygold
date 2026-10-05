# Tight Zones

An improved version of the "no unrequested changes" check. The main grader
in `../harness/` is not changed by it.

**The problem.** The check must ignore the areas the task changes: around
the buttons and the strip where the controller is widened. It ignored them
with wide boxes, so about 38% of the outer surface was never checked.

**The fix.** Ignore only what the task really changes:

- a tight zone around each button (1.5 mm, 4 mm for sticks, bumpers and
  triggers), instead of one wide box per group of buttons;
- the button pads the task moves to the other side are compared with
  their mirror image instead of being skipped;
- the widened middle is compared with the original cross-section.

The old check still runs and the lower score counts, so this version can
catch more than the main grader, never less.

**The result**, measured on the same 408 test points:

| | Main grader | Tight Zones |
|---|---:|---:|
| Outer surface not checked | 38% | **15%** |
| Points checked | 258 | **326** |
| 3 mm bump caught | 256 | **315** |
| Hole caught | 244 | **298** |
| 0.15 mm step caught | 223 | **258** |
| False alarms on harmless edits | 1 | 1 |

The correct solution keeps 8.0 (also in a live SolidWorks run), and no
shipped example scores higher. `tight_zones.diff` shows every change
against the main grader, and
[NOTES_GRADER_AUDIT.md](../../../../../../NOTES_GRADER_AUDIT.md) explains the approach
and the next steps.

## Run it

Exactly like the main grader, from `SolidWorks/1_playstation_controller`:

```bat
python tests\task\harness_tight_zones\harness.py solution\solution.SLDPRT
```
