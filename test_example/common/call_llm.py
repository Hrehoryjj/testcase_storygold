"""The one LLM entry point harnesses use for judge calls.

One line of indirection on purpose: `llm_judge` names this module and not
a provider, so the route can change without touching a judge.

WHICH MODEL ANSWERS IS `common/judge_route`'s DECISION. JUDGE_ROUTE picks
gpt (the default, matching the dataset repository's `gpt-5.6-sol`) or
claude (model from JUDGE_MODEL, fable51 by default). Read that module for
the switch; nothing is decided here.

THIS MODULE IS NOT THE LIVE PATH WHEN A HARNESS GRADES, and that is worth
knowing before changing it. `judge_runner` replaces `Judge.CALL` with its
own retrying wrapper before asking, so a route changed only here would
not reach a real run. Both now go through `judge_route`, which is the
point: there is one answer to "which model", and a Judge used outside
judge_runner -- a test, a one-off -- asks the same one a grading run does.

THE IMPORT IS THE POINT OF FAILURE, and deliberately so. There is no stub
and no fallback: a machine without the provider SDK (or without an Azure
credential) cannot complete a call, and a harness that names this module
at import time becomes unimportable there. That is why no harness imports
it directly -- `common/judge_runner` does the import inside the call and
records its absence as a fact, so `--score-from` keeps working on a host
with no judge instead of grading against placeholder scores.

The import is deferred for the same reason: naming `judge_route` costs
nothing, because `judge_route` imports no SDK until something is asked.
"""


def call_llm(prompt, *, images=()):
    from common import judge_route
    return judge_route.call(prompt, images=images)


def judge(prompt, **kw):
    """Kept for callers that imported the old provider-named alias."""
    return call_llm(prompt, images=kw.get("images", ()))
