#!/usr/bin/env python3
"""
ledger_report.py LEDGER.json TREE_DATA.json OUT.md — the research ledger as a
checklist for MyHeritage.

The ledger's additions live in research/tree-research.ged, which is rebuilt
from the cleaned GEDCOM. A fresh MyHeritage export would not have them. So
every verified addition is also listed here, per person, as what to enter on
MyHeritage and the record to cite, and every conflict or duplicate the research
found is listed as what to fix there.
"""
import collections
import json
import sys

TAG = {"BIRT": "Birth", "CHR": "Baptism", "BAPM": "Baptism", "CONF": "Confirmation",
       "MARR": "Marriage", "DEAT": "Death", "BURI": "Burial", "CENS": "Census",
       "RESI": "Residence", "OCCU": "Occupation", "EMIG": "Emigration",
       "IMMI": "Immigration", "NATU": "Naturalization"}


def main(ledger_path, tree_path, out_path):
    L = json.load(open(ledger_path, encoding="utf-8"))
    T = json.load(open(tree_path, encoding="utf-8"))
    name = {i: n for i, n in zip(T["ids"], T["names"])}
    tids = {p["tid"]: f'{p["given"]} {p["surname"]}'.strip() for p in L.get("people", [])}
    who = lambda x: name.get(x) or tids.get(x) or x
    src = {s["sid"]: s for s in L.get("sources", [])}

    def cite(sids):
        out = []
        for s in sids or []:
            r = src.get(s)
            if r:
                out.append(f'{r["title"]}' + (f' — {r["url"]}' if r.get("url") else ""))
        return "; ".join(out)

    per = collections.defaultdict(list)
    for p in L.get("people", []):
        kids = [c for f in L.get("families", []) for c in f.get("children", [])
                if p["tid"] in (f.get("husb"), f.get("wife"))]
        line = (f'**Add a new person: {tids[p["tid"]]}** ({ {"M": "male", "F": "female"}.get(p["sex"], "sex unknown") })'
                + (f', parent of {", ".join(who(k) for k in kids)}' if kids else ""))
        for e in p.get("events", []):
            line += f'\n  - {TAG.get(e["tag"], e["tag"])}: {e.get("date", "")} {e.get("place", "")} — cite {cite(e.get("sids"))}'
        per[kids[0] if kids else p["tid"]].append(line)
    for f in L.get("families", []):
        for c in f.get("children", []):
            parents = " and ".join(who(x) for x in (f.get("husb"), f.get("wife")) if x)
            per[c].append(f"**Add parents: {parents}** — cite {cite(f.get('sids'))}")
    for f in L.get("facts", []):
        what = TAG.get(f["tag"], f["tag"])
        where = " ".join(x for x in (f.get("date"), f.get("place")) if x)
        extra = f' (with {who(f["spouse"])})' if f.get("spouse") else ""
        det = f' — {f["detail"]}' if f.get("detail") else ""
        confirms = "confirm" in (f.get("reasoning", "") + f.get("new_vs_tree", "")).lower()
        verb = f"Cite this record on the existing {what.lower()}" if confirms else f"Add {what}"
        per[f["on"]].append(f"{verb}{extra}: {where}{det} — cite {cite(f.get('sids'))}")
    fixes = collections.defaultdict(list)
    for n in L.get("notes", []):
        fixes[n["on"]].append(n["text"])

    out = ["# What to enter on MyHeritage",
           "",
           "Generated from `research/additions.json` by `tools/ledger_report.py`. The additions are already in "
           "`research/tree-research.ged` and any view built from it; entering them on MyHeritage keeps them in the next export. "
           "Every item names the record it rests on. Nothing here comes from another user's tree.",
           ""]
    people = sorted(set(per) | set(fixes), key=lambda x: who(x))
    out += [f"**{len(people)} people** · {sum(len(v) for v in per.values())} additions · "
            f"{sum(len(v) for v in fixes.values())} things to fix", ""]
    for x in people:
        out.append(f"## {who(x)}" + (f" `{x}`" if x.startswith("@") else ""))
        for ln in per.get(x, []):
            out.append(f"- [ ] {ln}")
        for t in fixes.get(x, []):
            out.append(f"- [ ] **Fix:** {t}")
        out.append("")
    open(out_path, "w", encoding="utf-8").write("\n".join(out))
    print(f"wrote {out_path}: {len(people)} people")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
