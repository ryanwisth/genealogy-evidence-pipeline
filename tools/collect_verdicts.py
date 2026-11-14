#!/usr/bin/env python3
"""
collect_verdicts.py — turn adjudication results into the merge input file.

    python3 tools/collect_verdicts.py OUTDIR result1.json result2.json ... \
        --rule work/adjudication/accepted-by-rule.tsv ...

Each result file is the JSON returned by an adjudication run: `{total, merge,
maybe, keep}`, where every entry carries the two record ids, a same_person
verdict, a confidence and the reason it was decided that way.

Only `merge` entries — judged the same person with HIGH confidence, and not
refuted by the independent second reviewer — go into `accepted-all.tsv`, which
is what `clean_tree.py --accept` consumes. Everything else is written to
`rejected-all.tsv` with its reason, and is NOT merged.

The reasons are kept verbatim. If a merge in the final file ever looks wrong,
the line that produced it says who decided it and why.
"""

import sys
import os
import json
import argparse


def load_result(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    # A workflow task output wraps the script's return value.
    if isinstance(d, dict) and "result" in d and isinstance(d["result"], dict):
        d = d["result"]
    if not isinstance(d, dict) or "merge" not in d:
        raise ValueError(f"{path}: not an adjudication result "
                         f"(keys: {list(d)[:6] if isinstance(d, dict) else type(d)})")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("results", nargs="+")
    ap.add_argument("--rule", action="append", default=[],
                    help="a TSV of pairs settled by rule, folded in as accepted")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    accepted, rejected = [], []
    seen = set()
    counts = {}

    # A recovery run carries `refutations`: second opinions on merges that were
    # proposed in an EARLIER run whose verification agent died. Collect them
    # first, so a merge refuted after the fact is never accepted below.
    refuted = {}
    for path in args.results:
        d = load_result(path)
        for block in d.get("refutations", []):
            for r in block.get("results", []):
                if r.get("refuted"):
                    refuted[tuple(sorted((r["id_a"], r["id_b"])))] = (
                        block.get("pack", os.path.basename(path)), r.get("reason", ""))
    if refuted:
        print(f"  {len(refuted)} merges refuted by a later verification pass")

    for path in args.rule:
        if not os.path.exists(path):
            continue
        n = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                ids = [w for w in line.replace("#", " ").split() if w.startswith("@I")]
                if len(ids) < 2:
                    continue
                key = tuple(sorted(ids[:2]))
                if key in seen:
                    continue
                seen.add(key)
                why = line.split("#", 1)[1].strip() if "#" in line else "settled by rule"
                accepted.append((ids[0], ids[1], f"RULE: {why}"))
                n += 1
        # Several rounds each write a file called accepted-by-rule.tsv, so key
        # the tally by the last two path segments or they overwrite each other
        # and the report silently under-counts.
        counts[os.path.join(*path.split(os.sep)[-2:])] = n

    for path in args.results:
        d = load_result(path)
        n_ok = n_no = 0
        for v in d.get("merge", []):
            key = tuple(sorted((v["id_a"], v["id_b"])))
            if key in seen:
                continue
            seen.add(key)
            if key in refuted:
                pack, why = refuted[key]
                rejected.append(("REFUTED", v["id_a"], v["id_b"],
                                 f"refuted on re-check ({pack}): {why} "
                                 f"| first pass said: {v['reason']}"))
                n_no += 1
                continue
            accepted.append((v["id_a"], v["id_b"], f"REVIEWED: {v['reason']}"))
            n_ok += 1
        for v in d.get("keep", []) + d.get("maybe", []):
            key = tuple(sorted((v["id_a"], v["id_b"])))
            tag = ("REFUTED" if "REFUTED" in (v.get("reason") or "")
                   else "HELD-LOW-CONFIDENCE" if v.get("same_person")
                   else "DIFFERENT-PEOPLE")
            rejected.append((tag, v["id_a"], v["id_b"], v["reason"]))
            n_no += 1
        counts[os.path.basename(path)] = f"{n_ok} merged, {n_no} not"

    acc = os.path.join(args.outdir, "accepted-all.tsv")
    rej = os.path.join(args.outdir, "rejected-all.tsv")
    with open(acc, "w", encoding="utf-8") as fh:
        fh.write("# Duplicate pairs to merge. Every line carries the reason it was\n")
        fh.write("# accepted: RULE (conclusive dates/relatives) or REVIEWED (judged\n")
        fh.write("# the same person, then not refuted by an independent reviewer).\n")
        for a, b, why in accepted:
            fh.write(f"{a}\t{b}\t# {why}\n")
    with open(rej, "w", encoding="utf-8") as fh:
        fh.write("# NOT merged — left as separate records. Nothing here was applied.\n")
        for tag, a, b, why in rejected:
            fh.write(f"{tag}\t{a}\t{b}\t{why}\n")

    for k, v in counts.items():
        print(f"  {k:46} {v}")
    print(f"\naccepted {len(accepted)} pairs -> {acc}")
    print(f"held/rejected {len(rejected)} pairs -> {rej}")


if __name__ == "__main__":
    main()
