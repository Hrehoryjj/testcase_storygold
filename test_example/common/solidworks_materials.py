r"""Reading a SolidWorks material card, and finding the library it lives in.

Why this is a module and not three lines in a harness: a snap-fit hand calc
needs an elastic modulus, and there are exactly two ways to get one. Type it
into the harness as a constant and declare it assumed, or read it off the
part. Reading it off the part means resolving a material NAME against a
material DATABASE, and every step of that has a way of failing that looks
like data:

  * `GetMaterialPropertyName2` hands back the database as a bare library
    name -- `"solidworks materials"`, not a path -- so a lookup that only
    accepts paths finds nothing and reports "this material has no card",
    which is indistinguishable from "this material genuinely has none".
  * The install is wherever it is. Guessing `C:\Program Files\...` reported
    a machine with a full library as having none; this one has SolidWorks
    on D:.
  * SolidWorks writes the card's container as `<physicalproperties>` in
    LOWER case with `<EX>`/`<DENS>` upper case inside it. An exact-case
    match for `PhysicalProperties` finds nothing, and again nothing reads
    exactly like "no physical properties on this card".

Each of those is the same failure family: one value reachable from two
causes, one of which is a bug. So every lookup here returns WHY it failed
alongside the answer, and the caller decides whether to fall back to a
declared assumption or to stop.

What no route can give: a coefficient of friction. SolidWorks material
cards do not carry one at any level of assignment. A hand calc's mu stays
an assumption and has to say so.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

#: The density SolidWorks reports for a document with NO material assigned.
#: One number that settles "is anything assigned at all".
NO_MATERIAL_DENSITY = 1000.0

#: The physical properties a .sldmat card carries, and our names for them.
#: Friction is deliberately absent; see the module docstring.
SLDMAT_KEYS = {"EX": "elastic_modulus_pa", "NUXY": "poisson",
               "GXY": "shear_modulus_pa", "DENS": "density_kg_m3",
               "SIGXT": "tensile_strength_pa", "SIGYLD": "yield_strength_pa"}

#: How deep to walk a candidate root. The library sits at
#: <install>\lang\<language>\sldmaterials\*.sldmat -- four levels down. The
#: cap is what keeps a root that turns out to be a whole drive from looking
#: like a hang.
WALK_DEPTH = 6

#: What an engineering plastic's elastic modulus may plausibly be, in Pa.
#: The upper bound is 10 GPa because the standard library's Nylon 6/10 reads
#: 8.3 and is legitimately a plastic; aluminium is 69 and steel 200, so a
#: metal is still caught by a factor of seven.
PLASTIC_E_RANGE_PA = (0.5e9, 10.0e9)


def _local(tag):
    """An element's tag without its XML namespace."""
    t = str(tag)
    return t.rsplit("}", 1)[-1] if "}" in t else t


def _named(node, name):
    """Descendants whose local tag matches `name`, case-insensitively."""
    want = name.casefold()
    for el in node.iter():
        if _local(el.tag).casefold() == want:
            yield el


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:                                       # noqa: BLE001
        return default


# --------------------------------------------------------------------------
# finding the library
# --------------------------------------------------------------------------

def _walk_for(root, suffix=".sldmat", depth=WALK_DEPTH, cap=400):
    root = Path(root)
    out, base = [], len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).parts) - base >= depth:
                dirnames[:] = []
            for fn in filenames:
                if fn.lower().endswith(suffix):
                    out.append(Path(dirpath) / fn)
                    if len(out) >= cap:
                        return out
    except Exception:                                       # noqa: BLE001
        pass
    return out


def _roots_from_session(app):
    """Where SolidWorks says it lives. The only route that KNOWS."""
    roots, notes = [], []
    if app is None:
        return roots, notes
    def _get(name):
        m = getattr(app, name)
        return m() if callable(m) else m
    exe = _safe(lambda: str(_get("GetExecutablePath")))
    if exe and Path(exe).exists():
        d = Path(exe)
        d = d.parent if d.is_file() else d
        roots.extend([d / "lang", d, d.parent])
        notes.append(f"GetExecutablePath -> {exe}")
    # The string-LIST preferences hold the material database search paths,
    # but which enum member differs between API versions, and the wrong
    # index returns a DIFFERENT list of valid-looking paths rather than an
    # error. So read them all and keep only entries that exist on disk: a
    # path that resolves is evidence whichever preference produced it.
    for n in range(16):
        for v in (_safe(lambda i=n: list(
                app.GetUserPreferenceStringListValue(i) or [])) or []):
            p = Path(str(v))
            if p.exists():
                roots.append(p)
                notes.append(f"UserPreferenceStringList[{n}] -> {p}")
    return roots, notes


def _roots_from_registry():
    roots, notes = [], []
    try:
        import winreg
    except ImportError:
        return roots, notes
    for hive in (getattr(winreg, "HKEY_LOCAL_MACHINE"),
                 getattr(winreg, "HKEY_CURRENT_USER")):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\SolidWorks") as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    sub = winreg.EnumKey(k, i)
                    for leaf in ("Setup", r"Setup\SolidWorks"):
                        try:
                            with winreg.OpenKey(k, f"{sub}\\{leaf}") as s:
                                for nm in ("SolidWorks Folder",
                                           "SolidWorks Folder Path"):
                                    v = _safe(
                                        lambda n=nm: winreg.QueryValueEx(s, n)[0])
                                    p = Path(str(v)) if v else None
                                    if p and p.exists():
                                        roots.append(p)
                                        notes.append(
                                            f"registry {sub}\\{leaf}\\{nm} -> {p}")
                        except OSError:
                            continue
        except OSError:
            continue
    return roots, notes


def _roots_from_environment():
    roots = []
    for var, tail in (("ProgramFiles", "SOLIDWORKS Corp"),
                      ("ProgramFiles(x86)", "SOLIDWORKS Corp"),
                      ("ProgramW6432", "SOLIDWORKS Corp"),
                      ("ProgramData", "SOLIDWORKS"),
                      ("APPDATA", "SOLIDWORKS"),
                      ("LOCALAPPDATA", "SOLIDWORKS")):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / tail)
    # A second install drive is normal on a CAD workstation -- this repo's
    # own machine keeps SolidWorks on D:. Only exact candidates are tested;
    # no drive is ever walked blind.
    for letter in "CDEF":
        for tail in (r"Program Files\SOLIDWORKS Corp",
                     r"Program Files (x86)\SOLIDWORKS Corp",
                     r"SOLIDWORKS Corp", r"SolidWorks"):
            roots.append(Path(f"{letter}:\\") / tail)
    return roots


def databases(app=None, extra=()):
    """(files, tried). Every .sldmat on this machine, with the search log.

    `tried` is not decoration. Without it an empty `files` means either
    "this installation has no material library" or "the search looked in
    the wrong places", and those need opposite responses.
    """
    roots, notes = [], []
    r, n = _roots_from_session(app)
    roots += r
    notes += n
    r, n = _roots_from_registry()
    roots += r
    notes += n
    roots += _roots_from_environment()
    roots += [Path(e) for e in extra if e]

    seen, tried, out = set(), [], []
    for root in roots:
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        exists = root.exists()
        hits = _walk_for(root) if (exists and root.is_dir()) else []
        if exists and root.is_file() and root.suffix.lower() == ".sldmat":
            hits = [root]
        tried.append({"root": str(root), "exists": exists,
                      "found": len(hits), "notes": notes})
        out.extend(hits)

    seen, uniq = set(), []
    for f in out:
        key = str(f).casefold()
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq, tried


def _ordered_for(database, files):
    """`files`, with the ones the document names first.

    `GetMaterialPropertyName2` returns the database as a bare library NAME
    -- "solidworks materials" -- not a path. Matching only on paths is why
    a correctly assigned ABS reported no card at all. So a name is matched
    against each file's stem, and a path against the file itself; anything
    that matches is tried before anything that does not.
    """
    if not database:
        return list(files)
    want = str(database).strip()
    p = Path(want)
    named, rest = [], []
    for f in files:
        if (f.stem.casefold() == p.stem.casefold()
                or str(f).casefold() == want.casefold()):
            named.append(f)
        else:
            rest.append(f)
    return named + rest


# --------------------------------------------------------------------------
# reading a card
# --------------------------------------------------------------------------

def read_card(mat_element):
    props = {}
    for node in _named(mat_element, "physicalproperties"):
        for child in node:
            key = SLDMAT_KEYS.get(_local(child.tag).upper())
            if key is None:
                continue
            try:
                props[key] = float(child.get("value"))
            except (TypeError, ValueError):
                props[key] = child.get("value")
    return props


def scan(app=None, extra=(), limit_files=60):
    """[(database, classification, name, props)] over the whole library."""
    files, tried = databases(app=app, extra=extra)
    scan.last_tried = tried
    found = []
    for f in files[:limit_files]:
        root = _safe(lambda p=f: ET.parse(p).getroot())
        if root is None:
            continue
        for cls in _named(root, "classification"):
            cname = (cls.get("name") or "").strip()
            for mat in _named(cls, "material"):
                name = (mat.get("name") or "").strip()
                if name:
                    found.append((str(f), cname, name, read_card(mat)))
    return found


def lookup(name, database=None, app=None, extra=(), limit_files=60):
    """The card for a named material, or None, plus why.

    Returns (props, diagnostic). `props` carries `from_file` when found.
    `diagnostic` says which of the three failure modes happened, so a
    caller can log "no library on this machine" separately from "the
    library has no material by that name" -- they mean different things
    and only one of them is the harness's problem.
    """
    if not name:
        return None, "no material name on the document"
    files, tried = databases(app=app, extra=extra)
    if not files:
        existed = sum(1 for t in tried if t["exists"])
        return None, (f"no .sldmat found ({len(tried)} roots searched, "
                      f"{existed} existed)")
    want = str(name).strip().casefold()
    for f in _ordered_for(database, files)[:limit_files]:
        root = _safe(lambda p=f: ET.parse(p).getroot())
        if root is None:
            continue
        for mat in _named(root, "material"):
            if (mat.get("name") or "").strip().casefold() != want:
                continue
            props = read_card(mat)
            if props:
                props["from_file"] = str(f)
                return props, None
    return None, (f"{name!r} not found in {len(files)} database(s) "
                  f"(database hint {database!r})")


def clamp_modulus(ex_pa, fallback_pa):
    """(E, note). Keeps a graded modulus inside the plastics band."""
    lo, hi = PLASTIC_E_RANGE_PA
    if ex_pa is None:
        return fallback_pa, "no card; modulus assumed"
    if ex_pa < lo:
        return lo, f"card reads {ex_pa / 1e9:.2f} GPa, clamped up to {lo / 1e9:.1f}"
    if ex_pa > hi:
        return hi, f"card reads {ex_pa / 1e9:.2f} GPa, clamped down to {hi / 1e9:.1f}"
    return ex_pa, None
