"""Blind-Spot Audit: prove a change to the grader with numbers.

    python3 tools/blindspot_audit.py run OUTDIR [--quick] [--workers N]
        [--spots-from EARLIER_OUTDIR] [--import RESULTS.json ...]
    python3 tools/blindspot_audit.py compare BEFORE_OUTDIR AFTER_OUTDIR

`run` audits one version of the grader (this repo's harness, or the copy
named by BLINDSPOT_HARNESS) and leaves in OUTDIR:

  gates.json    every shipped capture graded in full: the reference must
                score full marks, the examples keep a record to compare
  results.json  the Blind-Spot Map: edits at spots on the outer skin, each
                graded by "no unrequested changes" (blindspot_map.py)
  zones.json    what-if: the share of the outer skin the check would skip
                with tighter zones around the controls and the strip
  map.html      the 3D map (build_blindspot_page.py)
  report.html   the verdict, figures and gates on one page

--quick spreads about 100 spots (a first look, about half an hour on four
workers), the default about 400 (about two and a half hours). A run that
stops can be started again and continues. --spots-from grades the same
spots as an earlier run, which `compare` needs: give the "after" run the
"before" run's folder. --import builds the folder from result files made
earlier by blindspot_map.py instead of grading.

`compare` puts two runs side by side in AFTER_OUTDIR/compare.html and exits
with 1 when a gate fails: the reference loses marks, an example scores
higher than before (the grader got weaker on it), a harmless edit is newly
flagged, an edit caught before is now missed, or a spot checked before is
now skipped.
"""
import hashlib
import html
import json
import os
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_argv, sys.argv = sys.argv, sys.argv[:1]
import build_blindspot_page as P  # noqa: E402
sys.argv = _argv
B = P.B
H = B.H

FULL, QUICK = 400, 100
MAX_SCORE = sum(H.ALL_CRITERIA.values())
EPS = 1e-6
# tighter zones for the what-if, mm
EDGE_MM = 1.5


def caught(score):
    return score < 1.0 - EPS


# -- gates ---------------------------------------------------------------

def _grade_capture(name):
    cap = B.load(name)
    rep = H.score_capture(cap, B.BASELINE, quiet=True)
    crit = {k: round(v["score"], 4) for k, v in rep["criteria"].items()}
    total = sum(H.ALL_CRITERIA[k] * s for k, s in crit.items())
    return name, {"total": round(total, 3), "criteria": crit}


def gates(workers):
    names = sorted(p.name[:-len(".json.gz")]
                   for p in B.CAPS.glob("*.json.gz"))
    with Pool(workers) as pool:
        graded = dict(pool.map(_grade_capture, names))
    try:                        # shown relative to the repo when inside it
        name = str(B.HARNESS.resolve().relative_to(B.ROOT.parent))
    except ValueError:
        name = str(B.HARNESS)
    return {"harness": name,
            "harness_version": H.HARNESS_VERSION,
            "harness_sha256": hashlib.sha256(
                B.HARNESS.read_bytes()).hexdigest()[:12],
            "max_score": MAX_SCORE, "captures": graded}


# -- map -----------------------------------------------------------------

def run_map(out, n, workers, spots_from):
    cmd = [sys.executable, str(HERE / "blindspot_map.py"), str(n),
           str(workers), str(out)]
    if spots_from:
        cmd += ["--at", str(Path(spots_from) / "spots.json")]
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=os.environ.copy())


def clean(paths, ref):
    """Merged results with only spots on the outer skin, as the map's page
    counts them, and every spot's position for a later run to reuse."""
    data = P.merged(paths)
    outer = P.outer_test(ref)
    exempt = B.exempt_test(ref)
    spots = [s for s in data["spots"] if outer(s["p"])]
    # skipped spots by this grader's own zones, a graded spot included if
    # the zones cover it after all
    ex = [B.spot_p(s) for s in data["exempt_spots"]] + [
        s["p"] for s in spots if exempt(s["p"])]
    data["spots"] = [s for s in spots if not exempt(s["p"])]
    data["exempt_spots"] = [{"p": p, "kind": exempt(p)} for p in ex
                            if outer(p)]
    spots = {"gap_mm": data["gap_mm"],
             "p": [s["p"] for s in data["spots"]]
             + [B.spot_p(s) for s in data["exempt_spots"]]}
    return data, spots


# -- what-if zones --------------------------------------------------------

def zone_tests(ref):
    """Which points of the reference's outer skin the check skips: as it
    does now, and with tighter zones. Each test returns True for a skipped
    point."""
    g = H.Grader(B.BASELINE, ref)
    g.c5_unrequested()
    MM = H.MM
    P0, dx = g.P * MM, g.plane_shift_m * MM
    half = g._actual_half_m() * MM
    strip = max(half, (g._shell_delta_mm() or 0.0) / 2)
    reg = g._rebuilt["reg"]
    margin = H.TOL["skin_margin_mm"]
    now = B.exempt_test(ref)

    def body_boxes(cap, moved):
        """A box per control body (not per group of controls), in mm,
        moved by the task's rule for the seed."""
        out = []
        for key, rec in cap["control_meshes"].items():
            tris = H.mesh_tris3_mm(rec)
            lo = [min(q[i] for t in tris for q in t) for i in range(3)]
            hi = [max(q[i] for t in tris for q in t) for i in range(3)]
            shifts = [0.0]
            if moved:
                cx = (lo[0] + hi[0]) / 2
                sg = 1 if cx > P0 else -1
                shifts = sorted({sg * half, sg * reg.get(sg, half)})
            for s in shifts:
                out.append((lo[0] + s, lo[1], lo[2], hi[0] + s, hi[1], hi[2]))
        return out
    seed_boxes = body_boxes(B.BASELINE, True)
    own_boxes = body_boxes(ref, False)

    def in_boxes(p, boxes, m):
        return any(all(b[i] - m <= p[i] <= b[i + 3] + m for i in range(3))
                   for b in boxes)
    joins = sorted({half, *reg.values()})

    def per_control(q):
        p = (q[0] + dx, q[1], q[2])
        if abs(p[0] - P0) <= strip + margin:
            return True
        return in_boxes(p, seed_boxes + own_boxes, margin)

    def tight(q):
        # the pads compared with the mirrored seed, so only the candidate's
        # own controls and a thin edge stay blind; the strip still skipped
        p = (q[0] + dx, q[1], q[2])
        if abs(p[0] - P0) <= strip + EDGE_MM:
            return True
        return in_boxes(p, own_boxes, EDGE_MM)

    def tight_middle(q):
        # and the middle checked against the seed's section drawn across
        # it: only the joins at each half's shift stay blind
        p = (q[0] + dx, q[1], q[2])
        d = abs(p[0] - P0)
        if min(joins) - EDGE_MM <= d <= max(joins) + EDGE_MM:
            return True
        return in_boxes(p, own_boxes, EDGE_MM)
    return {
        "now": (("As this grader works (measured)" if H.TOL.get("tz_sweep")
                 else "As the grader works now: a box around each group of "
                 "controls and the strip, 8 mm margin"),
                lambda q: bool(now(q))),
        "per_control": ("A box around each control instead of each group, "
                        "8 mm margin", per_control),
        "tight": (f"Pads compared with the mirrored original, so only each "
                  f"control's own box and {EDGE_MM} mm around it",
                  tight),
        "tight_middle": (f"As above, and the middle compared with the "
                         f"original section drawn across it: only "
                         f"{EDGE_MM} mm at the two joins",
                         tight_middle),
    }


def what_if(ref, data):
    """Share of the outer skin each zone rule skips, by area (all outer
    skin samples) and by the run's spots."""
    pts = [q for f in ref["housing_faces"] for q in f["p"]]
    flags = B.outer_flags([q[:3] + q[4:7] for q in pts])
    outer = [q for q, f in zip(pts, flags) if f]
    area = sum(q[3] for q in outer)
    spots = [s["p"] for s in data["spots"]] + [
        B.spot_p(s) for s in data["exempt_spots"]]
    out = {}
    for key, (text, skip) in zone_tests(ref).items():
        out[key] = {
            "text": text,
            "area_share": round(sum(q[3] for q in outer if skip(q))
                                / area, 4),
            "spot_share": round(sum(bool(skip(p)) for p in spots)
                                / len(spots), 4),
        }
    return {"outer_area_mm2": round(area), "spots": len(spots),
            "rules": out}


# -- summary and report -----------------------------------------------------

def summary(data):
    harm = data["harmless"]
    real = [e for e in data["edits"] if e not in harm]
    spots = data["spots"]
    kinds = [s.get("kind") if isinstance(s, dict) else None
             for s in data["exempt_spots"]]
    n = len(spots)
    return {
        "spots": n, "exempt": len(data["exempt_spots"]),
        "exempt_controls": kinds.count("controls"),
        "exempt_strip": kinds.count("strip"),
        "real": real, "harmless": harm,
        "per_edit": {e: sum(caught(s["un"][e]) for s in spots)
                     for e in data["edits"]},
        "every_edit": sum(all(caught(s["un"][e]) for e in real)
                          for s in spots),
        "false_alarms": sum(caught(s["un"][e]) for s in spots for e in harm),
    }


def load_run(d):
    d = Path(d)
    run = {k: json.loads((d / f"{k}.json").read_text())
           for k in ("gates", "results", "zones") if (d / f"{k}.json").exists()}
    run["dir"] = d
    return run


def verdict(before, after):
    """Gate failures, as plain sentences. With no earlier run only the
    reference is checked."""
    fails, notes = [], []
    g = after["gates"]
    ref = g["captures"].get("solution")
    if ref is None or abs(ref["total"] - g["max_score"]) > 1e-3:
        fails.append("The reference does not score full marks "
                     f"({ref and ref['total']} of {g['max_score']}).")
    if before is None:
        return fails, notes
    gb = before["gates"]["captures"]
    for name, rec in sorted(g["captures"].items()):
        if name == "solution" or name not in gb:
            continue
        d = rec["total"] - gb[name]["total"]
        if d > 1e-3:
            fails.append(f"{name} scores {d:+.3f} higher than before: the "
                         "grader got weaker on it.")
        elif d < -1e-3:
            notes.append(f"{name} scores {-d:.3f} lower than before.")
    rb = {tuple(s["p"]): s["un"] for s in before["results"]["spots"]}
    harm = after["results"]["harmless"]
    fresh = []
    for s in after["results"]["spots"]:
        old = rb.get(tuple(s["p"]))
        if old is None:
            fresh.append(s)
            continue
        for e, v in s["un"].items():
            if e not in old:
                continue
            if e in harm and caught(v) and not caught(old[e]):
                fails.append(f"New false alarm: {e} at {s['p']}.")
            if e not in harm and caught(old[e]) and not caught(v):
                fails.append(f"Missed now, caught before: {e} at {s['p']}.")
    now = {tuple(s["p"]) for s in after["results"]["spots"]}
    lost = [p for p in rb if p not in now]
    if lost:
        fails.append(f"{len(lost)} spot{'s' * (len(lost) != 1)} checked "
                     f"before {'are' if len(lost) != 1 else 'is'} skipped "
                     f"now, for example {list(lost[0])}.")
    if fresh:
        real = [e for e in after["results"]["edits"] if e not in harm]
        got = sum(caught(s["un"][e]) for s in fresh for e in real)
        notes.append(f"{len(fresh)} spots skipped before are checked now; "
                     f"{got} of {len(fresh) * len(real)} real edits there "
                     "are caught.")
    return fails, notes


def esc(x):
    return html.escape(str(x))


STYLE = """
:root { --bg:#f4f5f2; --panel:#fff; --fg:#1d2327; --muted:#5d666d;
  --line:#dde1dd; --accent:#1f6f8b; --ok:#2a7f5e; --bad:#c4462f;
  --display:"Saira Semi Condensed",system-ui,sans-serif;
  --body:"Source Sans 3",system-ui,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#12171a; --panel:#1a2125; --fg:#e6eaec; --muted:#9aa5ab;
  --line:#2b3438; --accent:#5fb3cf; --ok:#5cc29a; --bad:#ec7a63;
  color-scheme: dark } }
:root[data-theme="dark"] { --bg:#12171a; --panel:#1a2125; --fg:#e6eaec;
  --muted:#9aa5ab; --line:#2b3438; --accent:#5fb3cf; --ok:#5cc29a;
  --bad:#ec7a63; color-scheme: dark }
body { background:var(--bg); color:var(--fg); font:400 1rem/1.55 var(--body);
  margin:0; }
.wrap { max-width:960px; margin:0 auto; padding-inline:16px;
  padding-block:28px 48px; display:grid; gap:24px; }
h1 { font:700 2.2rem/1.1 var(--display); margin:0; text-wrap:balance; }
h2 { font:700 1.3rem/1.2 var(--display); margin:0; }
.eyebrow { font:600 .78rem/1 var(--mono); letter-spacing:.08em;
  text-transform:uppercase; color:var(--accent); }
.verdict { border-left:4px solid var(--ok); background:var(--panel);
  padding:14px 16px; border-radius:4px; display:grid; gap:6px; }
.verdict.bad { border-color:var(--bad); }
.verdict b { font:700 1.1rem/1.2 var(--display); }
section { display:grid; gap:10px; min-width:0; }
.tbl { overflow-x:auto; }
table { width:100%; border-collapse:collapse;
  font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
th { font:600 .74rem/1.2 var(--mono); letter-spacing:.05em;
  text-transform:uppercase; color:var(--muted); }
td.n { font-family:var(--mono); text-align:right; white-space:nowrap; }
.up { color:var(--ok); } .down { color:var(--bad); }
ul { margin:0; padding-left:1.2em; display:grid; gap:4px; }
p, li, td { overflow-wrap:anywhere; }
p { margin:0; max-width:68ch; }
.muted { color:var(--muted); }
a { color:var(--accent); }
"""


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def report(after, before=None):
    fails, notes = verdict(before, after)
    sa = summary(after["results"])
    sb = summary(before["results"]) if before else None
    g = after["gates"]
    parts = [f"<title>Blind-Spot Audit</title>",
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=Saira+Semi+Condensed:wght@700&family=Source+Sans+3:'
             'wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">',
             f"<style>{STYLE}</style><div class=wrap>",
             '<header><div class="eyebrow">Blind-Spot Audit</div>',
             "<h1>" + ("Before and after" if before else "Grader audit")
             + "</h1></header>"]
    ok = not fails
    parts.append(f'<div class="verdict{"" if ok else " bad"}"><b>'
                 + ("All gates pass" if ok else
                    f"{len(fails)} gate{'s' * (len(fails) != 1)} failed")
                 + "</b>")
    if fails:
        parts.append("<ul>" + "".join(f"<li>{esc(f)}</li>"
                                      for f in fails[:40]) + "</ul>")
    parts.append('<p class="muted">Gates: the reference scores full marks'
                 + ("; no example scores higher than before; no harmless "
                    "edit newly flagged; nothing caught before is missed "
                    "or skipped now." if before else ".") + "</p></div>")

    def row(label, a, b=None, unit=""):
        cells = f"<td>{esc(label)}</td>"
        if before:
            cells += f'<td class="n">{esc(b)}{unit}</td>'
        return f"<tr>{cells}<td class=\"n\">{esc(a)}{unit}</td></tr>"
    head = ("<tr><th>Measure</th>" + ("<th style='text-align:right'>Before"
            "</th>" if before else "") + "<th style='text-align:right'>"
            + ("After" if before else "Value") + "</th></tr>")
    outer_a = sa["spots"] + sa["exempt"]
    rows = [row("Spots on the outer skin", outer_a,
                sb and sb["spots"] + sb["exempt"]),
            row("Checked (outside the skipped zones)", sa["spots"],
                sb and sb["spots"]),
            row("Spots skipped near controls",
                pct(sa["exempt_controls"], outer_a),
                sb and pct(sb["exempt_controls"], sb["spots"] + sb["exempt"])),
            row("Spots skipped in the widening strip",
                pct(sa["exempt_strip"], outer_a),
                sb and pct(sb["exempt_strip"], sb["spots"] + sb["exempt"])),
            row("All real edits caught", f"{sa['every_edit']} of "
                f"{sa['spots']}", sb and f"{sb['every_edit']} of "
                f"{sb['spots']}")]
    for e in sa["per_edit"]:
        name = e + (" (false alarm if flagged)" if e in sa["harmless"]
                    else " caught")
        rows.append(row(name, f"{sa['per_edit'][e]} of {sa['spots']}",
                        sb and f"{sb['per_edit'].get(e, 'n/a')} of "
                        f"{sb['spots']}"))
    parts.append("<section><h2>Map</h2><div class=tbl><table>" + head
                 + "".join(rows) + "</table></div>"
                 + f'<p><a href="map.html">Open the 3D map</a>'
                 + (f' &middot; <a href="../{esc(before["dir"].name)}/map.html">'
                    "the earlier map</a>" if before else "") + "</p></section>")

    gb = before["gates"]["captures"] if before else {}
    grow = []
    for name, rec in sorted(g["captures"].items()):
        b = gb.get(name, {}).get("total")
        d = "" if b is None else rec["total"] - b
        cls = "" if d == "" or abs(d) < 1e-3 else (
            ' class="down"' if (d > 0) != (name == "solution") else
            ' class="up"')
        grow.append(f"<tr><td>{esc(name)}</td>"
                    + (f'<td class="n">{esc(b)}</td>' if before else "")
                    + f'<td class="n"{cls}>{rec["total"]}</td></tr>')
    parts.append("<section><h2>Shipped models, full grade</h2>"
                 f"<p class=muted>Out of {g['max_score']}. The reference must "
                 "keep full marks; the examples are broken on purpose, so a "
                 "higher score means a weaker grader.</p><div class=tbl>"
                 "<table><tr><th>Model</th>" + ("<th style='text-align:right'>"
                 "Before</th>" if before else "") + "<th style='text-align:"
                 "right'>" + ("After" if before else "Score") + "</th></tr>"
                 + "".join(grow) + "</table></div></section>")
    if notes:
        parts.append("<section><h2>Other changes</h2><ul>"
                     + "".join(f"<li>{esc(n)}</li>" for n in notes)
                     + "</ul></section>")

    z = after.get("zones")
    if z:
        zr = "".join(
            f"<tr><td>{esc(r['text'])}</td><td class=n>"
            f"{100 * r['area_share']:.0f}%</td></tr>"
            for r in z["rules"].values())
        parts.append(
            "<section><h2>What if the zones were tighter</h2>"
            "<p class=muted>Share of the reference's outer skin the check "
            f"skips, by area ({z['outer_area_mm2']:,} mm&sup2;). The first "
            "row is this grader; the others are geometry only, the reach of "
            "a change, not a measured result.</p><div class=tbl><table>"
            "<tr><th>Zone rule</th><th style='text-align:right'>Skipped"
            f"</th></tr>{zr}</table></div></section>")
    parts.append(f'<p class=muted>Grader {esc(g["harness"])} '
                 f'(sha256 {esc(g["harness_sha256"])}).</p></div>')
    return "\n".join(parts), fails


# -- commands ---------------------------------------------------------------

def cmd_run(args):
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    workers = 4
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    spots_from = None
    if "--spots-from" in args:
        spots_from = args[args.index("--spots-from") + 1]
    imports = []
    if "--import" in args:
        imports = []
        for a in args[args.index("--import") + 1:]:
            if a.startswith("--"):      # the next option ends the list
                break
            imports.append(a)
    n = QUICK if "--quick" in args else FULL

    gpath = out / "gates.json"
    sha = hashlib.sha256(B.HARNESS.read_bytes()).hexdigest()[:12]
    if gpath.exists() and json.loads(gpath.read_text()).get(
            "harness_sha256") == sha:
        print("1/5 shipped models already graded by this grader", flush=True)
    else:
        print("1/5 grading the shipped models", flush=True)
        gpath.write_text(json.dumps(gates(workers), indent=1))
    raw = out / "map_raw.json"
    if not imports:
        print("2/5 grading edits at spots on the outer skin", flush=True)
        run_map(raw, n, workers, spots_from)
        imports = [raw]
    print("3/5 keeping the outer skin", flush=True)
    ref = B.load("solution")
    data, spots = clean(imports, ref)
    (out / "results.json").write_text(json.dumps(data))
    (out / "spots.json").write_text(json.dumps(spots))
    print("4/5 what-if zones", flush=True)
    (out / "zones.json").write_text(json.dumps(what_if(ref, data), indent=1))
    print("5/5 pages", flush=True)
    subprocess.run([sys.executable, str(HERE / "build_blindspot_page.py"),
                    str(out / "map.html"), str(out / "results.json")],
                   check=True, env=os.environ.copy())
    page, fails = report(load_run(out))
    (out / "report.html").write_text(page)
    print(f"wrote {out / 'report.html'}: "
          + ("gates pass" if not fails else f"{len(fails)} gates failed"))
    return 1 if fails else 0


def cmd_compare(args):
    before, after = load_run(args[0]), load_run(args[1])
    if before["results"]["gap_mm"] != after["results"]["gap_mm"]:
        sys.exit("the two runs graded different spots; give the second "
                 "run --spots-from the first")
    page, fails = report(after, before)
    out = after["dir"] / "compare.html"
    out.write_text(page)
    print(f"wrote {out}: " + ("gates pass" if not fails else
                              f"{len(fails)} gates failed"))
    for f in fails[:20]:
        print("  " + f)
    return 1 if fails else 0


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("run", "compare"):
        sys.exit(__doc__)
    cmd = cmd_run if sys.argv[1] == "run" else cmd_compare
    sys.exit(cmd(sys.argv[2:]))


if __name__ == "__main__":
    main()
