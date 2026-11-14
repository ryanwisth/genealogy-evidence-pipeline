# How a research round runs, and what three rounds taught

## One round

1. `build_targets.py` picks targets from the view of the tree: the root person's ancestors with fewer than two parents, plus a link audit (ancestors whose parents are recorded, ranked by how many ancestors sit above them) and date fixes from the newest lint report. It routes each target to a lane by place, excludes what open records cannot reach (with the reason), and writes one compact dossier per target and a roster. Everything specific to one tree (lanes, place patterns, overrides, open contests) lives in a JSON config; `sample/lanes.json` is the demo's.
2. **Wave 1, finders** (`agents/finder.js`): one agent per roster entry, model pinned per lane (a stronger model where the work is reading handwriting or judging identity among reused names, a cheaper one where the archive is a searchable index). Finders return a structured proposal and write nothing.
3. **The gate** (`gate_claims.py`): free structural checks and quote checks, and a short review sheet per lane.
4. **Wave 2, skeptics** (`agents/skeptic.js`): one agent per lane reads the review sheet, re-opens what the gate could not confirm, tries to refute every claim, and writes the survivors as ledger entries, plus disputes and notes.
5. `merge_round.py` folds the verdicts into the ledger; `apply_additions.py` builds the new GEDCOM; `lint_tree.py` looks for impossible dates on the line; `ledger_report.py` writes a checklist of what to enter in the source tree.

Record pulls were done by separate private scripts. The pipeline consumes saved page text (`research/roundN/pages/*.txt`).

The agent scripts are Claude Code workflow scripts. They are launched by hand, and a stopped run is never resumed (resuming re-runs the whole fleet); its `journal.jsonl` is harvested offline instead, which is exactly what `gate_claims.py` reads.

## Lessons, measured on the private tree

These figures come from the project's own notes on a private tree. They are not reproduced by the demo.

- **A second model catches the first one's errors.** In round 1 the skeptics rejected 31 claims from a fast first pass, and 3 of 147 from a careful one.
- **Turns are the cost.** Round 2's finders averaged 78 turns, and every turn re-reads everything before it. Round 3 moved work into free Python (pre-built dossiers, the gate, review sheets) and ran at about 57k tokens per target.
- **Most rejections were duplicates.** Round 2's skeptics mostly rejected people who were already in the tree under another spelling. That became the gate's duplicate-child check and the "CHECK FIRST" flag for new parents.
- **Skeptics read what they judge.** Round 2's skeptics spent about half their context printing raw gated JSON. The review sheet gives them each claim on a few lines with its quotes and the page text around them.
- **Same year, same fact.** A ranged date (`BET 1752 AND 1752`) and an exact date in the same year are one fact; the ledger check compares person, tag and year.
