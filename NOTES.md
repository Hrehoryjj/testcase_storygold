# PS3 controller grading harness: what changed and why

> After submission I measured where this grader does not look and built a tighter version of one check beside it. The grader described here is unchanged. See [NOTES_UPDATE.md](NOTES_UPDATE.md).

Task: `test_example/SolidWorks/1_playstation_controller`. Harness: `tests/task/harness/harness.py`.

## In short

The harness gives a CAD model a score out of 8 for how well it did the task: widen the controller by 15 mm and mirror its controls for left-handed use. It had a few blind spots. A model with an extra change on the housing that nobody asked for scored the same 8.0 as the correct answer. A shell widened without its controls got no credit for the widening. One broken feature in the model's history dropped every geometry score to zero.

What it does now:

- **Full marks only for the correct answer.** The reference still scores 8.0. All nine other examples now score below it, each with a different total, and each loses points only for its own mistake.
- **It finds changes nobody asked for.** It compares the housing surface with the original part, and the left side of the part with its right side. On ordinary housing surface it found every random test bump, dent and hole of 7 of the 8 kinds tried, and 97% overall. The lower rates are only where the reference itself rebuilt the part (the grips, the seam between its two housing bodies, the edges of the control openings), so there the original part cannot serve as a template.
- **It checks that buttons and sticks still fit.** Every control must stay centred in its opening and keep its gap to the wall around it. This found a face button 0.09 mm inside the wall in one example.
- **Partial credit is fair.** A model with a few broken features loses in proportion instead of dropping to zero. A widened shell earns credit for its width even when its controls were left behind.
- **It can be checked without SolidWorks.** A self-test of 90 checks and the random-edit test run in plain Python on saved measurements.

Why these are good choices:

- Everything is measured from the shape of the part itself, never from feature names, the feature tree or the reference file, as the brief requires. A correct model built a different way scores the same.
- Every new rule was first run on the reference to make sure it does not charge the correct answer.
- Scores are continuous. A small mistake costs a little, a large one costs more.
- What each check cannot see is measured and written down below, so the reader knows exactly what is covered.

## Scores (out of 8.0)

Both columns score the same SolidWorks captures of the same 10 parts. Before is the original harness, after is this one. The captures were taken on Windows with SolidWorks by `harness.py --batch --capture-only` and are in `evidence/captures/`. Envelopes, summary tables and full reports are in `evidence/envelopes/` and `evidence/reports/`.

| Model | Before | After | What it gets wrong | Where it loses now |
|---|---:|---:|---|---|
| solution (reference) | 8.000 | 8.000 | nothing | nothing |
| adversarial_unrequested_change_elsewhere | **8.000** | **7.726** | a 0.15 mm emboss band on the housing | no unrequested changes |
| adversarial_missing_glyphs | 7.000 | 7.000 | face-button symbols erased | markings preserved |
| adversarial_widened_by_30mm | 6.500 | 6.500 | widened too far | widened by 15 mm |
| adversarial_text_mirrored_incorrectly | 6.300 | 6.300 | port-light glyphs crossed the plane | left-handed layout |
| adversarial_only_one_button_cluster_mirrored | 6.062 | 6.059 | one cluster not moved, and one face button 0.09 mm into the wall of its well | clusters, left-handed layout, a little interference (see change 10) |
| adversarial_widened_15mm_clusters_at_original_spacing | 2.500 | 3.211 | shell widened, controls not moved | clusters, interference, left-handed layout; keeps part of width for the widened shell |
| adversarial_feature_tree_with_errors | 0.769 | 2.780 | 15 of 286 features broken | everything, scaled by rebuild health |
| adversarial_unwidened_shell_with_correct_clusters | 0.496 | 0.442 | 34 of 280 features broken, shell barely widened | everything, scaled by rebuild health |
| input (untouched seed) | 3.000 | 3.000 | did nothing | everything it was asked to do |

Most totals barely move, and that is expected: the shipped examples each carry one known mistake, and the new checks look for mistakes they do not contain. Their effect shows on the random edits in "What the skin checks catch" below.

A fresh SolidWorks run on all 10 parts gave exactly these totals, at 31 to 36 s per part for measuring and scoring together. Re-freezing the baseline from the seed with `--capture-baseline` and re-scoring the same captures also gave exactly these totals, so the shipped `tests/task/prompt/input.json` grades exactly like one made by the documented command.

## Changes in detail

1. **A broken rebuild discounts the geometry instead of zeroing it.** Before, one newly broken feature zeroed all six geometry criteria, so a tree with one broken feature scored the same as a tree in ruins. Now each geometry criterion is multiplied by the rebuild-health score, and the unscaled value is kept as `measured_score`. Health now reaches 0 at 13.3% of the tree newly broken instead of 20%, a factor of 1.5. It falls faster because it now scales 7.0 points of geometry, not only its own 0.5. The factor was chosen so that a part that does not rebuild never outranks a clean part with a geometry mistake. On the shipped examples that holds from a factor of 1.37 up, and 1.5 is the nearest round value. A rebuild that cannot run at all still zeroes the geometry.

   Measured when the factor was chosen, before the skin and clearance checks; with them, x1.5 gives the 2.780 and 0.442 in the table above.

   | Factor | feature_tree / unwidened | Note |
   |---|---|---|
   | x1.0 | 3.64 / 0.99 | a broken part outranks the clean widened15 at 3.21 |
   | x1.5 | 3.06 / 0.46 | chosen |
   | x2.0 | 2.49 / 0.30 | also keeps the order, but takes more from a broken part than needed |

2. **Width reads the shell as well as the controls.** `widened by 15 mm` used only the growth of mirror-pair separation (sticks, triggers, bumpers), so a shell widened with its controls left behind scored 0 on width. Now the score is half pairs and half shell. The pairs half is unchanged. The shell half uses the growth of the housing halves (area-weighted X of the housing faces either side of the plane): full credit from +15 to +30 mm, rising from 0 below +15, falling to 0 at +45 mm. The shell is not sized to exactly 15 mm because the reference grows its halves +20.6 mm (bbox +21.9) while its controls move +15.0. For the same reason, "controls follow the grips" is reported in the cluster criterion but not scored.
3. **No unrequested changes: three skin checks.** On a widened, remodelled housing, sizes change everywhere, so a size comparison cannot find an extra edit. The checks instead move the seed housing skin onto the candidate by the task's own rule and look only where the task asked for nothing: outside the widening strip and an 8 mm margin around every control, at both their seed and candidate positions. None of them reads the reference.
   - **Skin splits** (shallow edits). Keep the candidate faces that still lie on the moved seed skin, within 0.3 mm with matching normals. Flag any seed face now covered by two candidate faces on the same side, where the smaller piece sits at least 0.1 mm off its sibling. This catches a 0.1 to 0.3 mm emboss or engraving, which is what `adversarial_unrequested_change_elsewhere` is.
   - **Skin holes** (edits of any height). The seed mesh is resampled every 1.5 mm. For every seed skin sample, ask whether the candidate's own triangles have surface facing the same way within 0.3 mm of it. There is no sideways allowance, so the untouched rim of a small edit cannot stand in for its middle. The skin is moved both by the controls' widening and by the shell's own move, registered per side as the X shift that puts most of that half back on the candidate. That keeps the reference (shell +20.6 mm, controls +15) and a shell widened without its controls from being charged. Missing skin is grouped into patches across face edges, so a hole on an edge is one hole, and each patch is judged by its size. On a face that is still mostly in place, 20 mm² or more is an edit: a boss, a pocket, a hole. A face less than 80% in place was rebuilt. The reference rebuilds its grips this way (about 8,000 mm², two faces) and adds screw holes of about 33 mm² on them. So on a rebuilt face a patch from 60 to 500 mm² is an edit, and a bigger one (the reference's are about 1,100 mm²) is the rebuild itself, reported and not charged.
   - **Skin mirror** (edits on rebuilt faces). Where a face was rebuilt, the seed says nothing about what it should be, but the part itself still does: the controller is mirror symmetric outside its controls, and so is any honest rebuild of it. The reference's two rebuilt grip faces are mirror images, screw holes included. So every candidate skin sample on and around the rebuilt faces is mirrored across the candidate's own symmetry plane (searched within 2 mm of the seed's) and must find surface facing the mirrored way within 0.3 mm. A one-sided patch of 20 to 500 mm² is an edit. On the reference only two patches have no mirror image, both larger than 500 mm² and both where its top and bottom housing bodies meet; they are reported, not charged. This check compares the part with itself rather than with any other model.

   All three score extra area the same way: free below 20 mm², half lost by 60 mm², zero at 2000 mm².
4. **Richer captures.** A capture now records `housing_halves` (for change 2), one record per housing face with SolidWorks' full tessellation and cone axes (for changes 3 and 8), and `control_meshes`, the tessellation of every control body (for change 10). The shipped baseline `tests/task/prompt/input.json` is the upstream file plus these blocks from a fresh capture of the seed, with body ids mapped to the baseline's by centroid. Older captures still score; the new checks fall back and say so. The cost: a capture grows from about 2.3 MB to 5 MB (1.5 MB gzipped), the baseline from 0.65 MB to 3.4 MB, and scoring takes about 20 s per model instead of a few seconds. The resampling loops were rewritten with the same arithmetic in the same order, which halved that time and left all ten reports identical. Each list of numbers is written on one line, which halves the file size.
5. **Batch finds the examples in their folders.** The examples ship in `examples/<name>/`, so `--batch` now looks in subfolders as well, and skips SolidWorks `~$` lock files.
6. **Self-test.** `python3 test_example/tools/selftest_synthetic.py`, the script the README mentions, runs without SolidWorks. It scores the 10 shipped captures and synthetic variants of the reference, then asserts the contract: the reference scores 8.0 and the seed 3.0. Every example loses, and only on its own criteria. A valid solution cut at the mirror plane into left and right pieces still scores 8.0. On covered faces, a 0.15 to 0.25 mm step, a 1 or 3 mm boss, a 1 mm pocket and a through hole (16 mm across) each lose only `no unrequested changes`, and a zero-height split line loses nothing. A one-sided boss, pocket or hole on a grip face the reference rebuilt loses only `no unrequested changes`, through the mirror check. A control pushed 0.3 mm sideways in its opening loses only `no new control interference`. All 90 checks pass.
7. **Docs updated.** README and `task.toml` describe max_score 8.0 with eight criteria and the 3.0 seed floor. Weights live in `ALL_CRITERIA` and are unchanged from upstream.
8. **Controls stay seated in their openings (coaxiality).** `no new control interference` now takes the weaker of the boolean interference volume and a coaxiality reading. Every control whose centre sits on the axis of a round housing opening in the seed (sticks, face buttons, PS button) must still sit on one in the candidate: free up to 1 mm off, zero at 5 mm, averaged over those controls. The bore it sits in must also keep the seed's radius: free within 0.2 mm, zero at 2 mm. Drafted (conical) bores count as openings; their radius is not compared. It reads only body centroids and the housing's own cylinders and cones, so the candidate is checked against itself. On the shipped examples it changes no total: the models it fires on already score 0 on interference. It is a second, independent witness for the same criterion.
9. **Coverage measured, not assumed.** `python3 test_example/tools/sweep_synthetic.py 20` makes boss, pocket and hole edits at 20 random spots on the skin the task did not ask to change and counts what `no unrequested changes` catches. Results are below.
10. **Every control keeps its gap to its own opening.** Coaxiality covers round openings only, and the interference volume forgives up to 5 mm³. So `no new control interference` also takes, for every control, horizontal sections through it every 0.5 mm and 16 rays around it, and on each ray the tightest gap between the control and the housing around it. The seed's gaps are compared with the candidate's, each within its own part, so no frame or widening enters. Only narrowing is charged (free up to 0.15 mm, zero at 0.6 mm, averaged over the controls). Widening is allowed, because the reference itself opens up its stick rings by about 1.2 mm and its face-button wells by up to 6 mm. Each control is compared with the closest seed pattern of its own kind, in either handedness, so two look-alike buttons matched the other way round are not charged. On the reference no gap narrows by more than 0.008 mm.

    **What it found.** In `adversarial_only_one_button_cluster_mirrored` one face button sits 0.19 mm closer to the wall of its well than any seed button does, which puts it 0.09 mm into the wall. The interference volume does not see it because it is under its 5 mm³ allowance, and the example's description does not mention it. It costs that model 0.003 points and changes no ranking.

    **Recommendation: charge this harder.** The loss is small because a narrowed gap is averaged over all controls (about 30) and the criterion weighs 0.5. On a real controller a button inside the wall of its well does not assemble, so a control that enters the housing should count as an assembly defect. Measured on the shipped captures: the seed already has four controls touching the housing at zero gap, so only depth beyond the seed's own gap should count; the reference's tightest gap is 0.087 mm. Taking the worst control rather than the mean, free up to 0.02 mm and zero at 0.1 mm, would leave the reference untouched and cost this example about 0.44 points (6.059 to about 5.62) without changing any ranking. It is left as a recommendation so the weighting stays the task owner's choice.

## What the skin checks catch

Run with `sweep_synthetic.py 20 1`: 20 random spots on the reference's skin outside the widening strip and the controls, eight edits each at two sizes, 320 grades. Grouped by where the edit lands:

| Where the edit is | Spots | Caught |
|---|---:|---|
| Ordinary housing surface | 14 | 218 of 224 (97%). Every edit of 7 kinds, 100%. Bumps of 0.5 mm, 22 of 28, missed only on three faces that point along the widening (see below) |
| Grips the reference rebuilt, away from its body seam | 2 | 23 of 32. Every edit 0.5 mm or taller, 20 of 20. The shallower ones are below the 0.3 mm surface tolerance |
| Grips next to the seam between the reference's two housing bodies | 2 | 6 of 32 |
| Partly inside the 8 mm margin around a control | 2 | 11 of 32 |

Every miss sits where the reference itself changed or rebuilt the part, so the original seed cannot say what the surface there should be. So the lower rows mostly reflect how this reference was modelled rather than the way the checks compare surfaces. On surface that a solution leaves in place, the first row is the rate to expect.

In the runs that graded every criterion, no edit cost any criterion other than `no unrequested changes`. By edit type, all 20 spots together, with the first version of these checks in brackets:

| edit | 8 mm across | 16 mm across |
|---|---|---|
| boss +0.15 mm | 15/20 (15) | 17/20 (17) |
| boss +0.25 mm | 14/20 (14) | 17/20 (17) |
| boss +0.50 mm | 14/20 (9) | 14/20 (11) |
| boss +1.00 mm | 17/20 (10) | 17/20 (15) |
| boss +3.00 mm | 17/20 (0) | 18/20 (16) |
| pocket -0.20 mm | 15/20 (15) | 17/20 (17) |
| pocket -1.00 mm | 16/20 (11) | 17/20 (16) |
| hole | 16/20 (0) | 17/20 (16) |

258 of 320 caught overall, against 180 for the first version. Each step was measured on the same edits: reading the full mesh, 199; exact distance to the candidate's triangles, 219; judging patches by size and across face edges, 234; the mirror check, 258 (on the four spots on the rebuilt grips, from 5 of 64 to 29).

What is still missed, and why:
- **Edits inside the 8 mm margin around a control.** The margin is there because the reference rebuilds its button wells and stick rings.
- **Bumps of 0.5 mm on faces that point along the widening axis.** There, 0.5 mm of offset looks the same as the shell being widened a little more.
- **Edits below 0.3 mm on the rebuilt grips**, which is the surface tolerance, and edits next to the seam between the reference's two housing bodies, where the reference itself is not symmetric.
- **The same edit made on both grips**, which the mirror check cannot see by construction.
- **Fillets and chamfers on edges.** See next steps.

## Ideas measured and set aside

- **Surface distance to the moved seed.** The reference itself sits about 4,900 mm² away from it (reshaped grips, hollow housing in two bodies), so no threshold separates it from a real edit.
- **Comparing cross-sections with the seed.** In the widening strip they would charge the reference; elsewhere they repeat what the skin checks already compare in full.
- **Counting bends along sections of the grips.** A rebuild alone changes the bend count on this model, so it cannot separate an edit from an honest rebuild.
- **Comparing the volume of the left and right grips.** A small edit is lost against tens of thousands of mm³, and a boss and a pocket cancel. The mirror check does the same comparison point by point.
- **Shape fingerprint on remodelled faces.** It caught tall bosses and large holes but not shallow edits, at about 30 s more per model, so it is left as a measured option.

## Next steps

- **Fillets and chamfers, through sharp edges.** Find the seed's sharp edges in its mesh (faces meeting at more than about 30 degrees), move them onto the candidate like the skin, and check the candidate still has the same edge within 0.3 mm. A fillet removes the edge and a chamfer moves it; the missing length gives a continuous score. Fillet89 in `adversarial_unrequested_change_elsewhere` is the case it would catch.
- **A control inside the housing wall as an assembly defect**, scored on the worst control (see change 10).
- **The mirror check beyond the rebuilt faces**, with each shell half mirrored by its own registered move.
- **A second valid solution built a different way.** Every example is derived from one reference, so a second, independently modelled correct answer is the best guard against charging a valid part.

## Known limitations

- **Tessellation quality.** The checks read SolidWorks' tessellation. A much coarser image-quality setting can hide an edit. Older captures without meshes still score, with the clearance check skipped and a note.
- **The shell-width witness is a proxy.** Hollowing the shell or adding large faces also moves it.
- **The reference is wider than the brief.** Its shell grows +20.6 mm against a requested +15. Worth confirming which one is the intended target.
- **unwidened_shell_with_correct_clusters is mostly a rebuild test.** It has 34 rebuild errors, so it tests rebuild health more than its name suggests.
- **Thresholds were set on the shipped examples**, which all come from one reference. The random sweep above is there to check them on edits the examples do not contain.

## Reproduce

```bat
cd test_example\SolidWorks\1_playstation_controller
python tests\task\harness\harness.py solution\solution.SLDPRT
python tests\task\harness\harness.py --batch --capture-only
python tests\task\harness\harness.py --batch --score-from <captures dir> --out <dir>
```

```bash
python3 test_example/tools/selftest_synthetic.py
python3 test_example/tools/sweep_synthetic.py 20
```
