"""Offline self-test of the PS3 controller harness. Runs anywhere, no SolidWorks.

    python3 tools/selftest_synthetic.py

Scores the shipped captures (evidence/captures/*.json.gz, one per model, taken
with SolidWorks by `harness.py --batch --capture-only`, schema /7 with full
meshes) and synthetic variants
built from them, then asserts the grading contract:

  * the reference scores full marks;
  * every example loses points, and only on the criteria it gets wrong
    (a broken rebuild scales every geometry criterion, so those two models
    may lose anywhere);
  * a reference cut at the mirror plane into left and right pieces (another
    valid way to widen) still scores full marks;
  * a reference with a 0.15 to 0.25 mm step cut into a face the check
    covers loses "no unrequested changes" and nothing else, and a
    zero-height split line on the same face costs nothing (a face whose
    other piece leaves the moved seed skin is outside what the check can
    see: counted as skipped);
  * the same for a taller edit on such a face: a boss, a pocket or a
    through hole 16 mm across;
  * a control pushed 0.3 mm sideways in its opening loses "no new control
    interference" and nothing else (schema /7 captures);
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
    # and one of its face buttons sits 0.19 mm closer to its well's wall
    # than any seed button (0.09 mm into it): the clearance check sees it
    "adversarial_only_one_button_cluster_mirrored": {CL, LH, IN},
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


def mesh_of(tris):
    """Triangles in mm back to a capture mesh record (schema /7)."""
    return H.mesh_record([v / H.MM for t in tris for q in t for v in q])


def refine(tris, near, cell=0.75):
    """Longest-edge bisection, down to `cell` mm, of the triangles `near`
    accepts, so an edit can cut a clean disc out of a coarse mesh."""
    out, stack = [], list(tris)
    while stack:
        t = stack.pop()
        if not near(t):
            out.append(t)
            continue
        e = [sum((t[(k + 1) % 3][j] - t[k][j]) ** 2 for j in range(3))
             for k in range(3)]
        k = max(range(3), key=lambda j: e[j])
        if e[k] <= cell * cell:
            out.append(t)
            continue
        a, b, c = t[k], t[(k + 1) % 3], t[(k + 2) % 3]
        m = tuple((a[j] + b[j]) / 2 for j in range(3))
        stack += [(a, m, c), (m, b, c)]
    return out


def facet_normal(t):
    u = [t[1][j] - t[0][j] for j in range(3)]
    v = [t[2][j] - t[0][j] for j in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
         u[0] * v[1] - u[1] * v[0]]
    ln = sum(x * x for x in n) ** 0.5 or 1.0
    return [x / ln for x in n]


def centroid(t):
    return [sum(q[j] for q in t) / 3 for j in range(3)]


def has_mesh(cap):
    return all("mt" in f for f in cap["housing_faces"])


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
    meshes = {}
    if f.get("mt"):
        # the same cut on the mesh: the lowest third (by z) of the face
        zcut = pts[k - 1][2] if k <= len(pts) else pts[-1][2]
        tri = refine(H.mesh_tris3_mm(f), lambda t: True, 1.5)
        lo = [t for t in tri if centroid(t)[2] <= zcut]
        hi = [t for t in tri if centroid(t)[2] > zcut]
        lo = [tuple(tuple(q[j] + step_mm * facet_normal(t)[j]
                          for j in range(3)) for q in t) for t in lo]
        if lo and hi:
            meshes = {True: mesh_of(hi), False: mesh_of(lo)}
    for part, keep in ((big, True), (small, False)):
        g = dict(f)
        if meshes:
            g.update(meshes[keep])
        g["p"] = part
        g["a"] = sum(q[3] for q in part)
        g["c"] = [sum(q[j] * q[3] for q in part) / g["a"] for j in range(3)]
        if keep:
            cap["housing_faces"][i] = g
        else:
            cap["housing_faces"].append(g)
    return cap


def disc_edit(cap, i, kind, h=0.0, r=8.0, c=None):
    """Edit a disc of radius r mm on the skin around c (default: the middle
    of face i): 'boss'/'pocket' move it h mm along the normal into a new
    face, 'hole' removes it."""
    cap = copy.deepcopy(cap)
    if c is None:
        f = cap["housing_faces"][i]
        c = min(f["p"], key=lambda q: sum((q[j] - f["c"][j]) ** 2
                                          for j in range(3)))
    new = []
    for g in cap["housing_faces"]:
        inside = [q for q in g["p"]
                  if sum((q[j] - c[j]) ** 2 for j in range(3)) <= r * r]
        if not inside:
            continue
        g["p"] = [q for q in g["p"] if q not in inside]
        if kind != "hole" and not has_mesh(cap):
            mv = [[q[0] + h * q[4], q[1] + h * q[5], q[2] + h * q[6]] + q[3:]
                  for q in inside]
            new.append(dict(g, p=mv))
    if has_mesh(cap):
        # the same edit on the mesh, which is what the skin checks read
        new = []
        for g in cap["housing_faces"]:
            r2 = (r + 2.0) ** 2
            if not any(sum((q[j] - c[j]) ** 2 for j in range(3)) <= r2
                       for t in H.mesh_tris3_mm(g) for q in t) \
                    and not any(sum((q[j] - c[j]) ** 2 for j in range(3))
                                <= r * r for q in g["p"]):
                continue
            tri = refine(H.mesh_tris3_mm(g), lambda t: min(
                sum((q[j] - c[j]) ** 2 for j in range(3)) for q in t)
                <= r2)
            ins = [t for t in tri if sum((centroid(t)[j] - c[j]) ** 2
                                         for j in range(3)) <= r * r]
            if not ins:
                continue
            g.update(mesh_of([t for t in tri if t not in ins]))
            if kind != "hole":
                mv = [tuple(tuple(q[j] + h * facet_normal(t)[j]
                                  for j in range(3)) for q in t) for t in ins]
                new.append(dict(g, p=[], **mesh_of(mv)))
        cap["housing_faces"] = [g for g in cap["housing_faces"]
                                if g.get("mt")] + new
    else:
        cap["housing_faces"] = [g for g in cap["housing_faces"]
                                if g["p"]] + new
    for g in cap["housing_faces"]:
        a = sum(q[3] for q in g["p"])
        if a > 0:
            g["a"] = a
            g["c"] = [sum(q[j] * q[3] for q in g["p"]) / a for j in range(3)]
    return cap


def cut_at_plane(cap):
    cap = copy.deepcopy(cap)
    P = cap["plane_x_m"] * H.MM
    out = []
    for f in cap["housing_faces"]:
        L = [q for q in f["p"] if q[0] < P]
        R = [q for q in f["p"] if q[0] >= P]
        if f.get("mt"):
            tri = H.mesh_tris3_mm(f)
            tl = [t for t in tri if centroid(t)[0] < P]
            tr = [t for t in tri if centroid(t)[0] >= P]
            if not (tl and tr):
                out.append(f)
                continue
            for pts, tt in ((L, tl), (R, tr)):
                g = dict(f, **mesh_of(tt))
                g["p"] = pts or [list(centroid(tt[0])) + [1e-3] + facet_normal(tt[0])]
                g["a"] = sum(q[3] for q in g["p"])
                g["c"] = [sum(q[j] * q[3] for q in g["p"]) / g["a"]
                          for j in range(3)]
                out.append(g)
            continue
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

TALL = (("boss", 1.0), ("boss", 3.0), ("pocket", -1.0), ("hole", 0.0))
for i in faces[:3]:
    for kind, h in TALL:
        _, s = grade(disc_edit(ref, i, kind, h))
        lost = {k for k, v in s.items() if v < 1.0 - 1e-6}
        check(lost == {UN}, f"{kind} {h:+.1f} mm, 16 mm across, on face {i}: "
                            f"loses only '{UN}' (lost {sorted(lost)}, "
                            f"{total(s):.3f})")

# a grip face the reference rebuilt: the seed cannot judge it, the part's
# own mirror image does
GRIP = min((q for f in ref["housing_faces"] for q in f["p"]),
           key=lambda q: sum((q[j] - (-8.0, -80.0, 91.0)[j]) ** 2
                             for j in range(3)))
for kind, h in (("boss", 1.0), ("pocket", -1.0), ("hole", 0.0)):
    rep, s = grade(disc_edit(ref, 0, kind, h, 8.0, c=GRIP))
    lost = {k for k, v in s.items() if v < 1.0 - 1e-6}
    mir = rep["criteria"][UN]["components"]["skin_mirror"]
    check(lost == {UN} and mir < 1.0,
          f"{kind} {h:+.1f} mm on a rebuilt grip, one side only: loses only "
          f"'{UN}' through skin_mirror (lost {sorted(lost)}, mirror "
          f"{mir:.3f}, {total(s):.3f})")

print("controls kept in their openings")
if ref.get("control_meshes"):
    g = H.Grader(BASELINE, ref)
    for bid in [b for r in ("dpad", "face_buttons") for b in
                BASELINE["roles"][r]][:3]:
        cid = g.match[bid]["cand"]
        for dx in (0.3, -0.3):
            cap = copy.deepcopy(ref)
            rec = cap["control_meshes"][cid]
            q = round(dx / H.MESH_UNIT_MM)
            rec["mv"] = [v + q if k % 3 == 0 else v
                         for k, v in enumerate(rec["mv"])]
            rep, s = grade(cap)
            lost = {k for k, v in s.items() if v < 1.0 - 1e-6}
            check(lost == {IN}, f"control {cid} pushed {dx:+.1f} mm in its "
                                f"opening: loses only '{IN}' (lost "
                                f"{sorted(lost)}, {total(s):.3f})")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
