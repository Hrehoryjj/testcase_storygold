"""Where the grader is blind, measured spot by spot. Runs anywhere, no SolidWorks.

    python3 tools/blindspot_map.py [SPOTS] [WORKERS] [OUT.json] [EDITS]
        [--extend EARLIER.json ...]

EDITS is a comma list of edit names to run only those. --extend adds spots
where earlier runs left gaps (at their spacing), for example after the
outer skin test changed. --at SPOTS.json grades the listed positions again
(with --extend naming the run whose spacing they keep); the page builder
lets later result files replace earlier ones spot by spot.

sweep_synthetic.py answers "what share of random edits does
'no unrequested changes' catch". This answers "where on the part are the
misses". It spreads SPOTS spots evenly over the reference's outer housing
skin, makes the same disc edits there that the sweep makes (one at a time,
on the reference's own capture), and records for every spot which edits the
check catches. Spots inside the zones the task asked to change (the
widening strip and the margins around the controls) are not edited: they
are exempt and are reported as such. At every spot it also makes two edits
that leave the shape as it is, which the check must not flag: that
measures false alarms, the other half of whether a grader can be trusted.
Only the skin sub-checks of the criterion can react to these edits (they
change the housing mesh and samples, not spans or body shapes).

The result is a JSON file that build_blindspot_page.py turns into a 3D map.
Each edit is one grade of the criterion (about 18 s), so 250 spots with 6
edits take about two hours on 4 workers.
"""
import copy
import gzip
import importlib.util
import json
import math
import random
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "SolidWorks" / "1_playstation_controller"
CAPS = TASK / "evidence" / "captures"

spec = importlib.util.spec_from_file_location(
    "harness", TASK / "tests" / "task" / "harness" / "harness.py")
H = importlib.util.module_from_spec(spec)
_argv, sys.argv = sys.argv, sys.argv[:1]
spec.loader.exec_module(H)
sys.argv = _argv

UN = "no unrequested changes"
BASELINE = H.load_baseline()

# (name, kind, height mm, radius mm), all 8 mm across, the discs
# sweep_synthetic.py makes: a tiny step at the surface tolerance, a small
# and a tall boss, and a through hole. The last two leave the shape as it
# is, so a grader that flags them is raising a false alarm: a split line
# (the disc becomes its own face, at zero height, as an imprinted sketch
# would make it) and a different tessellation (the disc meshed finer, every
# vertex moved up to RESAG_MM along the normal, as a mesh at another
# quality setting lies off the true surface by its chord sag).
EDITS = [("boss 0.15 mm", "boss", 0.15, 4.0),
         ("boss 0.5 mm", "boss", 0.5, 4.0),
         ("boss 3 mm", "boss", 3.0, 4.0),
         ("hole", "hole", 0.0, 4.0),
         ("split line", "boss", 0.0, 4.0),
         ("re-mesh", "remesh", 0.0, 4.0)]
HARMLESS = {"split line", "re-mesh"}
RESAG_MM = 0.05


def load(name):
    with gzip.open(CAPS / f"{name}.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


# The disc edit below is the one selftest_synthetic.py makes on a capture
# with meshes, copied so this tool runs without running the self-test, with
# one fix: the self-test refines only facets with a corner near the disc, so
# on a face meshed with large facets (flat faces are) the disc found no
# facet to cut and the edit was silently not made.

def mesh_of(tris):
    return H.mesh_record([v / H.MM for t in tris for q in t for v in q])


def refine(tris, near, cell=0.75):
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


def d2(a, b):
    return sum((a[j] - b[j]) ** 2 for j in range(3))


def resag(tris, c, r):
    """Move every vertex inside the disc by up to RESAG_MM along the disc's
    mean normal, the same amount wherever a vertex is shared, so the mesh
    stays closed."""
    ins = [t for t in tris if d2(centroid(t), c) <= r * r]
    if not ins:
        return tris
    n = [sum(facet_normal(t)[j] for t in ins) for j in range(3)]
    ln = math.sqrt(sum(x * x for x in n)) or 1.0
    n = [x / ln for x in n]

    def move(q):
        if d2(q, c) > r * r:
            return q
        k = random.Random(hash(tuple(round(v, 3) for v in q)) & 0xffff)
        s = RESAG_MM * (2 * k.random() - 1)
        return tuple(q[j] + s * n[j] for j in range(3))
    return [tuple(move(q) for q in t) for t in tris]


def disc_edit(cap, kind, h, r, c):
    """Edit a disc of radius r mm on the mesh around c: 'boss' moves it h mm
    along the facet normal into a new face, 'hole' removes it, 'remesh'
    only splits its triangles finer and leaves the surface where it is."""
    cap = copy.deepcopy(cap)
    new = []
    for g in cap["housing_faces"]:
        if kind != "remesh":
            g["p"] = [q for q in g["p"] if d2(q, c) > r * r]
        tris = H.mesh_tris3_mm(g)

        def near(t):
            # a large facet can cover the disc with every corner far away,
            # so ask how far the facet's bounding sphere is, not its corners
            m = centroid(t)
            return math.sqrt(d2(m, c)) <= r + 2.0 + max(
                math.sqrt(d2(m, q)) for q in t)
        if not any(near(t) for t in tris):
            continue
        tri = refine(tris, near)
        if kind == "remesh":
            g.update(mesh_of(resag(tri, c, r)))
            continue
        ins = [t for t in tri if d2(centroid(t), c) <= r * r]
        if not ins:
            continue
        g.update(mesh_of([t for t in tri if t not in ins]))
        if kind != "hole":
            mv = [tuple(tuple(q[j] + h * facet_normal(t)[j]
                              for j in range(3)) for q in t) for t in ins]
            new.append(dict(g, p=[], **mesh_of(mv)))
    cap["housing_faces"] = [g for g in cap["housing_faces"]
                            if g.get("mt")] + new
    for g in cap["housing_faces"]:
        a = sum(q[3] for q in g["p"])
        if a > 0:
            g["a"] = a
            g["c"] = [sum(q[j] * q[3] for q in g["p"]) / a for j in range(3)]
    return cap


def exempt_test(ref):
    """The check's own exemption, in the reference's frame: 'strip' for the
    widening strip the task changes, 'controls' for the margins the check
    leaves around the controls, None where the check looks. Like the skin
    checks it includes the control zones at each half's registered shift,
    and a point is exempt when either move (the task's half width or the
    registered shift) lands it in a zone. Grading the reference once gives
    those shifts."""
    g = H.Grader(BASELINE, ref)
    g.c5_unrequested()
    P, dx = g.P * H.MM, g.plane_shift_m * H.MM
    half = g._actual_half_m() * H.MM
    strip = max(half, (g._shell_delta_mm() or 0.0) / 2)
    reg = g._rebuilt["reg"]
    zones = g._skin_zones(P, half)
    for v in sorted(set(reg.values())):
        zones = zones + g._skin_zones(P, v)

    def kind(q):
        p = (q[0] + dx, q[1], q[2])
        sg = 1 if p[0] > P else -1
        alt = (p[0] + sg * (reg.get(sg, half) - half), p[1], p[2])
        alt2 = (p[0] - sg * (reg.get(sg, half) - half), p[1], p[2])
        if not any(g._skin_exempt(x, P, strip, zones)
                   for x in (p, alt, alt2)):
            return None
        if abs(p[0] - P) <= strip + H.TOL["skin_margin_mm"]:
            return "strip"
        return "controls"
    return kind


# Outer skin: a point someone looking at the part can see. The housing is a
# thin hollow shell, so its inner skin faces a closed cavity. From just off
# the surface, rays are cast over the half sphere the surface faces; on the
# outer skin a good share reach open space, from the cavity almost none do
# (a few leave through the openings around the controls). On this part the
# shares fall into two clear groups, under 0.1 and over 0.2.
RAY_CELL_MM = 6.0
OPEN_SHARE = 0.15


def _rays(ref):
    """The housing and control triangles, binned in a coarse grid."""
    tris = [t for f in ref["housing_faces"] for t in H.mesh_tris3_mm(f)]
    for m in ref["control_meshes"].values():
        tris += H.mesh_tris3_mm(m)
    grid = {}
    for i, t in enumerate(tris):
        lo = [int(math.floor(min(q[j] for q in t) / RAY_CELL_MM))
              for j in range(3)]
        hi = [int(math.floor(max(q[j] for q in t) / RAY_CELL_MM))
              for j in range(3)]
        for a in range(lo[0], hi[0] + 1):
            for b in range(lo[1], hi[1] + 1):
                for c in range(lo[2], hi[2] + 1):
                    grid.setdefault((a, b, c), []).append(i)
    lo = [min(k[j] for k in grid) for j in range(3)]
    hi = [max(k[j] for k in grid) for j in range(3)]
    return tris, grid, lo, hi


def _hits(t, o, d):
    """Moller-Trumbore: does the ray o + s d, s > 0, cross triangle t."""
    e1 = [t[1][j] - t[0][j] for j in range(3)]
    e2 = [t[2][j] - t[0][j] for j in range(3)]
    p = [d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2],
         d[0] * e2[1] - d[1] * e2[0]]
    det = sum(e1[j] * p[j] for j in range(3))
    if abs(det) < 1e-12:
        return False
    s = [o[j] - t[0][j] for j in range(3)]
    u = sum(s[j] * p[j] for j in range(3)) / det
    if u < 0 or u > 1:
        return False
    q = [s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2],
         s[0] * e1[1] - s[1] * e1[0]]
    v = sum(d[j] * q[j] for j in range(3)) / det
    if v < 0 or u + v > 1:
        return False
    return sum(e2[j] * q[j] for j in range(3)) / det > 1e-3


def _escapes(rays, o, d):
    """Whether the ray leaves the part's bounds without crossing anything:
    a walk through the grid cells it passes."""
    tris, grid, lo, hi = rays
    cell = [int(math.floor(o[j] / RAY_CELL_MM)) for j in range(3)]
    step = [1 if d[j] > 0 else -1 for j in range(3)]
    tmax, tdel = [], []
    for j in range(3):
        if abs(d[j]) < 1e-12:
            tmax.append(1e30)
            tdel.append(1e30)
        else:
            edge = (cell[j] + (d[j] > 0)) * RAY_CELL_MM
            tmax.append((edge - o[j]) / d[j])
            tdel.append(RAY_CELL_MM / abs(d[j]))
    seen = set()
    while all(lo[j] - 1 <= cell[j] <= hi[j] + 1 for j in range(3)):
        for i in grid.get(tuple(cell), ()):
            if i not in seen:
                seen.add(i)
                if _hits(tris[i], o, d):
                    return False
        k = min(range(3), key=lambda j: tmax[j])
        cell[k] += step[k]
        tmax[k] += tdel[k]
    return True


def _half_sphere(n):
    """31 directions over the half sphere around n: n itself and rings at
    30, 55 and 78 degrees from it."""
    a = [1, 0, 0] if abs(n[0]) < 0.9 else [0, 1, 0]
    u = [n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2],
         n[0] * a[1] - n[1] * a[0]]
    ln = math.sqrt(sum(x * x for x in u))
    u = [x / ln for x in u]
    v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2],
         n[0] * u[1] - n[1] * u[0]]
    out = [list(n)]
    for deg, k in ((30, 6), (55, 10), (78, 14)):
        ce, se = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        for i in range(k):
            th = 2 * math.pi * i / k
            out.append([ce * n[j] + se * (math.cos(th) * u[j]
                                          + math.sin(th) * v[j])
                        for j in range(3)])
    return out


_RAYS = None


def _outer_chunk(chunk):
    global _RAYS
    if _RAYS is None:
        _RAYS = _rays(load("solution"))
    out = []
    for q in chunk:
        n = q[3:6]
        o = [q[j] + 0.5 * n[j] for j in range(3)]
        dirs = _half_sphere(n)
        need = math.ceil(OPEN_SHARE * len(dirs))
        free = 0
        for k, d in enumerate(dirs):
            free += _escapes(_RAYS, o, d)
            if free >= need or free + len(dirs) - k - 1 < need:
                break
        out.append(free >= need)
    return out


def outer_flags(points, workers=4):
    """For each (x, y, z, nx, ny, nz) in mm on the reference's housing,
    whether it is on the outer skin."""
    pts = [list(q) for q in points]
    size = max(1, len(pts) // (workers * 8))
    chunks = [pts[i:i + size] for i in range(0, len(pts), size)]
    with Pool(workers) as pool:
        return [f for part in pool.map(_outer_chunk, chunks) for f in part]


def outer_points(ref, workers=4):
    """Skin samples on the outer skin. Edits on the inner skin are not seen
    by anyone looking at the part, so the map spends its budget outside."""
    pts = [q for f in ref["housing_faces"] for q in f["p"]]
    flags = outer_flags([q[:3] + q[4:7] for q in pts], workers)
    return [q for q, f in zip(pts, flags) if f]


def spread(points, n, seed=1, gap=None, taken=()):
    """About n of points, evenly spaced: a greedy Poisson-disk pick with the
    spacing halved until enough points fit. With a gap given, one pass at
    that spacing that also keeps clear of the points already taken."""
    if gap is None and n > len(points):
        raise SystemExit(f"asked for {n} spots, only {len(points)} samples")
    rnd = random.Random(seed)
    pts = points[:]
    rnd.shuffle(pts)
    if gap is None:
        area = sum(q[3] for q in pts)
        gap = 2.0 * math.sqrt(area / (n * math.pi))
        fixed = False
    else:
        fixed = True
    while True:
        cell, grid, out = gap, {}, []
        for q in taken:
            k = tuple(int(math.floor(q[j] / cell)) for j in range(3))
            grid.setdefault(k, []).append(q)
        for q in pts:
            k = tuple(int(math.floor(q[j] / cell)) for j in range(3))
            near = (grid.get((k[0] + a, k[1] + b, k[2] + c), [])
                    for a in (-1, 0, 1) for b in (-1, 0, 1)
                    for c in (-1, 0, 1))
            if all(d2(q, o) >= gap * gap for lst in near for o in lst):
                grid.setdefault(k, []).append(q)
                out.append(q)
                if len(out) == n and not fixed:
                    return out, gap
        if fixed:
            return out, gap
        gap *= 0.9


def spot_p(s):
    """A spot's position, from a graded spot or an exempt one (older files
    list exempt spots as bare positions)."""
    return s["p"] if isinstance(s, dict) else s


_REF = None


def _grade_spot(args):
    global _REF
    if _REF is None:
        _REF = load("solution")
    q, edits = args
    row = {"p": [round(v, 2) for v in q[:3]],
           "n": [round(v, 3) for v in q[4:7]], "un": {}}
    for name, kind, h, r in edits:
        cap = disc_edit(_REF, kind, h, r, q[:3])
        row["un"][name] = round(H.Grader(BASELINE, cap)
                                .c5_unrequested()["score"], 4)
    return row


def main():
    args = sys.argv[1:]
    extend, at = [], None
    if "--extend" in args:      # earlier result files to add spots to
        i = args.index("--extend")
        extend, args = args[i + 1:], args[:i]
    if "--at" in args:          # a JSON list of spot positions to grade
        i = args.index("--at")
        at = json.loads(Path(args[i + 1]).read_text())
        del args[i:i + 2]
    n = int(args[0]) if len(args) > 0 else 250
    workers = int(args[1]) if len(args) > 1 else 4
    out = Path(args[2]) if len(args) > 2 else Path("blindspots.json")
    edits = EDITS
    if len(args) > 3:           # a subset of the edits, by name
        edits = [e for e in EDITS if e[0] in args[3].split(",")]
    ref = load("solution")
    exempt = exempt_test(ref)
    outer = outer_points(ref, workers)
    if at is not None:
        want = {tuple(round(v, 2) for v in p) for p in at}
        spots = [q for f in ref["housing_faces"] for q in f["p"]
                 if tuple(round(v, 2) for v in q[:3]) in want]
        gap = json.loads(Path(extend[0]).read_text())["gap_mm"] \
            if extend else 0.0
    elif extend:
        # new spots only where the earlier runs left a gap, at their spacing
        old = [json.loads(Path(f).read_text()) for f in extend]
        gap = old[0]["gap_mm"]
        # earlier spots are samples; those now found on the inner skin do
        # not count, they would block the outer skin a wall's width away
        here = {tuple(round(v, 2) for v in q[:3]) for q in outer}
        taken = [p for p in (spot_p(s) for d in old
                             for s in d["spots"] + d.get("exempt_spots", []))
                 if tuple(p) in here]
        spots, gap = spread(outer, 0, gap=gap, taken=taken)
    else:
        spots, gap = spread(outer, n)
    free = [q for q in spots if not exempt(q)]
    print(f"{len(spots)} spots {gap:.1f} mm apart on the outer skin, "
          f"{len(free)} outside the exempt zones; "
          f"{len(free) * len(edits)} grades", flush=True)
    t0, rows = time.time(), []
    with Pool(workers) as pool:
        for i, row in enumerate(pool.imap_unordered(
                _grade_spot, [(q, edits) for q in free])):
            rows.append(row)
            missed = [k for k, s in row["un"].items()
                      if (s >= 1.0 - 1e-6) != (k in HARMLESS)]
            print(f"{i + 1}/{len(free)} at {[round(v) for v in row['p']]} "
                  f"wrong on {missed or 'nothing'}  "
                  f"({time.time() - t0:.0f} s)", flush=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"edits": [e[0] for e in edits], "harmless": sorted(HARMLESS),
                 "gap_mm": round(gap, 1),
                 "harness_version": H.HARNESS_VERSION, "spots": rows,
                 "exempt_spots": [{"p": [round(v, 2) for v in q[:3]],
                                   "kind": exempt(q)}
                                  for q in spots if exempt(q)]}))
            tmp.replace(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
