#!/usr/bin/env python3
"""
test_rules.py — regression checks for the evidence rules, on invented data only.

Each check pins a rule that once let a wrong claim through, or once blocked a
right one, on the private tree this pipeline was built for. Exit 1 on any failure.

    python3 tools/test_rules.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_claims as G

fails = []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    if not ok:
        fails.append(what)


PAGE = """Example Regional Archive - Baptisms 1852
Date of baptism: 14 March 1852
Name: Louisa
Father: Henri Druvelle, cooper
Mother: Jeanne Saltmere"""

# quote matching: a window of distinctive words, not a contiguous phrase
check(G.quote_status("Louisa, daughter of Henri Druvelle, cooper, and Jeanne Saltmere, 14 March 1852", PAGE)[0]
      == "quote-found", "a faithful quote of Label: value fields is found")
check(G.quote_status("Louisa daughter of Pierre Castellune and Ursula Brimsworth", PAGE)[0] == "quote-missing",
      "a quote naming other parents is missing")
check(G.quote_status("of and the", PAGE)[0] == "quote-empty", "a quote of only field labels is empty")
check(G.quote_status("HENRI DRUVELLE cooper", "father: henri  druvelle, cooper")[0] == "quote-found",
      "case and punctuation are folded")
check(G.quote_status("Søren Ålund", "Soren Alund")[0] == "quote-found", "ø and å fold to o and a")

# given-name matching used by the duplicate-child and possible-parent checks
check(G.same_first("Wm Saltmere", "William"), "Wm = William")
check(G.same_first("Frederick", "Fredk"), "Fredk = Frederick")
check(not G.same_first("Martha", "Margaret"), "Martha is not Margaret")
check(G.same_first("Gunnvald", "Gonval", loose=True) and not G.same_first("Gunnvald", "Gonval"),
      "spelling variants match only in the loose (flag-only) mode")

# years
check(G.year("BET 1752 AND 1758") == 1752, "the first year of a range")
check(G.year("ABT 1839") == 1839 and G.year("") is None, "ABT years, and no year")

# journal loading: label from the 'started' line, proposal from the 'result' line
import json, tempfile
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
    fh.write(json.dumps({"type": "started", "agentId": "x", "label": "research:lane-a-1"}) + "\n")
    fh.write(json.dumps({"type": "result", "agentId": "x", "result": {"targets": []}}) + "\n")
check(G.load_results(fh.name) == {"lane-a-1": {"targets": []}}, "journal.jsonl is read by label")
os.unlink(fh.name)

print(f"\n{len(fails)} failed")
sys.exit(1 if fails else 0)
