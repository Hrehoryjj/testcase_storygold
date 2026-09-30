"""Which model answers a judged criterion. One switch, read in one place.

    JUDGE_ROUTE=gpt      Azure GPT deployment (default; what the dataset
                         repository ships and what the customer's release
                         runs grade with)
    JUDGE_ROUTE=claude   Azure Claude deployment, model from JUDGE_MODEL

    JUDGE_MODEL=fable51  only meaningful on the claude route; 'fable51'
                         or 'sonnet5'. Ignored on gpt, whose model IS its
                         deployment -- see call_gpt.GPT_CONFIG.

WHY THIS FILE EXISTS. The judge moved from Claude to GPT in three places
at once -- `judge_runner._sdk_call`, `judge_runner.credentials` and
`call_llm` -- and a fourth, the verdict cache key, had to learn about it
so old answers would stop being replayed. Four edits to change one thing
is three too many: the next move would have been made in two of them and
the run would have asked one model while reporting the other. The route
is resolved here and nowhere else.

NOTHING HEAVY IS IMPORTED AT MODULE LEVEL, and that is load-bearing.
`judge_runner` is imported by every judged harness, including on machines
that have no provider SDK at all -- that is what `--score-from` is for.
The SDKs are imported inside `call`, the same way judge_runner has always
imported them, so naming the route costs nothing until somebody asks a
question.

AN UNKNOWN VALUE IS AN ERROR, NOT A DEFAULT. `JUDGE_ROUTE=GTP` falling
back to gpt would grade a whole set on a model nobody chose and say
nothing, which is the failure this repository has now met twice: once
when `HARNESS_NO_IMAGES` was set in place of `HARNESS_NO_JUDGE` and five
tasks crashed on a credential they had been told not to need, and once
when a run's summary blamed the judge for a misspelled variable name.
"""
import os

GPT = "gpt"
CLAUDE = "claude"
ROUTES = (GPT, CLAUDE)

#: THE DEFAULT IS GPT, to match the dataset repository. It was Claude
#: (fable) here for as long as this repository judged anything, and the
#: reason to move is not that GPT is better: it is that the customer's
#: release runs judge with gpt-5.6-sol, and two judges disagreeing about
#: the same criterion is a fight nobody can win by argument. Set
#: JUDGE_ROUTE=claude to compare.
DEFAULT_ROUTE = GPT

#: Fable, not Sonnet, when the claude route is chosen: it is the model
#: this repository's judged rows were written and calibrated against.
DEFAULT_CLAUDE_MODEL = "fable51"


class UnknownRoute(ValueError):
    """JUDGE_ROUTE held something that is not a route."""


def route() -> str:
    """'gpt' or 'claude'. Raises UnknownRoute on anything else."""
    raw = (os.getenv("JUDGE_ROUTE") or "").strip().lower()
    if not raw:
        return DEFAULT_ROUTE
    if raw not in ROUTES:
        raise UnknownRoute(
            f"JUDGE_ROUTE={raw!r} is not a judge route. "
            f"Use one of: {', '.join(ROUTES)}. "
            f"Unset it to get the default ({DEFAULT_ROUTE}).")
    return raw


def claude_model() -> str:
    """The Claude model name, whether or not the claude route is live."""
    return (os.getenv("JUDGE_MODEL") or "").strip() or DEFAULT_CLAUDE_MODEL


def gpt_deployment() -> str:
    """The GPT deployment name, read without importing the SDK.

    call_gpt.GPT_CONFIG is the real source, but importing it drags in
    `openai`. This is the same os.getenv with the same default, so the
    banner and the cache key can name the deployment on a machine that
    could not ask it anything.
    """
    return os.getenv("AZURE_GPT_DEPLOYMENT", "gpt-5.6-sol")


def cache_tag() -> str:
    """What identifies this judge in a verdict cache key.

    THE ROUTE IS PART OF THE KEY, always. A key that named only the model
    would hand a GPT run the Claude verdicts sitting in the same cache --
    and a changed judge is a changed answer, so those entries have to
    miss. `judge_runner.run` passes this as `model` when a caller names
    none.
    """
    r = route()
    return gpt_deployment() if r == GPT else f"claude:{claude_model()}"


def describe() -> str:
    """One line for a banner: which model is about to be asked."""
    r = route()
    if r == GPT:
        return f"gpt -- Azure deployment {gpt_deployment()}"
    return f"claude -- model {claude_model()}"


def credential_names() -> tuple:
    """The environment variables THIS route reads, in the order it reads
    them. Names only; nothing here ever touches a value."""
    if route() == GPT:
        return ("AZURE_API_KEY_GTM_RESEARCH", "AZURE_API_KEY",
                "AZURE_GPT_ENDPOINT", "AZURE_GPT_DEPLOYMENT")
    return ("AZURE_API_KEY", "AZURE_CLAUDE_RESOURCE")


def credentials() -> dict:
    """{"route": str|None, "judge": str, "why_no_route": str|None}.

    Reports rather than guesses: "no key" and "the wrong key" fail
    differently and look identical in a traceback. The import is inside
    the branch, so asking this question on a machine with neither SDK
    answers it instead of raising.
    """
    r = route()
    out = {"route": None, "judge": describe(), "why_no_route": None,
           "asked": r}
    if r == GPT:
        try:
            from common.call_gpt import GPT_CONFIG
        except Exception as exc:                            # noqa: BLE001
            out["why_no_route"] = (
                f"the openai SDK is not importable here "
                f"({type(exc).__name__}: {exc}); "
                f"install it from env_requirements.txt")
            return out
        key = GPT_CONFIG["api_key"]
        where = GPT_CONFIG["endpoint"] and GPT_CONFIG["deployment_name"]
        if not key:
            out["why_no_route"] = (
                "no Azure credential for GPT: set AZURE_API_KEY (or "
                "AZURE_API_KEY_GTM_RESEARCH, if the GPT deployment lives "
                "on a different resource than Claude) in .env")
        elif not where:
            out["why_no_route"] = ("no Azure GPT route: set "
                                   "AZURE_GPT_ENDPOINT and "
                                   "AZURE_GPT_DEPLOYMENT in .env")
        else:
            out["route"] = "azure"
        return out

    try:
        from common.call_claude import AZURE_API_KEY, AZURE_RESOURCE
    except Exception as exc:                                # noqa: BLE001
        out["why_no_route"] = (
            f"the anthropic SDK is not importable here "
            f"({type(exc).__name__}: {exc}); "
            f"install it from env_requirements.txt")
        return out
    if not AZURE_API_KEY:
        why = "no Azure credential: set AZURE_API_KEY in .env"
        #: A key under an old name is the commonest reason for this, and
        #: it is invisible from a traceback. Names only, never values.
        stale = sorted(k for k in os.environ
                       if k.startswith("AZURE_API_KEY_") and os.environ[k])
        if stale:
            why += (f" -- {', '.join(stale)} is set but is not read on "
                    f"this route; rename it to AZURE_API_KEY")
        out["why_no_route"] = why
    elif not AZURE_RESOURCE:
        out["why_no_route"] = ("no Azure resource: set "
                               "AZURE_CLAUDE_RESOURCE in .env")
    else:
        out["route"] = "azure"
    return out


def call(prompt, *, images=(), model=None):
    """Ask the live route one question. Text in, text out, no tools.

    `model` is honoured only on the claude route, where a model is a
    per-call argument. The GPT route has no such parameter -- its model
    is its deployment -- so a model passed there would be silently
    dropped, and silently dropping it is exactly what this refuses to do.
    """
    r = route()
    if r == GPT:
        if model and model != gpt_deployment():
            raise ValueError(
                f"the gpt route cannot be asked for model {model!r}: its "
                f"model is its deployment ({gpt_deployment()}). Set "
                f"AZURE_GPT_DEPLOYMENT, or use JUDGE_ROUTE=claude.")
        from common.call_gpt import call_gpt
        return call_gpt(prompt, images=list(images))
    from common.call_claude import call_claude
    return call_claude(prompt, model=model or claude_model(),
                       images=list(images))
