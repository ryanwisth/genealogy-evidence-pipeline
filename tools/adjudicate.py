#!/usr/bin/env python3
"""
adjudicate.py — settle the REVIEW band of the duplicate scan.

`dup_scan` sorts candidate pairs into AUTO (merge), REVIEW (needs a decision)
and WEAK/REJECT (leave alone). This script handles REVIEW in two passes:

  pass 1 — rules
      Some REVIEW pairs are only in REVIEW because the names differ by a middle
      name or a nickname, while the dates and relatives are conclusive. Two
      people do not share a full birth date *and* a full death date by accident,
      and they do not share a full birth date *and* a parent. Those are written
      straight to accepted-by-rule.tsv.

  pass 2 — dossiers
      Everything else gets a readable dossier of both records — every event,
      place, parent, spouse and child — for a human or a judgment pass to
      decide from. The scorer only ever saw a summary; the dossier is the whole
      record, which is what these cases turn on.

The default when a pair cannot be settled is DO NOT MERGE. A missed merge leaves
two records to find later; a wrong merge silently welds two different people
together and is close to undetectable afterwards.

    python3 tools/adjudicate.py SRC.ged --out DIR
"""

import sys
import os
import argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gedcom_lib as G
import dup_scan as D

EVENT_TAGS = ["BIRT", "CHR", "BAPM", "DEAT", "BURI", "CREM", "MARR", "RESI",
              "CENS", "OCCU", "IMMI", "EMIG", "NATU", "EDUC", "RELI", "CAUS",
              "TITL", "NATI", "EVEN"]


def rel_keys(pid, ctx, which):
    out = []
    for x in ctx[which][pid]:
        p = ctx["people"].get(x)
        if p:
            out.append(p)
    return sorted(out, key=lambda p: (p.full, p.by or 0))


def describe(p, ctx):
    r = p.rec
    lines = []
    lines.append(f"  id: {p.id}")
    names = [n.full_value() for n in r.kids("NAME")]
    lines.append(f"  name(s): {' | '.join(names) if names else '(none)'}")
    lines.append(f"  sex: {p.sex or '?'}")
    for tag in EVENT_TAGS:
        for e in r.kids(tag):
            d = e.kid("DATE")
            pl = e.kid("PLAC")
            ty = e.kid("TYPE")
            bits = []
            if ty:
                bits.append(f"[{ty.full_value()}]")
            if e.value and e.value not in ("Y",):
                bits.append(e.value)
            if d:
                bits.append(d.full_value())
            if pl:
                bits.append("@ " + pl.full_value())
            if bits:
                lines.append(f"  {tag}: {' '.join(bits)}")
    for label, which in (("parents", "parents"), ("spouses", "spouses"),
                         ("children", "children"), ("siblings", "siblings")):
        rs = rel_keys(p.id, ctx, which)
        if rs:
            lines.append(f"  {label} ({len(rs)}): " +
                         "; ".join(f"{x.full or '?'} [{x.by or '?'}-{x.dy or '?'}] {x.id}"
                                   for x in rs[:12]))
    nsrc = len(r.kids("SOUR"))
    lines.append(f"  source citations: {nsrc}")
    return "\n".join(lines)


def rule_verdict(a, b, ctx):
    """Return a reason string when the pair is conclusive, else None."""
    A, B = ctx["people"][a], ctx["people"][b]
    fullb = A.bmd and B.bmd and A.by == B.by and A.bmd == B.bmd
    fulld = A.dmd and B.dmd and A.dy == B.dy and A.dmd == B.dmd

    def shared(which):
        ka = {ctx["keyof"](x) for x in ctx[which][a]}
        kb = {ctx["keyof"](x) for x in ctx[which][b]}
        return len({k for k in ka if k[0]} & {k for k in kb if k[0]})

    rel = shared("parents") + shared("spouses") + shared("children")

    # If the two records name entirely different parents, the scanner has
    # already demoted this pair out of automatic merging. The rule pass must
    # not quietly undo that: raise the bar instead of ignoring the signal, so
    # only the strongest evidence still settles it without a human look.
    contradiction = D.parents_contradict(a, b, ctx)
    if contradiction and not (fullb and fulld) and rel < 3:
        return None

    if fullb and fulld:
        return "identical full birth date and full death date"
    if fullb and rel:
        return f"identical full birth date and {rel} shared close relative(s)"
    if fulld and rel:
        return f"identical full death date and {rel} shared close relative(s)"
    if (A.by and B.by and A.by == B.by and A.dy and B.dy and A.dy == B.dy
            and rel >= 2):
        return f"identical birth and death years and {rel} shared close relatives"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--exclude", action="append", default=[],
                    help="a TSV of pairs already judged; skip them. Repeatable. "
                         "Lets the scanner be improved and re-run without paying "
                         "to re-judge everything it found last time.")
    ap.add_argument("--band", choices=["review", "stubs"], default="review",
                    help="'stubs' takes the WEAK pairs where one or both records "
                         "carry no dates at all but share a close relative — the "
                         "shape that leaves small islands behind")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    dd = os.path.join(args.out, "dossiers")
    os.makedirs(dd, exist_ok=True)

    people, fam, recs, roots, dsu, results, ctx = D.run(args.src)

    def shared_relatives(a, b):
        n = 0
        for which in ("parents", "spouses", "children"):
            ka = {ctx["keyof"](x) for x in ctx[which][a]}
            kb = {ctx["keyof"](x) for x in ctx[which][b]}
            n += len({k for k in ka if k[0]} & {k for k in kb if k[0]})
        return n

    if args.band == "stubs":
        # A date-less record scores almost nothing on the numeric scale, so real
        # duplicates land in WEAK and leave an island behind — this is what the
        # family entered three times and a set of small fragments were.
        # Pull back the ones that share a close relative, which is the only
        # evidence a date-less record can offer.
        review = []
        for (a, b), (s, t, e, _) in results.items():
            if t != "WEAK":
                continue
            A, B = people[a], people[b]
            dateless_a = not (A.by or A.dy)
            dateless_b = not (B.by or B.dy)
            if (dateless_a or dateless_b) and shared_relatives(a, b) >= 1:
                review.append((a, b, s, e))
    else:
        review = [(a, b, s, e) for (a, b), (s, t, e, _) in results.items()
                  if t == "REVIEW"]
    seen = set()
    for path in args.exclude:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                ids = [w for w in line.replace("#", " ").split() if w.startswith("@I")]
                if len(ids) >= 2:
                    seen.add(tuple(sorted(ids[:2])))
    if seen:
        before = len(review)
        review = [r for r in review if tuple(sorted((r[0], r[1]))) not in seen]
        print(f"excluded {before - len(review)} already-judged pairs")

    review.sort(key=lambda r: -r[2])

    accepted, remaining = [], []
    for a, b, s, e in review:
        why = rule_verdict(a, b, ctx)
        if why:
            accepted.append((a, b, why, s))
        else:
            remaining.append((a, b, s, e))

    with open(os.path.join(args.out, "accepted-by-rule.tsv"), "w", encoding="utf-8") as fh:
        fh.write("# REVIEW pairs settled by rule — see adjudicate.py rule_verdict()\n")
        for a, b, why, s in accepted:
            fh.write(f"{a}\t{b}\t# score {s}: {why} :: "
                     f"{people[a].label()} == {people[b].label()}\n")

    n = 0
    for i in range(0, len(remaining), args.batch):
        chunk = remaining[i:i + args.batch]
        n += 1
        path = os.path.join(dd, f"batch-{n:03d}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# Duplicate-candidate dossiers, batch {n}\n")
            fh.write(f"# {len(chunk)} pairs from the family tree.\n\n")
            for j, (a, b, s, e) in enumerate(chunk, 1):
                fh.write(f"{'='*72}\nPAIR {a} {b}   (scanner score {s}: {'; '.join(e)})\n{'='*72}\n")
                fh.write("RECORD A\n" + describe(people[a], ctx) + "\n\n")
                fh.write("RECORD B\n" + describe(people[b], ctx) + "\n\n")

    print(f"REVIEW pairs        : {len(review)}")
    print(f"settled by rule     : {len(accepted)}  -> accepted-by-rule.tsv")
    print(f"need judgment       : {len(remaining)} in {n} dossier batches")


if __name__ == "__main__":
    main()
