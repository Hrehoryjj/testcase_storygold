from __future__ import annotations

try:
    import pythoncom
    from win32com.client import VARIANT
except ImportError:
    pythoncom = None
    VARIANT = None

SW_BODY_INTERSECT = 15901
SW_THIS_CONFIGURATION = 1

LABEL_FACE_MAX_AREA = 15e-6
SAMPLES_PER_EDGE = 9
BBOX_PAD = 1e-4
MIN_INTERFERENCE_VOLUME = 1e-12
_SKEW_EPS = 1e-18


# z lives in solidworks_session -- one definition, not two that can drift.
# Re-exported here because `from common.solidworks_measure import z` is
# written in harnesses and tools, and a module's import surface is part of
# its contract: consolidating a duplicate must not break the callers that
# never knew there was one.
from common.solidworks_session import z            # noqa: E402,F401


def _edge_param_range(edge):
    try:
        cpd = z(edge.GetCurveParams3)
        return float(cpd.UMinValue), float(cpd.UMaxValue)
    except Exception:
        pass
    p = z(edge.GetCurveParams2)
    return float(p[6]), float(p[7])


def _sample_face_outline(face, samples_per_edge: int = SAMPLES_PER_EDGE):
    pts = []
    edges = z(face.GetEdges)
    if not edges:
        return pts
    n = samples_per_edge - 1
    for edge in edges:
        try:
            curve = z(edge.GetCurve)
            t0, t1 = _edge_param_range(edge)
            for k in range(n + 1):
                t = t0 + (t1 - t0) * k / float(n)
                ev = curve.Evaluate2(t, 0)
                pts.append((float(ev[0]), float(ev[1]), float(ev[2])))
        except Exception:
            continue
    return pts


def _standardised_skew(values, mean: float) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    m2 = sum((v - mean) ** 2 for v in values) / n
    m3 = sum((v - mean) ** 3 for v in values) / n
    return (m3 / (m2 ** 1.5)) if m2 > _SKEW_EPS else 0.0


def face_metrics(face) -> dict:
    try:
        area = float(z(face.GetArea))
    except Exception:
        area = 0.0
    try:
        loops = int(z(face.GetLoopCount))
    except Exception:
        loops = 0
    try:
        box = z(face.GetBox)
    except Exception:
        box = None

    pts = _sample_face_outline(face)
    n = len(pts)
    if n:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        cx, cy, cz = sum(xs) / n, sum(ys) / n, sum(zs) / n
        skew_x = _standardised_skew(xs, cx)
        skew_z = _standardised_skew(zs, cz)
    else:
        cx = cy = cz = skew_x = skew_z = 0.0

    return {
        "area_m2": area,
        "loops": loops,
        "centroid_m": [cx, cy, cz],
        "skew_x": skew_x,
        "skew_z": skew_z,
        "outline_samples": n,
        "_box_xmin": (float(box[0]) if box else 0.0),
        "_box_zmin": (float(box[2]) if box else 0.0),
    }


def _appearance_key(area_m2: float, box_xmin_m: float, box_zmin_m: float) -> str:
    return f"{area_m2 * 1e6:.4f}|{box_xmin_m * 1000:.2f},{box_zmin_m * 1000:.2f}"


def face_area_index(bodies):
    """Every face of these bodies, keyed the way appearances are joined.

    The index is what makes an appearance reading checkable. `GetEntities`
    hands back faces from every configuration the appearance has ever been
    applied in -- for one instance of the pliers in task 28 it returned 110
    faces for a body that has 114, only 58 of them this body's -- so the
    entity list has to be intersected with the body actually open. Without
    this the map is a union across configurations and reports geometry that
    is not there.
    """
    idx = {}
    for body in (bodies or []):
        try:
            faces = z(body.GetFaces) or []
        except Exception:
            continue
        for face in faces:
            try:
                area = float(z(face.GetArea))
                box = z(face.GetBox)
                idx[_appearance_key(area, float(box[0]), float(box[2]))] = area
            except Exception:
                continue
    return idx


def _document_bodies(model_doc):
    for args in ((0, False), (0, True)):
        try:
            bodies = z(model_doc.GetBodies2(*args))
        except Exception:
            continue
        if bodies:
            return list(bodies)
    return []


def _render_materials(model_doc):
    """Every appearance on the ACTIVE configuration of this document.

    The active configuration is the caller's responsibility: both this call
    and `GetBodies2` answer for whichever one is in front, and a part
    instanced several times under different configurations will otherwise
    report the colours of whichever instance was read last.
    """
    try:
        return list(model_doc.Extension.GetRenderMaterials2(
            SW_THIS_CONFIGURATION, None) or [])
    except Exception:
        return []


def _material_facts(rm):
    name, rgb = "", None
    try:
        fn = str(rm.FileName)
        name = fn.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    except Exception:
        pass
    try:
        colorref = int(rm.PrimaryColor)
        rgb = [colorref & 0xFF, (colorref >> 8) & 0xFF, (colorref >> 16) & 0xFF]
    except Exception:
        pass
    try:
        z(rm.GetEntitiesCount())
    except Exception:
        pass
    try:
        entities = list(z(rm.GetEntities) or [])
    except Exception:
        entities = []
    return name, rgb, entities


def build_appearance_map(model_doc, bodies=None):
    """face key -> {name, rgb}, for the faces of these bodies only.

    `bodies` defaults to the document's own, which is right whenever the
    caller has already put the configuration it wants in front.
    """
    index = face_area_index(bodies if bodies is not None
                            else _document_bodies(model_doc))
    out = {}
    for rm in _render_materials(model_doc):
        name, rgb, entities = _material_facts(rm)
        for ent in entities:
            try:
                area = float(z(ent.GetArea))
                box = z(ent.GetBox)
                key = _appearance_key(area, float(box[0]), float(box[2]))
            except Exception:
                continue
            if key in index:
                out.setdefault(key, {"name": name, "rgb": rgb})
    return out


def appearance_coverage(model_doc, bodies=None):
    """How much of these bodies each colour covers.

    SolidWorks resolves an appearance face > body > document, and the
    document level is not a small case: in task 28's reference, three of
    the four plier halves carry their dark colour that way and only the
    yellow is on faces. An appearance applied to the whole object arrives
    as an entity with no area, so summing entity areas alone reports those
    halves as unpainted. Here the face-level layers take their area first
    and whatever is left over goes to the object-level layer, which is what
    "the rest of the part is this colour" actually means.

    Two object-level layers cannot both own the remainder. That does not
    happen in this corpus; if it ever does, `base_ambiguous` says so rather
    than the arithmetic quietly picking one.
    """
    if bodies is None:
        bodies = _document_bodies(model_doc)
    index = face_area_index(bodies)
    total = sum(index.values())
    layers = []
    for rm in _render_materials(model_doc):
        name, rgb, entities = _material_facts(rm)
        area, faces, whole = 0.0, 0, 0
        for ent in entities:
            key = None
            try:
                a = float(z(ent.GetArea))
                box = z(ent.GetBox)
                key = _appearance_key(a, float(box[0]), float(box[2]))
            except Exception:
                key = None
            if key is not None and key in index:
                area += index[key]
                faces += 1
            elif key is None:
                whole += 1
        layers.append({"debug_appearance": name, "rgb": rgb,
                       "faces": faces, "area_m2": area,
                       "whole_object": whole > 0})
    taken = sum(L["area_m2"] for L in layers)
    base = [L for L in layers if L["whole_object"]]
    remainder = max(0.0, total - taken)
    if len(base) == 1:
        base[0]["area_m2"] += remainder
        remainder = 0.0
    for L in layers:
        L["area_share"] = (L["area_m2"] / total) if total else 0.0
    return {"total_area_m2": total, "faces": len(index),
            "layers": sorted(layers, key=lambda L: -L["area_m2"]),
            "unpainted_area_m2": remainder,
            "unpainted_share": (remainder / total) if total else 0.0,
            "base_ambiguous": len(base) > 1}


def _select_label_face(body, max_area: float = LABEL_FACE_MAX_AREA):
    best, best_loops, best_area = None, -1, -1.0
    faces = z(body.GetFaces)
    if not faces:
        return None
    for face in faces:
        try:
            area = float(z(face.GetArea))
        except Exception:
            continue
        if area >= max_area:
            continue
        try:
            loops = int(z(face.GetLoopCount))
        except Exception:
            loops = 0
        if loops > best_loops or (loops == best_loops and area > best_area):
            best, best_loops, best_area = face, loops, area
    return best


def measure_logo_faces(body, appearance_map, appearance_name: str = "color") -> dict:
    faces_out, total_area = [], 0.0
    faces = z(body.GetFaces) or []
    for face in faces:
        try:
            area = float(z(face.GetArea))
            box = z(face.GetBox)
            key = _appearance_key(area, float(box[0]), float(box[2]))
        except Exception:
            continue
        found = appearance_map.get(key)
        if not found or found.get("name") != appearance_name:
            continue
        m = face_metrics(face)
        total_area += m["area_m2"]
        faces_out.append({
            "loops": m["loops"],
            "area_m2": m["area_m2"],
            "outline_centroid_m": m["centroid_m"],
            "skew_x": m["skew_x"],
            "skew_z": m["skew_z"],
            "color_rgb": found.get("rgb"),
        })
    return {"face_count": len(faces_out),
            "total_area_m2": total_area,
            "faces": faces_out}


def _wrap_com(obj):
    if obj is None:
        return None
    try:
        import win32com.client
        if isinstance(obj, win32com.client.CDispatch):
            return obj
        return win32com.client.Dispatch(obj)
    except Exception:
        return obj


def _operations2(body_a, body_b, op_type: int = SW_BODY_INTERSECT):
    res = None
    try:
        err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        res, code = body_a.Operations2(op_type, body_b, err), int(err.value)
    except (TypeError, ValueError, AttributeError, pythoncom.com_error):
        out = body_a.Operations2(op_type, body_b, 0)
        if isinstance(out, tuple) and len(out) == 2 and not hasattr(out[1], "__len__"):
            res, code = out[0], int(out[1])
        else:
            res, code = out, 0

    if res:
        res = [_wrap_com(r) for r in res]
    return res, code


def _boxes_overlap(a, b, pad: float = BBOX_PAD) -> bool:
    return (a[0] - pad <= b[3] and b[0] - pad <= a[3] and
            a[1] - pad <= b[4] and b[1] - pad <= a[4] and
            a[2] - pad <= b[5] and b[2] - pad <= a[5])


def measure_interference(bodies, boxes=None, pad: float = BBOX_PAD,
                         progress=None) -> dict:
    n = len(bodies)
    if boxes is None:
        boxes = []
        for b in bodies:
            try:
                boxes.append([float(v) for v in z(b.GetBodyBox)])
            except Exception:
                boxes.append([0.0] * 6)

    candidates = [(i, j) for i in range(n) for j in range(i + 1, n)
                  if _boxes_overlap(boxes[i], boxes[j], pad)]

    pairs, total_volume = [], 0.0
    for done, (i, j) in enumerate(candidates, start=1):
        volume, error_code, result_bodies = 0.0, 0, 0
        try:
            copy_a = z(bodies[i].Copy)
            copy_b = z(bodies[j].Copy)
            res, error_code = _operations2(copy_a, copy_b, SW_BODY_INTERSECT)
            if res:
                result_bodies = len(res)
                for rb in res:
                    try:
                        volume += float(rb.GetMassProperties(0.0)[3])
                    except (IndexError, TypeError, ValueError):
                        pass
        except Exception as exc:
            error_code = -1
            volume = 0.0
            print(f"[INTF-ERR] {i},{j} {exc}")

        if volume > MIN_INTERFERENCE_VOLUME or error_code != 0:
            total_volume += volume
            pairs.append({
                "a": f"b{i:02d}",
                "b": f"b{j:02d}",
                "volume_m3": volume,
                "result_bodies": result_bodies,
                "error_code": error_code,
            })

        if progress:
            progress(done, len(candidates))

    return {"tested_pairs": len(candidates),
            "total_volume_m3": total_volume,
            "pairs": pairs}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def interference_between(target, others, mm=1000.0):
    """Solid overlap between one set of bodies and another, in mm3.

    Directed rather than all-pairs: `target` against `others`, nothing
    inside either set. An assembly's fasteners routinely interfere with
    their own holes by design, so an all-pairs number says more about the
    vendor's model than about the edit being graded.

    Bodies must already be in a common coordinate system. Touching is not
    interference: coincident surfaces intersect to essentially nothing.

    Used where ToolsCheckInterference2 refuses to be called at all, which
    is every call shape on some assemblies.
    """
    if not target:
        return {"available": False, "reason": "nothing to test"}
    boxes_t = [_safe(lambda b=b: [float(v) for v in z(b.GetBodyBox)]) for b in target]
    boxes_o = [_safe(lambda b=b: [float(v) for v in z(b.GetBodyBox)]) for b in others]
    total, pairs, tested, failed = 0.0, [], 0, 0
    for i, tb in enumerate(target):
        if boxes_t[i] is None:
            continue
        for j, ob in enumerate(others):
            if boxes_o[j] is None or not _boxes_overlap(boxes_t[i], boxes_o[j]):
                continue
            tested += 1
            volume, code = 0.0, 0
            try:
                res, code = _operations2(z(tb.Copy), z(ob.Copy))
                for rb in (res or []):
                    v = _safe(lambda r=rb: float(z(r.GetMassProperties(0.0))[3]), 0.0)
                    volume += v or 0.0
            except Exception as exc:
                failed += 1
                code = -1
                pairs.append({"other_index": j, "error": str(exc)[:120]})
                continue
            if volume > 1e-12 or code:
                total += volume
                pairs.append({"other_index": j,
                              "volume_mm3": round(volume * mm ** 3, 6),
                              "error_code": code})
    return {"available": True, "tested_pairs": tested, "failed_pairs": failed,
            "total_volume_mm3": round(total * mm ** 3, 6), "pairs": pairs[:40]}
