"""Running a `llm_judge.Judge` from a harness without betting the run on it.

WHY THIS EXISTS. `common/llm_judge.Judge` is the contract -- the prompt, the
untrusted-data fence around the candidate, the Pydantic schema, the
criterion names. None of that is re-implemented here and none of it should
be. What it does not do is survive contact with a grading run, in three
ways that are the harness's problem rather than the judge's:

  IMPORT. `llm_judge` imports `call_llm` imports `call_gpt` imports
  `openai`, all at module level, so naming the judge anywhere in
  a harness makes that harness unimportable on a machine without the SDK.
  That is every machine that runs `--score-from`, which exists precisely
  so a stored capture can be re-scored without the heavy environment.
  Here the import happens inside the call, and its absence is a recorded
  fact.

  RAISING. `Judge.judge` raises on an empty reply, on JSON that will not
  validate, and on a missing criterion. Those are the right answers for a
  library and the wrong ones for a grader: a model that times out must not
  turn a candidate's grade into a stack trace. Each becomes a record here.

  REPEATABILITY. A grader that answers differently on the same file twice
  is not a grader. Answers are cached by prompt and model, so a re-run of
  the same capture costs nothing and returns the same words -- and the
  answer belongs in the CAPTURE, taken once at measure time, leaving
  scoring a pure function of the JSON like every geometric reading.

Nothing here decides what a judgement is worth. It returns the scores and
the reasons; weight is the harness's business, and zero is the honest
first setting.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import threading
import traceback
from pathlib import Path


#: Pictures are ON by default: a judge asked to place ribs from a list of
#: coordinates is being asked to do in prose what an eye does at a glance.
#: `--no-images` on any harness turns them off for that run, and
#: HARNESS_NO_IMAGES=1 does the same where there is no command line to put
#: a flag on -- a verifier container invokes the harness itself.
_LOOKING = None


def looking_enabled() -> bool:
    if _LOOKING is not None:
        return _LOOKING
    return (os.environ.get("HARNESS_NO_IMAGES", "").strip().lower()
            not in ("1", "true", "yes", "on"))


def set_looking(on: bool) -> None:
    """Set by the CLI when it sees `--no-images`. One switch, asked twice:
    once by whatever renders, once by whatever would hand the render over."""
    global _LOOKING
    _LOOKING = bool(on)


#: A SEPARATE SWITCH FROM THE PICTURES, because they are separate
#: questions. `--no-images` keeps the judge and takes away its eyes;
#: `--no-judge` does not ask at all. A task whose judged criterion falls
#: back to a measurement grades the same as it did before that criterion
#: existed -- which is the property that makes the switch safe to offer.
_ASKING = None


def asking_enabled() -> bool:
    if _ASKING is not None:
        return _ASKING
    return (os.environ.get("HARNESS_NO_JUDGE", "").strip().lower()
            not in ("1", "true", "yes", "on"))


def set_asking(on: bool) -> None:
    """Set by the CLI when it sees `--no-judge`."""
    global _ASKING
    _ASKING = bool(on)


def credentials() -> dict:
    """Whether this machine can reach the judge model, and why not.

    THE ROUTE DECIDES WHICH KEYS MATTER, so this asks `judge_route` and
    does not decide anything itself. It checked call_claude's names while
    the judge was calling GPT for one commit, and would have reported a
    healthy route over a deployment that was never set; the fix is not a
    better check here but one place that knows which judge is live.

    `azure` is kept because callers read it, and `judge` is new: the one
    line saying WHICH model this route would ask, so a report can name it
    without re-deriving it.
    """
    from common import judge_route as JRT

    out = JRT.credentials()
    return {"azure": out["route"] is not None,
            "route": out["route"],
            "judge": out["judge"],
            "asked": out["asked"],
            "why_no_route": out["why_no_route"]}

def _anonymous_names(images):
    """Names for the pictures that say nothing, numbered in the order
    they were given.

    THE FILENAME IS PART OF THE PROMPT, and that is not a theory.
    `call_claude.attach_files` keeps each file's basename and
    `attached_files_note` lists those names in the text that is sent, so a
    render written as `adversarial_piping_toward_wrong_outlet_hole.iso.png`
    hands the judge the answer before it opens anything. One did exactly
    that on task 20, writing into its own reasoning: "consistent with the
    case's own naming as routing toward a wrong outlet". Every harness in
    this repository names its renders after the model, so every judged
    task was leaking its corpus labels the same way.

    Grading here is geometry-only and reads no names -- not a file name,
    not a component name, not a feature name. A judge is grading. The rule
    is the same rule.

    Numbered rather than described, because the ORDER is what the prompts
    already lean on ("the first picture is the machine before anything was
    added"), and a role in the name would be one more thing to keep in
    step with the text.
    """
    #: No copy any more: the pictures ride in the request as image
    #: blocks, so only the NAMES reach the prompt. The rule above is
    #: unchanged -- it was always about the name, not the file.
    return [f"picture-{i}{Path(src).suffix.lower() or '.png'}"
            for i, src in enumerate(images, 1)]


def _sdk_call(prompt, *, model=None, images=(), log=None, timeout=None):
    """One question to the model, through the Anthropic SDK on Azure.

    WHY THERE IS NO SUBPROCESS HERE ANY MORE. This used to run the bundled
    `claude` CLI itself, because the agent SDK closed the CLI's stdin as
    soon as it had written the prompt: a question answered in five seconds
    survived that, one the model thought about for thirty died with no
    stderr and exit code 1. Running the CLI directly fixed it and cost a
    day to find.

    Both halves of that problem left with Bedrock. A judge question is now
    a single `messages.create`, so there is no CLI to locate, no stdin to
    close, no `--max-turns` budget to get wrong, and no stream-json to
    parse for the one line that held the real error text. The turn budgets
    that used to live above this function went with it: they existed
    because the CLI cut an answer off mid-JSON, and a single call cannot.

    Pictures ride in the request as image blocks rather than being copied
    into a scratch directory for a `Read` tool. `_anonymous_names` still
    governs what they are CALLED in the prompt -- that was never about the
    file, only about not handing the judge its answer in a filename.
    """
    #: WHICH MODEL ANSWERS IS `judge_route`'s DECISION, not this
    #: function's. `model` is carried for the cache key and the record;
    #: on the claude route it also selects the model, and on the gpt
    #: route it cannot, because that route's model is its deployment.
    #: judge_route.call refuses a model it would have to drop rather
    #: than dropping it quietly.
    from common import judge_route as JRT

    lines = []
    images = [str(i) for i in images]
    if images:
        names = _anonymous_names(images)
        prompt = (prompt + chr(10) + chr(10)
                  + "The pictures are attached to this message, in "
                  + "this order: " + ", ".join(names) + ".")

    asked = JRT.describe()
    lines.append(f"[judge] {asked}")
    text = JRT.call(prompt, images=images,
                    model=model if JRT.route() == JRT.CLAUDE else None)
    lines.append(f"[result] chars={len(text)} images={len(images)} "
                 f"via {asked}")
    if log is not None:
        for line in lines:
            log(line)
    return text, lines

def _retrying_call(model=None, images=(), attempts=None,
                   record=None):
    """A `Judge.CALL` that tries more than once and says what happened.

    Each attempt is recorded -- its error and the CLI's stderr -- rather
    than only the last, because "failed five times the same way" and
    "failed once on a timeout and four times on a quota" are different
    problems wearing the same message.
    """
    import time
    attempts = attempts or JUDGE_ATTEMPTS

    def call(prompt):
        last = None
        for i in range(attempts):
            try:
                text, lines = _sdk_call(prompt, model=model,
                                        images=images,
                                        timeout=JUDGE_TIMEOUT_S)
                if record is not None:
                    record.append({"attempt": i + 1, "ok": bool(text.strip()),
                                   "stderr": lines})
                if text.strip():
                    return text
                last = RuntimeError("the model returned an empty reply")
            except Exception as exc:                        # noqa: BLE001
                last = exc
                if record is not None:
                    record.append({"attempt": i + 1, "ok": False,
                                   "error": f"{type(exc).__name__}: {exc}",
                                   "stderr": locals().get("lines") or []})
                #: The transport already gave up for a reason that another
                #: attempt cannot touch -- a deployment that is not there,
                #: a key that is not valid, a proxy scheme httpx will not
                #: take. Repeating it here multiplies the wait by three.
                #: Still call_claude's, and deliberately: it matches on the
                #: exception's TEXT ("deployment does not exist", a bad key,
                #: a proxy scheme httpx refuses), which is the same text
                #: from either Azure route. It is a provider-agnostic
                #: matcher that happens to live in the Claude module.
                from common.call_claude import is_configuration_error
                if is_configuration_error(exc):
                    raise exc
            if i + 1 < attempts:
                time.sleep(JUDGE_BACKOFF_S)
        raise last or RuntimeError("the judge was never asked")

    return call





#: How long one question may take before the run gives up on it. The SDK
#: has retries and no deadline, so a `claude` process that answers nothing
#: blocks the grading run for as long as it feels like -- which on this
#: corpus was forever, waiting inside `receive_messages()` for a message
#: that never came.
#: RAISED FROM 240 ON MEASUREMENT, not on a feeling. Task 30's slowest
#: answer took 127 s and 240 looked generous. Task 24 asks a harder
#: question -- where does this box come apart, from eighteen bodies and a
#: picture -- and of its seven models two answered, at 219 s and 132 s,
#: while five were cut off at the deadline. A deadline that the work
#: reaches is not a safety net, it is the thing producing the failures.
#:
#: It is still a deadline: the reason this exists is that a `claude`
#: process which answers nothing otherwise blocks a grading run for as
#: long as it likes, which on this corpus was forever.
JUDGE_TIMEOUT_S = float(os.environ.get("HARNESS_JUDGE_TIMEOUT_S") or 600.0)


def _with_deadline(fn, seconds):
    """Run `fn` on a worker and give up on it after `seconds`.

    A thread rather than a signal: signals only fire on the main thread of
    the main interpreter and do not interrupt a blocking read on Windows,
    which is where this happens.

    THE WORKER IS NOT KILLED, because Python cannot kill a thread stuck in
    a blocking read. It is a daemon, so it cannot hold the process open at
    exit, and the subprocess it is waiting on dies with the process. The
    cost of a timeout is therefore one stuck `claude` until the run ends;
    the alternative was the run never ending at all.
    """
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:                        # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True,
                         name="judge-call")
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(
            f"the judge did not answer within {seconds:.0f}s; the call was "
            f"abandoned and the model was left ungraded on this criterion")
    if "error" in box:
        raise box["error"]
    return box.get("value")


#: How many times one question is asked before the run gives up on it, and
#: how long it waits between tries. Ours rather than the SDK's, because a
#: grader needs to know WHICH attempt failed and why, and the transport's
#: own retry keeps only the last error.
JUDGE_ATTEMPTS = 3
JUDGE_BACKOFF_S = 8.0

#: Turns allowed when there is a picture to open: enough to read it and
#: answer, not enough to wander.
#:
#: RAISED FROM 6 ON MEASUREMENT. Task 24 asks where an enclosure comes
#: apart, from eighteen bodies and a render, and one model spent its six
#: turns and came back `error_max_turns` -- recovered only because the
#: retry asked again. A ceiling that the work reaches is a ceiling set
#: too low: it turns a hard question into a failed one, and the failure
#: looks like the judge having nothing to say.
#: RAISED AGAIN, FROM 12, ON TASK 13. The pattern is the same one the
#: note above describes and it is worth stating as a rule: the questions
#: that run out of turns are the ones whose answer is NO. On 13 the belt
#: guard's access question died `error_max_turns` three times over on
#: each of the two models built to fail it, while the eight models that
#: pass answered first time -- a judge that has found something looks
#: again before it commits, and looking again costs turns. A ceiling
#: that only the failing cases reach does not save money; it silently
#: converts every real finding into "no verdict", which then prints as
#: 1.00 NOT CHARGED and reads exactly like a pass.
LOOKING_MAX_TURNS = 20

#: And a text question used to get exactly ONE, which is the same
#: mistake in the same place. MEASURED ON TASKS 61 AND 62, both of them
#: judged criteria that separate their corpus:
#:
#:   * on 61 the four-bolt question died six times over two models and
#:     fell back INVISIBLY, because the fallback happened to return the
#:     same 1.00 the judge would have;
#:   * on 62 the one question the whole criterion exists for died three
#:     times over on each of three different wordings -- a wider anchor,
#:     a worked mixed example, and finally the arithmetic done for it --
#:     while the five easy questions beside it answered first time.
#:
#: The common thread is not the prompt. It is that a question with a
#: genuine judgement in it needs room to reach its JSON, and one turn is
#: not room. Tools stay off for a text question, so the extra turns
#: cannot wander anywhere: they only let the answer finish.
TEXT_MAX_TURNS = 4


def _import_judge():
    """The Judge class, or the reason there isn't one."""
    try:
        from common.llm_judge import Judge
    except Exception as exc:                                # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    return Judge, None


def stance(harness) -> dict:
    """What one harness has to say about being judged. Never None.

    EVERY HARNESS ANSWERS THIS, including the nineteen that use no judge,
    and that is the whole point of the convention. "There is no judge
    here" is a decision somebody took and can be argued with; a harness
    that simply has no judge code in it is silence, and silence cannot be
    told apart from an oversight. So a harness declares either

        JUDGE_CRITERIA = {"the question": weight-free description}
        JUDGE_VERSION  = "slug/1"

    or

        JUDGE_NOTE = "why a judgement would add nothing here"

    and `--judge` prints whichever it finds. A harness carrying neither is
    reported as undeclared rather than as unjudged, because those are not
    the same thing.

    THE NOTE IS NOT ONLY FOR THE TASKS THAT SAY NO. A task can have a
    judge and still owe an explanation -- task 15 carries one at weight
    zero because it marked an adversarial above the reference, and a
    reader who sees only the criterion learns nothing about why it counts
    for nothing. So both are printed whenever both are there.
    """
    criteria = dict(getattr(harness, "JUDGE_CRITERIA", None) or {})
    note = getattr(harness, "JUDGE_NOTE", None)
    return {"declared": bool(criteria or note),
            "judged": bool(criteria),
            "criteria": criteria,
            "version": getattr(harness, "JUDGE_VERSION", None),
            "note": note}


def available() -> dict:
    """Whether a judge can be called here, and why not when it cannot.

    Worth asking before building a prompt, and worth recording beside the
    answer: "no judge on this host" and "the judge said nothing" are
    different facts about a capture.
    """
    cls, why = _import_judge()
    if cls is None:
        return {"ok": False, "why": why}
    #: THE CLASS IMPORTING IS NO LONGER PROOF OF A ROUTE. It used to be:
    #: `llm_judge` imported `call_llm` imported the provider SDK at module
    #: level, so a host without the SDK could not import the Judge at all
    #: and this answered honestly by accident. `call_llm` defers its
    #: import now -- which is what lets `--score-from` run anywhere -- so
    #: the SDK has to be asked about separately, or this would report a
    #: judge on a machine that cannot call one.
    creds = credentials()
    if creds["route"] is None:
        return {"ok": False, "why": creds.get("why_no_route")
                or "no judge route"}
    return {"ok": True, "why": None}


def cache_key(prompt: str, model: str | None, images=(),
              image_ids=None) -> str:
    """Everything the answer depends on, hashed.

    THE PICTURES ARE PART OF THE QUESTION. Keying on the prompt alone was
    right while the prompt was the whole question; the moment a judge can
    look at something, two candidates whose measurements read the same and
    whose pictures differ would share a key, and the second would be
    answered out of the cache with the first one's verdict.

    WHAT MAKES TWO PICTURES THE SAME PICTURE, though, is not their bytes.
    Hashing the file was the first answer and it is wrong in the direction
    that costs most: a screen capture out of a CAD kernel is not
    bit-reproducible -- re-rendering the same model from the same camera
    writes a file that differs in a few pixels -- so every run missed the
    cache, every question was re-asked, and the same capture came back
    with different verdicts. The reference read 0.85 on one run and 0.90
    on the next, from identical measurements. A grader that answers
    differently on the same file twice is not a grader, and the cache was
    supposed to be what stopped that.

    So a caller that KNOWS what its picture is passes `image_ids` -- what
    the render was asked for rather than what came out: the model, the
    named view, the pixel size, the camera. Two renders that agree on all
    of those are the same question about the same geometry, whatever the
    antialiasing did. A caller that does not know passes nothing and gets
    the content hash, which is still right for a picture from somewhere
    else.
    """
    h = hashlib.sha256()
    h.update((model or "default").encode("utf-8"))
    h.update(b"\0")
    h.update(prompt.encode("utf-8"))
    if image_ids is not None:
        for ident in image_ids:
            h.update(b"\0")
            h.update(str(ident).encode("utf-8"))
        return h.hexdigest()[:16]
    for img in images:
        h.update(b"\0")
        try:
            h.update(hashlib.sha256(Path(img).read_bytes()).digest())
        except OSError:
            # An unreadable picture is a different question from a readable
            # one, and must not collide with "no picture at all".
            h.update(f"unreadable:{img}".encode("utf-8"))
    return h.hexdigest()[:16]


def _read(cache_dir, key):
    if not cache_dir:
        return None
    f = Path(cache_dir) / f"{key}.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return None


class _CapturedStderr:
    """Everything written to file descriptor 2 while this is open.

    NOT `contextlib.redirect_stderr`. The agent SDK runs the `claude` CLI
    as a SUBPROCESS, and a subprocess inherits the file descriptor, not
    Python's `sys.stderr` object -- so redirecting the object catches the
    wrapper's own complaint ("Fatal error in message reader ... Check
    stderr output for details") and loses the details it is pointing at.
    Swapping the descriptor catches both.

    The original descriptor is restored and the text replayed, so a run
    still prints what it always printed; nothing is hidden in exchange for
    being recorded.
    """

    def __init__(self):
        self.text = ""
        self._tmp = self._saved = None

    def __enter__(self):
        import tempfile
        # A way out that does not need a code change. Moving file
        # descriptor 2 is the kind of thing that works everywhere until
        # the day it does not, and the day it does not, whoever is sitting
        # in front of it needs the run back, not a patch.
        if os.environ.get("HARNESS_NO_STDERR_CAPTURE", "").strip().lower() \
                in ("1", "true", "yes", "on"):
            return self
        try:
            sys.stderr.flush()
            self._saved = os.dup(2)
            self._tmp = tempfile.TemporaryFile(mode="w+b")
            os.dup2(self._tmp.fileno(), 2)
        except Exception:                                   # noqa: BLE001
            # A host that will not let us move the descriptor still grades;
            # it just grades without this record.
            self._tmp = self._saved = None
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            return False
        try:
            sys.stderr.flush()
            os.dup2(self._saved, 2)
            os.close(self._saved)
            self._tmp.seek(0)
            self.text = self._tmp.read().decode("utf-8", "replace")
            self._tmp.close()
        except Exception:                                   # noqa: BLE001
            pass
        if self.text:
            sys.stderr.write(self.text)
            sys.stderr.flush()
        return False


def error_dir(cache_dir):
    """Beside the cache, so one folder holds everything a run produced."""
    return Path(cache_dir).parent / "judge_errors" if cache_dir else None


def _write_diagnosis(cache_dir, key, record, prompt, stderr_text, images=()):
    """The whole failure, in one file, in the working tree.

    A judgement that did not happen used to leave one line in the capture
    and a truncated complaint on somebody's console. Neither says which
    call failed, what it was asked, or what the transport actually
    reported -- and the console is on a machine that is not the one
    debugging it. This is the file to read afterwards.
    """
    d = error_dir(cache_dir)
    if d is None:
        return None
    try:
        d.mkdir(parents=True, exist_ok=True)
        body = [
            f"key        : {key}",
            f"when       : {datetime.datetime.now().isoformat(timespec='seconds')}",
            f"why        : {record.get('why')}",
            f"route      : {record.get('route') or credentials().get('route')}",
            f"images     : {', '.join(str(i) for i in images) or '(none)'}",
            "",
            "-- stderr of the call " + "-" * 54,
            stderr_text.rstrip() or "(nothing was written to stderr)",
            "",
            "-- every attempt " + "-" * 58,
            json.dumps(record.get("attempts") or [], indent=1,
                       ensure_ascii=False) or "(none recorded)",
            "",
            "-- traceback " + "-" * 63,
            record.get("traceback") or "(no exception was raised here)",
            "",
            "-- the prompt that was sent " + "-" * 48,
            prompt,
        ]
        f = d / f"{key}.txt"
        f.write_text("\n".join(body), encoding="utf-8")
        # The same prompt on its own, so it can be put to the CLI without
        # the SDK in the way. When the transport reports nothing and the
        # stream carries nothing, the only reading left is the raw one --
        # and a prompt that has to be retyped is a prompt nobody checks.
        (d / f"{key}.prompt.txt").write_text(prompt, encoding="utf-8")
        return f
    except OSError:
        return None


def _write(cache_dir, key, record):
    if not cache_dir:
        return
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / f"{key}.json").write_text(
            json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass


def run(judge_factory, candidate: str, *, cache_dir=None,
        model: str | None = None, images=(), image_ids=None, **ctx) -> dict:
    """Ask one Judge and come back with a record, whatever happened.

    `judge_factory` is called only once a judge is known to be importable,
    so a harness can name its Judge subclass at module scope inside a
    function without paying the import at load time.

    The returned record always carries `available`, and carries `scores`
    only when the reply validated. A caller that reads `scores` without
    checking `available` gets a KeyError rather than a silent zero, which
    is the intended way round.
    """
    if not asking_enabled():
        # BEFORE the prompt is built, not after: switched off has to cost
        # nothing, and a record saying "switched off" must not be
        # confusable with one saying "asked and got nothing back".
        return {"available": False, "cached": False,
                "why": "not asked: the judge is switched off for this run "
                       "(--no-judge, or HARNESS_NO_JUDGE=1)"}
    cls, why = _import_judge()
    rec = {"available": False, "why": why, "cached": False}
    if cls is None:
        return rec

    try:
        judge = judge_factory()
        judge._candidate, judge._ctx = candidate, ctx
        prompt = judge.build_prompt(candidate, **ctx)
    except Exception as exc:                                # noqa: BLE001
        rec["why"] = f"prompt could not be built: {type(exc).__name__}: {exc}"
        return rec

    images = [str(p) for p in images if Path(p).is_file()]
    #: THE ROUTE IS PART OF THE KEY. `model` is normally None, and a key
    #: that ignores it would hand a run on one judge the cached verdicts of
    #: another: the judge moved from Claude Sonnet 5 to gpt-5.6-sol and
    #: every stale entry would have been replayed as if it were this
    #: model's. `judge_route.cache_tag` names the ROUTE as well as the
    #: model -- 'gpt-5.6-sol' or 'claude:fable51' -- so switching
    #: JUDGE_ROUTE makes the other route's entries miss, which is the
    #: behaviour wanted: a changed judge is a changed answer.
    #: THE CACHE TAG AND THE MODEL ARGUMENT ARE NOT THE SAME STRING, and
    #: conflating them was a bug this nearly shipped with. `cache_tag()`
    #: returns 'claude:fable51' on the claude route, which identifies the
    #: judge perfectly and is not a model name call_claude would accept.
    #: So the tag goes into the key and `model` stays as the caller left
    #: it -- None, meaning "the route's own default".
    from common import judge_route as JRT
    key_model = model if model is not None else JRT.cache_tag()
    key = cache_key(prompt, key_model, images, image_ids)
    rec.update(key=key, criteria=list(getattr(judge, "CRITERIA", {})),
               #: WHICH JUDGE ANSWERED, stored with the verdict. A record
               #: that does not say this cannot be compared with another
               #: machine's, and comparing them is the whole reason the
               #: route is selectable.
               judge=JRT.describe(), judge_tag=key_model,
               images=[Path(p).name for p in images],
               image_ids=list(image_ids) if image_ids is not None else None)
    hit = _read(cache_dir, key)
    if hit is not None:
        return dict(hit, cached=True)

    creds = credentials()
    if creds["route"] is None:
        rec["why"] = creds.get("why_no_route") or (
            "no credentials: set AZURE_API_KEY in .env")
        return rec
    original = type(judge).CALL
    attempts_log = []
    # ONE route for both questions now. The old split -- `call_claude` for
    # text, our own wrapper for pictures -- meant the text path was the
    # one we could not see into, and the text path is the one that failed.
    type(judge).CALL = staticmethod(
        _retrying_call(model=model, images=images,
                       record=attempts_log))
    scores = None
    with _CapturedStderr() as err:
        try:
            scores = _with_deadline(lambda: judge.judge(candidate, **ctx),
                                    JUDGE_TIMEOUT_S)
        except Exception as exc:                            # noqa: BLE001
            # Empty reply, invalid JSON, a criterion the model skipped, a
            # network failure: all of them are this record, none of them is
            # a traceback in the middle of a grading run.
            rec["why"] = f"{type(exc).__name__}: {exc}"
            rec["traceback"] = traceback.format_exc()
        finally:
            type(judge).CALL = original

    rec["attempts"] = attempts_log
    if scores is None:
        rec["diagnosis"] = str(_write_diagnosis(cache_dir, key, rec, prompt,
                                                err.text, images) or "")
        return rec

    rec.update(available=True, why=None, scores=scores, route=creds["route"],
               reasons=_justifications(judge))
    # A call that ANSWERED and still complained is worth a file too: the
    # SDK printed "Fatal error in message reader" beside perfectly good
    # verdicts, and nobody could tell whether that meant a silent retry --
    # which is the difference between a judgement costing one call or five.
    if err.text.strip():
        rec["noise"] = str(_write_diagnosis(cache_dir, key, rec, prompt,
                                            err.text, images) or "")
    _write(cache_dir, key, rec)
    return dict(rec, cached=False)


def _justifications(judge) -> dict:
    try:
        return dict(judge.justifications())
    except Exception:                                       # noqa: BLE001
        return {}


class JudgementMissing(RuntimeError):
    """A judged criterion was asked to score without a judgement.

    Raised, not scored. The alternatives are both lies: paying the
    criterion out at 1.0 hands every candidate a free point for an
    infrastructure failure -- including an untouched input -- and paying
    it at 0.0 invents a verdict, which `score_of` refuses to do for the
    same reason.

    Ten harnesses returned 1.0 here and said so in their evidence line,
    each on a criterion weighted 1.0 out of a six-to-eight point total.
    A grading box with no route to the judge model therefore scored
    twelve to seventeen per cent above the truth, silently, on every
    model it graded. Silently is the part that matters: the run reported
    a number and the number looked fine.

    `harness_cli` turns this into one ERROR row for the model that could
    not be scored and a nonzero exit, so a batch reports which models
    lack a judgement instead of averaging them in at full marks.
    """


def require(record, criterion, *, expect_version=None, task=None):
    """One criterion's judged score, or refuse to score at all.

    `record` is the judgement stored in the capture. A judgement from an
    older question is not a judgement of this question, so a version
    mismatch refuses exactly as an absent one does.
    """
    stale = (expect_version is not None
             and (record or {}).get("question_version")
             not in (None, expect_version))
    score = None if stale else score_of(record, criterion)
    if score is not None:
        return float(score)
    where = f"{task}: " if task else ""
    if stale:
        why = (f"the judgement in this capture answers "
               f"{(record or {}).get('question_version')!r}, not "
               f"{expect_version!r}")
    else:
        why = ((record or {}).get("why")
               or "the capture carries no judgement for this criterion")
    raise JudgementMissing(
        f"{where}cannot score {criterion!r}: {why}. This criterion is "
        f"judged, and a judgement neither happened nor was stored -- so "
        f"there is no score to report and none will be invented. Re-run "
        f"the capture on a machine that can reach the judge model, or "
        f"re-judge the stored capture; `judge_runner.credentials()` "
        f"reports whether this machine has a route to the judge model "
        f"and, when it has not, which credential is missing.")


def require_map(record, key, *, expect_version=None, task=None):
    """A judgement's per-item mapping, or refuse to score at all.

    The sibling of `require` for criteria judged item by item rather than
    once -- 24_seals_to_joint asks about each joint. An empty mapping is
    treated as an absent one: a judgement that named no items answered
    nothing about them.
    """
    stale = (expect_version is not None
             and (record or {}).get("question_version")
             not in (None, expect_version))
    got = None if stale else ((record or {}).get(key) or None)
    if got:
        return got
    where = f"{task}: " if task else ""
    why = ((f"the judgement in this capture answers "
            f"{(record or {}).get('question_version')!r}, not "
            f"{expect_version!r}") if stale
           else ((record or {}).get("why")
                 or f"the capture carries no {key!r} from a judgement"))
    raise JudgementMissing(
        f"{where}cannot score the judged criterion that reads {key!r}: "
        f"{why}. Re-run the capture on a machine that can reach the judge "
        f"model, or re-judge the stored capture.")


def criterion_key(name) -> str:
    """The key a judgement's REASONS are filed under.

    Scores come back under the criterion name as the harness wrote it;
    reasons come back under a normalised key. Reading the reasons by the
    written name therefore returns nothing for any criterion with a
    capital letter -- and nothing is not an error, so the report simply
    said `judged` where the argument should have been. One run lost every
    justification that way.

    Deliberately a COPY of `llm_judge._key` rather than an import: this
    module is imported by every harness, and `llm_judge` pulls in the
    agent SDK, which a scoring-only host does not have. The two are held
    together by an assertion in tools/selftest_common.py instead, so a
    drift fails a test rather than silently emptying the reasons again.
    """
    return re.sub(r"\s+", " ", str(name)).strip().strip("\"'`").strip(" .:").lower()


def reason_of(record, criterion, default=""):
    """One criterion's justification out of a record, by either spelling."""
    if not record:
        return default
    reasons = record.get("reasons") or {}
    if criterion in reasons:
        return reasons[criterion]
    return reasons.get(criterion_key(criterion), default)


def score_of(record, criterion, default=None):
    """One criterion's score out of a record, or `default`.

    Never invents a number. A judgement that did not happen is not a zero
    -- a zero is a verdict, and no verdict was reached.
    """
    if not record or not record.get("available"):
        return default
    return (record.get("scores") or {}).get(criterion, default)
