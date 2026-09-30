"""One deterministic picture of an open SolidWorks document.

WHY A RENDER AND NOT THE PICTURE THAT SHIPPED. Every task in this corpus
ships a PNG per specimen -- `solution/solution.png`, one per example. They
are the wrong thing for a grader to look at, twice over. A real candidate
is a part file somebody produced and has no picture at all, so a criterion
that read them would score the corpus and not the candidate; and even on
the corpus they are beauty renders made by whoever built the dataset,
while a candidate would get whatever this module produces. The judge would
then be comparing pictures taken under different rules, with the nicer one
always belonging to the reference.

So: EVERY model is rendered here, the reference included, by the same
call, at the same size, from the same camera. A shipped picture is a
fallback for the one case where rendering is impossible -- re-scoring a
stored capture on a machine with no SolidWorks -- and when that happens
the record says so, because "we drew this" and "it came with the dataset"
must never be indistinguishable afterwards.

DETERMINISM is the whole job. A named view, zoom to fit, an explicit pixel
size and an explicit display mode: change any of them between two models
and the judge is answering a different question about each. Nothing here
uses the window's current state.

NOTHING HERE RAISES. A render that fails is a record saying which step
failed, never an exception in the middle of a grading run: a picture is an
aid to one criterion, and losing it must not cost the other nine.
"""
from __future__ import annotations

import os
from pathlib import Path

from common.solidworks_assembly import safe

try:                                                     # Windows only
    from common.solidworks_session import dyn, z
except Exception:                                        # scoring-only host
    dyn = z = None


#: `ShowNamedView2` takes the name AND the id; the id is what actually
#: selects a standard view, the name is for the ones a user saved.
#: swStandardViews_e: Front 1, Back 2, Left 3, Right 4, Top 5, Bottom 6,
#: Isometric 7, Trimetric 8, Dimetric 9.
STANDARD_VIEWS = {
    "iso": ("*Isometric", 7),
    "trimetric": ("*Trimetric", 8),
    "front": ("*Front", 1),
    "back": ("*Back", 2),
    "left": ("*Left", 3),
    "right": ("*Right", 4),
    "top": ("*Top", 5),
    "bottom": ("*Bottom", 6),
}

#: Square on purpose: a fixed aspect means the camera distance after
#: zoom-to-fit depends only on the part, not on whatever shape somebody
#: left the SolidWorks window.
DEFAULT_SIZE = (1024, 1024)

#: HOW THE SOLID IS DRAWN, and THE CASING IS THE API'S. SolidWorks spells
#: these as one lower-case word and in British English; asked for
#: `ViewDisplayHiddenLinesGrayed` it raises, and a caller that swallowed
#: the error got a shaded picture it believed was something else. Task 34
#: lost a live run to exactly that.
#:
#: `hidden_grey` IS THE ONE THAT SHOWS INSIDES. It is SolidWorks' "Hidden
#: Lines Visible": the silhouette stays solid and every interior edge is
#: drawn in grey behind it. On a hollow casting -- a manifold, a housing,
#: a pipe run -- the bores are the answer to most questions worth asking
#: and `shaded` is a picture of the box they came in.
DISPLAY_MODES = {
    "shaded": "ViewDisplayShaded",
    "hidden_grey": "ViewDisplayHiddengreyed",
    "hidden_removed": "ViewDisplayHiddenremoved",
    "wireframe": "ViewDisplayWireframe",
}

#: Reference geometry and sketches, BY TYPE NAME. `GetTypeName2` reports
#: what a feature IS; the feature's own name is not read here and must
#: not be -- see `hide_reference_geometry`.
_REF_GEOM_TYPES = ("RefPlane", "RefAxis", "RefPoint", "CoordSys")
_SKETCH_TYPES = ("ProfileFeature", "3DProfileFeature")


def _as_png(bmp: Path) -> Path | None:
    """A .bmp turned into a .png, when this machine can do it.

    Worth doing: a 1024x1024 BMP is three megabytes of uncompressed pixels
    to hand to a model, and BMP is the least well supported of the image
    formats a reader might be given. Worth doing OPTIONALLY: Pillow is not
    part of what a grading host is promised, and a BMP the judge can open
    beats a PNG that was never written.
    """
    try:
        from PIL import Image
    except Exception:                                    # noqa: BLE001
        return None
    png = bmp.with_suffix(".png")
    try:
        with Image.open(bmp) as im:
            im.convert("RGB").save(png, "PNG", optimize=True)
    except Exception:                                    # noqa: BLE001
        return None
    try:
        os.remove(bmp)
    except OSError:
        pass
    return png


def _camera(md):
    """The nine numbers of the view's rotation matrix.

    THE POINT OF THIS MODULE IS THAT TWO MODELS ARE DRAWN FROM THE SAME
    CAMERA, and until this was recorded that was an assumption. It is the
    rotation only: zoom-to-fit legitimately changes the scale and the
    translation from part to part, while the orientation must not move. So
    two renders of a run whose first nine numbers agree were taken from
    the same direction, and it is checkable after the fact rather than
    believed.
    """
    xf = z(z(z(md.ActiveView).Transform).ArrayData)
    return [round(float(v), 6) for v in list(xf)[:9]]


def _select(md, comp, rec):
    """Select one component object. Reports every step; raises nothing."""
    try:
        import pythoncom
        from win32com.client import VARIANT
        null = VARIANT(pythoncom.VT_DISPATCH, None)
    except Exception as exc:                             # noqa: BLE001
        rec["steps"]["select"] = f"no pywin32: {exc}"
        return False
    safe(lambda: md.ClearSelection2(True))
    got = safe(lambda: bool(dyn(comp).Select4(False, null, False)))
    rec["steps"]["select"] = got
    return bool(got)


def view_scale(md):
    """The view's zoom, read back.

    `_camera` takes the first nine of `ActiveView.Transform.ArrayData`,
    which is the rotation and says nothing about how far away the camera
    is. Index 12 of the same sixteen is the scale, and it is the only
    number that answers "did the zoom call do anything" -- the first
    attempt at that question compared rotations, found them equal (they
    were, a zoom does not rotate) and concluded that nothing had
    happened. It may have; the measurement could not see it.
    """
    xf = safe(lambda: list(z(z(z(md.ActiveView).Transform).ArrayData)))
    if not xf or len(xf) < 13:
        return None
    return round(float(xf[12]), 8)


def hide_reference_geometry(doc):
    """Take the planes, axes and sketches OUT OF THE PICTURE.

    A judge is shown a rendering as evidence, and whatever is in the
    frame is evidence. A saved SolidWorks document usually has its
    reference geometry switched on, so the frame gets blue rectangles,
    dotted construction lines and centre marks over the part -- and, on
    every picture in task 34's first judged run, a plane LABELLED WITH
    ITS FEATURE NAME. That name was Russian. Two rules break at once: no
    grading in this repository reads a name, and nothing shipped carries
    Russian.

    SELECTED BY TYPE, never by name, for the same reason. `GetTypeName2`
    answers what a feature is; a document whose author named the plane
    something else is hidden just the same.

    Returns what it blanked and what it found, so a caller can record
    that the frame was cleaned rather than assume it. Blanking marks the
    document modified -- harmless on a read-only open, and the session's
    dialog watchdog answers the save prompt on close.
    """
    rec = {"planes": 0, "sketches": 0, "found": 0, "ok": False,
           "documents": 0}
    if dyn is None or doc is None:
        return rec
    md = dyn(doc)
    got = _blank_in(md)
    rec["found"] += got["found"]
    rec["planes"] += got["planes"]
    rec["sketches"] += got["sketches"]
    rec["documents"] = 1
    rec["ok"] = True
    #: ON AN ASSEMBLY THIS CLEANS ALMOST NOTHING, AND THAT IS NOW A
    #: DELIBERATE LIMIT RATHER THAN AN OVERSIGHT. The walk above sees the
    #: assembly's own two or three planes; every centreline and sketch a
    #: picture of an assembly actually carries belongs to a PART one
    #: level down, and blanking those was tried and taken out again.
    #:
    #: IT WORKED AND IT WRECKED THE SESSION. Walking each component
    #: document blanked 158 planes and 183 sketches over 42 documents on
    #: task 13 -- the frames did come out clean. But `BlankRefGeom` and
    #: `BlankSketch` MARK A DOCUMENT MODIFIED, and a batch holds one
    #: SolidWorks session across every model: forty-two dirty documents
    #: per model piled up until a model passed the 2400-second ceiling
    #: and was killed with its assembly still open and unsaved. The next
    #: task opened into that session and read `369 components, 0 live`
    #: with a save prompt for the previous task's file on screen; the one
    #: after it read zero solid bodies and timed out in turn. Three tasks
    #: failed downstream of one convenience.
    #:
    #: A view-level "hide all types" toggle would do this without
    #: touching a document, and nobody has written one. Until somebody
    #: does, an assembly's renders carry their sketches, and a judged
    #: task on an assembly has to be told so in its prompt rather than
    #: have it cleaned underneath.
    return rec


def _blank_in(md):
    """Blank every plane, axis, point and sketch in ONE open document.

    Yields once so the assembly branch above can treat the top document
    and a component's document with the same three lines.
    """
    planes, sketches = [], []
    feat = safe(lambda: z(md.FirstFeature))
    seen = 0
    while feat is not None and seen < 4000:
        seen += 1
        f = dyn(feat)
        kind = safe(lambda: str(z(f.GetTypeName2)))
        if kind in _REF_GEOM_TYPES:
            planes.append(f)
        elif kind in _SKETCH_TYPES:
            sketches.append(f)
        feat = safe(lambda: z(f.GetNextFeature))
    got = {"found": len(planes) + len(sketches), "planes": 0, "sketches": 0}
    for f in planes:
        if safe(lambda f=f: bool(z(f.Select2(False, 0)))):
            if safe(lambda: (md.BlankRefGeom(), True)[1]):
                got["planes"] += 1
    for f in sketches:
        if safe(lambda f=f: bool(z(f.Select2(False, 0)))):
            if safe(lambda: (md.BlankSketch(), True)[1]):
                got["sketches"] += 1
    safe(lambda: md.ClearSelection2(True))
    return got


def save_view(doc, out_path, view="iso", size=DEFAULT_SIZE, shaded=True,
              focus=None, recamera=True, keep_selected=True, box_mm=None,
              display=None):
    """Draw the open document once. Returns a record, never raises.

    `out_path` names the picture; the suffix is decided here, because
    which format actually got written depends on what the machine could
    do and the caller has to be told rather than assume.

    The record's `ok` is the only thing a caller should branch on. `steps`
    is there for the day it is False: each stage of setting the camera is
    reported separately, so "SolidWorks would not change the view" and
    "SolidWorks would not write the file" do not look alike.

    `focus` IS ONE COMPONENT TO FILL THE FRAME -- the object, not its
    name -- and it exists because
    a whole-machine zoom-to-fit cannot answer a question about one part
    of it. Task 13 asks a judge whether a belt guard's fixings could be
    reached; the guard is 152 mm across a machine 500 mm wide, and on the
    1024-pixel fit of the whole assembly its screw heads are not there to
    be seen. The judge spent twelve turns looking for them, three times
    over, on each of the two models whose answer was supposed to be no --
    and the eight models that DID answer answered from the same
    unanswerable picture. A verdict off evidence that cannot carry it is
    worse than no verdict, because it looks like one.

    MEASURED, THREE ROUTES AGAINST ONE MODEL, because the first two
    attempts at this each failed in a way the instrument could not see.
    The view scale on task 13's reference, from a whole-assembly fit of
    3627:

        Component2.Select4 + ViewZoomToSelection   8115   the guard,
                                                          its drive and
                                                          its mounting,
                                                          highlighted
        SelectByID2 by name + the same             8115   same frame,
                                                          but the plain
                                                          name does not
                                                          select and the
                                                          '@assembly'
                                                          form must be
                                                          spelled
        ViewZoomTo2 on the component's own box    15228   too tight, and
                                                          on a thin
                                                          translucent
                                                          part it shows
                                                          what is UNDER
                                                          it

    So the object is what is taken. A null COM interface is not `None`:
    `Select4`'s callout wants VARIANT(VT_DISPATCH, None), and passing
    Python's None is what made the first attempt raise "type mismatch"
    at argument 2 and look like a route that does not exist.

    When the selection fails the camera falls back to the fit, and
    `steps` says which happened -- a close-up that quietly became a wide
    shot would be the same bug again.
    """
    out = Path(out_path)
    rec = {"path": None, "view": view, "size": list(size), "ok": False,
           "source": "rendered", "camera": None, "steps": {}}
    if dyn is None or doc is None:
        rec["steps"]["session"] = "no SolidWorks on this host"
        return rec

    md = dyn(doc)
    name, view_id = STANDARD_VIEWS.get(view, STANDARD_VIEWS["iso"])
    # The camera, in the order the API wants it: orientation, then display
    # mode, then fit. Fitting before the orientation changes fits the old
    # one, which is the kind of mistake that produces a plausible picture
    # of the wrong thing.
    # `ShowNamedView2` RETURNS NOTHING. The first version of this recorded
    # `bool(...)` of it and duly wrote `named_view: false` under a picture
    # that was correctly isometric -- a step record that reports a failure
    # which did not happen is worse than no record, because it sends the
    # next person looking for a bug in the working half. Same mistake as
    # the shipped harness reading errors through GetErrorCode. Success
    # here is "the call did not raise"; whether the camera actually moved
    # is answered by `camera` below, which reads the state back.
    #: `recamera=False` SAVES WHAT IS ALREADY ON SCREEN and touches
    #: nothing. It exists because the diagnostic written to find out
    #: whether a zoom call had worked drew its evidence through this
    #: function -- which re-orients and re-fits before every shot, so all
    #: four routes produced the same fitted picture and the run proved
    #: nothing about any of them. An instrument that resets the thing it
    #: is measuring measures the reset.
    if recamera:
        rec["steps"]["named_view"] = safe(
            lambda: (md.ShowNamedView2(name, view_id), True)[1], None)
    #: `display` NAMES THE MODE; `shaded` is the old two-valued spelling
    #: of the same thing and still works. A name that is not a mode is
    #: reported rather than quietly drawn shaded, because a picture in
    #: the wrong mode looks like a picture.
    mode = display or ("shaded" if shaded else None)
    if mode is not None:
        call = DISPLAY_MODES.get(mode)
        if call is None:
            rec["steps"]["display"] = f"no such display mode: {mode!r}"
        else:
            rec["steps"]["display"] = safe(
                lambda: (getattr(md, call)(), True)[1], None)
            rec["display"] = mode
    framed = False
    #: `box_mm` FRAMES A PLACE, not a part, and it is the route the note
    #: above calls "too tight". Too tight is the point here. Task 20 has
    #: to ask whether a pipe is welded to one particular stub among
    #: several lookalikes on the same casing, and a judge given the whole
    #: machine cannot tell them apart -- seven live readings of one model
    #: never once named the right end. The two stubs it cares about are
    #: fixed features of a FROZEN seed, so their positions are known
    #: constants, and the camera can be put on one of them. The
    #: identification then happens in geometry, where it is exact, and
    #: the judge is left with the one thing a picture answers well: is
    #: something welded to the thing in the middle of this frame.
    #:
    #: Model space, millimetres, (xlo, ylo, zlo, xhi, yhi, zhi). The API
    #: wants metres.
    if box_mm and len(box_mm) == 6:
        xs = [float(v) / 1000.0 for v in box_mm]
        framed = safe(lambda: (md.ViewZoomTo2(xs[0], xs[1], xs[2],
                                              xs[3], xs[4], xs[5]), True)[1],
                      None) or False
        rec["steps"]["zoom_to_box"] = framed
        rec["scale"] = view_scale(md)
    if focus is not None and not framed:
        picked = _select(md, focus, rec)
        if picked:
            framed = safe(lambda: (md.ViewZoomToSelection(), True)[1],
                          None) or False
            rec["steps"]["zoom_to_selection"] = framed
            rec["scale"] = view_scale(md)
            #: THE SELECTION IS LEFT ON BY DEFAULT, and the part comes
            #: out highlighted. That is usually the point: it says WHICH
            #: part the question is about without naming it, in a picture
            #: where a dozen others are in frame.
            #:
            #: `keep_selected=False` EXISTS BECAUSE THE HIGHLIGHT IS A
            #: COLOUR, and a task whose question turns on colour cannot
            #: afford it. Task 20 points its candidate at "the stub shown
            #: in turquoise", and SolidWorks draws a selected body in a
            #: light cyan that is the same colour to a reader: the first
            #: render of that task's reference came back with the whole
            #: added run painted the exact shade the judge had been told
            #: to look for on one small stub. Zoom with the selection,
            #: then drop it, and the frame is the same while every colour
            #: in it is the model's own.
            if not keep_selected:
                rec["steps"]["deselect"] = safe(
                    lambda: (md.ClearSelection2(True), True)[1], None)
    if not framed and recamera:
        rec["steps"]["zoom_to_fit"] = safe(
            lambda: (md.ViewZoomtofit2(), True)[1], None)
    safe(lambda: md.GraphicsRedraw2())
    rec["camera"] = safe(lambda: _camera(md), None)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        rec["steps"]["mkdir"] = f"{type(exc).__name__}: {exc}"
        return rec

    # SaveBMP is the one route that is documented to take a size. Save As
    # can write a PNG directly but sizes it from the window, which is the
    # thing this module exists to stop depending on.
    bmp = out.with_suffix(".bmp")
    rec["steps"]["save_bmp"] = safe(
        lambda: bool(z(md.SaveBMP(str(bmp), int(size[0]), int(size[1])))),
        None)
    if not bmp.is_file():
        rec["steps"]["written"] = False
        return rec

    png = _as_png(bmp)
    rec["steps"]["to_png"] = png is not None
    rec.update(path=str(png or bmp), ok=True)
    #: THE SIZE OF THE FILE, because `ViewZoomTo2` can return True over an
    #: empty picture: tasks 20 and 24 have shipped judged frames that were
    #: reported framed, reported saved, and drew nothing. Identical byte
    #: counts across models are the tell, and they are only visible if
    #: somebody wrote the number down.
    try:
        rec["bytes"] = (png or bmp).stat().st_size
    except OSError:
        pass
    return rec


def _pick(md, objs, rec):
    """Select a list of component objects. Reports, raises nothing."""
    try:
        import pythoncom
        from win32com.client import VARIANT
        null = VARIANT(pythoncom.VT_DISPATCH, None)
    except Exception as exc:                             # noqa: BLE001
        rec["steps"]["select"] = f"no pywin32: {exc}"
        return 0
    safe(lambda: md.ClearSelection2(True))
    n = 0
    for obj in objs:
        if safe(lambda: bool(dyn(obj).Select4(True, null, False))):
            n += 1
    return n


def hide_components(doc, objs):
    """Take these components out of the picture. Returns a record.

    `objs` is component OBJECTS, never names: what to photograph is
    decided by geometry upstream and the object is what travels.

    Pairs with `show_components`, which puts them back, so a run can
    walk several places in one document instead of re-opening it per
    picture. Neither touches geometry -- visibility is display state --
    but both DO mark the document modified, and SolidWorks then offers
    to save on the way out; that prompt is answered "No" by the
    session's dialog watchdog and appears in the log as one `save dialog
    dismissed` line. On a host without the watchdog the close would sit
    on a modal dialog, so do not call either from a run with no dialog
    handling.
    """
    rec = {"n": 0, "ok": False, "steps": {}}
    if dyn is None or doc is None or not objs:
        rec["steps"]["session"] = "nothing to hide"
        return rec
    md = dyn(doc)
    rec["n"] = _pick(md, objs, rec)
    if rec["n"]:
        #: `HideComponent2` acts on the SELECTION, which is why every
        #: component is selected with append=True first.
        rec["steps"]["hide"] = safe(lambda: (md.HideComponent2(), True)[1],
                                    None)
        rec["ok"] = bool(rec["steps"]["hide"])
    safe(lambda: md.ClearSelection2(True))
    safe(lambda: md.GraphicsRedraw2())
    return rec


def show_components(doc, objs):
    """Put back what `hide_components` took away.

    A hidden component cannot be picked in the graphics area, but it is
    still there to `Select4`, which is why the caller keeps the objects
    rather than trying to find them again afterwards.
    """
    rec = {"n": 0, "ok": False, "steps": {}}
    if dyn is None or doc is None or not objs:
        rec["steps"]["session"] = "nothing to show"
        return rec
    md = dyn(doc)
    rec["n"] = _pick(md, objs, rec)
    if rec["n"]:
        rec["steps"]["show"] = safe(lambda: (md.ShowComponent2(), True)[1],
                                    None)
        rec["ok"] = bool(rec["steps"]["show"])
    safe(lambda: md.ClearSelection2(True))
    safe(lambda: md.GraphicsRedraw2())
    return rec


def isolate(doc, comps, keep):
    """Hide everything but one component, so that the camera can see it.

    WHY THE CAMERA WAS NOT ENOUGH. `focus` above aims at a part and fills
    the frame with it, and that is the right instrument for a part on the
    OUTSIDE of a machine. Task 17 asks a judge what an added part IS, and
    the part a candidate added there lives inside a closed enclosure: all
    seven standard views of it, taken with `focus`, came back as two flat
    fields of colour -- fourteen hundred pixels of the wall in front of
    it. The judge said as much on the first live run: "flat, featureless
    gray fields with no visible panel, part, or marker". Zoom-to-selection
    does not care what stands between the camera and its subject, and
    there is no direction here that is clear.

    So the walls come off instead of the camera moving.

    `comps` is the walk -- (component, depth) pairs in tree order -- and
    `keep` indexes the one to leave standing. Everything else AT THE TOP
    LEVEL is hidden. A subject nested inside a sub-assembly keeps that
    whole sub-assembly, because the nearest top-level ancestor of an entry
    in a depth-first walk is the last depth-0 entry before it, and hiding
    the ancestor would hide the subject with it.

    HIDING IS A DISPLAY STATE. It moves no geometry, and every document
    this package opens is opened read-only, so nothing can be written
    back. It is still destructive to the picture, and there is no
    show-all here to undo it: TAKE THE WIDE SHOT FIRST.

    IT DOES MARK THE DOCUMENT MODIFIED, and SolidWorks then offers to
    save it on the way out. That prompt is answered "No" by the session's
    dialog watchdog and shows up in the run log as one `save dialog
    dismissed` line -- the expected trace of this function, not a fault.
    On a host without the watchdog the close would sit on a modal dialog,
    so do not call this from a run that has no dialog handling.

    Returns a record, never raises. `hidden` is how many components were
    put away, and a `hidden` of zero under an `ok` of False means the
    close-up that follows is the same obstructed frame as before.
    """
    rec = {"hidden": 0, "kept": None, "ok": False, "steps": {}}
    if dyn is None or doc is None or not comps:
        rec["steps"]["session"] = "no SolidWorks on this host"
        return rec
    top = 0
    for j in range(min(int(keep), len(comps) - 1), -1, -1):
        if comps[j][1] == 0:
            top = j
            break
    rec["kept"] = top
    got = hide_components(doc, [p[0] for j, p in enumerate(comps)
                                if p[1] == 0 and j != top])
    rec["hidden"], rec["ok"], rec["steps"] = got["n"], got["ok"], got["steps"]
    return rec


def existing_render(task_dir, model):
    """A picture that came WITH the dataset, for the fallback case only.

    Never preferred over one we draw. Returned with `source` saying where
    it came from, so a record cannot claim we rendered something we did
    not: a shipped render is a picture of the specimen the corpus was
    built from, taken by somebody else under rules we do not know.
    """
    task_dir = Path(task_dir)
    for cand in (task_dir / "solution" / f"{model}.png",
                 task_dir / "examples" / model / f"{model}.png",
                 task_dir / "environment" / f"{model}.png"):
        if cand.is_file():
            return {"path": str(cand), "view": None, "ok": True,
                    "source": "shipped with the dataset", "camera": None,
                    "steps": {}}
    return None


def _view_spec(v):
    """A view entry is "back", ("back", "hidden_grey"), or that plus a box.

    THE THIRD ELEMENT IS A WINDOW IN MODEL SPACE, millimetres, as
    `save_view` takes it: (xlo, ylo, zlo, xhi, yhi, zhi). It exists
    because a zoom-to-fit of a whole assembly is not always a picture of
    the thing being asked about. Task 57 asks whether two gear wheels
    mesh; fitted to the pair, the 5 mm by which their teeth overlap is
    thirty pixels of a 1400-pixel frame, and three readings in a row
    reported a gap there -- on the REFERENCE, whose overlap the
    measurements put beyond doubt. A window aimed by geometry, the same
    width in millimetres for every model, is the same question asked at
    a size the eye can answer.

    A boxed view writes to its own file: two shots of one model from one
    camera at two magnifications are two pictures, and sharing a path
    would leave whichever ran last.
    """
    if isinstance(v, (tuple, list)):
        name = str(v[0])
        mode = str(v[1]) if len(v) > 1 and v[1] else None
        box = list(v[2]) if len(v) > 2 and v[2] else None
        return name, mode, box
    return str(v), None, None


def renders_for(doc, task_dir, model, out_dir, views=("iso",),
                size=DEFAULT_SIZE, allow_shipped=True, focus=None,
                focus_views=(), keep_selected=True, clean=False):
    """Every picture one judgement gets, in the order they are preferred.

    Rendering first and always. A shipped picture is used only when
    nothing was drawn -- which means no SolidWorks, which means a re-score
    rather than a measurement -- and never alongside one that was.

    A VIEW MAY NAME ITS DISPLAY MODE: `("back", "hidden_grey")` beside
    the plain `"iso"`. The mode goes into the file name as well as the
    record, because two shots of one model from one camera in two modes
    are two different pictures and writing them to one path would leave
    whichever ran last.

    `clean` blanks reference geometry and sketches first -- see
    `hide_reference_geometry` for why any judged task wants it. IT IS
    OFF BY DEFAULT AND THAT IS NOT AN ENDORSEMENT: five harnesses that
    already shipped draw through this function, and turning it on for
    them here would change their evidence without re-running them.
    Task 34 passes `clean=True`; the others are a sweep still owed, and
    DATASET_ISSUES carries it.
    """
    out, drawn = [], []
    cleaned = hide_reference_geometry(doc) if clean else None
    for v in views:
        name, mode, box = _view_spec(v)
        suffix = f"{name}.{mode}" if mode else name
        if box:
            suffix += ".window"
        rec = save_view(doc, Path(out_dir) / f"{model}.{suffix}.png",
                        view=name, size=size, display=mode or "shaded",
                        box_mm=box)
        if cleaned is not None:
            rec["cleaned"] = cleaned
        out.append(rec)
        if rec.get("ok"):
            drawn.append(rec)
    #: CLOSE-UPS LAST, so that a caller reading `drawn[0]` still gets the
    #: wide shot it used to get.
    for v in (focus_views if focus else ()):
        #: A CLOSE-UP MAY NAME ITS DISPLAY MODE TOO, exactly as a wide
        #: view can. It could not before, and the gap was not academic:
        #: task 13 asks whether a guard's fixings can be reached, the
        #: answer turns on how deep the guard's box is and on what sits
        #: INSIDE it, and `hidden_grey` is the mode that draws insides.
        #: A plain string behaves exactly as it did -- `_view_spec`
        #: returns it with no mode, and `display=mode or "shaded"` is
        #: what `save_view` already defaulted to.
        name, mode, _box = _view_spec(v)
        suffix = f"{name}.{mode}" if mode else name
        rec = save_view(doc, Path(out_dir) / f"{model}.{suffix}.close.png",
                        view=name, size=size, focus=focus,
                        keep_selected=keep_selected,
                        display=mode or "shaded")
        #: RECORDED HERE TOO. It was recorded only on the wide views, so
        #: the two tasks that draw exclusively through `focus_views` got
        #: no `cleaned` key at all -- which is why nobody could see that
        #: the cleaning had done nothing on an assembly.
        if cleaned is not None:
            rec["cleaned"] = cleaned
        out.append(rec)
        if rec.get("ok"):
            drawn.append(rec)
    if drawn:
        return drawn, out
    if allow_shipped:
        shipped = existing_render(task_dir, model)
        if shipped:
            return [shipped], out + [shipped]
    return [], out
