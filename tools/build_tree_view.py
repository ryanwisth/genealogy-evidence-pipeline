#!/usr/bin/env python3
"""
build_tree_view.py — lay out the connected family group for an interactive view.

    python3 tools/build_tree_view.py CLEAN.ged OUT.json --root ID --me ID

Tens of thousands of people will not fit in a readable static picture at any zoom, so this
produces coordinates for a pannable canvas instead of an image.

Layout
------
  y = birth year. That is the axis that means something in a genealogy, and
      most records have one. Where a year is missing it is estimated from
      relatives — a parent's year plus a generation, a child's minus one — and
      the estimate is marked so the view can show it differently.

  x = position in a depth-first walk down from the earliest ancestors, children
      visited in birth order. Relatives come out adjacent, so each descent line
      reads as a vertical band and the whole group reads as a river widening
      through time.

Also emits, per person, the branch they descend from (the founding ancestor the
walk reached them through), which is what makes the separate lines visible as
distinct bands of colour.

Data contract v2 (docs/payload-stamp.md) — added, nothing existing renamed or reordered
-----------------------------------------------------------------------
  stamp    sha1("\\n".join(ids))[:16]; the details payload carries the same one
  meIndex  index of --me (the viewer), -1 if not drawn
  names    cleaned display names (display_name(): no repeated words, no glued
           "/" alternates, no titles, ALL-CAPS title-cased, placeholders and
           database ids out); the raw form is in the details
           payload as "Recorded as"
  surn     display surname          aka   [[also-known-as names]] for search
  ev       0 backed by a record · 1 other users' trees only · 2 no sources
  flags    1 the root's line · 2 birth year estimated from relatives · 4 the root ·
           8 female · 16 male · 32 died, date unknown · 64 recorded birth year
           approximate (ABT/EST/BEF…, or a christening year) · 128 living
           (Gramps rule: no death/burial/cremation, born < 100 years ago)
  stats    lineSize (people with bit 1), living, livingRule

The person order is a pure function of the GEDCOM: every loop that feeds the
layout walks people sorted by id, so a rebuild gives the same ids order and
the same stamp (it used to follow Python's per-run set order).
"""

import re
import sys
import os
import json
import gzip
import base64
import hashlib
import argparse
import datetime
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gedcom_lib as G
import dup_scan as D
import build_stats
import build_details
import evidence

GEN = 30          # years per generation, for estimating a missing birth year

# Where a person was born, grouped into broad regions for colour.
# Surnames were tried first and abandoned: in a large tree thousands of distinct
# surnames share the colours, and the top two dozen cover a small fraction.
# Birth place works because most people have one. Edit the table for your tree.
#
# Order matters: the first pattern that matches a place string wins, so the
# more specific regions come before the countries that contain them.
REGIONS = [
    ("Canada", ["canada", "ontario", "quebec", "québec", "manitoba", "saskatchewan",
                "alberta", "british columbia", "nova scotia", "new brunswick"]),
    ("United States", ["united states", "usa", "u.s.", "america", "california",
                       "new york", "texas", "illinois", "ohio", "pennsylvania",
                       "michigan", "minnesota", "missouri", "washington", "oregon",
                       "colorado", "nebraska", "massachusetts"]),
    ("Latin America", ["mexico", "brazil", "argentina", "chile", "peru", "colombia", "cuba"]),
    ("Nordic countries", ["norway", "norge", "sweden", "sverige", "denmark", "danmark",
                          "finland", "suomi", "iceland"]),
    ("British Isles", ["united kingdom", "england", "scotland", "wales", "ireland"]),
    ("Low Countries", ["netherlands", "nederland", "holland", "belgium", "luxembourg"]),
    ("France", ["france"]),
    ("Central Europe", ["germany", "deutschland", "austria", "switzerland", "czech",
                        "hungary", "poland", "polska"]),
    ("Southern Europe", ["italy", "italia", "spain", "portugal", "greece"]),
    ("Eastern Europe", ["russia", "ukraine", "belarus", "romania", "lithuania",
                        "latvia", "estonia"]),
]


def region_of(place):
    """Which region a birth place belongs to, or None."""
    if not place:
        return None
    s = place.lower()
    for name, keys in REGIONS:
        for k in keys:
            if k in s:
                return name
    return None


# ---- display names ------------------------------------------------------
# The display name used to be the first NAME line with its slashes deleted,
# which glued alternative surnames together ("/Lefevre/Lefèvre/" ->
# "LefevreLefèvre") and kept everything MyHeritage had typed into it: the
# surname repeated inside the given names ("John Doe doe"), titles
# ("MRS. Doe Doe"), all-caps surnames, placeholder words and
# database ids. The raw form is never lost — build_details
# lists it as "Recorded as" whenever the cleaned name differs.
_TITLES = re.compile(r"^(?:(?:mrs|mr|ms|miss|dr|rev|fr|lt|col|capt|sgt|prof|sir|"
                     r"lady|pastor|elder|mme|mlle|herr|frau|fru|hr)\.?(?:\s+|$))+", re.I)
_PARTICLES = {"von", "van", "der", "den", "de", "du", "la", "le", "di", "da",
              "dit", "del", "della", "des", "ten", "ter", "zu", "zum", "zur",
              "vom", "af", "av", "y", "e", "op", "het", "'t", "dos", "das", "do",
              "und", "og", "et", "i", "à", "au", "aux", "auf", "an", "am", "in",
              "of", "and", "st.", "ste."}
_PLACEHOLDER = re.compile(r"^(unknown|unk|n\.?\s?n\.?|nn|inconnue?|ukjent|"
                          r"unbekannt|living|private|\[private\]|no name|"
                          r"\(no name\)|unnamed|\?+|_+|-+)$", re.I)
_MAIDEN = re.compile(r"\(\s*(?:born|née|nee|geb\.?|geborene|født|f\.)\s+([^)]+)\)", re.I)
_FSID = re.compile(r"\(\s*[A-Z0-9]{4}-[A-Z0-9]{3,4}\s*\)")
_ROMAN = re.compile(r"^[IVX]+\.?$")
NAME_FIXES = ("title stripped", "placeholder removed", "repeated word removed",
              "all-caps word title-cased", "lower-case word capitalised",
              "alternate spelling moved to aka", "maiden name moved to aka",
              "digits or ids removed")


def _split_name(value):
    """(given, surname, suffix) from a GEDCOM NAME value."""
    v = " ".join((value or "").split())
    m = re.match(r"^(.*?)(?:^|\s)/(.*)/([^/]*)$", v)
    if m:
        return m.group(1), m.group(2), m.group(3)
    if v.count("/") == 1 and v.endswith("/"):
        return "", v[:-1], ""
    return v, "", ""


def _scrub(s, fixes):
    """Ids, bare digits and stray brackets out of one name part."""
    t = _FSID.sub(" ", s)
    t = re.sub(r"\[\s*\d*\s*\]?", " ", t)
    t = re.sub(r"(?<![\w(])\d+(?![\w)])(?![^(]*\))", " ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"(\S)\(", r"\1 (", t)
    t = " ".join(t.split()).strip(" ,;")
    if t != " ".join(s.split()).strip(" ,;"):
        fixes.add("digits or ids removed")
    return t


def _case(tok, surname, fixes):
    core = re.sub(r"[^\w]", "", tok)
    if not core or _ROMAN.match(tok):
        return tok
    # all capitals — allowing one lower-case accented letter, which is how a
    # mis-decoded capital arrives ("LEFéVRE")
    low = [ch for ch in core if ch.islower()]
    caps = core.isupper() or (len(core) >= 4 and len(low) == 1 and ord(low[0]) > 127)
    if caps and core.isalpha() and len(core) >= (2 if surname else 3):
        fixes.add("all-caps word title-cased")
        return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[0] + m.group(0)[1:].lower(), tok)
    if tok[0].islower() and tok.lower() not in _PARTICLES and len(core) > 1 \
            and not re.match(r"^[a-z]'", tok):
        fixes.add("lower-case word capitalised")
        return tok[0].upper() + tok[1:]
    return tok


def _fold(s):
    return build_details.fold(re.sub(r"[.,;]+$", "", s))


def _clean(given, sur, suffix, fixes):
    """The cleaning pipeline for one NAME: (display, given part, surname part,
    other given spellings, other surnames, maiden names)."""
    maiden = [m.group(1).strip() for m in _MAIDEN.finditer(given + " " + sur)]
    if maiden:
        fixes.add("maiden name moved to aka")
        given, sur = _MAIDEN.sub(" ", given), _MAIDEN.sub(" ", sur)
    t = _TITLES.sub("", given.strip())
    if t != given.strip():
        fixes.add("title stripped")
    given = t
    # alternatives written with a slash: "Johanna/Janna", "Lefevre/LEFÈVRE"
    galts = [g.strip() for g in given.split("/") if g.strip()]
    salts = [x.strip() for x in sur.split("/") if x.strip()]
    if len(galts) > 1 or len(salts) > 1:
        fixes.add("alternate spelling moved to aka")
    given = galts[0] if galts else ""
    sur = salts[0] if salts else ""
    given, sur, suffix = (_scrub(given, fixes), _scrub(sur, fixes),
                          _scrub(suffix, fixes))

    def placeholder(w):
        return bool(_PLACEHOLDER.match(w.strip().strip("()[]").strip()))

    def toks(text, surname):
        out = []
        for w in text.split():
            if placeholder(w):
                fixes.add("placeholder removed")
                continue
            out.append(_case(w, surname, fixes))
        return out
    if placeholder(given):
        fixes.add("placeholder removed")
        given = ""
    if placeholder(sur):
        fixes.add("placeholder removed")
        sur = ""
    words = [(w, "g") for w in toks(given, False)] + \
            [(w, "s") for w in toks(sur, True)] + \
            [(w, "x") for w in toks(suffix, False)]
    kept = []
    for w, part in words:
        if kept and _fold(w) and _fold(w) == _fold(kept[-1][0]):
            fixes.add("repeated word removed")
            if kept[-1][1] == "g" and part == "s":
                kept[-1] = (w, part)          # keep the surname's copy
            continue
        kept.append((w, part))
    disp = " ".join(w for w, _ in kept)
    first = " ".join(w for w, p in kept if p == "g")
    surname = " ".join(w for w, p in kept if p == "s")
    return disp, first, surname, galts[1:], salts[1:], maiden


def display_name(rec):
    """(display, surname, [also-known-as], {fixes}) for one INDI record."""
    fixes = set()
    nls = rec.kids("NAME")
    # the first NAME line that says something: MyHeritage writes an empty
    # "NAME //" for a person it knows only as someone's mother, and research
    # (research/additions.json) can add the name a record gives her as a
    # second NAME line
    named = [n for n in nls if (n.full_value() or "").replace("/", "").strip()]
    if named and named[0] is not nls[0]:
        nls = named + [n for n in nls if n not in named]
    raw = nls[0].full_value() if nls else ""
    disp, first, surname, galts, salts, maiden = _clean(*_split_name(raw), fixes)

    # also-known-as: alternate spellings, maiden and married names, the other
    # NAME lines, nicknames — what name search should also find.
    fx = set()

    def part(text, surn):
        return _clean("" if surn else text, text if surn else "", "", fx)[0]
    aka = [part(g, False) + " " + surname for g in galts]
    aka += [first + " " + part(x, True) for x in salts + maiden]
    for k, nm in enumerate(nls):
        if k > 0:
            aka.append(_clean(*_split_name(nm.full_value()), fx)[0])
        for c in nm.kids("_MARNM"):
            v = c.full_value() or ""
            if not re.search(r"\bnot\b", v, re.I):
                aka.append(first + " " + part(re.sub(r"\(.*?\)", " ", v), True))
        for c in list(nm.kids("_AKA")) + list(nm.kids("_FORMERNAME")):
            aka.append(_clean(*_split_name(c.full_value() or ""), fx)[0])
        for c in nm.kids("NICK"):
            aka.append(part(c.full_value() or "", False) + " " + surname)
    out, seen = [], {_fold(disp)}
    for a in aka:
        a = " ".join(a.split())
        k = _fold(a)
        if a and k not in seen:
            seen.add(k)
            out.append(a)
    return disp or "(no name)", surname, out, fixes


def old_display(rec):
    """The display name the previous build produced, for the before/after log."""
    nm = rec.kid("NAME")
    disp = nm.full_value().replace("/", "").strip() if nm else ""
    return " ".join(disp.split()) or "(no name)"


def name_defects(names):
    """Counts of the audit-data §1.7 defects over a list of display names."""
    c = Counter()
    for n in names:
        ws = n.split()
        fw = [build_details.fold(re.sub(r"[^\w]", "", w)) for w in ws]
        if any(a and a == b for a, b in zip(fw, fw[1:])):
            c["repeated adjacent word"] += 1
        if any(re.search(r"[a-zà-ÿ][A-ZÀ-Þ]{2,}|[a-zà-ÿ]{3}[A-ZÀ-Þ][a-zà-ÿ]{3}", w)
               and not re.match(r"^(Mc|Mac|De|Le|La|Van|Von|Di|Da|O')", w) for w in ws):
            c["glued alternates (camel-case join)"] += 1
        if any(re.sub(r"[^\w]", "", w).isupper() and re.sub(r"[^\w]", "", w).isalpha()
               and len(re.sub(r"[^\w]", "", w)) >= 2 and not _ROMAN.match(w) for w in ws):
            c["ALL-CAPS word"] += 1
        if _TITLES.match(n):
            c["leading title"] += 1
        if any(_PLACEHOLDER.match(w) for w in ws) or n == "(no name)":
            c["placeholder or (no name)"] += 1
        if re.search(r"(?<!\()\b\d+\b(?![^(]*\))", n):
            c["digits outside brackets"] += 1
        if "/" in n:
            c["slash in name"] += 1
        if n != "(no name)" and any(
                w[:1].islower() and w.lower() not in _PARTICLES and len(w) > 1
                and not re.match(r"^[a-z]'", w) for w in ws):
            c["lower-case word"] += 1
    return c


# ---- flags --------------------------------------------------------------
_APPROX = re.compile(r"^\s*(ABT|ABOUT|EST|CAL|BEF|BEFORE|AFT|AFTER|BET|FROM|TO|"
                     r"CIR|CIRCA|CA\.?|C\.|UM|OMKRING|CIRKA|ANSLAGSVIS|~)\b|\?", re.I)


def death_evidence(rec):
    """The Gramps rule: any death, burial or cremation event, dated or not."""
    for c in rec.children:
        if c.tag in ("DEAT", "BURI", "CREM"):
            return True
        if c.tag == "EVEN" and build_details.fact_kind("EVEN", c) in (
                build_details.K["death"], build_details.K["burial"]):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--root", required=True,
                    help="the person whose direct line the view highlights")
    ap.add_argument("--me", required=True,
                    help="the viewer (the viewer): kinship is named relative to them")
    args = ap.parse_args()
    # Every loop below that feeds the layout walks people in id order. It
    # used to walk Python sets, whose order is randomised per run, so two
    # builds of the same GEDCOM put people in a different ORDER — and every
    # payload that addresses people by index (search, map) silently pointed at
    # strangers after a rebuild . Sorted, the order is a pure
    # function of the GEDCOM, and so is the stamp.
    S = sorted

    people, fam, recs, roots_, meta = D.build(args.src)
    parents, children, spouses, siblings = D.family_index(people, fam)
    print(f"{len(people)} individuals, {len(fam)} families")
    # cleaned display names for every INDI (spouse names in details too)
    dn = {p: display_name(people[p].rec) for p in sorted(people)}
    display = {p: v[0] for p, v in dn.items()}

    # ---- largest connected group ---------------------------------------
    adj = defaultdict(set)
    for pid in people:
        adj[pid] |= parents[pid] | children[pid] | spouses[pid]
    seen, comps = set(), []
    for pid in people:
        if pid in seen:
            continue
        stack, mem = [pid], []
        seen.add(pid)
        while stack:
            x = stack.pop()
            mem.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        comps.append(mem)
    comps.sort(key=len, reverse=True)
    group = set(comps[0])
    gsorted = sorted(group)
    print(f"largest group: {len(group)} people")
    if args.root not in group:
        print(f"  !! {args.root} is not in the largest group")

    # ---- birth years, estimated where missing --------------------------
    year = {}
    for pid in gsorted:
        p = people[pid]
        if p.by:
            year[pid] = float(p.by)
    estimated = set()
    # Propagate from known years through parent/child links until stable.
    for _ in range(40):
        changed = 0
        for pid in gsorted:
            if pid in year:
                continue
            cands = []
            for q in S(parents[pid]):
                if q in year:
                    cands.append(year[q] + GEN)
            for q in S(children[pid]):
                if q in year:
                    cands.append(year[q] - GEN)
            for q in S(spouses[pid]):
                if q in year:
                    cands.append(year[q])
            for q in S(siblings[pid]):
                if q in year:
                    cands.append(year[q])
            if cands:
                year[pid] = sum(cands) / len(cands)
                estimated.add(pid)
                changed += 1
        if not changed:
            break
    # An estimate chained through several generations can drift outside any
    # plausible range; clamp it rather than letting one outlier stretch the
    # whole canvas.
    recorded = {p for p in group if people[p].by}
    for pid in gsorted:
        year.setdefault(pid, 1800.0)
        if pid in estimated:
            year[pid] = min(2026.0, max(1400.0, year[pid]))
        if pid not in recorded:
            estimated.add(pid)
    print(f"  birth years: {len(group) - len(estimated)} recorded, "
          f"{len(estimated)} estimated from relatives")

    # ---- x order: descent-first walk from the earliest ancestors -------
    founders = sorted((p for p in group if not parents[p]), key=lambda p: (year[p], p))
    order, placed = [], set()
    branch = {}

    def walk(root):
        stack = [root]
        while stack:
            p = stack.pop()
            if p in placed:
                continue
            placed.add(p)
            order.append(p)
            branch[p] = root
            # spouses sit beside their partner
            for s in sorted(spouses[p], key=lambda q: (year[q], q)):
                if s not in placed:
                    placed.add(s)
                    order.append(s)
                    branch[s] = root
            kids = sorted(children[p], key=lambda q: (-year[q], q))
            for c in kids:
                if c not in placed:
                    stack.append(c)

    for f in founders:
        walk(f)
    for p in sorted(group, key=lambda q: (year[q], q)):
        if p not in placed:
            walk(p)
    print(f"  founders: {len(founders)}; ordered {len(order)}")

    xof = {p: i for i, p in enumerate(order)}

    # ---- the highlighted direct line -----------------------------------
    line = set()
    if args.root in group:
        stack = [args.root]
        line.add(args.root)
        while stack:                      # ancestors
            p = stack.pop()
            for q in parents[p]:
                if q not in line:
                    line.add(q)
                    stack.append(q)
        stack = [args.root]
        while stack:                      # descendants
            p = stack.pop()
            for q in children[p]:
                if q not in line:
                    line.add(q)
                    stack.append(q)
    print(f"  direct line of {args.root}: {len(line)} people")

    # ---- region, inherited from relatives where the record has no place --
    region = {}
    for pid in gsorted:
        r = region_of(G.event_place(people[pid].rec, "BIRT"))
        if r:
            region[pid] = r
    direct = len(region)
    for _ in range(30):
        changed = 0
        for pid in gsorted:
            if pid in region:
                continue
            votes = Counter()
            for q in S(parents[pid]) + S(children[pid]) + S(siblings[pid]):
                if q in region:
                    votes[region[q]] += 2
            for q in S(spouses[pid]):
                if q in region:
                    votes[region[q]] += 1
            if votes:
                region[pid] = votes.most_common(1)[0][0]
                changed += 1
        if not changed:
            break
    print(f"  region: {direct} from their own birth place, "
          f"{len(region) - direct} inherited, {len(group) - len(region)} unknown")
    rnames = [r for r, _ in Counter(region.values()).most_common()]
    rindex = {r: i for i, r in enumerate(rnames)}

    xof_pre = {p: i for i, p in enumerate(order)}

    # ---- distance from the highlighted line -----------------------------
    # This is the radius in the 3D view: the traced line becomes the trunk at
    # the centre and everyone else sits at the number of family steps they
    # really are from it, so "how is this person related to me" is a distance
    # you can see rather than a path you have to read.
    from collections import deque
    INF = 10 ** 9
    dist = {p: INF for p in group}
    dq = deque()
    for p in (line or {args.root}):
        if p in dist:
            dist[p] = 0
            dq.append(p)
    while dq:
        p = dq.popleft()
        for q in list(parents[p]) + list(children[p]) + list(spouses[p]):
            if q in dist and dist[q] > dist[p] + 1:
                dist[q] = dist[p] + 1
                dq.append(q)
    # ---- angle: every limb owns a wedge, and divides it among its own ---
    #
    # The first version gave each person their attachment point's angle plus an
    # offset of 0.62/distance — a spread that SHRANK the further out you went.
    # Branches therefore converged instead of diverging, and what should have
    # been a fan came out as a handful of thin smears leaning to one side: a
    # quarter of the family ended up inside a tenth of the angular range.
    # Trees do the opposite. A limb that carries a thousand descendants needs a
    # thousand people's worth of room, and it needs more of it the further from
    # the trunk it gets, not less.
    #
    # So each node owns an arc, and hands each of its own a slice of that arc
    # in proportion to how many descendants come through them. Nobody is ever
    # placed outside their parent's wedge, which is what keeps a limb readable
    # as one limb; and a wedge is never subdivided below what its population
    # needs, which is what stops the far side collapsing into a line.
    import math
    theta = {}
    trunk_sorted = sorted((p for p in group if dist.get(p) == 0),
                          key=lambda p: (year[p], xof_pre.get(p, 0)))

    # 1 · the BFS tree outward from the trunk, so every person has one parent
    #     in the layout even though the family graph has many paths.
    kids_of = defaultdict(list)
    seen2 = set(trunk_sorted)
    dq2 = deque(trunk_sorted)
    while dq2:
        p = dq2.popleft()
        for q in sorted(set(list(parents[p]) + list(children[p]) + list(spouses[p])),
                        key=lambda z: (year.get(z, 0), z)):
            if q in dist and q not in seen2:
                seen2.add(q)
                kids_of[p].append(q)
                dq2.append(q)

    # 2 · how many people hang off each node, counted from the leaves back.
    order2 = []
    st = list(trunk_sorted)
    while st:
        p = st.pop()
        order2.append(p)
        st.extend(kids_of[p])
    weight = {}
    for p in reversed(order2):
        weight[p] = 1 + sum(weight[q] for q in kids_of[p])

    # 3 · hand the arcs out. The trunk splits the full turn between its own
    #     limbs by weight; everyone below divides what they were given.
    arc = {}
    total = sum(weight[p] for p in trunk_sorted) or 1
    a = 0.0
    for p in trunk_sorted:
        w = 2 * math.pi * weight[p] / total
        arc[p] = (a, a + w)
        a += w
    for p in order2:
        a0, a1 = arc.get(p, (0.0, 2 * math.pi))
        theta[p] = (a0 + a1) / 2
        ks = kids_of[p]
        if not ks:
            continue
        tot = sum(weight[q] for q in ks) or 1
        # A limb keeps a little of its own arc rather than handing every
        # radian to its children, so a parent is not buried inside the fan of
        # its descendants.
        keep = 0.12 * (a1 - a0)
        cur = a0 + keep / 2
        span = (a1 - a0) - keep
        for q in ks:
            w = span * weight[q] / tot
            arc[q] = (cur, cur + w)
            cur += w
    for p in group:
        theta.setdefault(p, 0.0)

    far = max((v for v in dist.values() if v < INF), default=0)
    for p in dist:
        if dist[p] >= INF:
            dist[p] = far + 1
    print(f"  distance from that line: 0–{far}")

    # ---- radius: equal area, not equal distance ------------------------
    # Radius used to be the step count times a constant, and the step counts
    # are not spread evenly: 40% of the family is within six steps and 96%
    # within twenty, while the outer third of the disc held 4% of them. Drawn
    # that way the model is a spike standing in an empty cylinder, which is
    # exactly what it looked like.
    #
    # Area grows with the square of the radius, so to give each step the room
    # its population needs the radius has to follow the square root of the
    # running total. A step with 1,800 people in it then gets a wide band and
    # a step with 12 gets a thin one, and the family fills the space it is
    # drawn in. The meaning is unchanged — further out is still further from
    # the line — but the scale is now population, so it is no longer linear in
    # steps and the rings are labelled accordingly.
    pop = Counter(dist[p] for p in group)
    ntot = sum(pop.values()) or 1
    run, radius_of = 0, {}
    for d in sorted(pop):
        lo = run / ntot
        run += pop[d]
        hi = run / ntot
        # place the step at the middle of the band its population occupies
        radius_of[d] = math.sqrt((lo + hi) / 2)
    # The line itself is the trunk and belongs on the axis. Equal-area would
    # put its 230 people at 0.07, which is a visible hollow pipe rather than a
    # spine; the page gives them their own small spread instead.
    radius_of[0] = 0.0
    print("  radius: equal-area (" +
          ", ".join(f"{d}→{radius_of[d]:.2f}" for d in sorted(pop)[:6]) + " …)")

    # ---- branches worth colouring --------------------------------------
    sizes = Counter(branch.values())
    top = [b for b, _ in sizes.most_common(11)]
    bindex = {b: i for i, b in enumerate(top)}

    # ---- label anchors --------------------------------------------------
    # A point cloud with nothing written on it reads as noise. These put the
    # biggest families' names into the scene itself, each anchored on a real
    # member near the middle of that family rather than on a centroid that
    # might float in empty space.
    import math as _m
    ymin0 = min(year[p] for p in group)
    RMAX = 9.4 * far          # the radius the old linear scale reached
    def coords(p):
        th = theta[p]
        rr = radius_of.get(dist[p], 1.0) * RMAX
        return (rr * _m.cos(th), year[p] - ymin0, rr * _m.sin(th))
    bysur = defaultdict(list)
    for p in gsorted:
        sn = people[p].surn
        if sn and sn not in ("", "?"):
            bysur[sn].append(p)
    labels = []
    for sn, mem in sorted(bysur.items(), key=lambda kv: -len(kv[1]))[:26]:
        if len(mem) < 45:
            continue
        cx = sum(coords(m)[0] for m in mem) / len(mem)
        cy = sum(coords(m)[1] for m in mem) / len(mem)
        cz = sum(coords(m)[2] for m in mem) / len(mem)
        best, bd = None, None
        for m in mem:
            x, y, z = coords(m)
            d = (x-cx)**2 + (y-cy)**2 + (z-cz)**2
            if bd is None or d < bd:
                bd, best = d, m
        # the spelling most of them carry ("Grønvoldsdatter"), not the folded key
        shown = Counter(dn[m][1] for m in mem if dn[m][1]).most_common(1)
        labels.append({"t": shown[0][0] if shown else sn.title(),
                       "i": xof_pre[best], "n": len(mem)})
    print(f"  in-scene labels: {len(labels)} surnames "
          f"({', '.join(l['t'] for l in labels[:6])}…)")

    # ---- per-person facts the view can colour by ------------------------
    # One 3-D model, several questions. Each of these was checked against the
    # data before being added: lifespan is computable for 14,805 people, and
    # 2,321 of the 9,756 with both places recorded died in a different region
    # from the one they were born in.
    life, moved = [], []
    for p in order:
        pr = people[p]
        a = (pr.dy - pr.by) if (pr.by and pr.dy) else None
        life.append(a if (a is not None and 0 <= a <= 110) else -1)
        rb = region_of(G.event_place(pr.rec, "BIRT"))
        rd = region_of(G.event_place(pr.rec, "DEAT"))
        moved.append(1 if (rb and rd and rb != rd) else (0 if (rb and rd) else -1))

    # ---- the three findings worth charting ------------------------------
    import statistics as _st
    def band(v, w):  return (v // w) * w
    fam_size, spans, flows = defaultdict(list), defaultdict(list), Counter()
    for f in fam.values():
        m = f.kid("MARR")
        if m and m.kid("DATE"):
            my = G.year(m.kid("DATE").full_value())
            if my:
                fam_size[band(my, 50)].append(len([c for c in f.kids("CHIL") if c.value]))
    for p in gsorted:
        pr = people[p]
        if pr.by and pr.dy and 0 <= pr.dy - pr.by <= 110:
            spans[band(pr.by, 50)].append(pr.dy - pr.by)
        rb = region_of(G.event_place(pr.rec, "BIRT"))
        rd = region_of(G.event_place(pr.rec, "DEAT"))
        if rb and rd and rb != rd:
            flows[(rb, rd)] += 1
    # A birth cohort is only usable for lifespan once nearly everyone in it has
    # died. Include a recent band and the median collapses — not because people
    # died younger, but because the only ones with a recorded death are the ones
    # who died early. Cutting at band + 100 years keeps the chart honest.
    latest = D_YEAR_MAX = max(int(v) for v in year.values())
    usable_life = [[k, round(_st.median(v), 1), len(v)]
                   for k, v in sorted(spans.items())
                   if len(v) >= 60 and k + 100 <= latest]
    findings = {
        "familySize": [[k, round(_st.median(v), 1), len(v)]
                       for k, v in sorted(fam_size.items()) if len(v) >= 40],
        "lifespan":   usable_life,
        "lifespanCut": latest - 100,
        "flows":      [[a, b, n] for (a, b), n in flows.most_common(8)],
        "movedCount": sum(1 for m in moved if m == 1),
        "placedCount": sum(1 for m in moved if m >= 0),
    }
    print(f"  findings: family size {len(findings['familySize'])} bands, "
          f"lifespan {len(findings['lifespan'])} bands, "
          f"{findings['movedCount']} of {findings['placedCount']} changed region")

    # ---- emit ----------------------------------------------------------
    idx = {p: i for i, p in enumerate(order)}
    names, by, dy, xs, ys, br, fl = [], [], [], [], [], [], []
    surn, aka, evl = [], [], []
    name_fix = Counter()
    for p in order:
        name_fix.update(dn[p][3])
    srcmap = evidence.sources(recs)
    this_year = datetime.date.today().year
    living_from = this_year - 100
    for p in order:
        pr = people[p]
        d, sn, ak, _ = dn[p]
        names.append(d)
        surn.append(sn)
        aka.append(ak)
        evl.append(evidence.person_evidence(pr.rec, fam, srcmap)[0])
        by.append(pr.by or 0)
        dy.append(pr.dy or 0)
        xs.append(xof[p])
        ys.append(round(year[p], 1))
        br.append(rindex.get(region.get(p), -1))
        f = 0
        if p in line:
            f |= 1
        if p in estimated:
            f |= 2
        if p == args.root:
            f |= 4
        if pr.sex == "F":
            f |= 8
        elif pr.sex == "M":
            f |= 16
        # 32: died, date unknown — a death, burial or cremation is recorded
        #     (dated or not) but there is no death year to print.
        dead = death_evidence(pr.rec)
        if dead and not pr.dy:
            f |= 32
        # 64: the recorded birth year is approximate — qualified (ABT, EST,
        #     BEF, BET…), or borrowed from a christening or baptism. People
        #     with no recorded year at all carry bit 2 instead.
        bdate = G.event_date(pr.rec, "BIRT")
        if pr.by and (_APPROX.search(bdate or "") or not G.year(bdate)):
            f |= 64
        # 128: living, by the Gramps rule (PLAN D12): no death evidence of any
        #      kind and born fewer than 100 years ago (the estimated year when
        #      none is recorded).
        if not dead and not pr.dy and (pr.by or year[p]) >= living_from:
            f |= 128
        fl.append(f)

    edges = []
    for p in order:
        for c in S(children[p]):
            if c in idx:
                edges.append((idx[p], idx[c]))
    marr = []
    done = set()
    for p in order:
        for s in S(spouses[p]):
            if s in idx:
                k = tuple(sorted((idx[p], idx[s])))
                if k not in done:
                    done.add(k)
                    marr.append(k)

    # A compact per-person index of relatives, so the page can trace a line
    # without rebuilding the graph.
    # ---- the full statistics view ---------------------------------------
    # The rail's findings panel answers three questions in a 300px column.
    # This is the same data asked properly: every series pre-aggregated per
    # split so the page's toggles are instant and no arithmetic runs in the
    # browser. See tools/build_stats.py for the two rules it enforces.
    # places.json is written by build_places.py, which runs AFTER this on a
    # first build — so the distance chart appears from the second run onward
    # rather than failing when it is absent.
    _pp = os.path.join(os.path.dirname(args.out), "places.json")
    _places = json.load(open(_pp, encoding="utf-8")) if os.path.exists(_pp) else None
    stats_block = build_stats.build(people, fam, group, year, idx, parents, args.root,
                                    region_of, G.event_place, G.parse_date,
                                    G.event_date, _places)
    print(f"  stats: {len(stats_block['charts'])} charts "
          f"({', '.join(c['id'] for c in stats_block['charts'])}), "
          f"lifespan cut at {stats_block['lifeCut']}")

    par = [[idx[q] for q in S(parents[p]) if q in idx] for p in order]
    kid = [[idx[q] for q in S(children[p]) if q in idx] for p in order]
    spo = [[idx[q] for q in S(spouses[p]) if q in idx] for p in order]
    ids = [p for p in order]
    # The build stamp (docs/payload-stamp.md): every payload that addresses people by index
    # carries it, so a page or build_pages.py can refuse one built for a
    # different person order instead of lighting up strangers.
    stamp = hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()[:16]

    data = {
        "count": len(order),
        "names": names, "birth": by, "death": dy,
        "x": xs, "y": ys, "branch": br, "flags": fl,
        "dist": [dist[p] for p in order],
        # the drawn radius, 0..1 — equal-area in the step counts above, so the
        # page does not have to know how the scale was derived
        "rad": [round(radius_of.get(dist[p], 1.0), 4) for p in order],
        "theta": [round(theta[p], 4) for p in order],
        "maxDist": far,
        "edges": edges, "marriages": marr,
        "parents": par, "children": kid, "spouses": spo,
        "ids": ids,
        "rootIndex": idx.get(args.root, -1),
        "stamp": stamp,
        "meIndex": idx.get(args.me, -1),
        # 0 backed by a record · 1 other users' trees only · 2 no sources
        "ev": evl,
        "surn": surn,
        "aka": aka,
        "labels": labels,
        "life": life, "moved": moved, "findings": findings,
        "statsView": stats_block,
        "regionNames": rnames,
        "regionSizes": [sum(1 for p in group if region.get(p) == r) for r in rnames],
        "branchNames": [names[idx[b]] if b in idx else "?" for b in top],
        "branchSizes": [sizes[b] for b in top],
        "stats": {
            "people": len(order),
            "families": len(fam),
            "edges": len(edges),
            "marriages": len(marr),
            "estimated": len(estimated),
            "yearMin": int(min(ys)), "yearMax": int(max(ys)),
            "lineSize": sum(1 for f in fl if f & 1),
            "living": sum(1 for f in fl if f & 128),
            "livingRule": f"no death, burial or cremation recorded, and born in "
                          f"{living_from} or later (recorded or estimated year)",
            "regionKnown": len(region),
            "regionDirect": direct,
        },
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
    print(f"wrote {args.out} ({os.path.getsize(args.out)/1e6:.1f} MB)")

    # ---- the full record behind each point ------------------------------
    # 29 MB of facts and record transcripts against 2.6 MB of coordinates:
    # inlining it raw would make a 32 MB page that parses all of it before
    # drawing anything, to show one person's panel. So it ships gzipped and
    # base64'd (5.6 MB), and is unpacked on the first click, once.
    dstats = {}
    det = build_details.build(people, fam, recs, display=display, stats=dstats)
    det_list = [det["people"].get(p) or 0 for p in order]
    blob = {"stamp": stamp, "count": len(order),
            "schema": det["schema"], "kinds": det["kinds"],
            "citeClasses": det["citeClasses"],
            "srcTitles": det["srcTitles"], "srcTypes": det["srcTypes"],
            "rows": det_list}
    raw = json.dumps(blob, separators=(",", ":"), ensure_ascii=False).encode()
    b64 = base64.b64encode(gzip.compress(raw, 6)).decode()
    dout = os.path.splitext(args.out)[0] + "-details.b64"
    with open(dout, "w", encoding="utf-8") as fh:
        fh.write(b64)
    nf = sum(len(r["f"]) for r in det_list if r and "f" in r)
    nc = sum(len(r["c"]) for r in det_list if r and "c" in r)
    print(f"wrote {dout} ({len(b64)/1e6:.1f} MB base64, {len(raw)/1e6:.1f} MB raw)")
    print(f"  {sum(1 for r in det_list if r)} people with a record, "
          f"{nf} facts, {nc} citations, {len(det['srcTitles'])} sources")
    for k, v in dstats.items():
        print(f"    {k:<36}{v:>8,}")
    print(f"  years {data['stats']['yearMin']}–{data['stats']['yearMax']}, "
          f"{len(edges)} parent-child links, {len(marr)} marriages")
    print(f"  stamp {stamp}; me = {args.me} at index {data['meIndex']}")
    print("  flags: " + ", ".join(f"{b}: {sum(1 for f in fl if f & b):,}"
                                   for b in (1, 2, 4, 8, 16, 32, 64, 128)))
    print("  evidence: " + ", ".join(f"{evidence.LEVEL_NAMES[k]} {evl.count(k):,}"
                                      for k in range(3)))
    print(f"  aka: {sum(1 for a in aka if a):,} people, {sum(len(a) for a in aka):,} names")
    print("  display names — fixes applied (people):")
    for k in NAME_FIXES:
        print(f"    {k:<36}{name_fix[k]:>8,}")
    before = name_defects([old_display(people[p].rec) for p in order])
    after = name_defects(names)
    print("  display-name defects, before -> after:")
    for k in sorted(set(before) | set(after)):
        print(f"    {k:<36}{before[k]:>8,} -> {after[k]:,}")


if __name__ == "__main__":
    main()
