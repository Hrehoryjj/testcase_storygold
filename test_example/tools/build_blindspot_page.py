"""Turn blindspot_map.py results into one self-contained 3D page.

    python3 tools/build_blindspot_page.py OUT.html blindspots.json [MORE.json ...]
        [--reading notes.html]

Several result files (a second run with only the harmless edits, a run that
added spots) are merged spot by spot. --reading puts a short HTML note of
your own under "What the map says".

Every vertex of the reference's housing mesh gets a value per view,
interpolated from the measured spots around it (inverse distance, spots
within 1.5 spot gaps that face the same way): the share of edits caught
there, overall and for each edit. A harmless edit is shown the other way
round: the share of spots where the check correctly let it pass. Only the
outer skin is coloured; the inside of the hollow housing is left neutral,
and so are vertices with no measured spot nearby. Vertices in the zones the
check exempts get their own colours: the strip the task changes and the
margins left around the controls. The page draws the part with three.js
and lets the reader switch views.
"""
import base64
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_argv, sys.argv = sys.argv, sys.argv[:1]
import blindspot_map as B  # noqa: E402
sys.argv = _argv

TEMPLATE = Path(__file__).resolve().parent / "blindspot_page_template.html"
# vertex codes; 0..200 is the share of edits handled right
STRIP, UNMEASURED, CONTROLS = 255, 254, 253


def b64(fmt, values):
    return base64.b64encode(struct.pack(f"<{len(values)}{fmt}",
                                        *values)).decode()


def gather(meshes):
    """Mesh records (integer vertices) into one indexed triangle list, with
    shared vertices merged across faces."""
    index, verts, tris = {}, [], []
    for rec in meshes:
        mv, mt = rec["mv"], rec["mt"]
        local = []
        for i in range(0, len(mv), 3):
            key = (mv[i], mv[i + 1], mv[i + 2])
            k = index.get(key)
            if k is None:
                k = index[key] = len(verts) // 3
                verts.extend(key)
            local.append(k)
        tris.extend(local[j] for j in mt)
    return verts, tris


def split_long(verts, tris, longest):
    """Split triangles until no edge is longer than longest (mesh units),
    so colours interpolated across a large flat facet do not smear from one
    end of the part to the other. Midpoints are shared between the two
    triangles of an edge where both split it."""
    verts, mid = list(verts), {}

    def middle(a, b):
        key = (a, b) if a < b else (b, a)
        k = mid.get(key)
        if k is None:
            k = mid[key] = len(verts) // 3
            verts.extend(round((verts[3 * a + j] + verts[3 * b + j]) / 2)
                         for j in range(3))
        return k

    def length2(a, b):
        return sum((verts[3 * a + j] - verts[3 * b + j]) ** 2
                   for j in range(3))
    out, stack = [], [tuple(tris[i:i + 3]) for i in range(0, len(tris), 3)]
    while stack:
        t = stack.pop()
        e = [length2(t[k], t[(k + 1) % 3]) for k in range(3)]
        k = max(range(3), key=lambda j: e[j])
        if e[k] <= longest * longest:
            out.extend(t)
            continue
        a, b, c = t[k], t[(k + 1) % 3], t[(k + 2) % 3]
        m = middle(a, b)
        stack += [(a, m, c), (m, b, c)]
    return verts, out


def caught(score):
    return score < 1.0 - 1e-6


def merged(paths):
    """One result set from several runs: runs of other edits over the same
    spots, runs that added spots, and runs that graded spots again (the
    later file wins), merged spot by spot."""
    data, by_p, exempt, redone = None, {}, {}, 0
    for path in paths:
        d = json.loads(Path(path).read_text())
        if data is None:
            data = dict(d, edits=[], harmless=[])
        for k in ("gap_mm", "harness_version"):
            if d.get(k) != data.get(k):
                sys.exit(f"{path}: {k} {d.get(k)} differs from "
                         f"{data.get(k)}; not the same spots or grader")
        data["edits"] += [e for e in d["edits"] if e not in data["edits"]]
        data["harmless"] += d.get("harmless", [])
        for s in d["spots"]:
            un = by_p.setdefault(tuple(s["p"]), dict(s, un={}))["un"]
            redone += bool(set(un) & set(s["un"]))
            un.update(s["un"])
        for s in d.get("exempt_spots", []):
            exempt[tuple(B.spot_p(s))] = s
    data["harmless"] = sorted(set(data["harmless"]) & set(data["edits"]))
    data["spots"] = [s for s in by_p.values()
                     if set(s["un"]) == set(data["edits"])]
    data["exempt_spots"] = list(exempt.values())
    if redone:
        print(f"{redone} spots graded again, the later result kept")
    if len(data["spots"]) < len(by_p):
        print(f"{len(by_p) - len(data['spots'])} spots lack some edits "
              "and are left out")
    return data


def outer_test(ref):
    """Whether a point of the reference's housing with normal n is on the
    outer skin: the ray test of blindspot_map.py on the skin samples, and
    for any other point the nearest sample that faces the same way (the
    shell's two skins are a wall's width apart and face opposite ways)."""
    pts = [q for f in ref["housing_faces"] for q in f["p"]]
    flags = B.outer_flags([q[:3] + q[4:7] for q in pts])
    cell, grid = 4.0, {}
    for q, f in zip(pts, flags):
        k = tuple(int(math.floor(q[j] / cell)) for j in range(3))
        grid.setdefault(k, []).append((q, f))
    at = {tuple(round(v, 2) for v in q[:3]): f for q, f in zip(pts, flags)}

    def outer(p, n=None):
        f = at.get(tuple(round(v, 2) for v in p))
        if f is not None:
            return f
        k = tuple(int(math.floor(p[j] / cell)) for j in range(3))
        best = None
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                for c in (-1, 0, 1):
                    for q, f in grid.get((k[0] + a, k[1] + b, k[2] + c), ()):
                        if n is not None and sum(
                                n[j] * q[4 + j] for j in range(3)) < 0.3:
                            continue
                        d = B.d2(p, q)
                        if best is None or d < best[0]:
                            best = (d, f)
        return bool(best and best[1])
    return outer


def seed_test(ref):
    """Whether a point of the reference lies on the seed's own surface,
    moved the way the check moves it (each half by its registered shift,
    or by the task's half width): within 0.5 mm, facing the same way.
    Where it does not, the reference reshaped the part and the seed is no
    template for what the surface should be."""
    g = B.H.Grader(B.BASELINE, ref)
    g.c5_unrequested()
    P = g.P * B.H.MM
    half = g._actual_half_m() * B.H.MM
    reg = g._rebuilt["reg"]
    faces = B.BASELINE["housing_faces"]
    meshes = {sg: [B.H.SkinMesh(faces, sg * m, 0.5)
                   for m in sorted({half, reg.get(sg, half)})]
              for sg in (1, -1)}

    def on(p, n):
        sg = 1 if p[0] > P else -1
        return any(m.on(p, n, 0.7) for m in meshes[sg])
    return on


def vertex_normals(verts, tris):
    acc = [0.0] * len(verts)
    for i in range(0, len(tris), 3):
        a, b, c = (tris[i + k] * 3 for k in range(3))
        u = [verts[b + j] - verts[a + j] for j in range(3)]
        v = [verts[c + j] - verts[a + j] for j in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        for k in (a, b, c):
            for j in range(3):
                acc[k + j] += n[j]
    out = []
    for i in range(0, len(acc), 3):
        ln = math.sqrt(sum(x * x for x in acc[i:i + 3])) or 1.0
        out.append([x / ln for x in acc[i:i + 3]])
    return out


def main():
    args = sys.argv[1:]
    reading = ""
    if "--reading" in args:
        i = args.index("--reading")
        reading = Path(args[i + 1]).read_text()
        del args[i:i + 2]
    if len(args) < 2:
        sys.exit(__doc__)
    out = Path(args[0])
    data = merged(args[1:])
    edits, spots, gap = data["edits"], data["spots"], data["gap_mm"]
    harmless = data["harmless"]
    real = [e for e in edits if e not in harmless]
    ref = B.load("solution")
    exempt = B.exempt_test(ref)
    unit = B.H.MESH_UNIT_MM
    outer = outer_test(ref)
    # spots an older outer skin test placed on the inner skin are dropped
    inner = [s for s in spots if not outer(s["p"])]
    spots = [s for s in spots if outer(s["p"])]
    ex_p = [B.spot_p(s) for s in data.get("exempt_spots", [])]
    inner_ex = sum(not outer(p) for p in ex_p)
    if inner or inner_ex:
        print(f"{len(inner)} graded and {inner_ex} exempt spots are on the "
              "inner skin and left out")
    # spots the run treated as free but the exemption covers are dropped
    # from the results and counted with the exempt ones
    kinds = [exempt(p) for p in ex_p if outer(p)]
    moved = [s for s in spots if exempt(s["p"])]
    kinds += [exempt(s["p"]) for s in moved]
    spots = [s for s in spots if not exempt(s["p"])]
    if moved:
        print(f"{len(moved)} graded spots are exempt and left out")

    verts, tris = gather(ref["housing_faces"])
    verts, tris = split_long(verts, tris, 5.0 / unit)
    normals = vertex_normals(verts, tris)
    cverts, ctris = gather(ref["control_meshes"].values())

    views = ["all"] + edits + ["seed"]
    on_seed = seed_test(ref)
    # 1 where the check did the right thing: caught a real edit, or let a
    # harmless one pass
    vals = {s: [float(caught(spot["un"][e]) != (e in harmless))
                for e in edits]
            for s, spot in enumerate(spots)}
    real_i = [edits.index(e) for e in real]
    reach = 1.5 * gap
    cell = reach
    grid = {}
    for s, spot in enumerate(spots):
        k = tuple(int(math.floor(v / cell)) for v in spot["p"])
        grid.setdefault(k, []).append(s)

    codes = {v: [] for v in views}
    for i in range(0, len(verts), 3):
        p = [verts[i + j] * unit for j in range(3)]
        nv = normals[i // 3]
        if not outer(p, nv):
            for v in views:             # inner skin: not mapped
                codes[v].append(UNMEASURED)
            continue
        kind = exempt(p)
        if kind:
            for v in views:
                codes[v].append(STRIP if kind == "strip" else CONTROLS)
            continue
        k = tuple(int(math.floor(x / cell)) for x in p)
        acc, wsum = [0.0] * len(edits), 0.0
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                for c in (-1, 0, 1):
                    for s in grid.get((k[0] + a, k[1] + b, k[2] + c), ()):
                        d = math.dist(p, spots[s]["p"])
                        if d > reach or sum(
                                nv[j] * spots[s]["n"][j]
                                for j in range(3)) < 0.5:
                            continue
                        w = 1.0 / max(d, 1.0) ** 2
                        wsum += w
                        for e in range(len(edits)):
                            acc[e] += w * vals[s][e]
        codes["seed"].append(200 if on_seed(p, nv) else 0)
        if not wsum:
            for v in views[:-1]:
                codes[v].append(UNMEASURED)
            continue
        share = [a / wsum for a in acc]
        codes["all"].append(round(200 * sum(share[i] for i in real_i)
                                  / len(real_i)))
        for e, name in enumerate(edits):
            codes[name].append(round(200 * share[e]))

    n = len(spots)
    every = sum(all(caught(s["un"][e]) for e in real) for s in spots)
    summary = {
        "harness_version": data.get("harness_version"),
        "spots": n, "gap_mm": gap, "edits": real, "harmless": harmless,
        "exempt_strip": kinds.count("strip"),
        "exempt_controls": len(kinds) - kinds.count("strip"),
        "every_edit": every,
        "per_edit": {e: sum(caught(s["un"][e]) for s in spots)
                     for e in edits},
        "never": sum(not any(caught(s["un"][e]) for e in real)
                     for s in spots),
    }
    payload = {
        "unit": unit, "views": views, "summary": summary,
        "v": b64("i", verts), "t": b64("I", tris),
        "cv": b64("i", cverts), "ct": b64("I", ctris),
        "codes": {v: base64.b64encode(bytes(c)).decode()
                  for v, c in codes.items()},
        "spots": [{"p": s["p"], "ok": [int(x) for x in vals[i]]}
                  for i, s in enumerate(spots)],
    }
    html = TEMPLATE.read_text().replace(
        "/*DATA*/null", json.dumps(payload, separators=(",", ":"))).replace(
        "<!--READING-->", reading)
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): "
          f"{n} spots, {every} caught every edit, {summary['never']} none, "
          f"false alarms {[summary['per_edit'][e] for e in harmless]}")


if __name__ == "__main__":
    main()
