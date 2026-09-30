"""Assembly-level SolidWorks capture: transport and censuses.

Nothing here knows about a particular assembly. The split follows the same
rule as common/solidworks_capture.py -- a function belongs in common when it
does not know which part it is looking at -- and these are the calls any
assembly task would otherwise copy verbatim: opening a document read-only
and saying why when it will not open, walking the component tree by two
independent routes, reading bodies, faces and analytic surfaces, and the
feature/mate census.

What stays with a harness is interpretation: which torus is the thing being
graded, which component is the addition, what a number means.

Coordinates: bodies and their faces come back in PART space. The component
transform maps them into assembly space -- see apply_transform for the
convention, which is measured rather than documented.

Windows + pywin32 only, like the rest of the solidworks_* modules; importing
it elsewhere works, calling it does not.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from common import solidworks_session as sws
from common.solidworks_session import z

try:                                                     # Windows only
    from common import solidworks_measure as SM
except Exception:                                        # noqa: BLE001
    #: Scoring-only host. `interference_census` reports that it could
    #: not measure rather than reporting nothing overlapping, which
    #: are opposite answers wearing the same zero.
    SM = None

#: SolidWorks speaks metres; every consumer of this module works in
#: millimetres. The conversion happens here and nowhere else.
MM = 1000.0

#: swFileLoadError_e / swFileLoadWarning_e out-param bits.
LOAD_ERRORS = {
    1: "GenericError", 2: "FileNotFound", 4: "SimultaneousAccess",
    16: "LowResources", 32: "NoDisplayData", 128: "LiquidMachineDoc",
    512: "FileRequiresRepair", 1024: "FileCriticalDamage",
    65536: "FileWithSameTitleAlreadyOpen", 262144: "FutureVersion",
    2097152: "ApplicationBusy",
}
LOAD_WARNINGS = {
    1: "IdMismatch", 2: "ReadOnly", 4: "SharingViolation",
    16: "ViewMissingReferencedConfig", 32: "MissingDesignTable",
    128: "AlreadyOpen", 512: "RevolveDimTolerance", 8192: "NeedsRegen",
    16384: "BasePartNotLoaded", 32768: "ComponentMissingReferencedConfig",
}



def decode_bits(value, table):
    return [name for bit, name in sorted(table.items()) if value & bit]



def safe(fn, default=None):
    """Swallow COM quirks on OPTIONAL reads only.

    Never where an empty result is also a legitimate answer -- that is how
    "0 components" ends up meaning "the query failed". Structural reads go
    through probed().
    """
    try:
        return fn()
    except Exception:
        return default



def probed(fn, label):
    """(value, error_text) -- for calls whose failure must not look like data."""
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"



def apply_transform(rot, trans, p):
    """Point from part space into assembly space.

    MathTransform.ArrayData is nine rotation values then three
    translations, and the nine are COLUMN-major -- hence the transpose.
    Worth re-checking against a known pair of concentric features on an
    unfamiliar build: the row-major reading is wrong by hundreds of
    millimetres and still looks like a coordinate.
    """
    r = np.asarray(rot, float).reshape(3, 3).T
    return (r @ np.asarray(p, float) + np.asarray(trans, float)).tolist()



def apply_rotation(rot, vec):
    """Direction from part space into assembly space (no translation)."""
    r = np.asarray(rot, float).reshape(3, 3).T
    return (r @ np.asarray(vec, float)).tolist()



def invert_transform(rot, trans, p):
    """Point from ASSEMBLY space back into the part's own space.

    Needed wherever a query has to be asked in the space the geometry
    actually lives in. Component bodies come out of the component's
    PartDoc, so their coordinates are part coordinates; a ray built in
    assembly space and fired at them intersects nothing, and "no
    intersection" is indistinguishable from "nothing is in the way".

    The rotation SolidWorks hands over is orthogonal -- mirrored instances
    included, where the determinant is -1 -- so the transpose is the
    inverse and no general matrix inverse is wanted here.
    """
    r = np.asarray(rot, float).reshape(3, 3).T
    return (r.T @ (np.asarray(p, float) - np.asarray(trans, float))).tolist()


def invert_rotation(rot, vec):
    """Direction from assembly space back into part space."""
    r = np.asarray(rot, float).reshape(3, 3).T
    return (r.T @ np.asarray(vec, float)).tolist()


def open_readonly(app, path, doc_type=None):
    """Open a document read-only, reporting why when it will not open.

    Read-only matters: SolidWorks rewrites parts it migrates on open, and a
    grader that mutates what it grades makes its own next run unrepeatable.

    `doc_type` defaults to the file extension. It exists because task 8
    grades a standalone .SLDPRT, and opening a part with swDocASSEMBLY does
    not raise -- OpenDoc6 returns null with a generic error code, which
    reads exactly like a missing or damaged file.
    """
    p = str(Path(path).resolve())
    if doc_type is None:
        doc_type = (sws.DOC_PART if p.lower().endswith(".sldprt")
                    else sws.DOC_ASSEMBLY)
    existing = safe(lambda: sws.find_document(app, p))
    if existing is not None:
        return sws.dyn(existing), {"reused_open_document": True}
    # Every folder here ships its own copy of the same 24 filenames and
    # SolidWorks resolves references BY FILENAME: a document left open from
    # the previous candidate is silently reused for this one. Sweep first.
    sws.close_all_documents(app)
    errs, warns = sws.byref_i4(), sws.byref_i4()
    opts = sws.OPEN_SILENT | sws.OPEN_READONLY
    raw, err = probed(lambda: app.OpenDoc6(p, doc_type, opts, "",
                                           errs, warns), "OpenDoc6")
    ev = safe(lambda: int(errs.value), 0) or 0
    wv = safe(lambda: int(warns.value), 0) or 0
    diag = {"error_code": ev, "warning_code": wv,
            "errors": decode_bits(ev, LOAD_ERRORS),
            "warnings": decode_bits(wv, LOAD_WARNINGS),
            "exception": err}
    if raw is None:
        return None, diag
    doc = sws.dyn(raw)
    if doc_type == sws.DOC_ASSEMBLY:
        # Lightweight components expose no bodies at all, so resolving is
        # what makes the assembly measurable. Reported, never assumed. A
        # part has no components; calling it there raises, and the
        # exception text would be filed as an open diagnostic.
        r, rerr = probed(lambda: z(doc.ResolveAllLightWeightComponents(False)),
                         "ResolveAllLightWeightComponents")
        diag["resolve_lightweight"] = {"result": r, "error": rerr}
    return doc, diag



def walk_components(doc):
    """Every component, by two independent routes, each reporting failure.

    `GetRootComponent3` is on the CONFIGURATION, not the document; calling
    it on the document raises AttributeError. The flat `GetComponents` route
    runs too: two counts that disagree are visible, one count that is wrong
    is not.

    "Empty" at a first touch can mean "not loaded yet" rather than "not
    there", so the cheap route is read twice and both counts recorded.
    """
    out = {"placeholders": 0, "errors": {}}
    flat, err = probed(lambda: list(z(doc.GetComponents(False)) or []),
                       "GetComponents")
    if err:
        out["errors"]["GetComponents"] = err
    conf, err = probed(lambda: z(doc.GetActiveConfiguration),
                       "GetActiveConfiguration")
    if err:
        out["errors"]["GetActiveConfiguration"] = err
    tree = []
    if conf is not None:
        root, err = probed(lambda: conf.GetRootComponent3(True),
                           "GetRootComponent3")
        if err:
            out["errors"]["GetRootComponent3"] = err
        if root is not None:
            def rec(node, depth):
                for kid in (safe(lambda: z(node.GetChildren)) or []):
                    if kid is None:
                        out["placeholders"] += 1
                        continue
                    c = sws.dyn(kid)
                    path = safe(lambda: str(z(c.GetPathName)), "") or ""
                    state = safe(lambda: int(z(c.GetSuppression)), None)
                    if not path and state in (None, -1):
                        out["placeholders"] += 1
                        continue
                    tree.append((c, depth))
                    if depth < 6:
                        rec(c, depth + 1)
            rec(sws.dyn(root), 0)
    comps = tree or [(sws.dyn(c), 0) for c in (flat or []) if c is not None]
    flat2, _ = probed(lambda: list(z(doc.GetComponents(False)) or []),
                      "GetComponents(second read)")
    out.update(route=("GetRootComponent3" if tree else
                      ("GetComponents" if flat else None)),
               count_tree=len(tree), count_flat=len(flat or []),
               count_flat_second_read=len(flat2 or []),
               count=len(comps))
    out["stable"] = out["count_flat"] == out["count_flat_second_read"]
    return comps, out



def mass_props(md):
    """Centroid, volume and area, whichever call shape works.

    GetMassProperties2 takes a status OUT-parameter and builds disagree on
    whether a plain 0 will do.
    """
    ext = safe(lambda: z(md.Extension)) if md is not None else None
    if ext is None:
        return None
    status = sws.byref_i4()
    for fn in (lambda: list(z(ext.GetMassProperties2(1, status, False))),
               lambda: list(z(ext.GetMassProperties2(1, 0, False))),
               lambda: list(z(ext.GetMassProperties(1, status))),
               lambda: list(z(ext.GetMassProperties(1, 0)))):
        v = safe(fn)
        if v and len(v) >= 5:
            return {"centroid_mm": [round(x * MM, 4) for x in v[0:3]],
                    "volume_mm3": round(v[3] * MM ** 3, 4),
                    "area_mm2": round(v[4] * MM * MM, 4)}
    return None



def assembly_mass(doc):
    return mass_props(doc)



def rebuild_state(doc, force=True):
    """Force a rebuild, say whether it worked -- and what it COST.

    The old docstring said "read-only is fine: the rebuild happens in
    memory and CloseDoc throws it away". That is true about the FILE and
    false about the MEASUREMENT. Every reading taken after this call sees
    the rebuilt tree, and on an assembly whose parts are modelled in
    context the forced rebuild cannot update their external references, so
    it MANUFACTURES errors: on task 60's corpus every model, the reference
    included, went from 0 feature errors to 10 dangling mates and LOST
    eight features -- and the one model that really did carry broken mates
    was buried under them.

    "Errors after I broke it" and "errors as delivered" are two different
    facts, and returning only `ok` let them arrive as one. So the tree is
    now counted BEFORE and AFTER, and the difference is reported:

        before / after   feature and error counts either side of the call
        manufactured     errors this call created and features it lost
        trustworthy      False when it created any -- the health reading
                         taken after this call is about the rebuild, not
                         about the model

    `force=False` takes the reading without rebuilding anything, which is
    what a criterion about the model as delivered wants.

    A caller that only reads `ok` keeps working; nothing is removed.
    """
    def summary(c):
        """Only the few numbers this reading needs. The whole census here
        would double the size of every capture for data nothing reads.

        WARNINGS ARE IN THE LIST because the damage this function exists to
        report arrives as warnings, not as errors: a dangling external
        reference flags a feature YELLOW, and `feature_census` counts
        yellow separately from red. A before/after pair that omits the
        warning count cannot see the very thing the docstring above
        describes -- ten dangling mates read as nothing changed.
        """
        c = c or {}
        return {k: c.get(k) for k in
                ("features", "error_count", "warning_count",
                 "suppressed", "mates")}

    before = summary(safe(lambda: feature_census(doc)))
    if not force:
        return {"ok": None, "error": "not forced (force=False)",
                "forced": False, "trustworthy": True,
                "before": before, "after": before, "manufactured": {}}
    ok, err = probed(lambda: bool(z(doc.ForceRebuild3(False))), "ForceRebuild3")
    after = summary(safe(lambda: feature_census(doc)))

    def n(d, k):
        v = d.get(k)
        return v if isinstance(v, (int, float)) else None

    made, lost, warned = None, None, None
    eb, ea = n(before, "error_count"), n(after, "error_count")
    fb, fa = n(before, "features"), n(after, "features")
    wb, wa = n(before, "warning_count"), n(after, "warning_count")
    if eb is not None and ea is not None:
        made = max(0, ea - eb)
    if fb is not None and fa is not None:
        lost = max(0, fb - fa)
    #: A dangling reference the rebuild created is yellow, not red, so
    #: counting only `error_count` reports the harmless case and misses
    #: the one this function was written for.
    if wb is not None and wa is not None:
        warned = max(0, wa - wb)
    manufactured = {}
    if made:
        manufactured["errors_created"] = made
    if warned:
        manufactured["warnings_created"] = warned
    if lost:
        manufactured["features_lost"] = lost
    # Unknown is not the same as none: if either census failed we cannot
    # claim the rebuild was harmless.
    known = None not in (eb, ea, fb, fa)
    return {"ok": ok, "error": err, "forced": True,
            "trustworthy": bool(known and not manufactured),
            "counts_read": known,
            "before": before, "after": after,
            "manufactured": manufactured}


def tree_faults(census, notice_code=1):
    """What a feature census says is WRONG, as against merely noted.

    Two numbers and the tally behind them:

        serious    errors above `notice_code`. Adding a component or a
                   hole series raises `Reference:1` and `HoleSeries:1` on
                   almost every candidate that did the work, the
                   reference included; a mate that will not solve comes
                   back at 48. Counting those equally grades a model
                   for doing what it was asked to do. Task 13 found
                   this and filtered it locally; this is that filter,
                   in the one place.

        dangling   warnings whose TYPE is `Reference`. A dangling
                   external reference flags its feature YELLOW, so
                   `error_count` cannot see it at all -- and it sits AT
                   notice level, so a filter by code cannot let it
                   through either. It is picked out by type instead,
                   which is what the criterion it serves actually names:
                   "no rebuild errors or dangling references".

    Every other warning is left alone. `MateGroup:1` and `HoleSeries:1`
    turn up on models that did nothing wrong, and a row that charged for
    them would charge the reference.
    """
    census = census or {}
    errs = census.get("errors") or []
    warns = census.get("warnings") or []
    tally = {}
    for e in list(errs) + list(warns):
        k = f"{e.get('type')}:{e.get('code')}"
        tally[k] = tally.get(k, 0) + 1
    serious = sum(1 for e in errs if (e.get("code") or 0) > notice_code)
    dangling = sum(1 for w in warns
                   if str(w.get("type") or "").startswith("Reference"))
    return {"serious": serious, "dangling": dangling, "tally": tally,
            "errors": len(errs), "warnings": len(warns)}


def rebuild_damage(rec):
    """What a stored `rebuild` block says the forced rebuild cost.

    Returns None when the capture predates this reading -- which a scorer
    must treat as "not known", never as "nothing happened".
    """
    if not isinstance(rec, dict) or "manufactured" not in rec:
        return None
    return {"errors_created": rec["manufactured"].get("errors_created", 0),
            "warnings_created": rec["manufactured"].get(
                "warnings_created", 0),
            "features_lost": rec["manufactured"].get("features_lost", 0),
            "trustworthy": rec.get("trustworthy")}



def _feature_error(f):
    """(code, is_warning) for one feature, or (None, False).

    `GetErrorCode2` takes a BY-REF "is this a warning" flag and returns the
    code. The earlier version passed a literal `0` there, which is not a
    by-ref, so the call did not go through and the walk fell all the way
    down to the deprecated `GetErrorCode` -- a DIFFERENT code space with no
    warning flag at all. The visible symptom on task 20's reference: an
    assembly whose four flagged features are all warnings reported one
    error with code 51, and a must-pass "rebuilds without errors" gate
    would have zeroed the reference over it.

    A warning and an error are not the same fact and must not arrive as one
    number. The route that answered is recorded so that "no errors" and
    "could not read the codes" stay distinguishable.
    """
    warn = sws.byref_bool(False)
    code = safe(lambda: int(z(f.GetErrorCode2(warn))), None)
    if code is not None:
        return code, bool(getattr(warn, "value", False))
    # Legacy fallback. It cannot report warnings, so anything it finds is
    # reported as an error -- which is the safe direction, and the census
    # says which route it used.
    code = safe(lambda: int(z(f.GetErrorCode)), None)
    return code, False


def feature_census(doc):
    """Feature tree by TYPE, with suppression and error state.

    Types, never names. Mates are sub-features of the mate group, so the
    walk recurses -- a flat pass finds one feature where there are thirty.

    Errors and WARNINGS are counted separately: see `_feature_error`.
    `error_count` therefore means what it says, and callers that used to
    read it as "errors and warnings together" now get the smaller, correct
    number.
    """
    out = {"features": 0, "by_type": {}, "errors": [], "warnings": [],
           "suppressed": 0, "mates": 0, "mate_types": {}, "error_count": 0,
           "warning_count": 0, "code_route": None, "walk_error": None}
    root, err = probed(lambda: z(doc.FirstFeature), "FirstFeature")
    if root is None:
        out["walk_error"] = err
        return out
    seen = 0

    def visit_one(f, depth):
        """One feature: count it, then walk whatever hangs under it."""
        nonlocal seen
        if True:
            seen += 1
            tname = safe(lambda: str(z(f.GetTypeName2)), None) or \
                safe(lambda: str(z(f.GetTypeName)), None) or "?"
            out["features"] += 1
            out["by_type"][tname] = out["by_type"].get(tname, 0) + 1
            if (safe(lambda: bool(z(f.IsSuppressed2(1, 0, 0))), None)
                    or safe(lambda: bool(z(f.IsSuppressed)), False)):
                out["suppressed"] += 1
            code, is_warning = _feature_error(f)
            if code and is_warning:
                out["warning_count"] += 1
                out["warnings"].append({"type": tname, "code": code,
                                        "depth": depth})
            elif code:
                out["error_count"] += 1
                out["errors"].append({"type": tname, "code": code,
                                      "depth": depth})
            if tname.startswith("Mate"):
                out["mates"] += 1
                out["mate_types"][tname] = out["mate_types"].get(tname, 0) + 1
            # Sub-features chain through GetNextSubFeature, not
            # GetNextFeature -- the wrong one leaves the chain after a step.
            sub = safe(lambda: z(f.GetFirstSubFeature))
            g = 0
            while sub is not None and depth < 4 and g < 5000:
                g += 1
                visit_one(sws.dyn(sub), depth + 1)
                sub = safe(lambda x=sub: z(x.GetNextSubFeature))

    feat, guard = root, 0
    while feat is not None and guard < 20000:
        guard += 1
        visit_one(sws.dyn(feat), 0)
        feat = safe(lambda x=feat: z(x.GetNextFeature))
    # Which call answered, so that "clean tree" and "codes unreadable on
    # this build" cannot be read as the same result.
    probe = safe(lambda: z(doc.FirstFeature))
    if probe is not None:
        w = sws.byref_bool(False)
        ok = safe(lambda: int(z(sws.dyn(probe).GetErrorCode2(w))), None)
        out["code_route"] = "GetErrorCode2" if ok is not None else "GetErrorCode"
    return out



def param_extremes(surf, uv):
    """Surface points at the corners of a face's parameter box.

    Areas and radii say how big an arc is, not where it starts; parameter
    bounds cannot either, since the zero direction of a torus' sweep
    parameter is internal to the surface. Evaluating at the bounds turns
    both into ordinary points.
    """
    (u0, u1, v0, v1) = uv[0], uv[1], uv[2], uv[3]
    um, vm = 0.5 * (u0 + u1), 0.5 * (v0 + v1)

    def at(u, v):
        # Call shape varies between builds and dispatch modes.
        for fn in (lambda: surf.Evaluate(u, v, 0, 0),
                   lambda: surf.Evaluate(u, v, 1, 1),
                   lambda: surf.Evaluate(u, v)):
            p = safe(lambda f=fn: list(z(f())))
            if p and len(p) >= 3:
                return [round(float(c) * MM, 4) for c in p[0:3]]
        return None

    out = {}
    for key, (u, v) in (("u_lo", (u0, vm)), ("u_hi", (u1, vm)),
                        ("v_lo", (um, v0)), ("v_hi", (um, v1)),
                        ("mid", (um, vm))):
        p = at(u, v)
        if p is not None:
            out[key] = p
    return out or None



def surface_census(body):
    """Per-face analytic type, plus every cylinder and torus.

    A swept thin-wall sleeve is made of TORUS faces, not cylinders, so a
    census reading only IsCylinder misses the graded object entirely.
    Parameter bounds come along because they answer what the parameters
    cannot: how far the face wraps its tube (see is_full_tube).
    """
    kinds, cyls, tori, planes, cones = {}, [], [], [], []
    faces = safe(lambda: z(body.GetFaces)) or []
    for f in faces:
        area = safe(lambda: float(z(f.GetArea)), 0.0) or 0.0
        surf = safe(lambda: z(f.GetSurface))
        if surf is None:
            kinds["unreadable"] = kinds.get("unreadable", 0) + 1
            continue
        kind = "other"
        for name, flag in (("plane", "IsPlane"), ("cylinder", "IsCylinder"),
                           ("cone", "IsCone"), ("sphere", "IsSphere"),
                           ("torus", "IsTorus"), ("bsurf", "IsBSurface")):
            if safe(lambda fl=flag: bool(z(getattr(surf, fl)))):
                kind = name
                break
        kinds[kind] = kinds.get(kind, 0) + 1
        uv = safe(lambda: list(z(f.GetUVBounds)))
        uv = [round(float(v), 6) for v in uv] if uv and len(uv) >= 4 else None
        ends = param_extremes(surf, uv) if (uv and kind in ("torus", "cylinder")) else None
        # WHERE the face is, not just what shape it is. Surface parameters
        # answer "what" and are silent about "where": a cylinder's origin
        # is any point on its axis, and a plane's is any point on the
        # plane, so two features 100 mm apart can report identical
        # parameters. IFace2::GetBox is the only reading that separates
        # them, and without it a criterion can only ever say "somewhere in
        # this part there exists a face of this size" -- which is what the
        # shipped harness here had to settle for, by its own admission.
        box = safe(lambda: [round(float(v) * MM, 4) for v in z(f.GetBox)])
        box = box if (box and len(box) >= 6) else None
        if kind == "plane":
            # Recorded because a face COUNT cannot answer where a face is,
            # and "removable without tools" turns entirely on that: a hex
            # socket and a knurl are both flats parallel to the axis, and
            # what tells the tool drive from the finger grip is that one is
            # recessed inside the head and the other is out on its rim.
            pp = safe(lambda: list(z(surf.PlaneParams)))
            if pp and len(pp) >= 6:
                planes.append({"normal": [round(v, 6) for v in pp[0:3]],
                               "point_mm": [round(v * MM, 4) for v in pp[3:6]],
                               "area_mm2": round(area * MM * MM, 4),
                               "box_mm": box})
        elif kind == "cylinder":
            p = safe(lambda: list(z(surf.CylinderParams)))
            if p and len(p) >= 7:
                cyls.append({"origin_mm": [round(v * MM, 4) for v in p[0:3]],
                             "axis": [round(v, 6) for v in p[3:6]],
                             "radius_mm": round(p[6] * MM, 4),
                             "area_mm2": round(area * MM * MM, 4),
                             "u_range": uv[0:2] if uv else None,
                             "v_range": uv[2:4] if uv else None,
                             "ends_mm": ends,
                             "box_mm": box})
        elif kind == "cone":
            # Cones were COUNTED in face_kinds and then thrown away, and a
            # count cannot answer the one question a weld prep asks: at
            # what angle, and how deep. Task 20's whole ISO 9692-1
            # criterion is a cone's half-angle and the depth that follows
            # from its area, and the live capture reported "no conical
            # face on the piping" while face_kinds said four -- a shape
            # the offline corpus could not reproduce, because the probe
            # records cones and this did not.
            #
            # ConeParams: a point on the axis (3), the axis (3), the
            # radius AT that point, and the half-angle in radians measured
            # from the AXIS.
            p = safe(lambda: list(z(surf.ConeParams)))
            if p and len(p) >= 8:
                cones.append({"origin_mm": [round(v * MM, 4) for v in p[0:3]],
                              "axis": [round(v, 6) for v in p[3:6]],
                              "radius_mm": round(p[6] * MM, 4),
                              "half_angle_deg": round(math.degrees(p[7]), 4),
                              "area_mm2": round(area * MM * MM, 4),
                              "box_mm": box})
        elif kind == "torus":
            p = safe(lambda: list(z(surf.TorusParams)))
            if p and len(p) >= 8:
                tori.append({"centre_mm": [round(v * MM, 4) for v in p[0:3]],
                             "axis": [round(v, 6) for v in p[3:6]],
                             "major_r_mm": round(p[6] * MM, 4),
                             "minor_r_mm": round(p[7] * MM, 4),
                             "area_mm2": round(area * MM * MM, 4),
                             "u_range": uv[0:2] if uv else None,
                             "v_range": uv[2:4] if uv else None,
                             "ends_mm": ends,
                             "box_mm": box})
    return {"face_kinds": kinds, "faces": len(faces),
            "cylinders": cyls, "tori": tori, "planes": planes,
            "cones": cones}



def entity_rgb(obj):
    """The legacy colour array of any entity that has one, or None.

    IFace2, IBody2, IComponent2 and IModelDoc2 all expose
    MaterialPropertyValues in the same shape. A leading -1 means "not set at
    this level" -- a legitimate answer that must not be read as a colour, or
    every unpainted face comes back dark red.
    """
    v = safe(lambda: list(z(obj.MaterialPropertyValues)))
    if not v or len(v) < 3:
        return None
    try:
        if v[0] is None or float(v[0]) < 0:
            return None
        return [round(float(x), 4) for x in v[:3]]
    except (TypeError, ValueError):
        return None



def face_palette(body, top=12):
    """Face colours of one body, weighted by area.

    SolidWorks resolves appearance face > feature > body > document, so a
    STEP import that is visibly painted reports nothing at document level.
    Area-weighted because the colour of a part is the colour most of its
    surface is, not the colour of whichever face came back first.
    """
    slots, faces = {}, safe(lambda: z(body.GetFaces)) or []
    for f in faces:
        area = safe(lambda: float(z(f.GetArea)), 0.0) or 0.0
        key = tuple(entity_rgb(f) or ())
        slot = slots.setdefault(key, {"faces": 0, "area_m2": 0.0})
        slot["faces"] += 1
        slot["area_m2"] += area
    total = sum(s["area_m2"] for s in slots.values()) or 1.0
    palette = sorted(({"rgb": list(k) or None, "faces": s["faces"],
                       "area_mm2": round(s["area_m2"] * MM * MM, 4),
                       "area_share": round(s["area_m2"] / total, 6)}
                      for k, s in slots.items()),
                     key=lambda r: -r["area_share"])
    return {"faces": len(faces), "colours": palette[:top]}



def body_record(body, index):
    props = safe(lambda: list(z(body.GetMassProperties(1.0)))) or []
    box = safe(lambda: list(z(body.GetBodyBox))) or []
    rec = {"id": f"b{index:03d}",
           "debug_name": safe(lambda: str(z(body.Name)), "?"),
           "bbox_mm": [round(v * MM, 4) for v in box],
           "surfaces": surface_census(body)}
    if len(props) >= 5:
        rec["centroid_mm"] = [round(v * MM, 4) for v in props[0:3]]
        rec["volume_mm3"] = round(props[3] * MM ** 3, 4)
        rec["area_mm2"] = round(props[4] * MM * MM, 4)
    return rec




#: swBodyType_e. Solid is the geometry; sheet and wire are construction
#: leftovers when they survive into a delivered model.
SW_BODY_TYPES = ((0, "solid"), (1, "sheet"), (2, "wire"))

#: The census key for a document with no components. See body_census.
PART_KEY = "<document>"


def body_census(doc):
    """{component name: {"solid": n, "sheet": n, "wire": n}}.

    A PART answers for itself under its own title, so the same reading
    works for a single-part task and for an assembly.

    Counts EVERY body, not just the visible ones: a surface body hidden in
    the tree is still shipped, and hiding it is not cleaning it up.
    """
    out = {}

    def count(md, key):
        if md is None:
            return
        row = {}
        for t, label in SW_BODY_TYPES:
            r, _ = probed(lambda t=t: md.GetBodies2(t, False),
                          f"GetBodies2({t})")
            try:
                row[label] = len([b for b in (r or []) if b is not None])
            except TypeError:
                row[label] = 0
        out[key] = row

    comps = safe(lambda: list(z(doc.GetComponents(False)) or []), [])
    if comps:
        for c in comps:
            name = safe(lambda c=c: str(z(c.Name2)).split("/")[-1], "?")
            count(safe(lambda c=c: sws.redispatch(z(c.GetModelDoc2))), name)
    else:
        # A PART IS KEYED BY A SENTINEL, NOT ITS TITLE. The title is the
        # file name, and a candidate's file is `solution` where the seed's
        # is `input` -- keying on that would match nothing between them,
        # and body_delta would report no change at all. That is a silent
        # false pass, which is worse than no check. Component names inside
        # an assembly are stable across the pair, so they keep theirs.
        count(doc, PART_KEY)
    return out


def body_delta(census, seed_census):
    """What the candidate ADDED and REMOVED, per body type, vs the seed.

    A DELTA, never an absolute. Task 27's seed ships six sheet bodies
    across five of its fifteen components, so "no surface bodies" would
    fail the seed and the reference alike; what marks a candidate is
    CHANGING the inventory.

    BOTH DIRECTIONS, because both are real. Measured on task 27: one
    candidate added a sheet body to the bottle part and shipped it, and
    the same candidate also dropped a solid, 16 to 15 -- a removal that
    the component-level "all baseline components remain present and
    unsuppressed" criterion cannot see, because the component is still
    there and still live, just emptier.

    Matched by component name: a candidate that edits a part in place
    keeps its name. A renamed or new component is a different question
    and is deliberately not answered here.

    Returns (added, removed, detail); detail names every component whose
    inventory moved, in either direction.
    """
    seed = seed_census or {}
    added = {label: 0 for _, label in SW_BODY_TYPES}
    removed = {label: 0 for _, label in SW_BODY_TYPES}
    detail = []
    for name, row in sorted((census or {}).items()):
        base = seed.get(name)
        if base is None:
            continue
        moved = {}
        for _, label in SW_BODY_TYPES:
            d = int(row.get(label, 0)) - int(base.get(label, 0))
            if d > 0:
                added[label] += d
            elif d < 0:
                removed[label] += -d
            if d:
                moved[label] = d
        if moved:
            detail.append({"component": name, "delta": moved})
    return added, removed, detail

def bodies_of(comp):
    """EVERY solid body of a component, in PART coordinates.

    The document route is first: the component-level GetBodies2 shapes fail
    outright on some assemblies. `comp.GetBody` is last and is a trap -- it
    returns ONE body of a multi-body part, and not the same one twice.
    """
    md = safe(lambda: sws.redispatch(z(comp.GetModelDoc2)))
    trials = []
    if md is not None:
        trials += [("part.GetBodies2(0,True)", lambda: md.GetBodies2(0, True)),
                   ("part.GetBodies2(0,False)", lambda: md.GetBodies2(0, False))]
    trials += [("comp.GetBodies2(0,False)", lambda: comp.GetBodies2(0, False)),
               ("comp.GetBodies2(0,True)", lambda: comp.GetBodies2(0, True)),
               ("comp.GetBody[SINGLE BODY ONLY]", lambda: [z(comp.GetBody)])]
    tried = []
    for how, fn in trials:
        r, err = probed(fn, how)
        try:
            r = [b for b in (r or []) if b is not None]
        except TypeError:
            r = []
        tried.append({"how": how, "error": err, "count": len(r)})
        if r:
            return r, how, tried
    return [], None, tried



def component_record(comp, cid, depth):
    rec = {
        "id": cid,
        "depth": depth,
        "debug_name": safe(lambda: str(z(comp.Name2)), "?"),
        "debug_file": Path(safe(lambda: str(z(comp.GetPathName)), "") or "").name,
        "suppressed": safe(lambda: bool(z(comp.IsSuppressed)), None),
        "state": safe(lambda: int(z(comp.GetSuppression)), None),
    }
    rec["resolved"] = bool(Path(safe(lambda: str(z(comp.GetPathName)), "")
                                or "?").is_file())
    tr = safe(lambda: z(comp.Transform2))
    arr = safe(lambda: list(z(tr.ArrayData))) if tr is not None else None
    if arr and len(arr) >= 12:
        rec["rotation"] = [round(v, 8) for v in arr[0:9]]
        rec["translation_mm"] = [round(v * MM, 4) for v in arr[9:12]]
    else:
        rec["rotation"] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        rec["translation_mm"] = [0.0, 0.0, 0.0]
    mp = mass_props(safe(lambda: z(comp.GetModelDoc2)))
    if mp:
        rec.update(mp)
    raw, how, tried = bodies_of(comp)
    rec["bodies"] = [body_record(b, i) for i, b in enumerate(raw)]
    rec["bodies_via"] = how
    whole = rec.get("volume_mm3")
    part_sum = sum(b.get("volume_mm3") or 0.0 for b in rec["bodies"])
    rec["bodies_complete"] = True
    # A short body list looks exactly like a small part; only this says so.
    if raw and whole and abs(part_sum - whole) > max(1e-3, 1e-4 * abs(whole)):
        rec["bodies_complete"] = False
        rec["bodies_tried"] = tried
    if not raw:
        rec["bodies_tried"] = tried
    return rec, raw



def in_assembly_space(comp, body):
    """A copy of `body` moved into assembly coordinates, or None."""
    cp = safe(lambda: z(body.Copy))
    tr = safe(lambda: z(comp.Transform2))
    if cp is None or tr is None:
        return None
    ok = safe(lambda: bool(z(cp.ApplyTransform(tr))), False)
    return cp if ok else None



def aabb_in_assembly(bbox_mm, rot, trans):
    """Axis-aligned box of a part-space box after the component transform.

    All eight corners go through, so the result can only be larger than the
    true box -- the safe direction for a filter that must not discard a real
    overlap.
    """
    if not bbox_mm or len(bbox_mm) < 6:
        return None
    lo, hi = np.asarray(bbox_mm[0:3], float), np.asarray(bbox_mm[3:6], float)
    corners = np.array([[lo[0] if i & 1 else hi[0],
                         lo[1] if i & 2 else hi[1],
                         lo[2] if i & 4 else hi[2]] for i in range(8)])
    r = np.asarray(rot, float).reshape(3, 3).T
    pts = corners @ r.T + np.asarray(trans, float)
    return pts.min(axis=0).tolist() + pts.max(axis=0).tolist()



#: Slack on the box test; a false discard is an interference never seen.
AABB_PAD_MM = 1.0


def aabb_overlap(a, b, pad=AABB_PAD_MM):
    return (a[0] - pad <= b[3] and b[0] - pad <= a[3] and
            a[1] - pad <= b[4] and b[1] - pad <= a[4] and
            a[2] - pad <= b[5] and b[2] - pad <= a[5])

# --------------------------------------------------------------------------
# interference
# --------------------------------------------------------------------------
#
# MOVED HERE FROM A HARNESS, NOT REWRITTEN. Task 12 has graded with this
# code since it was written, including the parts that look paranoid -- the
# re-read of body pointers after a rebuild, the count of pairs actually
# compared, the refusal to call an untested assembly a clean one. Every one
# of those is a bug that was hit. A second task needs the same work, and a
# second copy of code like this drifts: this repository already has the
# scars from three copies of the batch runner, one of which read the FIRST
# JSON object on stdout where another read the last.
#
# So the bodies below are the old text, moved, with the names made public
# and the `SA.` prefixes dropped now that they are in `SA` themselves.

def component_boxes(rec):
    """Assembly-space box per body and for the whole component."""
    boxes = []
    for b in rec.get("bodies") or []:
        box = aabb_in_assembly(b.get("bbox_mm"), rec["rotation"],
                                  rec["translation_mm"])
        b["aabb_mm"] = box
        if box:
            boxes.append(box)
    if not boxes:
        return None
    lo = [min(b[i] for b in boxes) for i in range(3)]
    hi = [max(b[i + 3] for b in boxes) for i in range(3)]
    return [round(v, 4) for v in lo + hi]


def place_body(comp, body):
    """One body copied into assembly space, or None and the step that failed."""
    cp, err = probed(lambda: z(body.Copy), "IBody2::Copy")
    if cp is None:
        return None, f"Copy: {err or 'returned nothing'}"
    tr, err = probed(lambda: z(comp.Transform2), "IComponent2::Transform2")
    if tr is None:
        return None, f"Transform2: {err or 'returned nothing'}"
    ok, err = probed(lambda: bool(z(cp.ApplyTransform(tr))),
                        "IBody2::ApplyTransform")
    if not ok:
        return None, f"ApplyTransform: {err or 'returned false'}"
    return cp, None


def interference_census(records, comps):
    """Solid overlap between every pair of components.

    All pairs, not directed: there are four components and any of them can
    be the one a candidate drives into another. ToolsCheckInterference2
    refuses every call shape on these assemblies -- "parameter is
    required" -- so the boolean is done here.

    The number is compared against the seed's own, never against zero: a
    flange slipped over its nozzle and a vessel resting in its stand may
    overlap by design, and that is the reference's business rather than
    the candidate's.
    """
    if SM is None:
        return {"available": False, "reason": "no SolidWorks session"}
    live = [(i, r) for i, r in enumerate(records)
            if not r.get("suppressed") and r.get("aabb_mm")]
    cache, why = {}, {}

    def note(reason):
        why[reason] = why.get(reason, 0) + 1

    def placed(i):
        """Component i's bodies in assembly space, fetched at this moment.

        Re-read rather than reused: the capture forces a rebuild before
        this runs, and a rebuild is entitled to invalidate every body
        pointer taken before it. A stale pointer fails quietly -- Copy
        returns nothing -- and an untested pair then looks exactly like a
        clean one.
        """
        if i not in cache:
            comp = comps[i][0]
            raw, _how, _tried = bodies_of(comp)
            if not raw:
                note("no solid body could be read back from the component")
            out = []
            for b in raw or []:
                cp, err = place_body(comp, b)
                if cp is None:
                    note(err)
                else:
                    out.append(cp)
            cache[i] = out
        return cache[i]

    total, tested, failed, skipped, pairs = 0.0, 0, 0, 0, []
    for n, (i, a) in enumerate(live):
        for j, b in live[n + 1:]:
            if not aabb_overlap(a["aabb_mm"], b["aabb_mm"]):
                continue
            ba, bb = placed(i), placed(j)
            if not ba or not bb:
                skipped += 1
                continue
            got = SM.interference_between(ba, bb)
            tested += got.get("tested_pairs", 0)
            failed += got.get("failed_pairs", 0)
            vol = float(got.get("total_volume_mm3") or 0.0)
            if vol > 0.0:
                total += vol
                pairs.append({"a": a["id"], "b": b["id"],
                              "debug_a": a.get("debug_file"),
                              "debug_b": b.get("debug_file"),
                              "volume_mm3": round(vol, 6)})
    out = {"tested_pairs": tested, "failed_pairs": failed,
           "skipped_pairs": skipped, "why": why,
           "total_volume_mm3": round(total, 6),
           "pairs": sorted(pairs, key=lambda p: -p["volume_mm3"])[:20]}
    # Nothing compared is not the same as nothing overlapping, and the
    # difference is a whole criterion: reported as measured, an untested
    # assembly scores full marks for being clean.
    out["available"] = tested > 0
    if not tested:
        out["reason"] = ("no pair of solids could be compared"
                         if skipped else "no two components share a box")
    return out


def interference_with(records, comps, target, aabb_key="aabb_mm"):
    """Solid overlap between ONE component and every other, in mm3.

    DIRECTED, AND THAT IS THE POINT. An all-pairs number on a machine
    assembly says more about the vendor's model than about the edit being
    graded: a flange slipped over its nozzle, a stud through its own hole
    and a pump resting in its stand all overlap by design, and they were
    there before the candidate arrived. Asking instead "does the thing the
    candidate ADDED run through anything" needs no baseline to subtract,
    because the thing the candidate added is not in the seed at all.

    `target` is an index into `records`; `comps[i]` is the (component,
    depth) pair the record came from, so the two lists must line up. The
    body pointers are re-read here rather than carried in, for the reason
    `interference_census` gives: a rebuild between the walk and this call
    is entitled to invalidate every pointer taken before it, and a stale
    pointer fails quietly -- an untested pair then looks exactly like a
    clean one.
    """
    if SM is None:
        return {"available": False, "reason": "no SolidWorks session"}
    if not (0 <= target < len(records)):
        return {"available": False, "reason": "no component to test"}
    why = {}

    def note(reason):
        why[reason] = why.get(reason, 0) + 1

    def placed(i):
        comp = comps[i][0]
        raw, _how, _tried = bodies_of(comp)
        if not raw:
            note("no solid body could be read back from the component")
        out = []
        for b in raw or []:
            cp, err = place_body(comp, b)
            if cp is None:
                note(err)
            else:
                out.append(cp)
        return out

    a = records[target]
    mine = placed(target)
    if not mine:
        return {"available": False, "why": why,
                "reason": "the component under test has no readable solid"}

    total, tested, failed, pairs = 0.0, 0, 0, []
    for j, b in enumerate(records):
        if j == target or b.get("suppressed") or not b.get(aabb_key):
            continue
        if a.get(aabb_key) and not aabb_overlap(a[aabb_key], b[aabb_key]):
            continue
        theirs = placed(j)
        if not theirs:
            continue
        got = SM.interference_between(mine, theirs)
        tested += got.get("tested_pairs", 0)
        failed += got.get("failed_pairs", 0)
        vol = float(got.get("total_volume_mm3") or 0.0)
        if vol > 0.0:
            total += vol
            pairs.append({"a": a["id"], "b": b["id"],
                          "debug_a": a.get("debug_file"),
                          "debug_b": b.get("debug_file"),
                          "volume_mm3": round(vol, 6)})
    out = {"tested_pairs": tested, "failed_pairs": failed, "why": why,
           "target": a["id"], "debug_target": a.get("debug_file"),
           "total_volume_mm3": round(total, 6),
           "pairs": sorted(pairs, key=lambda p: -p["volume_mm3"])[:20]}
    # Nothing compared is not the same as nothing overlapping, and the
    # difference is a whole criterion: reported as measured, an untested
    # assembly scores full marks for being clean.
    out["available"] = tested > 0
    if not tested:
        out["reason"] = "no pair of solids could be compared"
    return out
