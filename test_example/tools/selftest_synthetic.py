"""Offline self-test of the PS3 controller harness. Runs anywhere, no SolidWorks.

    python3 tools/selftest_synthetic.py

Scores the shipped captures (evidence/captures/*.json.gz, one per model, taken
with SolidWorks by `harness.py --batch --capture-only`) and synthetic variants
built from them, then asserts the grading contract:

  * the reference scores full marks;
  * every example loses points, and only on the criteria it gets wrong
    (a broken rebuild scales every geometry criterion, so those two models
    may lose anywhere);
  * a reference cut at the mirror plane into left and right pieces (another
    valid way to widen) still scores full marks;
  * a reference with a 0.2 mm step cut into a face the check covers loses
    "no unrequested changes" and nothing else, and a zero-height split line
    on the same face costs nothing (a face whose other piece leaves the
    moved seed skin is outside what the check can see: counted as skipped);
  * the untouched seed scores the 3.0 floor of the negative criteria.

This checks the scoring logic on real measurements. It does not check
capture() itself, which needs SolidWorks.
"""
import copy
import gzip
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "SolidWorks" / "1_playstation_controller"
CAPS = TASK / "evidence" / "captures"

spec = importlib.util.spec_from_file_location(
    "harness", TASK / "tests" / "task" / "harness" / "harness.py")
H = importlib.util.module_from_spec(spec)
sys.argv = sys.argv[:1]
spec.loader.exec_module(H)

W = "widened by 15 mm"
CL = "clusters at mirrored positions"
IN = "no new control interference"
LH = "left-handed layout achieved"
UN = "no unrequested changes"
MK = H.C_MARKINGS
EVERYTHING = set(H.ALL_CRITERIA)

# What each example gets wrong, from its name and its README description.
ALLOWED_LOSSES = {
    "adversarial_widened_15mm_clusters_at_original_spacing": {W, CL, IN, LH},
    "adversarial_widened_by_30mm": {W},
    "adversarial_text_mirrored_incorrectly": {LH},
    "adversarial_only_one_button_cluster_mirrored": {CL, LH},
    "adversarial_missing_glyphs": {MK},
    "adversarial_unrequested_change_elsewhere": {UN},
    # rebuild errors: health scales every geometry criterion
    "adversarial_feature_tree_with_errors": EVERYTHING,
    "adversarial_unwidened_shell_with_correct_clusters": EVERYTHING,
}

BASELINE = H.load_baseline()
failures = []


def check(cond, msg):
    print(("  ok    " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


def load(name):
    with gzip.open(CAPS / f"{name}.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def grade(cap):
    rep = H.score_capture(cap, BASELINE, quiet=True)
    return rep, {k: v["score"] for k, v in rep["criteria"].items()}


def total(scores):
    return sum(H.ALL_CRITERIA[k] * s for k, s in scores.items())


def skin_faces(cap):
    """Reference faces the skin-split check covers: on the moved seed skin
    and wholly clear of the plane strip and the controls. Largest first."""
    rep, _ = grade(cap)
    ids = rep["criteria"][UN]["detail"]["skin_splits"]["faces_on_skin"]
    g = H.Grader(BASELINE, cap)
    P, half = g.P * H.MM, g._actual_half_m() * H.MM
    dx = g.plane_shift_m * H.MM
    zones = g._skin_zones(P, half)
    clear = [i for i in ids if not any(
        g._skin_exempt((q[0] + dx, q[1], q[2]), P, half, zones)
        for q in cap["housing_faces"][i]["p"])]
    return sorted(clear, key=lambda i: -cap["housing_faces"][i]["a"])


def split_face(cap, i, step_mm):
    """Cut face i into two faces; the smaller one moved step_mm along its
    normal (0 = a split line, >0 = an emboss)."""
    cap = copy.deepcopy(cap)
    f = cap["housing_faces"][i]
    pts = sorted(f["p"], key=lambda q: q[2])
    k = max(1, len(pts) // 3)
    small, big = pts[:k], pts[k:]
    small = [[q[0] + step_mm * q[4], q[1] + step_mm * q[5],
              q[2] + step_mm * q[6]] + q[3:] for q in small]
    for part, keep in ((big, True), (small, False)):
        g = dict(f)
        g["p"] = part
        g["a"] = sum(q[3] for q in part)
        g["c"] = [sum(q[j] * q[3] for q in part) / g["a"] for j in range(3)]
        if keep:
            cap["housing_faces"][i] = g
        else:
            cap["housing_faces"].append(g)
    return cap


def cut_at_plane(cap):
    cap = copy.deepcopy(cap)
    P = cap["plane_x_m"] * H.MM
    out = []
    for f in cap["housing_faces"]:
        L = [q for q in f["p"] if q[0] < P]
        R = [q for q in f["p"] if q[0] >= P]
        if not (L and R):
            out.append(f)
            continue
        for part in (L, R):
            g = dict(f)
            g["p"] = part
            g["a"] = sum(q[3] for q in part)
            g["c"] = [sum(q[j] * q[3] for q in part) / g["a"]
                      for j in range(3)]
            out.append(g)
    cap["housing_faces"] = out
    return cap


print("real captures")
ref = load("solution")
_, s = grade(ref)
check(abs(total(s) - 8.0) < 1e-6, f"reference scores 8.0 (got {total(s):.4f})")

for name, allowed in ALLOWED_LOSSES.items():
    _, s = grade(load(name))
    lost = {k for k, v in s.items() if v < 1.0 - 1e-6}
    check(total(s) < 8.0 - 1e-6, f"{name} loses points ({total(s):.3f})")
    check(lost and lost <= allowed,
          f"{name} loses only where it is wrong: {sorted(lost)}")

_, s = grade(load("input"))
check(abs(total(s) - 3.0) < 1e-6,
      f"untouched seed scores the 3.0 floor (got {total(s):.4f})")

print("synthetic variants of the reference")
_, s = grade(cut_at_plane(ref))
check(abs(total(s) - 8.0) < 1e-6,
      f"cut at the plane into left and right pieces: 8.0 (got {total(s):.4f})")

faces = skin_faces(ref)
check(bool(faces), f"found {len(faces)} untouched skin faces to edit")
STEPS = (0.15, 0.2, 0.25, -0.15, -0.25)
tested = skipped = 0
for i in faces:
    if tested >= 8:
        break
    rep, s = grade(split_face(ref, i, 0.2))
    seen = rep["criteria"][UN]["detail"]["skin_splits"]["faces_on_skin"]
    if not {i, len(ref["housing_faces"])} <= set(seen):
        skipped += 1     # one piece left the skin: outside the contract
        continue
    tested += 1
    for step in STEPS:
        _, s = grade(split_face(ref, i, step))
        lost = {k for k, v in s.items() if v < 1.0 - 1e-6}
        check(lost == {UN}, f"{step} mm step on face {i}: loses only "
                            f"'{UN}' (lost {sorted(lost)}, {total(s):.3f})")
    _, s = grade(split_face(ref, i, 0.0))
    check(abs(total(s) - 8.0) < 1e-6,
          f"split line on face {i}: 8.0 (got {total(s):.4f})")
check(tested >= 5, f"step tested on {tested} faces "
                   f"({skipped} skipped: a piece left the skin)")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
