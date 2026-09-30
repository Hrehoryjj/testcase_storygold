#!/bin/bash
# TODO: not runnable in a container. tests/task/harness/harness.py grades a
# live SolidWorks session over COM (pywin32): either the document currently
# open, or a path passed as argv[1] that it loads into that session. Either
# way it needs a real, licensed, GUI SolidWorks process on Windows, which
# this environment cannot provide (see environment/Dockerfile).
#
# The harness is complete, not a placeholder. It grades continuously --
# every criterion returns a subscore in [0,1] -- and no criterion is a
# score-zeroing gate. Weights live in ALL_CRITERIA in the harness and
# nowhere else; task.toml's max_score must equal their sum.
#
# Run locally on Windows instead, from this task's directory:
#   python tests/task/harness/harness.py <path-to-candidate.SLDPRT>
#   # or, to grade whatever is currently open in SolidWorks:
#   python tests/task/harness/harness.py
#   # or the whole shipped corpus, reference and adversarials included:
#   python tests/task/harness/harness.py --batch
#
# It prints a JSON score envelope to stdout (see common/harness_base.py's
# finalize()): {"score": ..., "max_score": 7.0, "passed": ...,
# "subscores": {...}}. The readable breakdown goes to stderr, so
# redirecting stdout leaves the envelope alone.
set -euo pipefail
echo "TODO: no automated verifier here -- see comments in this file" >&2
mkdir -p /logs/verifier 2>/dev/null || true

# This task's assets are pinned as whole folders ([[metadata.assets]], one
# manifest per folder): fetch anything missing or stale straight off them.
TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$TASK_DIR" 2>/dev/null <<'FETCH' || true
import hashlib, os, sys, tomllib, urllib.parse, urllib.request
task_dir = sys.argv[1]

def ok(dest, sha):
    return os.path.exists(dest) and hashlib.sha256(open(dest, "rb").read()).hexdigest() == sha

def download(url, dest, sha):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    assert ok(dest, sha), f"asset checksum mismatch: {dest}"

meta = tomllib.load(open(os.path.join(task_dir, "task.toml"), "rb")).get("metadata", {})
for asset in meta.get("assets", []):
    if "manifest" in asset:
        base = asset["url"].rstrip("/")
        for rel, sha, size in asset["manifest"]:
            dest = os.path.join(task_dir, asset["path"], rel)
            if not ok(dest, sha):
                download(f"{base}/{urllib.parse.quote(rel)}", dest, sha)
    else:
        dest = os.path.join(task_dir, asset["path"])
        if not ok(dest, asset["sha256"]):
            download(asset["url"], dest, asset["sha256"])
FETCH

exit 1
