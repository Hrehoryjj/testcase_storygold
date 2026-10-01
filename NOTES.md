# PS3 controller grading harness: what changed and why

Task: `test_example/SolidWorks/1_playstation_controller`. Harness: `tests/task/harness/harness.py`, now 2.4.0.

Goal from the brief: the reference scores full marks, every example loses points only on what it gets wrong, grading reads geometry only, and every score is continuous.

## Scores (out of 8.0)

Both columns score the same SolidWorks captures of the same 10 parts. Before is the original harness, after is 2.4.0. The captures were taken on Windows with SolidWorks by `harness.py --batch --capture-only` and are in `evidence/captures/`. Envelopes, summary tables and full reports are in `evidence/envelopes/` and `evidence/reports/`.

| Model | Before | After | What it gets wrong | Where it loses now |
|---|---:|---:|---|---|
| solution (reference) | 8.000 | 8.000 | nothing | nothing |
| adversarial_unrequested_change_elsewhere | **8.000** | **7.733** | a 0.15 mm emboss band on the housing | no unrequested changes |
| adversarial_missing_glyphs | 7.000 | 7.000 | face-button symbols erased | markings preserved |
| adversarial_widened_by_30mm | 6.500 | 6.500 | widened too far | widened by 15 mm |
| adversarial_text_mirrored_incorrectly | 6.300 | 6.300 | port-light glyphs crossed the plane | left-handed layout |
| adversarial_only_one_button_cluster_mirrored | 6.062 | 6.062 | one cluster not moved | clusters, left-handed layout |
| adversarial_widened_15mm_clusters_at_original_spacing | 2.500 | 3.211 | shell widened, controls not moved | clusters, interference, left-handed layout; keeps part of width for the widened shell |
| adversarial_feature_tree_with_errors | 0.769 | 3.064 | 15 of 286 features broken | everything, scaled by rebuild health |
| adversarial_unwidened_shell_with_correct_clusters | 0.496 | 0.457 | 34 of 280 features broken, shell barely widened | everything, scaled by rebuild health |
| input (untouched seed) | 3.000 | 3.000 | did nothing | everything it was asked to do |

Before, the reference and `adversarial_unrequested_change_elsewhere` were tied at 8.000. Now all 10 totals are distinct, and every example loses only on its own defect.

## Changes

1. **A broken rebuild discounts the geometry instead of zeroing it.** Before, one newly broken feature zeroed all six geometry criteria. A tree with one broken feature then scored the same as a tree in ruins, and what the model actually got wrong was never measured. Now each geometry criterion is multiplied by the rebuild-health score, and the unscaled value is kept as `measured_score`. Health now reaches 0 at 13.3% of the tree newly broken instead of 20%, a factor of 1.5. It has to fall faster because it now scales 7.0 points of geometry, not only its own 0.5. The factor was chosen so that a part that does not rebuild never outranks a clean part with a geometry mistake. On this corpus that holds from a factor of 1.37 up, and 1.5 is the nearest round value. This threshold comes from the corpus, not from the task. A rebuild that cannot run at all still zeroes the geometry.
2. **Width reads the shell as well as the controls.** `widened by 15 mm` used only the growth of mirror-pair separation (sticks, triggers, bumpers). A shell widened with its controls left behind therefore scored 0 on width. Now the score is half pairs and half shell. The pairs half is unchanged. The shell half uses the growth of the housing halves (area-weighted X of the housing faces either side of the plane): full credit from +15 to +30 mm, rising from 0 below +15, falling to 0 at +45 mm. The shell is not sized to exactly 15 mm because the reference grows its halves +20.6 mm (bbox +21.9) while its controls move +15.0. For the same reason, "controls follow the grips" is reported in the cluster criterion but not scored.
3. **No unrequested changes: skin-split check.** On a widened, remodelled housing, sizes change everywhere, so a size comparison cannot find an extra edit. This check works by topology:
   1. It moves every seed housing surface sample by the task's own rule: left half by minus half the widening, right half by plus half.
   2. It keeps the candidate faces that still lie on that moved skin, within 0.3 mm with matching normals.
   3. It flags any seed face that is now covered by two candidate faces on the same side, where the smaller piece sits at least 0.1 mm off its sibling.

   The widening strip and an 8 mm margin around every control are left out, at both their seed and candidate positions. The extra area scores continuously: free below 20 mm², half lost by 60 mm², zero at 2000 mm². The check uses only the seed and the task rule, never the reference.
4. **Capture schema /6.** It adds `housing_halves` (for change 2) and `housing_faces`: one record per housing face, with one surface sample per 3 mm cell taken from SolidWorks' own tessellation, for change 3. The shipped baseline `tests/task/prompt/input.json` is the upstream file plus these two blocks from a fresh /6 capture of the seed. Older captures still score; the new checks fall back and say so.
5. **Batch finds the examples.** `--batch` only globbed `examples/*.SLDPRT`, but every example ships in `examples/<name>/`, so a batch with no arguments silently graded only the reference and the seed. It now recurses, and skips SolidWorks `~$` lock files.
6. **Self-test.** `python3 test_example/tools/selftest_synthetic.py` runs without SolidWorks. It scores the 10 shipped captures and synthetic variants of the reference, then asserts the contract: the reference scores 8.0 and the seed 3.0. Every example loses, and only on its own criteria. A valid solution cut at the mirror plane into left and right pieces still scores 8.0. On covered faces, a 0.15 to 0.25 mm step loses only `no unrequested changes`, and a zero-height split line loses nothing. All 69 checks pass. The README already referred to this script, but it did not exist.
7. **Docs match the code.** max_score 8.0 with eight criteria (the README still said 7.0 and seven). The 3.0 seed floor is documented. `task.toml` no longer claims weights are read from it at run time; they live only in `ALL_CRITERIA`.

The weights are unchanged from upstream.

## Tried and not adopted

- **Shell width sized to exactly +15 mm.** It fails the reference, which grows its shell +20.6 mm.
- **"Controls follow the grips" as a scored term.** It fails the reference for the same reason. Kept as a reported number.
- **Surface deviation against the moved seed.** The reference itself sits about 4,900 mm² outside the moved seed at 0.3 mm. Its grips are reshaped, and its housing is hollow and split top and bottom. The unrequested band adds about 200 mm² to that, so no threshold separates the two.
- **Zone check on feature footprints** (new features outside the control zones). It flagged 16 features of the reference: bosses, screws, grip lofts, LEDs. It also read the feature tree, so it was removed.
- **Wider skin tolerance (1.0 mm) to catch taller inserts.** The reference then lost 0.25 points.
- **Rebuild-health factor.** Measured on the two broken models (feature_tree / unwidened):

  | Factor | Scores | Note |
  |---|---|---|
  | x1.0 | 3.64 / 0.99 | a broken part outranks the clean widened15 at 3.21 |
  | x1.5 | 3.06 / 0.46 | chosen |
  | x2.0 | 2.49 / 0.30 | |
  | Half of geometry kept unconditionally | 3.83 / 1.03 | |
  | No multiplier | 4.66 / 1.75 | |

## Known limitations

- **What the skin-split check misses.** It catches shallow inserts only, 0.1 to 0.3 mm proud or sunk, on housing the task did not ask to change. It misses:
  - a taller addition;
  - an edit on the grips, which the reference legitimately reshapes;
  - an edit within 8 mm of a control;
  - a fillet. Fillet89 in the unrequested example is not caught.

  Its thresholds were tuned on a corpus in which every example is derived from one reference.
- **Tessellation quality.** The check samples SolidWorks' tessellation, so a much coarser image-quality setting can hide an insert. It cannot invent one.
- **The shell-width witness is a proxy.** Hollowing the shell or adding large faces also moves it.
- **The reference is wider than the brief.** Its shell grows +20.6 mm against a requested +15. Worth checking which one is the intended truth.
- **unwidened_shell_with_correct_clusters is mostly a rebuild test.** It has 34 rebuild errors, so it tests rebuild health more than what its name says.
- **Handedness on a remodelled housing rests on one witness**, the port-light glyphs (unchanged from upstream).

## Reproduce

```bat
cd test_example\SolidWorks\1_playstation_controller
python tests\task\harness\harness.py solution\solution.SLDPRT
python tests\task\harness\harness.py --batch --capture-only
python tests\task\harness\harness.py --batch --score-from <captures dir> --out <dir>
```

```bash
python3 test_example/tools/selftest_synthetic.py
```
