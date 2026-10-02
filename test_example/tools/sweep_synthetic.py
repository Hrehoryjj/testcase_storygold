"""What "no unrequested changes" catches, measured. Runs anywhere, no SolidWorks.

    python3 tools/sweep_synthetic.py [N] [SEED]

Picks N random spots on the reference's housing skin outside the widening
strip and the controls (where the task asked for nothing) and makes one
edit at a time on the reference's own capture: a boss or a pocket of
several heights, or a through hole, 8 or 16 mm across. Each edit is graded
and counted as caught when "no unrequested changes" drops below 1.0. The
table is the honest coverage of the check, blind spots included. It runs
the self-test first, and takes a while.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ARGS = sys.argv[1:]
import selftest_synthetic as S  # noqa: E402  (runs the self-test once)

N = int(ARGS[0]) if ARGS else 20
random.seed(int(ARGS[1]) if len(ARGS) > 1 else 1)
H, ref, UN = S.H, S.load("solution"), S.UN

g = H.Grader(S.BASELINE, ref)
P, dx = g.P * H.MM, g.plane_shift_m * H.MM
half = g._actual_half_m() * H.MM
strip = max(half, (g._shell_delta_mm() or 0.0) / 2)
zones = g._skin_zones(P, half)
spots = [q for f in ref["housing_faces"] for q in f["p"]
         if not g._skin_exempt((q[0] + dx, q[1], q[2]), P, strip, zones)]
spots = random.sample(spots, N)

EDITS = [("boss", 0.15), ("boss", 0.25), ("boss", 0.5), ("boss", 1.0),
         ("boss", 3.0), ("pocket", -0.2), ("pocket", -1.0), ("hole", 0.0)]

rows = {}
for q in spots:
    for r in (4.0, 8.0):
        for kind, h in EDITS:
            _, s = S.grade(S.disc_edit(ref, 0, kind, h, r, c=q))
            caught = s[UN] < 1.0 - 1e-6
            others = sorted(k for k, v in s.items()
                            if k != UN and v < 1.0 - 1e-6)
            rows.setdefault((kind, h, r), []).append(caught)
            print(f"at {[round(v) for v in q[:3]]} {kind} {h:+.2f} mm "
                  f"{2 * r:.0f} mm across: "
                  f"{'caught' if caught else 'missed'}"
                  + (f"  ALSO LOST {others}" if others else ""), flush=True)

print("\n| edit | 8 mm across | 16 mm across |\n|---|---|---|")
for kind, h in EDITS:
    a, b = rows[(kind, h, 4.0)], rows[(kind, h, 8.0)]
    print(f"| {kind} {h:+.2f} mm | {sum(a)}/{len(a)} | {sum(b)}/{len(b)} |")
