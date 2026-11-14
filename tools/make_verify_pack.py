#!/usr/bin/env python3
"""
make_verify_pack.py — build a self-contained file for re-checking merge claims.

    python3 tools/make_verify_pack.py SRC.ged OUT.txt --claims claims.txt

Why self-contained: the first attempt at this pointed the verifying agents at
the shared dossier directory, and that directory is regenerated whenever the
scanner is re-run. Regenerating it under a live workflow left the agents looking
for pairs that were no longer in the file they had been sent to. A verification
pack carries the claim *and* both full records in one file, so nothing it needs
can move underneath it.

The claims file is one pair per line:

    @I123@ @I456@ -- claimed same because: <the first reviewer's reason>
"""

import sys
import os
import re
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dup_scan as D
import adjudicate as A


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--claims", required=True)
    args = ap.parse_args()

    claims = []
    with open(args.claims, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            m = re.match(r"\s*(@I\d+@)\s+(@I\d+@)\s*(?:--\s*)?(.*)", line)
            if m:
                claims.append((m.group(1), m.group(2), m.group(3).strip()))
    if not claims:
        sys.exit(f"no claims parsed from {args.claims}")

    people, fam, recs, roots, meta = D.build(args.src)
    parents, children, spouses, siblings = D.family_index(people, fam)
    ctx = {"parents": parents, "children": children, "spouses": spouses,
           "siblings": siblings, "people": people}

    missing = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(f"# Verification pack — {len(claims)} merge claims to re-check.\n")
        fh.write("# Each claim is followed by the FULL record of both individuals.\n\n")
        for i, (a, b, why) in enumerate(claims, 1):
            if a not in people or b not in people:
                missing += 1
                continue
            fh.write("=" * 72 + "\n")
            fh.write(f"CLAIM {i}: {a} and {b} are the SAME PERSON\n")
            fh.write(f"first reviewer's reason: {why}\n")
            fh.write("=" * 72 + "\n")
            fh.write("RECORD A\n" + A.describe(people[a], ctx) + "\n\n")
            fh.write("RECORD B\n" + A.describe(people[b], ctx) + "\n\n")

    print(f"wrote {args.out}: {len(claims) - missing} claims"
          + (f" ({missing} skipped — id not in this file)" if missing else ""))


if __name__ == "__main__":
    main()
