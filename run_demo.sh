#!/bin/bash
# run_demo.sh [WORKDIR] — the whole pipeline on the invented sample, offline.
#
# Cleanup half:  dup_scan -> adjudicate -> clean_tree (merge) -> verify_clean
# Research half: build view -> gate_claims (offline) -> merge_round with the
#                FIXTURE skeptic verdicts -> apply_additions -> lint_tree -> ledger_report
#
# WORKDIR defaults to ./work and is recreated from sample/ on every run.
# No network, no AI model: the skeptic step is a committed fixture file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
T="$HERE/tools"
W="${1:-$HERE/work}"
export PYTHONHASHSEED=0          # build_tree_view's person order is then reproducible

rm -rf "$W"; mkdir -p "$W"
python3 "$T/make_demo_research.py" "$W/in"
cp -R "$W/in/research" "$W/research"

echo; echo "== 1. cleanup: score duplicate candidates"
python3 "$T/dup_scan.py" "$W/in/demo-messy.ged" | tail -3
echo; echo "== 2. cleanup: adjudicate (rule pass; REVIEW pairs become dossiers for agents)"
python3 "$T/adjudicate.py" "$W/in/demo-messy.ged" --out "$W/adjudication" | tail -4
echo; echo "== 3. cleanup: merge duplicates, lossless"
python3 "$T/clean_tree.py" "$W/in/demo-messy.ged" "$W/clean.ged" --reports "$W/reports" | tail -6
echo; echo "== 4. cleanup: prove nothing was dropped"
python3 "$T/verify_clean.py" "$W/in/demo-messy.ged" "$W/clean.ged" --log "$W/reports/merge-log.tsv" | tail -4

echo; echo "== 5. research: build the view payload the research tools read"
mkdir -p "$W/view" "$W/view2"
python3 "$T/build_tree_view.py" "$W/clean.ged" "$W/view/tree-data.json" --root @I46@ --me @I50@ > /dev/null
ls "$W/view"
echo; echo "== 6. research: gate the finder journal (offline, saved pages only)"
python3 "$T/gate_claims.py" "$W/research/round1/journal.jsonl" "$W/research/round1/gated" \
  --research "$W/research" --view "$W/view"
echo; echo "== 7. research: fold the (fixture) skeptic verdicts into the ledger"
python3 "$T/merge_round.py" "$W/research/round1/skeptic-verdicts.json" 2026-01-15 --round 1 \
  --base "$W/clean.ged" --research "$W/research" --view "$W/view" --write
echo; echo "== 8. research: write the ledger into a new GEDCOM (append-only)"
python3 "$T/apply_additions.py" "$W/clean.ged" "$W/research/additions.json" "$W/research/tree-research.ged"
echo; echo "== 9. research: date lint on the root person's line"
python3 "$T/build_tree_view.py" "$W/research/tree-research.ged" "$W/view2/tree-data.json" --root @I46@ --me @I50@ > /dev/null
python3 "$T/lint_tree.py" "$W/view2" "$W/research/LINT.md" --root @I46@ --research "$W/research"
echo; echo "== 10. research: the ledger as a checklist"
python3 "$T/ledger_report.py" "$W/research/additions.json" "$W/view2/tree-data.json" "$W/research/LEDGER.md"
echo; echo "== 11. next round: pick targets and write dossiers for the finder agents"
python3 "$T/build_targets.py" "$W/view2" "$W/research/round2/targets.json" --root @I46@ \
  --config "$HERE/sample/lanes.json" --research "$W/research" | head -2
echo; echo "done: outputs in $W"
