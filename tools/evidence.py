#!/usr/bin/env python3
"""
evidence.py — what a citation is worth, and what a person's record rests on.

    import evidence as E
    E.source_class(title, stype)      -> 0 record · 1 user tree · 2 Discovery · 3 Smart Match
    E.person_level(classes)           -> 0 backed by a record · 1 other trees only · 2 nothing
    E.sources(recs)                   -> {"@S1@": (title, type)}
    E.person_evidence(indi, fams, S)  -> (level, [class of every citation])

    python3 tools/evidence.py CLEAN.ged          # prints the counts

MyHeritage types every citation with `_TYPE` on the source record: "Collection",
"Discovery" or "Smart Matching". The trap is "Collection": MyHeritage files the
FamilySearch Family Tree, Geni, Filae, WikiTree and GenealogieOnline under it
too, and those are other people's trees, not records. Taking the label at its
word told 7,321 people they had a record when they had none (audit-data §1.3).

So the class is decided here, once, and every tool and the page read the same
answer:

  0  record      a Collection that is not a user tree — a census, a church book,
                 a death index, a newspaper. Evidence.
  1  user tree   a Collection that IS a user tree (the USERTREE rule below, the
                 one build_worklist.py introduced), or an untyped source with a
                 tree-like title. A lead, not evidence.
  2  Discovery   MyHeritage's Discovery matches — also other users' trees.
  3  Smart Match MyHeritage Smart Matching — another user's tree agreeing.

And the person's level is the strict rule: 0 only when at least one citation
is a class-0 record; 1 when every citation is someone else's tree; 2 when there
is no citation at all.
"""

import re
import sys
import collections

RECORD, USER_TREE, DISCOVERY, SMART_MATCH = 0, 1, 2, 3
CLASS_NAMES = ["record", "user tree", "Discovery", "Smart Match"]

BACKED, TREES_ONLY, NOTHING = 0, 1, 2
LEVEL_NAMES = ["backed by a record", "other users' trees only", "no sources"]

# A user-contributed tree is not a record, whatever the exporter labels it.
# Copied from build_worklist.py (USERTREE), which owns the original; extended
# with the two compiled-pedigree collections in this export that are user
# submissions too ("Everton Pedigree and Family Group Sheets").
USERTREE = re.compile(r"(Family Tree|Web Site|Family Site|Filae|Geni|WikiTree|"
                      r"Website|nettsted|webside|MyHeritage Family Trees|"
                      r"Family Group Sheets|Pedigree)", re.I)


def source_class(title, stype):
    """The class of one citation, from its source record's title and _TYPE."""
    t = (stype or "").strip().lower()
    if t.startswith("smart"):
        return SMART_MATCH
    if t == "discovery":
        return DISCOVERY
    if USERTREE.search(title or ""):
        return USER_TREE
    return RECORD


def person_level(classes):
    """0 backed by a record · 1 other trees only · 2 nothing."""
    classes = list(classes)
    if not classes:
        return NOTHING
    return BACKED if RECORD in classes else TREES_ONLY


def sources(recs):
    """{source id: (title, type)} for every SOUR record."""
    out = {}
    for sid, r in recs.items():
        if r.tag == "SOUR":
            t = r.kid("TITL")
            out[sid] = ((t.full_value().strip() if t is not None else ""),
                        (r.kid_value("_TYPE") or "").strip())
    return out


def _cites(node):
    """Every SOUR pointer anywhere under a record (person- and fact-level)."""
    stack = list(node.children)
    while stack:
        n = stack.pop()
        if n.tag in ("CONC", "CONT"):
            continue
        if n.tag == "SOUR" and n.value.startswith("@"):
            yield n.value
        stack.extend(n.children)


def person_evidence(indi, fams, srcmap):
    """(level, classes) for one INDI record.

    `fams` maps family id -> FAM record; a citation on the family (a marriage
    record) counts for both spouses, because it names both of them.
    """
    classes = [source_class(*srcmap.get(s, ("", ""))) for s in _cites(indi)]
    for fs in indi.kids("FAMS"):
        f = fams.get(fs.value)
        if f is not None:
            classes += [source_class(*srcmap.get(s, ("", ""))) for s in _cites(f)]
    return person_level(classes), classes


def main():
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gedcom_lib as G
    recs, roots, _ = G.parse(sys.argv[1])
    S = sources(recs)
    fams = {k: v for k, v in recs.items() if v.tag == "FAM"}
    lv, cl = collections.Counter(), collections.Counter()
    for r in recs.values():
        if r.tag != "INDI":
            continue
        level, classes = person_evidence(r, fams, S)
        lv[level] += 1
        cl.update(classes)
    print("citations by class:")
    for k in range(4):
        print(f"  {k} {CLASS_NAMES[k]:<12}{cl[k]:>8,}")
    print("people by evidence level:")
    for k in range(3):
        print(f"  {k} {LEVEL_NAMES[k]:<26}{lv[k]:>8,}")


if __name__ == "__main__":
    main()
