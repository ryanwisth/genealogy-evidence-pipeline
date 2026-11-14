#!/usr/bin/env python3
"""
relationship-path.py — find how two (or more) people in a GEDCOM are related.

Why this exists: MyHeritage will tell you *that* two people are connected, but the
path it shows is easy to lose and impossible to audit later. This reads the exported
GEDCOM directly and prints the full step-by-step path, marking whether each step is
blood or marriage, the distinction that matters when only descent by blood counts.

Usage
-----
  # who matches a name (run this first — names in the tree are often spelled differently)
  python3 relationship-path.py TREE.ged --find "Varnquist"

  # relationship between two people
  python3 relationship-path.py TREE.ged "David Varnquist" "Ruth Saltmere"

  # pin down an ambiguous name with its @ID@ from --find
  python3 relationship-path.py TREE.ged "@I46@" "@I12@"

Matching ignores accents and case, so "Muller" finds "Müller".
Read-only: never writes to the GEDCOM.

IMPORTANT — duplicate records
-----------------------------
A messy export can hold the same person as several un-merged records, which cuts
the tree into disconnected islands. Run cold on the tree this was written for, the
script reported NO PATH between the root person and a whole branch of their own
ancestors, which was wrong.

`aliases.tsv` (next to the GEDCOM, or passed with --alias) lists the known
duplicate pairs and is loaded AUTOMATICALLY. Each line is:

    @ID_A@    @ID_B@    # why we believe they are the same person

Find new duplicates with `tools/tree-audit.py dups`, and add them there.
Use --no-alias to see the raw, unbridged tree.
"""

import argparse
import os
import re
import sys
import unicodedata
from collections import deque


# ---------------------------------------------------------------- parsing

def norm(s):
    """Lowercase, strip accents and punctuation — so Müller == Muller == MUELLER."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "ss").replace("/", " ")
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def read_lines(path):
    """Decode the GEDCOM.

    MyHeritage exports declare `1 CHAR UTF-8` but are not always *valid* UTF-8 —
    a handful of stray bytes survive from the source data. Falling back to latin-1
    on those files is a trap: it silently turns "Müller" into "MÃ¼ller", which then
    fails every accent-normalised search. So honour the declared charset and replace
    the bad bytes instead, and only fall back if the header says otherwise.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    head = raw[:2048].decode("latin-1", "replace").upper()

    if "CHAR UTF-8" in head or "CHAR UNICODE" in head:
        try:
            return raw.decode("utf-8-sig").splitlines(), "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("utf-8-sig", "replace")
            bad = text.count("�")
            return text.splitlines(), f"utf-8 ({bad} undecodable byte(s) replaced)"

    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc).splitlines(), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace").splitlines(), "latin-1 (lossy)"


LINE = re.compile(r"^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\w+)(?:\s(.*))?$")


def parse(path):
    lines, enc = read_lines(path)
    indi, fam = {}, {}
    cur = None          # the record dict being filled
    kind = None         # 'INDI' or 'FAM'
    stack = []          # (level, tag) breadcrumb, so we know BIRT.DATE vs DEAT.DATE

    for raw in lines:
        m = LINE.match(raw)
        if not m:
            continue
        level, xref, tag, val = int(m.group(1)), m.group(2), m.group(3), (m.group(4) or "").strip()

        if level == 0:
            cur, kind = None, None
            if xref and tag == "INDI":
                cur = indi.setdefault(xref, {"id": xref, "name": "", "sex": "",
                                             "birt": "", "deat": "", "famc": [], "fams": []})
                kind = "INDI"
            elif xref and tag == "FAM":
                cur = fam.setdefault(xref, {"id": xref, "husb": None, "wife": None,
                                            "chil": [], "marr": ""})
                kind = "FAM"
            stack = []
            continue

        if cur is None:
            continue
        stack = [f for f in stack if f[0] < level]
        stack.append((level, tag))
        parent_tag = stack[-2][1] if len(stack) > 1 else None

        if kind == "INDI":
            if tag == "NAME" and not cur["name"]:
                cur["name"] = val.replace("/", "").strip()
            elif tag == "SEX":
                cur["sex"] = val[:1].upper()
            elif tag == "FAMC" and val:
                cur["famc"].append(val)
            elif tag == "FAMS" and val:
                cur["fams"].append(val)
            elif tag == "DATE" and parent_tag in ("BIRT", "DEAT"):
                key = parent_tag.lower()[:4]
                if not cur[key]:
                    cur[key] = val
        else:
            if tag == "HUSB" and val:
                cur["husb"] = val
            elif tag == "WIFE" and val:
                cur["wife"] = val
            elif tag == "CHIL" and val:
                cur["chil"].append(val)
            elif tag == "DATE" and parent_tag == "MARR" and not cur["marr"]:
                cur["marr"] = val

    return indi, fam, enc


def label(indi, pid):
    p = indi.get(pid)
    if not p:
        return pid
    years = "–".join(x for x in (yr(p["birt"]), yr(p["deat"])) if x)
    return f"{p['name'] or '(no name)'}" + (f" ({years})" if years else "") + f" {pid}"


def yr(datestr):
    m = re.search(r"\b(\d{4})\b", datestr or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- graph

def build_edges(indi, fam):
    """edges[pid] = list of (neighbour, kind) where kind is parent/child/spouse."""
    edges = {pid: [] for pid in indi}
    for f in fam.values():
        parents = [p for p in (f["husb"], f["wife"]) if p in indi]
        kids = [c for c in f["chil"] if c in indi]
        for c in kids:
            for p in parents:
                edges[c].append((p, "parent"))
                edges[p].append((c, "child"))
        if len(parents) == 2:
            edges[parents[0]].append((parents[1], "spouse"))
            edges[parents[1]].append((parents[0], "spouse"))
    return edges


def load_aliases(gedcom_path, explicit=None):
    """Read the duplicate-record map. Returns (pairs, path_used)."""
    path = explicit or os.path.join(os.path.dirname(os.path.abspath(gedcom_path)), "aliases.tsv")
    if not os.path.exists(path):
        return [], None
    pairs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("@") and parts[1].startswith("@"):
                pairs.append((parts[0], parts[1]))
    return pairs, path


def apply_aliases(edges, indi, pairs):
    """Join duplicate records with a zero-cost 'same-person' edge."""
    applied, skipped = 0, []
    for a, b in pairs:
        if a in indi and b in indi:
            edges.setdefault(a, []).append((b, "same-person"))
            edges.setdefault(b, []).append((a, "same-person"))
            applied += 1
        else:
            skipped.append((a, b))
    return applied, skipped


def bfs(edges, start, goal, max_depth=40):
    """Plain BFS — shortest path wins, which is the relationship MyHeritage reports."""
    if start == goal:
        return [(start, None)]
    seen = {start: None}
    q = deque([(start, 0)])
    while q:
        node, d = q.popleft()
        if d >= max_depth:
            continue
        for nxt, kind in edges.get(node, []):
            if nxt in seen:
                continue
            seen[nxt] = (node, kind)
            if nxt == goal:
                path, cur = [], nxt
                while cur is not None:
                    prev = seen[cur]
                    path.append((cur, prev[1] if prev else None))
                    cur = prev[0] if prev else None
                return list(reversed(path))
            q.append((nxt, d + 1))
    return None


ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
       7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def describe(kinds, sex_b):
    """Turn the edge sequence into English. Blood only if there is no spouse step."""
    if any(k == "spouse" for k in kinds):
        return "related by MARRIAGE (the path crosses a spouse link — not a blood line)"

    ups = sum(1 for k in kinds if k == "parent")
    downs = sum(1 for k in kinds if k == "child")

    # A pure up-then-down path means one common ancestor couple.
    if kinds != ["parent"] * ups + ["child"] * downs:
        return f"blood relative ({ups} generations up, {downs} down — non-standard path, read the steps)"

    def greats(n, base_m, base_f, neutral=None):
        word = {"M": base_m, "F": base_f}.get(sex_b, neutral or base_m)
        if n <= 0:
            return word
        return ("great-" * (n - 1) + "grand" + word) if n >= 1 else word

    if downs == 0:
        return {0: "the same person", 1: "parent", 2: "grandparent"}.get(
            ups, "great-" * (ups - 2) + "grandparent")
    if ups == 0:
        return {1: "child", 2: "grandchild"}.get(
            downs, "great-" * (downs - 2) + "grandchild")
    if ups == 1 and downs == 1:
        return {"M": "brother", "F": "sister"}.get(sex_b, "sibling")
    if ups == 1:
        n = downs - 1
        return ("great-" * (n - 1) + "nephew" if sex_b == "M"
                else "great-" * (n - 1) + "niece" if sex_b == "F"
                else "great-" * (n - 1) + "nephew/niece")
    if downs == 1:
        n = ups - 1
        return "great-" * (n - 1) + ("uncle" if sex_b == "M" else "aunt" if sex_b == "F" else "uncle/aunt")

    degree = min(ups, downs) - 1
    removed = abs(ups - downs)
    out = f"{ORD.get(degree, str(degree) + 'th')} cousin"
    if removed:
        out += f", {removed} time{'s' if removed > 1 else ''} removed"
    return out


# ---------------------------------------------------------------- lookup

def resolve(indi, query):
    if query.startswith("@") and query in indi:
        return [query]
    tokens = norm(query).split()
    hits = [pid for pid, p in indi.items()
            if all(t in norm(p["name"]) for t in tokens)]
    # exact full-name matches first
    hits.sort(key=lambda pid: (norm(indi[pid]["name"]) != norm(query), indi[pid]["name"]))
    return hits


def main():
    ap = argparse.ArgumentParser(description="How are these people related in a GEDCOM?")
    ap.add_argument("gedcom")
    ap.add_argument("names", nargs="*", help="two or more names, or @IDs")
    ap.add_argument("--find", metavar="NAME", help="just list everyone matching NAME")
    ap.add_argument("--alias", metavar="FILE", help="duplicate-record map (default: aliases.tsv beside the GEDCOM)")
    ap.add_argument("--no-alias", action="store_true", help="do NOT bridge duplicate records")
    args = ap.parse_args()

    indi, fam, enc = parse(args.gedcom)
    print(f"# {args.gedcom}: {len(indi)} individuals, {len(fam)} families ({enc})\n")

    if args.find:
        hits = resolve(indi, args.find)
        print(f"{len(hits)} match(es) for {args.find!r}:")
        for pid in hits[:200]:
            p = indi[pid]
            par = []
            for fc in p["famc"]:
                for role in ("husb", "wife"):
                    q = fam.get(fc, {}).get(role)
                    if q:
                        par.append(indi.get(q, {}).get("name", q))
            print(f"  {label(indi, pid)}" + (f"  [parents: {', '.join(par)}]" if par else ""))
        if len(hits) > 200:
            print(f"  … {len(hits) - 200} more")
        return

    if len(args.names) < 2:
        sys.exit("give at least two names, or use --find")

    picked = []
    for q in args.names:
        hits = resolve(indi, q)
        if not hits:
            sys.exit(f"no one matches {q!r} — try --find with a shorter fragment")
        if len(hits) > 1:
            print(f"! {q!r} is ambiguous ({len(hits)} matches); using the first. Pass an @ID@ to pin it:")
            for pid in hits[:10]:
                print(f"    {label(indi, pid)}")
            print()
        picked.append(hits[0])

    edges = build_edges(indi, fam)

    bridged = 0
    if not args.no_alias:
        pairs, apath = load_aliases(args.gedcom, args.alias)
        if pairs:
            bridged, skipped = apply_aliases(edges, indi, pairs)
            print(f"# bridged {bridged} duplicate-record pair(s) from {apath}")
            for a, b in skipped:
                print(f"#   ! skipped {a} <-> {b} (id not in this GEDCOM)")
            print()
        elif apath is None:
            print("# no aliases.tsv found — duplicate records are NOT bridged, "
                  "so a NO PATH result may be a data defect rather than a real gap.\n")

    for i in range(len(picked)):
        for j in range(i + 1, len(picked)):
            a, b = picked[i], picked[j]
            print("=" * 72)
            print(f"{label(indi, a)}\n  ->  {label(indi, b)}")
            path = bfs(edges, a, b)
            if not path:
                print("  NO PATH — not connected in this tree.")
                if not bridged:
                    print("  (No duplicate records were bridged. This tree is known to be split "
                          "into islands by un-merged duplicates — run `tree-audit.py dups` before "
                          "concluding these two are genuinely unrelated.)")
                print()
                continue
            # 'same-person' steps are bookkeeping, not generations — exclude them
            kinds = [k for _, k in path[1:] if k != "same-person"]
            print(f"\n  {describe(kinds, indi[b]['sex'])}   ({len(kinds)} steps)\n")
            for step, (pid, kind) in enumerate(path):
                if kind is None:
                    arrow = ""
                elif kind == "same-person":
                    arrow = "  ==SAME PERSON (duplicate record)==>  "
                else:
                    arrow = f"  --{kind}-->  "
                print(f"   {step:>2}. {arrow}{label(indi, pid)}")
            print()


if __name__ == "__main__":
    main()
