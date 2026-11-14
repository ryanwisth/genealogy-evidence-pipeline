#!/usr/bin/env python3
"""
tree-audit.py — structural checks on a MyHeritage GEDCOM export.

Written 2026-09-16 while working out how another user's Smart Match connected to
the tree. Every subcommand here answers a question that came up in that hunt and
would otherwise have to be re-derived from scratch.

Subcommands
-----------
  components            How many disconnected islands is the tree in, and which one
                        is each key person in? (This is what exposed the duplicates.)
  dups                  Candidate duplicate records: same normalised name + same
                        birth/death year. Feed confirmed ones into data/aliases.tsv.
  cites   @Sxxxxx@      Which individuals cite a given source record. Use it to see
                        exactly where another user's tree touched ours.
  sources [substring]   List SOUR records, newest MyHeritage "Smart Matching" first,
                        so you can see whose trees have matched.
  descend @Ixxxxx@      Print a person's descendants with spouses.
  surname NAME          Every individual with that surname, and which island each is in.

All subcommands are read-only.

Examples
--------
  python3 tools/tree-audit.py components
  python3 tools/tree-audit.py sources Smart
  python3 tools/tree-audit.py cites @S1@
  python3 tools/tree-audit.py descend @I46@
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
DEFAULT_GED = os.environ.get("GEDCOM", os.path.join(PROJECT, "sample", "sample-family.ged"))

sys.path.insert(0, HERE)
import importlib.util

_spec = importlib.util.spec_from_file_location("rp", os.path.join(HERE, "relationship-path.py"))
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

# People worth reporting on by default — the anchors of this project.
# Set LANDMARKS to the ids you care about; these are the sample family's.
LANDMARKS = {
    "@I46@": "root person (sample family)",
    "@I50@": "viewer (sample family)",
}


def load(path):
    indi, fam, enc = rp.parse(path)
    print(f"# {path}\n# {len(indi)} individuals, {len(fam)} families ({enc})\n")
    return indi, fam


def components(indi, fam, args):
    edges = rp.build_edges(indi, fam)
    comp, seen, cid = {}, set(), 0
    sizes = {}
    for pid in indi:
        if pid in seen:
            continue
        stack, members = [pid], []
        seen.add(pid)
        while stack:
            n = stack.pop()
            members.append(n)
            comp[n] = cid
            for nxt, _ in edges.get(n, []):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        sizes[cid] = len(members)
        cid += 1

    print(f"{len(indi)} individuals in {cid} disconnected component(s)\n")
    print("Largest:")
    for c, n in sorted(sizes.items(), key=lambda kv: -kv[1])[:10]:
        surnames = Counter()
        for p, pc in comp.items():
            if pc == c and indi[p]["name"]:
                surnames[indi[p]["name"].split()[-1]] += 1
        top = ", ".join(s for s, _ in surnames.most_common(6))
        print(f"  island {c:>3}: {n:>6} people   [{top}]")

    print("\nLandmark people:")
    for pid, who in LANDMARKS.items():
        if pid in comp:
            c = comp[pid]
            print(f"  island {c:>3} (size {sizes[c]:>6})  {pid}  {who}")
        else:
            print(f"  {'MISSING':>17}  {pid}  {who}")
    print("\nTwo landmarks in different islands that should be connected = a duplicate-record\n"
          "defect, not a genealogical gap. Run `dups`, then record the pair in data/aliases.tsv.")


def dups(indi, fam, args):
    buckets = defaultdict(list)
    for pid, p in indi.items():
        if not p["name"]:
            continue
        key = (rp.norm(p["name"]), rp.yr(p["birt"]), rp.yr(p["deat"]))
        if not key[1] and not key[2]:
            continue          # no dates at all — too weak to call
        buckets[key].append(pid)

    groups = [v for v in buckets.values() if len(v) > 1]
    print(f"{len(groups)} candidate duplicate group(s) "
          f"(identical name + identical birth/death year)\n")
    known = set()
    apath = os.path.join(PROJECT, "data", "aliases.tsv")
    if os.path.exists(apath):
        pairs, _ = rp.load_aliases(os.path.join(PROJECT, "data", "x.ged"), apath)
        for a, b in pairs:
            known.add(frozenset((a, b)))

    shown = 0
    for g in sorted(groups, key=lambda g: -len(g)):
        tag = ""
        if len(g) == 2 and frozenset(g) in known:
            tag = "   [already in aliases.tsv]"
        print(f"  {rp.label(indi, g[0])}{tag}")
        for pid in g[1:]:
            print(f"    == {rp.label(indi, pid)}")
        shown += 1
        if args.limit and shown >= args.limit:
            print(f"\n  … stopping at {args.limit}; pass --limit 0 for all")
            break
    print("\nA shared name+year is a CANDIDATE, not proof — common names collide. Confirm by\n"
          "checking the two records carry complementary family links before aliasing them.")


def cites(indi, fam, args):
    target = args.arg
    raw = open(args.gedcom, "rb").read().decode("utf-8-sig", "replace")
    cur = curtag = None
    names, hits = {}, []
    for ln in raw.splitlines():
        ln = ln.rstrip("\r")
        m = re.match(r"^0 (@[^@]+@) (\w+)", ln)
        if m:
            cur, curtag = m.group(1), m.group(2)
            continue
        if cur and curtag == "INDI" and ln.startswith("1 NAME ") and cur not in names:
            names[cur] = ln[7:].replace("/", "").strip()
        if target in ln and not ln.startswith("0 "):
            hits.append((cur, curtag))
    seen = []
    for pid, tag in hits:
        if pid and pid not in [s[0] for s in seen]:
            seen.append((pid, tag))
    print(f"{len(seen)} record(s) cite {target}:\n")
    for pid, tag in seen:
        print(f"  {tag} {pid}  {names.get(pid, '(name not on record)')}")


def sources(indi, fam, args):
    raw = open(args.gedcom, "rb").read().decode("utf-8-sig", "replace")
    recs, cur = [], None
    for ln in raw.splitlines():
        ln = ln.rstrip("\r")
        m = re.match(r"^0 (@S[^@]+@) SOUR", ln)
        if m:
            cur = {"id": m.group(1), "auth": "", "titl": "", "type": ""}
            recs.append(cur)
            continue
        if re.match(r"^0 ", ln):
            cur = None
            continue
        if cur:
            if ln.startswith("1 AUTH "):
                cur["auth"] = ln[7:].strip()
            elif ln.startswith("1 TITL "):
                cur["titl"] = ln[7:].strip()
            elif ln.startswith("1 _TYPE "):
                cur["type"] = ln[8:].strip()
    q = (args.arg or "").lower()
    sel = [r for r in recs if not q or q in (r["auth"] + r["titl"] + r["type"]).lower()]
    print(f"{len(sel)} of {len(recs)} source record(s) matching {args.arg or '(all)'}:\n")
    for r in sel[:400]:
        who = f"  by {r['auth']}" if r["auth"] else ""
        kind = f"  [{r['type']}]" if r["type"] else ""
        print(f"  {r['id']}  {r['titl']}{who}{kind}")
    if len(sel) > 400:
        print(f"  … {len(sel) - 400} more")
    print("\n'_TYPE Smart Matching' = another MyHeritage user's tree, not a document.\n"
          "The AUTH name is that account holder — NOT necessarily a relative.")


def descend(indi, fam, args):
    root = args.arg
    if root not in indi:
        hits = rp.resolve(indi, root)
        if not hits:
            sys.exit(f"no one matches {root!r}")
        root = hits[0]

    def spouses(pid):
        out = []
        for fs in indi[pid]["fams"]:
            for r in ("husb", "wife"):
                q = fam.get(fs, {}).get(r)
                if q and q != pid:
                    out.append(q)
        return out

    def kids(pid):
        out = []
        for fs in indi[pid]["fams"]:
            out += [c for c in fam.get(fs, {}).get("chil", []) if c in indi]
        return out

    def walk(pid, d=0, pre=""):
        sp = spouses(pid)
        s = "  m. " + "; ".join(indi[x]["name"] for x in sp) if sp else ""
        print(f"{pre}{rp.label(indi, pid)}{s}")
        if d >= args.depth:
            return
        for c in kids(pid):
            walk(c, d + 1, pre + "   ")

    walk(root)


def surname(indi, fam, args):
    edges = rp.build_edges(indi, fam)
    comp, seen, cid = {}, set(), 0
    sizes = {}
    for pid in indi:
        if pid in seen:
            continue
        stack, members = [pid], []
        seen.add(pid)
        while stack:
            n = stack.pop()
            members.append(n)
            comp[n] = cid
            for nxt, _ in edges.get(n, []):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        sizes[cid] = len(members)
        cid += 1
    hits = rp.resolve(indi, args.arg)
    print(f"{len(hits)} individual(s) matching {args.arg!r}:\n")
    for pid in hits[:300]:
        c = comp[pid]
        print(f"  island {c:>3} (size {sizes[c]:>6})  {rp.label(indi, pid)}")
    if len(hits) > 300:
        print(f"  … {len(hits) - 300} more")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["components", "dups", "cites", "sources", "descend", "surname"])
    ap.add_argument("arg", nargs="?", help="source id / person id / surname, per subcommand")
    ap.add_argument("--gedcom", default=DEFAULT_GED)
    ap.add_argument("--limit", type=int, default=40, help="dups: max groups to print (0 = all)")
    ap.add_argument("--depth", type=int, default=4, help="descend: generations")
    args = ap.parse_args()

    if not os.path.exists(args.gedcom):
        sys.exit(f"GEDCOM not found: {args.gedcom}\n"
                 f"Is the Extreme SSD mounted? See the project README.")
    if args.cmd in ("cites", "descend", "surname") and not args.arg:
        sys.exit(f"`{args.cmd}` needs an argument")

    indi, fam = load(args.gedcom)
    {"components": components, "dups": dups, "cites": cites,
     "sources": sources, "descend": descend, "surname": surname}[args.cmd](indi, fam, args)


if __name__ == "__main__":
    main()
