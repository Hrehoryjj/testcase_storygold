"""Line of sight, measured: what stands between a solid and the outside.

A bounding box cannot see a hole and a projection cannot see round a
corner. When an instruction says a thing is "visible" or asks for it to be
covered, the sentence is about lines of sight, and rays are the only cheap
answer that is actually about the same thing.

Two pieces of COM knowledge are baked in here because both cost a debugging
pass to find and neither announces itself when wrong:

  bodies live in PART space.  `IComponent2` hands its bodies over from the
      component's own PartDoc, so their coordinates are the part's. Rays
      built in assembly space and fired at 1288 of them returned ZERO
      intersections -- which is exactly what "nothing is in the way" looks
      like. Rays are therefore carried into each component's space and cast
      one component at a time; the hit distance stays comparable because
      the transform is rigid.

  the hit array's layout is not fixed.  `GetRayIntersectionsPoints` is a
      PROPERTY, not a method, and its columns move with the options passed.
      With swRayPtsOptsTOPOLS set, column 0 is a topology id: on a real
      assembly it held 513 and 529 where 25 rays existed. Read as the ray
      index it raised "out of range" on two components and passed silently
      on 83 others whose ids happened to fall under 25 -- 202 intersections
      of plausible nonsense. So the layout is DERIVED from the numbers (see
      infer_packing) and re-checked on every call.
"""
from __future__ import annotations

import numpy as np

from . import solidworks_assembly as SA
from .solidworks_assembly import MM, invert_rotation, invert_transform, safe

M = 0.001

#: swRayPtsOpts_e. Named so that a run which returns nothing can be repeated
#: with a different set without hunting for a bare integer inside a call.
RAY_NORMALS = 1
RAY_ENTRY_EXIT = 2
RAY_TOPOLS = 4
RAY_UNBLOCKED = 8
DEFAULT_OPTIONS = RAY_NORMALS | RAY_ENTRY_EXIT | RAY_TOPOLS

#: A hit point further than this from the ray it claims is not on that ray.
ON_RAY_TOL_MM = 0.05


def _variant_r8(values):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                   [float(v) for v in np.asarray(values, float).ravel()])


def _variant_disp(objects):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(objects))


def _points(doc):
    """The hit array, without betting on how the member is exposed.

    Late-bound, `doc.GetRayIntersectionsPoints()` evaluates the property and
    then calls its result: "'NoneType' object is not callable" -- a message
    that names the symptom and hides the cause.
    """
    from .solidworks_session import z
    for fn in (lambda: doc.GetRayIntersectionsPoints,
               lambda: doc.GetRayIntersectionsPoints(),
               lambda: doc.IGetRayIntersectionsPoints):
        try:
            v = fn()
            if v is None or callable(v):
                continue
            v = list(v)
            if v:
                return v
        except Exception:                                   # noqa: BLE001
            continue
    return []


def infer_packing(raw, count, origins, dirs, nrays, tol=ON_RAY_TOL_MM):
    """Every layout of the flat array that is consistent with the rays.

    For each stride that divides the array, each column that could be a ray
    index (integral, in range) and each column triple that could be a point.
    A triple counts as the point only when every row lies ON the ray that
    row names -- the one test no coincidence survives across hundreds of
    hits, and what makes the answer evidence instead of a reading.
    """
    n = len(raw)
    out = []
    for st in range(4, 25):
        if n % st or (count and n // st != count):
            continue
        a = np.asarray(raw, float).reshape(n // st, st)
        for ic in range(st):
            col = a[:, ic]
            if not np.all(col == np.floor(col)) or col.min() < 0 \
                    or col.max() >= nrays:
                continue
            ri = col.astype(int)
            for i in range(st - 2):
                v = a[:, i:i + 3] * MM - origins[ri]
                t = np.einsum("ij,ij->i", v, dirs[ri])
                off = float(np.abs(np.linalg.norm(
                    v - t[:, None] * dirs[ri], axis=1)).max())
                if off <= tol:
                    out.append({"stride": st, "ray_col": ic, "point_col": i,
                                "max_off_mm": round(off, 6)})
    return out


def cast_at(doc, bodies, rot, trn, origins, dirs, layout=None,
            options=DEFAULT_OPTIONS, hit_radius=1e-6, tol=ON_RAY_TOL_MM):
    """Fire assembly-space rays at one component's bodies.

    Returns (pairs, layout, error). `pairs` are (ray index, distance along
    the ray in mm). An empty list with no error means the rays missed --
    legitimate and common, and different from a failure, which is why the
    two are separate return values rather than one empty result.
    """
    from .solidworks_session import z
    o = np.asarray([invert_transform(rot, trn, p) for p in origins], float)
    d = np.asarray([invert_rotation(rot, v) for v in dirs], float)
    try:
        count = z(doc.RayIntersections(_variant_disp(bodies),
                                       _variant_r8(o * M), _variant_r8(d),
                                       int(options), float(hit_radius), 0.0))
    except Exception as exc:                                # noqa: BLE001
        return None, layout, f"{type(exc).__name__}: {exc}"
    if not count:
        return [], layout, None
    raw = _points(doc)
    if not raw:
        return None, layout, "hit count non-zero but no point array"
    count = int(count)

    def read(lay):
        st, rc, pc = lay["stride"], lay["ray_col"], lay["point_col"]
        if len(raw) % st or len(raw) // st != count:
            return None
        a = np.asarray(raw, float).reshape(count, st)
        col = a[:, rc]
        if not np.all(col == np.floor(col)) or col.min() < 0 \
                or col.max() >= len(origins):
            return None
        ri = col.astype(int)
        v = a[:, pc:pc + 3] * MM - o[ri]
        t = np.einsum("ij,ij->i", v, d[ri])
        if float(np.abs(np.linalg.norm(v - t[:, None] * d[ri],
                                       axis=1)).max()) > tol:
            return None
        return ri, t

    got = read(layout) if layout else None
    if got is None:
        cands = infer_packing(raw, count, o, d, len(origins), tol)
        if not cands:
            return None, layout, (f"raw_len={len(raw)} count={count}: no "
                                  "layout puts the hits on their own rays")
        keys = {(c["stride"], c["ray_col"], c["point_col"]) for c in cands}
        if len(keys) > 1:
            return None, layout, (f"{len(keys)} layouts all fit {sorted(keys)}"
                                  " -- ambiguous, refusing to read")
        layout = cands[0]
        got = read(layout)
        if got is None:
            return None, layout, "inferred layout failed its own check"
    ri, t = got
    # A hit behind the emitter is not a hit: RayIntersections reports the
    # infinite line, and a negative distance read as positive puts geometry
    # in front of the target that is behind it.
    keep = t > 0
    return list(zip(ri[keep].tolist(), t[keep].tolist())), layout, None


def verdict(target_t, other_t, other_by, added_t, added_by, prior_t,
            eps=1e-9):
    """Three questions per ray, kept apart because they have different owners.

      hidden by anything     context: how visible the target is now
      hidden by an addition  the criterion: only the candidate's own parts
      open to begin with     the denominator: rays the supplied geometry
                             does not already block

    Folding them together is how an articulated sub-assembly -- parked
    differently in every model, and already hiding a fifth of the target in
    the supplied one -- ends up scored as the candidate's work.
    """
    out = {"exposed": 0, "blocked": 0, "open_rays": 0, "add_rays": 0,
           "blockers": {}, "adders": {}, "open_not_covered": []}
    for r, tt in target_t.items():
        to, ta, tp = other_t.get(r), added_t.get(r), prior_t.get(r)
        hid_any = to is not None and to < tt - eps
        hid_add = ta is not None and ta < tt - eps
        hid_pre = tp is not None and tp < tt - eps
        if hid_any:
            out["blocked"] += 1
            k = other_by[r]
            out["blockers"][k] = out["blockers"].get(k, 0) + 1
            if hid_add:
                k = added_by[r]
                out["adders"][k] = out["adders"].get(k, 0) + 1
        else:
            out["exposed"] += 1
        if not hid_pre:
            out["open_rays"] += 1
            if hid_add:
                out["add_rays"] += 1
            else:
                out["open_not_covered"].append(r)
    return out


def grid(lo, hi, axis, outward, n, pad=2.0, start_beyond=500.0):
    """An n x n sheet of parallel rays covering a box, fired from outside."""
    keep = [i for i in range(3) if i != axis]
    a = np.linspace(lo[keep[0]] - pad, hi[keep[0]] + pad, n)
    b = np.linspace(lo[keep[1]] - pad, hi[keep[1]] + pad, n)
    A, B = np.meshgrid(a, b, indexing="ij")
    o = np.zeros((A.size, 3))
    o[:, keep[0]] = A.ravel()
    o[:, keep[1]] = B.ravel()
    o[:, axis] = (lo[axis] - start_beyond if outward < 0
                  else hi[axis] + start_beyond)
    d = np.zeros(3)
    d[axis] = 1.0 if outward < 0 else -1.0
    cell = (float(a[1] - a[0]) * float(b[1] - b[0])) if n > 1 else 0.0
    return o, np.tile(d, (A.size, 1)), A.ravel(), B.ravel(), cell


def visibility(doc, comps, target_ids, added_ids, origins, dirs,
               options=DEFAULT_OPTIONS, progress=None):
    """How much of the target is still in plain sight, and behind what.

    `comps` is a sequence of {id, rot, trn, bodies, debug_file}. Which of
    them are the target and which the candidate added are the caller's to
    decide -- this function never looks at a name.
    """
    say = progress or (lambda *_: None)
    tgt, oth, oth_by, add, add_by, pre = {}, {}, {}, {}, {}, {}
    layout, errors, hits = None, {}, 0
    for i, c in enumerate(comps, 1):
        pairs, layout, err = cast_at(doc, c["bodies"], c["rot"], c["trn"],
                                     origins, dirs, layout, options)
        if err:
            errors[c["id"]] = err
            continue
        hits += len(pairs)
        is_t = c["id"] in target_ids
        is_new = c["id"] in added_ids
        for r, t in pairs:
            if is_t:
                if r not in tgt or t < tgt[r]:
                    tgt[r] = t
                continue
            if r not in oth or t < oth[r]:
                oth[r], oth_by[r] = t, c.get("debug_file") or c["id"]
            if is_new:
                if r not in add or t < add[r]:
                    add[r], add_by[r] = t, c.get("debug_file") or c["id"]
            elif r not in pre or t < pre[r]:
                pre[r] = t
        if i % 25 == 0:
            say(f"      {i}/{len(comps)} cast")
    v = verdict(tgt, oth, oth_by, add, add_by, pre)
    v.update({"array_layout": layout, "errors": errors,
              "intersections": hits, "components_cast": len(comps),
              "rays": len(origins), "options": options})
    return v


def cast_available(app=None):
    """Whether this host can cast at all -- pywin32, and nothing else."""
    try:
        import pythoncom                                   # noqa: F401
        from win32com.client import VARIANT                # noqa: F401
    except Exception:                                      # noqa: BLE001
        return False
    return True
