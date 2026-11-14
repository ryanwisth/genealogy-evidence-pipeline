#!/usr/bin/env python3
"""
lint_tree.py VIEW_DIR OUT.md --root ID [--research DIR] — find impossible dates
on the root person's line, for free.

    python3 tools/lint_tree.py work/view work/research/LINT.md --root @I46@ --research work/research

Round 2's skeptics found these by hand, one at a time: a birth after its own
baptism, a family holding children born before its marriage, a death four days
before a census that lists the man alive. This checks every one of the root
person's ancestors and their children, with the disproved links in
research/disputes.json cut:

  * a birth after the baptism (both dates exact to the day);
  * death before birth; burial before death;
  * a parent under 13 or over 80 at a child's birth (the payload carries no sex,
    so a mother's tighter limit cannot be applied);
  * a child born after the mother's death, or more than 10 months after the father's;
  * a marriage before age 13; a lifespan over 105.

Each hit is a lead, not a verdict: one of the two dates is wrong, and a record
decides which. The report lists the direct line first.
"""
import base64
import collections
import datetime
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workspace as W


def main(view, out, ME, research):
    t = json.load(open(os.path.join(view, "tree-data.json"), encoding="utf-8"))
    D = json.loads(gzip.decompress(base64.b64decode(open(os.path.join(view, "tree-data-details.b64")).read())))
    ids, names = t["ids"], t["names"]
    idx = {x: i for i, x in enumerate(ids)}
    dpath = os.path.join(research, "disputes.json")
    cut = {(d["child"], d["parent"]) for d in json.load(open(dpath))["disputes"]} if os.path.exists(dpath) else set()
    par = [[q for q in (p or []) if (ids[i], ids[q]) not in cut] for i, p in enumerate(t["parents"])]
    kids = collections.defaultdict(list)
    for i, ps in enumerate(par):
        for q in ps:
            kids[q].append(i)
    gen, st = {}, [(idx[ME], 0)]
    while st:
        i, g = st.pop()
        for q in par[i]:
            if q not in gen or gen[q] > g + 1:
                gen[q] = g + 1
                st.append((q, g + 1))

    def when(i, labels):
        """Earliest sortKey (yyyymmdd; 00 for unknown month/day) among facts with these labels."""
        row = D["rows"][i] or {}
        ks = [f[7] for f in row.get("f", []) if f[0].split(" —")[0] in labels and f[7]]
        return min(ks) if ks else 0

    def yr(k):
        return k // 10000 if k else 0

    def exact(k):
        return k and k % 100 and (k // 100) % 100

    def day(k):
        try:
            return datetime.date(k // 10000, (k // 100) % 100, k % 100).toordinal()
        except ValueError:
            return None

    scope = set(gen) | {c for a in gen for c in kids[a]}
    hits = []
    for i in scope:
        b, c = when(i, {"Born"}), when(i, {"Christened", "Baptized", "Baptism"})
        d, bu = when(i, {"Died"}), when(i, {"Buried"})
        m = when(i, {"Married"})
        if exact(b) and exact(c) and day(b) and day(c) and day(c) < day(b):
            hits.append((i, f"born {b} after the baptism {c} ({day(b) - day(c)} days)"))
        if yr(d) and yr(b) and yr(d) < yr(b):
            hits.append((i, f"died {yr(d)} before birth {yr(b)}"))
        if exact(bu) and exact(d) and day(bu) and day(d) and day(bu) < day(d):
            hits.append((i, f"buried {bu} before death {d}"))
        if yr(m) and yr(b) and yr(m) - yr(b) < 13:
            hits.append((i, f"married {yr(m)} at about {yr(m) - yr(b)}"))
        if yr(d) and yr(b) and yr(d) - yr(b) > 105:
            hits.append((i, f"lifespan {yr(d) - yr(b)} years ({yr(b)}-{yr(d)})"))
        cb = yr(b) or t["birth"][i]
        for q in par[i]:
            pb, pd = t["birth"][q], yr(when(q, {"Died"})) or t["death"][q]
            if cb and pb:
                age = cb - pb
                if age < 13 or age > 80:
                    hits.append((i, f"parent {names[q]} {ids[q]} was {age} at the birth"))
            if cb and pd and cb > pd + 1:
                hits.append((i, f"born {cb}, after parent {names[q]} {ids[q]} died {pd}"))
    L = json.load(open(os.path.join(research, "additions.json"), encoding="utf-8"))
    tid_id = {p["tid"]: f"@IR{n:04d}@" for n, p in enumerate(L["people"], 1)}
    noted = {tid_id.get(n["on"], n["on"]) for n in L["notes"]}
    seen, rows = set(), []
    for i, what in hits:
        if (i, what) in seen:
            continue
        seen.add((i, what))
        rows.append((0 if i in gen else 1, gen.get(i, 99), names[i], ids[i],
                     what + (" *(a research note already covers this person)*" if ids[i] in noted else "")))
    rows.sort()
    line = [r for r in rows if r[0] == 0]
    kin = [r for r in rows if r[0] == 1]
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# Date lint — {len(line)} on the root person's direct line, {len(kin)} among their children\n\n"
                 "Each is a lead: one of the dates is wrong, and a record decides which. Disproved links "
                 "(research/disputes.json) are cut before checking. Built by `tools/lint_tree.py`.\n\n"
                 "## On the direct line\n\n| gen | person | problem |\n|---|---|---|\n")
        fh.writelines(f"| {g} | {n} `{x}` | {w} |\n" for _, g, n, x, w in line)
        fh.write("\n## Their children\n\n| person | problem |\n|---|---|\n")
        fh.writelines(f"| {n} `{x}` | {w} |\n" for _, g, n, x, w in kin)
    print(f"lint: {len(line)} on the direct line, {len(kin)} among their children -> {out}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    me = W.pop_opt(argv, "--root")
    research = W.research_dir(argv)
    if len(argv) != 2 or not me:
        sys.exit(__doc__)
    main(argv[0], argv[1], me, research)
