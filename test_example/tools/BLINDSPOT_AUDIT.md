# Blind-Spot Audit

A grader that a model is trained against teaches the model whatever the
grader cannot see. The audit measures that on the part itself and makes
every change to the grader prove itself with numbers before it ships.

Everything runs offline from the captures in `evidence/captures`, no
SolidWorks needed. Python 3 only, no packages.

## The pieces

- **Blind-Spot Map** (`blindspot_map.py`, `build_blindspot_page.py`):
  spots spread over the reference's outer housing skin, at each spot small
  edits nobody asked for (a 0.15 mm step, a 0.5 mm and a 3 mm boss, a
  through hole, all 8 mm across) and two edits that leave the shape as it
  is (a split line, a finer mesh). Each edited part is graded by
  `no unrequested changes`. The page shows on a 3D model where edits are
  caught, where they are missed, and where the check does not look.
- **Audit** (`blindspot_audit.py`): runs the map, grades every shipped model
  in full, checks the gates and writes one report.

## Audit a version of the grader

```
python3 tools/blindspot_audit.py run audit/now --quick   # about half an hour
python3 tools/blindspot_audit.py run audit/now           # about 2.5 h
```

Times are for four workers (`--workers N`). A run that stops continues
where it left off when started again. The folder gets `report.html` (the
verdict and figures), `map.html` (the 3D map) and the data behind them.

## Prove a change

1. Audit the current grader: `run audit/before`.
2. Change the grader. To keep both versions, point the audit at a copy:
   `BLINDSPOT_HARNESS=path/to/new/harness.py`.
3. Audit it on the same spots:
   `run audit/after --spots-from audit/before`.
4. Compare: `compare audit/before audit/after`. It writes
   `audit/after/compare.html` and exits with 1 if a gate fails.

The gates:

- the reference keeps full marks;
- no example scores higher than before (they are broken on purpose, so a
  higher score means a weaker grader);
- no harmless edit is newly flagged;
- nothing caught before is missed now, and no spot checked before is
  skipped now.

The report also shows a what-if: how much of the outer skin the check
would skip with tighter zones around the controls and the widening strip.
That part is geometry only. It shows how far a change could reach, not
what it achieves, which only an audited change can show.

## A change it proved

`SolidWorks/1_playstation_controller/tests/task/harness_tight_zones/` is a
proposed change audited this way: tighter zones around the controls, the
button pads compared with the original pad from the other side, and the
widened middle checked. The skipped share of the outer skin drops from 38%
to 15% with every gate passing. Its README has the numbers.

Results made elsewhere can be turned into a run without grading again:
`run OUTDIR --import RESULTS.json ...` (the shipped models are still graded).

## Reading the numbers

Each spot is one deterministic trial, so read patches on the map, not
single points. The quick mode is for trying an idea; quote the full mode.
The map covers the skin part of `no unrequested changes`, the part these
edits can reach; the criterion's span and body-shape checks are not
exercised.
