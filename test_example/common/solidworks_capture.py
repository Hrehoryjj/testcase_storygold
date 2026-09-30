
from __future__ import annotations

from common import solidworks_measure as M
from common.solidworks_measure import z

SW_SOLID_BODY = 0


# ---------------------------------------------------------------------------
# session entry
# ---------------------------------------------------------------------------

def attach_app():
    """The SolidWorks application object, late-bound.

    Always go through this rather than win32com.client.GetActiveObject:
    that silently upgrades to early-bound dispatch once pywin32 has cached
    makepy support for the SolidWorks typelib (which it does automatically
    and persistently on the first successful attach on a machine), after
    which any manual VARIANT(VT_BYREF, ...) call -- OpenDoc6's error and
    warning byrefs among them -- fails with "TypeError: int() argument must
    be ... not 'VARIANT'". Confirmed on the grading box 2026-08-15.
    """
    from common import solidworks_session as sws
    return sws.attach()


def open_or_active(app, path=None, doc_type=None):
    """The document at `path`, or the active one when no path is given.

    open_document sweeps the whole session closed before any fresh open.
    Confirmed live on 30_shampoo_bottle that a multi-component assembly can
    otherwise silently reuse a same-named component document left open by a
    previous candidate. Sweeping also keeps the session from accumulating
    open documents run over run.

    doc_type defaults to inferring part/assembly from the file extension.
    """
    if path:
        import os
        from common import solidworks_session as sws
        doc, _opened_here = sws.open_document(app, os.path.abspath(path),
                                              doc_type=doc_type)
        if doc is not None:
            return doc
    doc = app.ActiveDoc
    if doc is None:
        raise RuntimeError("no active SolidWorks document and no path given")
    return doc


# ---------------------------------------------------------------------------
# model-quality census
# ---------------------------------------------------------------------------

def modelling_census(doc):
    """Model-quality facts that geometry alone does not expose.

    Sketch constraint status, suppressed features, external references.
    Every probe is optional: a SolidWorks build that does not expose one of
    these must degrade the census, never abort the grade -- so each block
    records what it managed to read and reports the rest as unavailable.

    Sketch statuses are deliberately kept as RAW API values, for the caller
    to compare against a seed distribution rather than interpret.  That keeps
    the check correct without depending on the numeric meaning of
    swSketchConstrainedStatus_e, which varies between API versions.
    """
    out = {"available": {}, "notes": []}

    # -- sketches ------------------------------------------------------
    status_counts, n_sketches = {}, 0
    try:
        feat = z(doc.FirstFeature)
        while feat is not None:
            try:
                tname = str(z(feat.GetTypeName2))
            except Exception:
                tname = ""
            if tname in ("ProfileFeature", "3DProfileFeature"):
                n_sketches += 1
                try:
                    sk = z(feat.GetSpecificFeature2)
                    st = int(z(sk.GetConstrainedStatus))
                    status_counts[str(st)] = status_counts.get(str(st), 0) + 1
                except Exception:
                    status_counts["unreadable"] = \
                        status_counts.get("unreadable", 0) + 1
            feat = z(feat.GetNextFeature)
        out["sketches"] = {"count": n_sketches, "status_counts": status_counts}
        out["available"]["sketches"] = True
    except Exception as exc:
        out["available"]["sketches"] = False
        out["notes"].append(f"sketch census unavailable: {exc}")

    # -- suppressed features -------------------------------------------
    try:
        suppressed, n_feat = [], 0
        feat = z(doc.FirstFeature)
        while feat is not None:
            n_feat += 1
            try:
                if bool(z(feat.IsSuppressed)):
                    suppressed.append(str(feat.Name))
            except Exception:
                pass
            feat = z(feat.GetNextFeature)
        out["suppressed"] = {"count": len(suppressed),
                             "names": sorted(suppressed)[:25],
                             "features_scanned": n_feat}
        out["available"]["suppressed"] = True
    except Exception as exc:
        out["available"]["suppressed"] = False
        out["notes"].append(f"suppression census unavailable: {exc}")

    # -- external references -------------------------------------------
    # Several API shapes exist across SolidWorks versions and none is
    # guaranteed; try each and accept only a genuinely iterable result.
    refs, how = None, None
    ext = (lambda: doc.Extension)
    own = (lambda: doc)
    for owner, getter in ((ext, "ListExternalFileReferences"),
                          (ext, "ListExternalFileReferences2"),
                          (own, "GetDependencies2"),
                          (own, "GetDependencies")):
        try:
            got = z(getattr(owner(), getter))
            if got is None or isinstance(got, (str, bytes)):
                continue
            probe = list(got)          # raises if not really iterable
            refs, how = probe, getter
            break
        except Exception:
            continue
    if refs is None:
        out["available"]["external_refs"] = False
        out["notes"].append("external reference census unavailable: no "
                            "supported API on this SolidWorks build")
    else:
        names = [str(r) for r in refs]
        out["external_refs"] = {"count": len(names), "names": names[:25],
                                "via": how}
        out["available"]["external_refs"] = True

    return out


def _colour_distance(a, b):
    if not a or not b:
        return None
    return max(abs(u - v) for u, v in zip(a, b))


def _shortlist_faces(bodies, appearance_map, max_area):
    out = []
    for idx, body in enumerate(bodies):
        faces = z(body.GetFaces)
        if not faces:
            continue
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
            try:
                box = z(face.GetBox)
                key = M._appearance_key(area, float(box[0]), float(box[2]))
            except Exception:
                key = None
            app = appearance_map.get(key, {}) if key else {}
            out.append((idx, face, app.get("rgb"), area, loops,
                        app.get("name", "")))
    return out


def discover_glyphs(bodies, appearance_map, seed_labels,
                    colour_tol=24, area_tol_frac=0.35,
                    max_area=M.LABEL_FACE_MAX_AREA):
    shortlist = _shortlist_faces(bodies, appearance_map, max_area)
    labels, taken = [], set()

    for seed in seed_labels:
        want_rgb = seed.get("color_rgb")
        best, best_cost = None, None
        for (idx, face, rgb, area, loops, appname) in shortlist:
            if id(face) in taken:
                continue
            cd = _colour_distance(want_rgb, rgb)
            if cd is None or cd > colour_tol:
                continue
            da = abs(area - seed["area_m2"]) / max(seed["area_m2"], 1e-30)
            cost = (cd, da)
            if best_cost is None or cost < best_cost:
                best, best_cost = (idx, face, area, loops), cost

        confidence = "colour"
        if best is None:
            for (idx, face, rgb, area, loops, appname) in shortlist:
                if id(face) in taken:
                    continue
                if loops != seed["loops"]:
                    continue
                da = abs(area - seed["area_m2"]) / max(seed["area_m2"], 1e-30)
                if da > area_tol_frac:
                    continue
                if best_cost is None or (da,) < best_cost:
                    best, best_cost = (idx, face, area, loops), (da,)
            confidence = "loops+area (no colour - LOW CONFIDENCE)"

        if best is None:
            labels.append({"glyph": seed["glyph"], "found": False,
                           "error": "no face matched colour, loops or area"})
            continue

        idx, face, area, loops = best
        taken.add(id(face))
        m = M.face_metrics(face)
        entry = {
            "glyph": seed["glyph"], "found": True,
            "host_body": f"c{idx:02d}", "loops": m["loops"],
            "area_m2": m["area_m2"],
            "outline_centroid_m": m["centroid_m"],
            "skew_x": m["skew_x"], "skew_z": m["skew_z"],
            "outline_samples": m["outline_samples"],
            "identified_by": confidence,
        }
        app = appearance_map.get(
            M._appearance_key(m["area_m2"], m["_box_xmin"], m["_box_zmin"]), {})
        entry["appearance"] = app.get("name", "")
        entry["color_rgb"] = app.get("rgb")
        if m["loops"] != seed["loops"]:
            entry["loop_count_changed"] = [seed["loops"], m["loops"]]
        labels.append(entry)
    return labels


def feature_census(doc, rebuild="edit"):
    try:
        import pythoncom
        from win32com.client import VARIANT
    except ImportError:
        pythoncom = VARIANT = None

    if rebuild:
        try:
            if rebuild == "force":
                doc.ForceRebuild3(False)
            else:
                z(doc.EditRebuild3)
        except Exception as exc:
            return None, {"note": f"rebuild call failed: {exc}"}

    census, nfeat = {}, 0
    feat = z(doc.FirstFeature)
    while feat is not None:
        nfeat += 1
        try:
            if VARIANT is None:
                raise RuntimeError("no pywin32")
            warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
            code = feat.GetErrorCode2(warn)
            is_warning = bool(warn.value)
        except Exception:
            code, is_warning = z(feat.GetErrorCode), False
        if code:
            census[str(feat.Name)] = [int(code), is_warning]
        feat = z(feat.GetNextFeature)
    return census, {"features": nfeat}


def health_gate(doc, baseline=None):
    census, meta = feature_census(doc)
    if census is None:
        return {"ok": False, "errors": -1, "broken_features": [], **meta}

    nfeat = meta.get("features", 0)
    hard = {n: c for n, (c, w) in census.items() if not w}
    warn = {n: c for n, (c, w) in census.items() if w}

    seed = (baseline or {}).get("rebuild", {}).get("feature_errors")
    if seed is None:
        return {"ok": not hard, "errors": len(hard), "warnings": len(warn),
                "features": nfeat,
                "broken_features": [{"name": n, "code": c}
                                    for n, c in sorted(hard.items())[:25]],
                "graded": "absolute",
                "note": "no seed rebuild census in the baseline - graded "
                        "against zero, which this seed cannot pass. Run "
                        "harness.py --capture-seed-rebuild with the seed "
                        "open to record it."}

    seed_hard = {n for n, v in seed.items() if not v[1]}
    seed_any = set(seed)

    newly_broken = [{"name": n, "code": c} for n, c in sorted(hard.items())
                    if n not in seed_hard]
    new_warnings = [{"name": n, "code": c} for n, c in sorted(warn.items())
                    if n not in seed_any]
    repaired = sorted(n for n in seed_hard if n not in hard)

    return {
        "ok": not newly_broken,
        "errors": len(newly_broken),
        "features": nfeat,
        "graded": "delta_vs_seed",
        "broken_features": newly_broken[:25],
        "new_warnings": new_warnings[:25],
        "pre_existing_errors": len(seed_hard),
        "pre_existing_still_present": len(seed_hard & set(hard)),
        "repaired": repaired[:25],
        "note": (f"{len(newly_broken)} newly broken vs the seed's "
                 f"{len(seed_hard)} pre-existing; "
                 f"{len(new_warnings)} new warnings (reported, not failed)"),
    }


def capture_bodies(doc):
    raw = doc.GetBodies2(SW_SOLID_BODY, False)
    raw = list(raw) if raw else []
    bodies, boxes = [], []
    gmin, gmax = [1e30] * 3, [-1e30] * 3
    for idx, b in enumerate(raw):
        mp = b.GetMassProperties(0.0)
        box = [float(v) for v in z(b.GetBodyBox)]
        boxes.append(box)
        for k in range(3):
            gmin[k] = min(gmin[k], box[k])
            gmax[k] = max(gmax[k], box[k + 3])
        bodies.append({
            "id": f"c{idx:02d}", "name": str(b.Name),
            "centroid_m": [float(mp[0]), float(mp[1]), float(mp[2])],
            "volume_m3": float(mp[3]), "area_m2": float(mp[4]),
            "inertia_com": {"Ixx": float(mp[6]), "Iyy": float(mp[7]),
                            "Izz": float(mp[8]), "Ixy": float(mp[9]),
                            "Izx": float(mp[10]), "Iyz": float(mp[11])},
            "bbox_m": box,
        })
    return raw, bodies, boxes, gmin, gmax


def renumber_interference(intf):
    out = dict(intf)
    out["pairs"] = [{**p, "a": "c" + p["a"][1:], "b": "c" + p["b"][1:]}
                    for p in intf.get("pairs", [])]
    return out


def capture(baseline, doc=None, width_axis=0, progress=None,
            label_face_max_area=None, label_colour_tol=24,
            label_area_tol_frac=0.35):
    if doc is None:
        from common import solidworks_session as sws
        doc = sws.active_doc(sws.attach())
        if doc is None:
            raise RuntimeError("no active SolidWorks document")

    max_area = (label_face_max_area if label_face_max_area is not None
                else M.LABEL_FACE_MAX_AREA)

    rebuild = health_gate(doc, baseline)
    raw, bodies, boxes, gmin, gmax = capture_bodies(doc)
    # The bodies just measured, not the document's own: an assembly
    # has none of its own, and the map has to be intersected with
    # what is actually here or it reports faces from configurations
    # this reading never looked at.
    amap = M.build_appearance_map(doc, bodies=raw)
    labels = discover_glyphs(raw, amap, baseline.get("labels", []),
                             colour_tol=label_colour_tol,
                             area_tol_frac=label_area_tol_frac,
                             max_area=max_area)
    intf = renumber_interference(
        M.measure_interference(raw, boxes=boxes, progress=progress))

    return {
        "document": str(z(doc.GetTitle)),
        "rebuild": rebuild,
        "global": {"bbox_m": gmin + gmax,
                   "width_m": gmax[width_axis] - gmin[width_axis]},
        "bodies": bodies,
        "labels": [l for l in labels if l.get("found")],
        "labels_missing": [l for l in labels if not l.get("found")],
        "interference": intf,
        "appearance_faces_mapped": len(amap),
    }

# ---------------------------------------------------------------------------
# the open / measure / close cycle, shared by every assembly task
# ---------------------------------------------------------------------------

def measure_assembly(build, empty, path=None, baseline=None, progress=None,
                     doc_type=None):
    """Open a document, measure it, close it. Returns the capture dict.

    `doc_type` defaults to the file extension, so this serves a task that
    grades a standalone part (task 8) as well as the assembly tasks it was
    written for. The name stays for the harnesses that already call it.

    The task supplies only the two halves that are actually its own:

        build(doc, baseline, source_path, say) -> capture dict
        empty                                  -> the capture SHAPE returned
                                                  when nothing could be
                                                  measured

    Everything else is the same in every assembly task, and was copied
    between tasks 15 and 17 with the differences going the wrong way: 17 had
    grown a pywin32 guard and a progress line that 15 never got, and 15's
    version would have raised AttributeError on a machine without pywin32
    instead of saying what to do about it.

    **A failed open returns a capture, not an exception.** A grading
    pipeline is better served by an envelope that scores zero and states the
    reason than by a traceback someone has to read. `empty` exists so that
    capture still has the shape the scorer expects -- the keys are present
    and empty rather than absent, which is the difference between "measured
    nothing" and "this file is not a capture".

    attach() arms quiet mode and the save-dialog watchdog once per process;
    it is deliberately NOT armed again here. Two watchdogs on one dialog
    each report a firing the other caused.
    """
    # Imported here, not at module scope: this module must stay importable
    # on a machine with no pywin32 so that --score-from works off a saved
    # capture. Same discipline as attach_app() above.
    import sys
    from pathlib import Path
    from common import solidworks_session as sws
    from common import solidworks_assembly as SA
    if sws.win32com is None:
        #: WHY, not just THAT. "Not installed" and "installed but its DLL
        #: will not load" are different faults; this line used to print
        #: the same sentence for both and send the reader to a `pip
        #: install` that changes nothing.
        raise SystemExit(
            f"measuring needs pywin32 on Windows, and this process cannot "
            f"use it -- {sws.why_no_win32()}. Python is "
            f"{sys.executable}. Score a stored capture with --score-from "
            f"instead, or run the harness from the interpreter that has "
            f"pywin32.")
    safe = SA.safe
    say = progress or (lambda *_: None)
    app = sws.attach()

    if path is None:
        doc = safe(lambda: sws.dyn(sws.active_doc(app)))
        if doc is None:
            return dict(empty, source_path=None,
                        open={"active_document": True},
                        open_error="no active document")
        cap = build(doc, baseline, None, say)
        cap["open"] = {"active_document": True}
        return cap

    say(f"  opening {Path(path).name}")
    doc, diag = SA.open_readonly(app, path, doc_type=doc_type)
    if doc is None:
        return dict(empty, source_path=str(path), open=diag,
                    open_error=diag.get("errors") or "null document")
    try:
        cap = build(doc, baseline, path, say)
        cap["open"] = diag
        return cap
    finally:
        safe(lambda: sws.close_all_documents(app))
        # A dismissed save dialog means this run met a modal it had to click
        # through. Silence about it would make a batch look cleaner than it
        # was -- and the panel assembly raises one on every rebuild.
        for line in sws.session_report():
            print(f"  ! {line}", file=sys.stderr)
