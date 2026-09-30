"""Command line and batch runner shared by every SolidWorks harness.

What a harness keeps for itself is measuring and scoring. Everything around
that -- finding the corpus, running one model per child process, reading the
envelope back, printing the table, writing results/ -- is the same work in
every task, and was copied three times before this module existed. The three
copies had already drifted: one raised on a missing path where another warned,
one read the FIRST JSON object on stdout where another read the last and
checked it was an envelope.

A harness declares a `Spec` and calls `cli()`. Nothing here knows what is
being measured; nothing in a harness knows how a batch is run.

    SPEC = HC.Spec(harness_file=__file__, version=HARNESS_VERSION, ...)

    def cli(argv=None):
        HC.cli(SPEC, argv)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from common import harness_base as HB


class Spec:
    """Everything the runner needs that differs between tasks.

    Callables rather than values wherever the harness has to do the work:
    the runner never imports a harness, so measuring, scoring and formatting
    arrive as functions.
    """

    def __init__(self, *, harness_file, version, criteria, short,
                 default_task_dir, default_capture_dir,
                 model_glob, model_noun, discover=None,
                 measure, dump, capture_note, score, summarise, envelope,
                 harness_cli, capture_baseline=None, seed_finder=None,
                 order=(), model_width=34, capture_schema=None,
                 baseline_schema=None, extra_modes=None, extra_usage="",
                 default_timeout=900.0, seed_dir="environment",
                 seed_name=None, skip_models=(),
                 adv_label_trim="adversarial_"):
        self.harness_file = str(Path(harness_file).resolve())
        self.version = version
        #: Criterion names, in the order they appear in the table.
        self.criteria = tuple(criteria)
        #: {criterion: column heading}. Headings are padded to 7 columns.
        self.short = dict(short)
        #: task_dir -> {label: model path}. None uses the standard layout
        #: (solution/, examples/, environment/input.*) via discover_models().
        self.discover = discover or (lambda task_dir:
                                     (discover_corpus(self, task_dir)
                                      if self.seed_name is not None
                                      else discover_models(self, task_dir)))
        #: Corpus layout: the seed's folder and (extension-free) name.
        #: Setting seed_name switches the default discoverer to the corpus
        #: style -- seed_untouched + solution + one folder per examples/<dir>
        #: -- and derives seed_finder from it.
        self.seed_dir = seed_dir
        self.seed_name = seed_name
        #: Substrings of examples/ folder names to skip, with a stderr note.
        self.skip_models = tuple(skip_models)
        #: Folder-name prefix rewritten to "adv_" in batch labels.
        self.adv_label_trim = adv_label_trim
        self.default_task_dir = Path(default_task_dir)
        self.default_capture_dir = Path(default_capture_dir)
        #: Suffix to glob when a directory is not laid out like the task.
        self.model_glob = model_glob
        #: ("part", "parts") / ("assembly", "assemblies") -- help text only.
        self.model_noun = tuple(model_noun)
        #: path|None -> capture
        self.measure = measure
        #: capture -> str, for writing to disk
        self.dump = dump
        #: capture -> one line about what was measured
        self.capture_note = capture_note
        #: capture path -> report
        self.score = score
        #: report -> the readable breakdown for stderr
        self.summarise = summarise
        #: report -> envelope
        self.envelope = envelope
        #: the graded single-model entry point, called last
        self.harness_cli = harness_cli
        #: (seed path, out path) -> None
        self.capture_baseline = capture_baseline
        #: task_dir -> seed path, when --capture-baseline may be given none
        self.seed_finder = seed_finder
        #: labels pinned to the top of the table -- the rows worth reading
        #: before any other, usually the seed and the reference.
        self.order = tuple(order)
        self.model_width = model_width
        self.capture_schema = capture_schema
        self.baseline_schema = baseline_schema
        #: {"--flag": handler(argv_after_flag)} for modes only one task has.
        self.extra_modes = dict(extra_modes or {})
        self.extra_usage = extra_usage
        self.default_timeout = default_timeout
        if self.seed_finder is None and seed_name is not None:
            self.seed_finder = (lambda task_dir: named_model(
                Path(task_dir) / self.seed_dir, self.seed_name,
                glob=self.model_glob))


# --------------------------------------------------------------------------
# collecting models
# --------------------------------------------------------------------------

def nfc(text):
    """Unicode-normalised, casefolded key for filename comparison."""
    import unicodedata
    return unicodedata.normalize("NFC", str(text)).casefold()

def named_model(folder, *stems, glob="*.SLDASM"):
    """The file in `folder` whose stem (or name) matches one of `stems`,
    compared NFC-casefolded.

    Exists because naive globbing breaks on non-ASCII dataset filenames:
    Turkish "İ" sorts after Latin and round-trips through different Unicode
    compositions depending on who saved the file. Earlier `stems` win when
    several match.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    want = [nfc(x) for x in stems]
    hits = {}
    for p in folder.glob(glob):
        key = nfc(p.stem)
        if key in want:
            hits.setdefault(want.index(key), p)
        key = nfc(p.name)
        if key in want:
            hits.setdefault(want.index(key), p)
    return hits[min(hits)] if hits else None

def discover_models(spec, task_dir):
    """{label: path} for a task directory laid out the standard way.

    solution/solution.<ext> is the reference; examples/<name>/ folders are
    the candidates, each graded via its top-level model named after the
    folder (examples/<name>/<name>.<ext> -- flat files in examples/ are
    never candidates); environment/input.<ext> is the "did nothing"
    control.  Any other directory falls back to a recursive glob, so
    pointing the batch at a folder of submissions needs no convention.
    Ordering comes from spec.order via _order().
    """
    task_dir = Path(task_dir)
    glob = spec.model_glob                      # e.g. "*.SLDPRT"
    ext = glob.rsplit(".", 1)[-1]
    found = {}
    ref = task_dir / "solution" / f"solution.{ext}"
    if ref.is_file():
        found["solution"] = ref
    ex = task_dir / "examples"
    if ex.is_dir():
        # Folder-only candidates: a whole example per directory, graded
        # via its top-level model named after the folder itself --
        # examples/<name>/<name>.<ext> (the same convention tools/report.py
        # uses). Matched NFC-casefolded so non-ASCII dataset names resolve.
        # Flat files in examples/ are never candidates.
        for d in sorted(q for q in ex.iterdir() if q.is_dir()):
            q = named_model(d, d.name, glob=glob)
            if q is not None:
                found.setdefault(d.name, q)
    seed = task_dir / "environment" / f"input.{ext}"
    if seed.is_file():
        found["input"] = seed
    if not found:
        for q in sorted(task_dir.rglob(glob)):
            _add(found, q)
    return _order(spec, found)

def discover_corpus(spec, task_dir):
    """{label: path} for a corpus-style task: the seed (inaction control),
    the reference, and one folder-style candidate per examples/<dir>.

    Used automatically when spec.seed_name is set. The inaction control is
    the seed itself, graded against its own frozen baseline: whatever it
    scores is what a candidate gets for opening nothing. Every folder may
    ship many models, so the top-level one is named, not globbed
    (named_model, NFC-casefolded); folder-name prefixes in
    spec.adv_label_trim shorten to "adv_" in the labels, and
    spec.skip_models excludes folders by substring with a stderr note.
    """
    task_dir = Path(task_dir)
    out, missing = {}, []
    seed = spec.seed_finder(task_dir) if spec.seed_finder else None
    if seed is not None:
        out["seed_untouched"] = seed
    else:
        missing.append(f"{spec.seed_dir}/{spec.seed_name} "
                       f"(inaction control)")
    p = named_model(task_dir / "solution", "solution",
                    glob=spec.model_glob)
    if p is not None:
        out["solution"] = p
    else:
        missing.append("solution/solution" + spec.model_glob[1:])
    ex = task_dir / "examples"
    if ex.is_dir():
        for d in sorted(q for q in ex.iterdir() if q.is_dir()):
            label = d.name.replace(spec.adv_label_trim, "adv_")
            if any(skip in d.name for skip in spec.skip_models):
                print(f"[skip] {label} -- see spec.skip_models",
                      file=sys.stderr)
                continue
            q = named_model(d, d.name, glob=spec.model_glob)
            if q is not None:
                out[label] = q
            else:
                missing.append(f"{d.name}/{d.name}{spec.model_glob[1:]}")
    for x in missing:
        print(f"[warn] no top-level assembly for {x}", file=sys.stderr)
    return _order(spec, out)

def _add(models, path):
    label = Path(path).stem
    n, base = 2, label
    while label in models:
        label, n = f"{base}_{n}", n + 1
    models[label] = Path(path)


def _order(spec, models):
    front = [k for k in spec.order if k in models]
    return {k: models[k] for k in front + sorted(set(models) - set(front))}


def collect_models(spec, paths):
    """Models to grade, from files and/or directories.

    A missing path warns instead of raising: in a batch of nine, losing the
    other eight to one typo is the wrong trade.
    """
    models = {}
    for p in paths:
        p = Path(p)
        if p.is_dir():
            found = spec.discover(p)
            if found:
                models.update(found)
            else:
                for q in sorted(p.rglob(spec.model_glob)):
                    _add(models, q)
        elif p.exists():
            _add(models, p)
        else:
            print(f"[warn] no such path: {p}", file=sys.stderr)
    return _order(spec, models)


def capture_family(schema):
    """The task a capture belongs to: everything before the version."""
    return str(schema or "").split("/")[0] or None


def collect_captures(spec, paths):
    """Captures to score, from files and/or directories.

    A CAPTURE FROM ANOTHER TASK IS SKIPPED, NOT GRADED. `results/` is
    relative to the working directory, so running several tasks from one
    folder piles their captures into one place, and this used to glob all
    of them: eight rake captures scored by the pipe harness came out as
    eight models at 0.000, which reads exactly like eight failing
    candidates and is what somebody copies into a README.

    The family is the part of the schema before the version. A capture of
    the same family but an older version IS still scored, with a note --
    re-scoring old captures to see what a change did is the reason
    `--score-from` exists, and refusing them would take that away. What is
    refused is a capture of a different task, which can only be noise.
    """
    want = capture_family(spec.capture_schema)
    caps, wrong, old_ver = {}, [], []
    for p in paths:
        p = Path(p)
        for q in (sorted(p.glob("*.json")) if p.is_dir() else [p]):
            if not q.is_file():
                continue
            got = None
            if want:
                try:
                    got = json.loads(q.read_text(encoding="utf-8")).get("schema")
                except Exception:                               # noqa: BLE001
                    got = None
                if capture_family(got) and capture_family(got) != want:
                    wrong.append((q.name, got))
                    continue
                if got and got != spec.capture_schema:
                    old_ver.append((q.name, got))
            _add(caps, q)
    if wrong:
        print(f"skipped {len(wrong)} capture(s) belonging to another task "
              f"(this harness reads {spec.capture_schema}):", file=sys.stderr)
        for name, got in wrong[:10]:
            print(f"  {name}: {got}", file=sys.stderr)
    if old_ver:
        print(f"{len(old_ver)} capture(s) written by an earlier version of "
              f"this harness; scored as they are, and anything measured "
              f"since will read as absent:", file=sys.stderr)
        for name, got in old_ver[:10]:
            print(f"  {name}: {got} (now {spec.capture_schema})",
                  file=sys.stderr)
    return _order(spec, caps)


# --------------------------------------------------------------------------
# running one model per child process
# --------------------------------------------------------------------------

def read_envelope(stdout):
    """The last complete JSON object on stdout that looks like an envelope.

    Brace-matched rather than "parse from the first {": a harness may print
    other JSON alongside, and json.loads on a prefix of the stream happens to
    succeed often enough to be dangerous.
    """
    depth, start = 0, None
    for i, ch in enumerate(stdout):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(stdout[start:i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and "subscores" in obj:
                    return obj
    return None


def _run_child(cmd, out_dir, label, timeout, report_path=None,
               capture_path=None):
    """One model in its own process.

    Its own process because a COM session that fell over on one model is not
    fit to measure the next, and because a hung SolidWorks has to be killable
    without taking the batch with it.
    """
    env = dict(os.environ)
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        env["HARNESS_REPORT_JSON"] = str(report_path)
    if capture_path:
        Path(capture_path).parent.mkdir(parents=True, exist_ok=True)
        env["HARNESS_CAPTURE_JSON"] = str(capture_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / f"{label}.log"
    t0 = time.time()
    # stderr goes STRAIGHT to the log rather than into a pipe we read at the
    # end: a run that has to be killed is exactly the one whose progress
    # lines matter, and capture_output threw them away with the process.
    try:
        with open(log, "wb") as fh:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=fh,
                                    env=env)
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return {"label": label, "ok": False, "killed": True,
                        "elapsed": time.time() - t0,
                        "error": f"timeout >{timeout:g}s (see {log.name})"}
    except Exception as exc:                                    # noqa: BLE001
        return {"label": label, "ok": False, "elapsed": time.time() - t0,
                "error": f"{type(exc).__name__}: {exc}"}
    stderr = log.read_text(encoding="utf-8", errors="replace")
    return {"label": label, "elapsed": time.time() - t0, "rc": proc.returncode,
            "stdout": out.decode("utf-8", "replace"),
            "stderr": stderr, "ok": True}


def batch_capture(spec, models, out_dir, cap_dir, timeout):
    results = []
    cap_dir.mkdir(parents=True, exist_ok=True)
    for i, (label, path) in enumerate(models.items(), 1):
        print(f"  [{i}/{len(models)}] {label} ... ", end="", flush=True)
        dest = cap_dir / f"{label}.json"
        cmd = [sys.executable, spec.harness_file, "--capture-only",
               str(Path(path).resolve()), "-o", str(dest)]
        res = _run_child(cmd, out_dir, label, timeout)
        if res["ok"] and res["rc"] == 0 and dest.is_file():
            cap = json.loads(dest.read_text(encoding="utf-8"))
            print(f"{spec.capture_note(cap)}  ({res['elapsed']:.0f}s)")
        else:
            res["ok"] = False
            res.setdefault("error", f"rc={res.get('rc')}")
            print(f"ERROR: {res['error']}")
        results.append(res)
    return results


def _drop(paths):
    """Remove outputs of a model this run failed on.

    The summary says ERROR, but a stale envelope and report from an earlier
    run stay readable beside it, and nothing in them says which run they
    came from. One results/ directory, one run.
    """
    for p in paths:
        try:
            Path(p).unlink()
        except OSError:
            pass


def batch_grade(spec, models, out_dir, cap_dir, timeout, score=False,
                keep_going=False):
    """models maps label -> model path, or label -> capture when score.

    Grading keeps the capture. Measuring a corpus costs an hour of CAD and
    used to leave nothing behind to re-score, so every threshold change
    meant measuring it again.
    """
    results = []
    for i, (label, path) in enumerate(models.items(), 1):
        print(f"  [{i}/{len(models)}] {label} ... ", end="", flush=True)
        flag = ["--score-from"] if score else []
        cmd = [sys.executable, spec.harness_file] + flag + \
              [str(Path(path).resolve())]
        res = _run_child(cmd, out_dir, label, timeout,
                         report_path=out_dir / "full" / f"{label}.report.json",
                         capture_path=(None if score else
                                       cap_dir / f"{label}.json"))
        stale = [out_dir / f"{label}.envelope.json",
                 out_dir / "full" / f"{label}.report.json"]
        if not res["ok"]:
            print(f"ERROR: {res['error']}")
            _drop(stale)
            results.append(res)
            # Killing the child does not close the CAD session: it is left
            # holding an assembly, possibly part way through a rebuild, and
            # every model measured after that shares it. Those measurements
            # come back looking perfectly ordinary. Stop instead, unless
            # told otherwise.
            if res.get("killed") and not keep_going:
                print(f"\n  stopping: {label} had to be killed, and the CAD "
                      f"session it leaves behind is not fit to measure the "
                      f"rest.\n  Restart it, then re-run -- captures already "
                      f"taken are kept. --keep-going overrides.")
                break
            continue
        env = read_envelope(res["stdout"])
        if env is None:
            res["ok"] = False
            res["error"] = f"no JSON envelope (rc={res['rc']})"
            print(f"ERROR: {res['error']}")
            _drop(stale)
            for line in (res["stderr"] or res["stdout"]).strip().splitlines()[-8:]:
                print(f"        | {line}")
            results.append(res)
            continue
        (out_dir / f"{label}.envelope.json").write_text(
            json.dumps(env, indent=1), encoding="utf-8")
        res["envelope"] = env
        print(f"{env['score']}/{env['max_score']}  ({res['elapsed']:.0f}s)")
        results.append(res)
    return results


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def _row(spec, res):
    env = res["envelope"]
    subs = env.get("subscores", {})
    row = {"model": res["label"]}
    for c in spec.criteria:
        row[spec.short[c]] = subs.get(c)
    row["score"] = env.get("score")
    row["max"] = env.get("max_score")
    row["pct"] = (100.0 * env["score"] / env["max_score"]
                  if env.get("max_score") else None)
    row["passed"] = env.get("passed")
    row["sec"] = round(res["elapsed"], 1)
    return row


def print_batch_table(spec, results):
    w = spec.model_width
    hdr = (f"{'model':<{w}}" + "".join(f"{spec.short[c]:>7}"
                                       for c in spec.criteria)
           + f"{'score':>8}{'max':>6}{'pct':>7}{'sec':>7}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for res in results:
        if not res.get("envelope"):
            print(f"{res['label']:<{w}}  ERROR: {res.get('error')}")
            continue
        r = _row(spec, res)
        cells = "".join(
            (f"{r[spec.short[c]]:>7.2f}"
             if isinstance(r[spec.short[c]], (int, float)) else f"{'-':>7}")
            for c in spec.criteria)
        print(f"{r['model']:<{w}}{cells}{r['score']:>8.3f}{r['max']:>6}"
              f"{r['pct']:>6.1f}%{r['sec']:>7.1f}")
    scored = [r for r in results if r.get("envelope")]
    if scored:
        totals = {round(r["envelope"]["score"], 4) for r in scored}
        print(f"\n{len(totals)} distinct total(s) across {len(scored)} "
              f"graded model(s)")


def write_batch_summary(spec, out_dir, results):
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = [spec.short[c] for c in spec.criteria]
    with open(out_dir / "summary.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model"] + cols + ["score", "max_score", "pct_of_max",
                                       "passed", "seconds", "error"])
        for res in results:
            if not res.get("envelope"):
                w.writerow([res["label"]] + [""] * len(cols)
                           + ["", "", "", "", round(res.get("elapsed", 0), 1),
                              res.get("error")])
                continue
            r = _row(spec, res)
            w.writerow([r["model"]] + [r[c] for c in cols]
                       + [r["score"], r["max"], round(r["pct"], 1),
                          r["passed"], r["sec"], ""])

    lines = [f"# Batch results -- harness {spec.version}", "",
             "| model | " + " | ".join(cols)
             + " | score | max | pct | passed |",
             "|" + "---|" * (len(cols) + 5)]
    for res in results:
        if not res.get("envelope"):
            lines.append(f"| {res['label']} | " + " | ".join(["-"] * len(cols))
                         + f" | ERROR | | | {res.get('error')} |")
            continue
        r = _row(spec, res)
        cells = " | ".join(
            (f"{r[c]:.2f}" if isinstance(r[c], (int, float)) else "-")
            for c in cols)
        lines.append(f"| {r['model']} | {cells} | {r['score']:.3f} | "
                     f"{r['max']} | {r['pct']:.1f}% | {r['passed']} |")
    lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {out_dir / 'summary.md'}")
    print(f"wrote {out_dir / 'summary.csv'}")


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

def model_budget(spec, asked):
    """How long one model may take, and why that number.

    TWO DEADLINES USED TO SIT SIDE BY SIDE WITH NOTHING COMPARING THEM,
    and they crossed. `default_timeout` is how long MEASURING one model
    takes -- it was calibrated before any task asked a judge anything.
    `judge_runner.JUDGE_TIMEOUT_S` is how long one judgement may take. The
    child does both, so its budget has to hold both; task 24 had 600 for
    the first and 600 for the second, and five of its seven models were
    killed at the exact moment the judgement was still allowed to run.

    Nothing had said they were related, which is the same shape as the
    weights that lived in two files: one number derived from the other
    cannot disagree with itself. So the judge's deadline is ADDED, and
    only when a judgement will actually be asked for -- a task with no
    judge, or a run with --no-judge, keeps the budget it always had.

    An explicit --timeout is obeyed exactly. Somebody who names a number
    means it.
    """
    if asked is not None:
        return float(asked), "given with --timeout"
    base = float(spec.default_timeout)
    from common import judge_runner as JR
    st = JR.stance(sys.modules.get("__main__"))
    if not st.get("judged"):
        return base, "measuring only; this task asks no judgement"
    if not JR.asking_enabled():
        return base, "measuring only; --no-judge is set"
    return base + JR.JUDGE_TIMEOUT_S, (
        f"{base:g}s to measure plus {JR.JUDGE_TIMEOUT_S:g}s for the "
        f"judgement, which happens inside the same child")


def do_batch(spec, argv):
    one, many = spec.model_noun
    ap = argparse.ArgumentParser(
        prog="harness.py --batch",
        description=f"Grade several {many} in one run and tabulate them.")
    ap.add_argument("paths", nargs="*",
                    help=f"{many} and/or directories. A directory laid out "
                         f"like the shipped task (solution/, examples/) is "
                         f"expanded with the usual labels; any other "
                         f"directory is globbed recursively.")
    ap.add_argument("--out", type=Path, default=Path("results"),
                    help="output directory (default ./results)")
    ap.add_argument("--only", default=None,
                    help="comma-separated substrings; keep matching labels")
    ap.add_argument("--timeout", type=float, default=None,
                    help=f"seconds per {one} (default {spec.default_timeout:g}, "
                         f"plus the judge's own deadline where a judgement "
                         f"is asked for)")
    ap.add_argument("--capture-only", action="store_true",
                    help="measure and store captures, do not score. "
                         "Needs SolidWorks.")
    ap.add_argument("--keep-going", action="store_true",
                    help="carry on after a model has to be killed. Off by "
                         "default: the CAD session it leaves is shared.")
    ap.add_argument("--score-from", type=Path, default=None,
                    help="score stored captures from this directory instead "
                         "of measuring. Needs no SolidWorks.")
    args = ap.parse_args(argv)

    cap_dir = (args.score_from or (args.out / "captures")).resolve()
    if args.score_from:
        models = collect_captures(spec, args.paths or [cap_dir])
    else:
        models = collect_models(spec, args.paths or [spec.default_task_dir])

    if args.only:
        want = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        models = {k: v for k, v in models.items()
                  if any(w in k.lower() for w in want)}
    if not models:
        raise SystemExit("no models selected")

    budget, why_budget = model_budget(spec, args.timeout)

    mode = ("capture" if args.capture_only
            else "score" if args.score_from else "grade")
    print(f"harness  : {spec.version}  [{mode}]")
    print(f"models   : {len(models)} -> {', '.join(models)}")
    print(f"out      : {args.out.resolve()}")
    if mode != "score":
        print(f"budget   : {budget:g}s per {one}  ({why_budget})")
    if mode != "score":
        print(f"\nSolidWorks must be RUNNING. Each {one} is opened in the "
              f"live session\nand closed again -- save your work first.\n")

    out_dir = args.out.resolve()
    if mode == "capture":
        results = batch_capture(spec, models, out_dir, cap_dir, budget)
        n = sum(1 for r in results if r["ok"])
        print(f"\n{n}/{len(results)} capture(s) under {cap_dir}. Score them "
              f"any time, without SolidWorks:\n  python harness.py --batch "
              f"--score-from {cap_dir}")
        raise SystemExit(0 if n == len(results) else 1)

    results = batch_grade(spec, models, out_dir, cap_dir, budget,
                          score=(mode == "score"),
                          keep_going=args.keep_going)
    print_batch_table(spec, results)
    write_batch_summary(spec, out_dir, results)
    if mode == "grade" and any(r.get("envelope") for r in results):
        print(f"\ncaptures kept under {cap_dir} -- rescore without "
              f"SolidWorks:\n  python harness.py --batch --score-from "
              f"{cap_dir}")
    raise SystemExit(0 if all(r.get("envelope") for r in results) else 1)


def out_flag(argv, default=None):
    """Pull -o/--out out of a raw argv; returns (value, remaining)."""
    out, rest, i = default, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("-o", "--out") and i + 1 < len(argv):
            out, i = argv[i + 1], i + 2
            continue
        if a.startswith("--out="):
            out, i = a.split("=", 1)[1], i + 1
            continue
        rest.append(a)
        i += 1
    return out, rest


def do_capture_only(spec, argv):
    """Measure one model and write the capture to disk. Needs SolidWorks."""
    out, rest = out_flag(argv)
    path = rest[0] if rest else None
    cap = spec.measure(path)
    out = Path(out) if out else Path(
        (Path(path).stem if path else "active") + ".capture.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(spec.dump(cap), encoding="utf-8")
    print(f"wrote {out} ({spec.capture_note(cap)}, "
          f"schema {cap.get('schema')})", file=sys.stderr)
    raise SystemExit(0)


def check_capture_schema(spec, path):
    """Refuse another task's capture, and say so about an older one.

    THE BATCH FORM HAS CHECKED THIS SINCE THE RAKE CAPTURES WERE SCORED BY
    THE PIPE HARNESS; the single-file form did not, and that is the more
    dangerous of the two because it answers with one number and no table
    to look wrong. Task 28's captures predate the commit that gave its
    mate records their roles, and scored by the harness that reads those
    roles the model named for dangling mates comes back 10.0/10 PASSED --
    a second reference, produced from a capture that simply does not
    carry the field the criterion reads. Nothing in the output said so.

    Same rule as the batch form: a different family is refused, an older
    version of the same family is scored with the warning that anything
    measured since will read as absent.
    """
    want = capture_family(spec.capture_schema)
    if not want:
        return
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8")).get("schema")
    except Exception:                                           # noqa: BLE001
        return
    if capture_family(got) and capture_family(got) != want:
        raise SystemExit(
            f"{Path(path).name} is a capture of another task: it says "
            f"{got!r} and this harness reads {spec.capture_schema!r}.")
    if got and got != spec.capture_schema:
        print(f"{Path(path).name} was written by an earlier version of this "
              f"harness ({got}, now {spec.capture_schema}); scored as it is, "
              f"and anything measured since will read as ABSENT rather than "
              f"as failing. Re-run --batch before trusting this number.",
              file=sys.stderr)


def do_score_from(spec, argv):
    """Grade a stored capture. No SolidWorks, runs anywhere."""
    out, rest = out_flag(argv)
    if not rest:
        raise SystemExit("Usage: harness.py --score-from capture.json "
                         "[-o report.json]")
    # A whole directory of captures is the batch form. Without this the
    # open below fails with "Permission denied: results\\captures", which
    # names an access problem that is not there and hides the missing flag.
    if Path(rest[0]).is_dir():
        raise SystemExit(
            f"{rest[0]} is a directory of captures, and this mode scores one "
            f"file.\nDid you mean:\n  harness.py --batch --score-from "
            f"{rest[0]}")
    check_capture_schema(spec, rest[0])
    report = spec.score(rest[0])
    for dest in (out, os.environ.get("HARNESS_REPORT_JSON")):
        if not dest:
            continue
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(json.dumps(report, indent=1, default=str),
                              encoding="utf-8")
    print(spec.summarise(report), file=sys.stderr)
    envelope = spec.envelope(report)
    print(json.dumps(envelope, indent=1))
    # Harbor reads a reward FILE and never stdout. No-op off a verifier.
    written = HB.write_reward(envelope)
    if written:
        print(f"reward written to {written}", file=sys.stderr)
    raise SystemExit(0)


def usage(spec):
    one, many = spec.model_noun
    suffix = spec.model_glob.lstrip("*.")
    schemas = ""
    if spec.capture_schema:
        schemas = (f"\n  (capture schema {spec.capture_schema}, baseline "
                   f"schema {spec.baseline_schema})")
    text = f"""harness.py -- grading harness {spec.version}{schemas}

One {one} -- the graded contract, exactly one JSON envelope on stdout:
  harness.py                          grade the document already open
  harness.py MODEL.{suffix}             grade one {one}

Measure and score separately (calibration and regression work):
  harness.py --capture-only MODEL.{suffix} [-o cap.json]
  harness.py --score-from cap.json [-o report.json]

Many {many} -- table, per-model envelopes, logs and full reports on disk:
  harness.py --batch A.{suffix} B.{suffix} ...     an explicit list
  harness.py --batch path/to/task_dir          a directory of {many}
  harness.py --batch                           the shipped task directory
  harness.py --batch DIR --capture-only        measure only, store captures
  harness.py --batch --score-from results/captures    no SolidWorks needed
  options: --out DIR  --only substr,substr  --timeout SECONDS

The questions a measurement cannot settle -- every task answers, including
the ones that use no judgement at all:
  harness.py --judge                  what this task judges, or why it does not
  options on any mode above:
    --no-judge                        do not ask. The judged criterion falls
                                      back to what the geometry alone can
                                      say, so the task grades as it did
                                      before that criterion existed
    --no-images                       ask, but without showing a render

Both act where the QUESTION is put, which is at capture time. Scoring stays
a pure function of the capture: a stored verdict is read from the capture it
was recorded in, and no flag on a --score-from run can talk it out of that.
"""
    if spec.capture_baseline:
        arg = f"[SEED.{suffix}]" if spec.seed_finder else f"SEED.{suffix}"
        text += (f"\nRe-freeze the seed baseline (prompt/input.json):\n"
                 f"  harness.py --capture-baseline {arg} [out.json]\n")
    return text + spec.extra_usage


def _capture_baseline(spec, argv):
    path = argv[0] if argv else None
    if not path and spec.seed_finder:
        path = spec.seed_finder(spec.default_task_dir)
        if path is None:
            raise SystemExit("no seed model found; pass one explicitly")
        print(f"seed     : {path}")
    spec.capture_baseline(path, argv[1] if len(argv) > 1 else None)
    raise SystemExit(0)


def do_judge(spec):
    """`--judge`: what this task does about the questions measurement cannot
    answer, and whether this machine could ask one.

    EVERY TASK ANSWERS, which is why this mode exists on all of them and
    not only on the few that use a judge. Someone opening the nineteenth
    harness should be told "no judgement here, and here is why" rather
    than be left to conclude it from an absence.

    The harness is read off `__main__`. That is not a trick: `harness.py`
    IS the program being run -- the runner is a library it calls -- so the
    module is already in memory, and reading it here keeps the rule that
    this file never imports a harness of its own accord.
    """
    from common import judge_runner as JR
    harness = sys.modules.get("__main__")
    st = JR.stance(harness)
    # parents[3]: harness/ -> task/ -> tests/ -> the task folder. Counted
    # down from the harness for the same reason DEFAULT_TASK_DIR is:
    # this file has moved once already, and a wrong count here printed
    # "tests" as the name of the task.
    print(f"task     : {Path(spec.harness_file).parents[3].name}")
    print(f"harness  : {spec.version}")

    if not st["declared"]:
        print("\njudge    : UNDECLARED -- this harness says nothing either "
              "way.\n           A harness declares JUDGE_CRITERIA (with a "
              "question) or\n           JUDGE_NOTE (with a reason). Neither "
              "is present, so this\n           is an oversight rather than a "
              "decision.")
        raise SystemExit(1)

    if not st["judged"]:
        print("\njudge    : none on this task, and that is deliberate")
        print(f"\n{_wrap(st['note'], 4)}")
        return

    print(f"\njudge    : {len(st['criteria'])} criterion(s), "
          f"question version {st['version']!r}")
    # No weight printed beside the question, and deliberately not: the
    # judge's criterion string is the one the MODEL is asked about, and
    # the rubric's is the one that carries the weight. On task 30 they
    # differ by an article. Matching them up by text would be a guess that
    # looks like a fact, so the weight is stated in the note, where it can
    # be said in words that are checked by a human rather than by a
    # string comparison.
    for name, description in st["criteria"].items():
        print(f"\n  {name}")
        print(_wrap(str(description), 4))
    if st["note"]:
        print(f"\n{_wrap(st['note'], 4)}")

    # Only now the machine, because whether a task WANTS a judgement is a
    # property of the task, and whether this box can obtain one is not.
    state, creds = JR.available(), JR.credentials()
    print(f"\nimportable       : {state['ok']}"
          f"{'' if state['ok'] else '  -- ' + str(state['why'])}")
    print(f"credential route : {creds['route']}  "
          f"(azure={creds['azure']})")
    print(f"asking           : {JR.asking_enabled()}"
          f"    (--no-judge turns this off, at CAPTURE time)")
    print(f"pictures         : {JR.looking_enabled()}"
          f"    (--no-images turns these off)")
    if not JR.asking_enabled() or creds["route"] is None:
        print("\nNothing would be asked on this run. The judged criterion "
              "falls back to\nwhat the geometry alone can say, and the "
              "capture records the reason.")


def _wrap(text, indent, width=72):
    """One paragraph, wrapped, at a fixed indent. Reasons are prose."""
    import textwrap
    pad = " " * indent
    return "\n".join(textwrap.wrap(" ".join(str(text).split()),
                                   width=width - indent,
                                   initial_indent=pad, subsequent_indent=pad))


def cli(spec, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Taken out BEFORE any mode parses, because it applies to all of them
    # and each mode has its own parser that would reject an unknown flag.
    if "--no-images" in argv:
        argv = [a for a in argv if a != "--no-images"]
        from common import judge_runner as _JR
        _JR.set_looking(False)
    if "--no-judge" in argv:
        argv = [a for a in argv if a != "--no-judge"]
        from common import judge_runner as _JR
        _JR.set_asking(False)
    if argv and argv[0] == "--judge":
        do_judge(spec)
        raise SystemExit(0)
    if argv and argv[0] in ("-h", "--help"):
        print(usage(spec))
        raise SystemExit(0)
    if argv and argv[0] in spec.extra_modes:
        spec.extra_modes[argv[0]](argv[1:])
        raise SystemExit(0)
    if argv and argv[0] == "--capture-baseline" and spec.capture_baseline:
        _capture_baseline(spec, argv[1:])
    if argv and argv[0] == "--batch":
        do_batch(spec, argv[1:])
    if argv and argv[0] == "--capture-only":
        do_capture_only(spec, argv[1:])
    if argv and argv[0] == "--score-from":
        do_score_from(spec, argv[1:])
    # Several paths, or a directory, is unambiguously a batch: the graded
    # single-model contract takes exactly one file. Counted by what exists on
    # disk, because the base class's own form is `harness.py candidate
    # [timeout_s]` and a timeout must not be read as a second model.
    if not argv or argv[0] != "--_run":
        paths = [a for a in argv
                 if not a.startswith("-") and Path(a).exists()]
        if len(paths) > 1 or (len(paths) == 1 and Path(paths[0]).is_dir()):
            do_batch(spec, argv)
    spec.harness_cli()


def main(spec, argv=None):
    """The harness file's __main__: UTF-8 streams, then the flag runner.

    Wide characters reach stdout/stderr on every task (Turkish filenames,
    the ⌀ in evidence strings), and a Windows console default of cp1252
    would otherwise take the whole grade down with one print.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:                                           # noqa: BLE001
        pass
    cli(spec, argv)
