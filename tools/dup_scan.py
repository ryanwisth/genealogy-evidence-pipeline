#!/usr/bin/env python3
"""
dup_scan.py — find duplicate individual records in a GEDCOM file.

The problem
-----------
A MyHeritage tree built by accepting Smart Matches without merging holds the
same person as many separate records. The tree this was written for had tens
of thousands of individuals in dozens of disconnected islands: run cold, the
tools said one man was unrelated to his own father.

Approach — iterative co-reference resolution
--------------------------------------------
A pair of records is scored on name, dates, places and *relatives*. The relative
evidence is the interesting part: two "John Doe 1820" records are far more
likely the same man if they also share a wife and three children. But those
relatives are themselves duplicated, so a single pass under-counts. We therefore
run to a fixed point: merge the certain pairs, recompute relatives through the
merged clusters, re-score, repeat. Each round makes the next round's relative
evidence stronger.

Safety
------
Wrongly merging two people corrupts the genealogy, and unlike a missed merge it
is invisible afterwards. So the hard blockers below veto a pair outright no
matter how high it scores, and anything not conclusive lands in a REVIEW band
for a human (or a judgment pass) rather than being merged silently.

Hard blockers (never merge):
  * conflicting sex (M vs F)
  * both have a full birth date and they differ
  * both have a full death date and they differ
  * birth years more than 2 apart when both are known
  * they are parent and child of each other
  * they are spouses of each other
  * they are siblings in the same family (same FAMC) — a real pattern, where a
    child who died young is followed by a sibling given the same name

Output: a TSV of scored pairs with the evidence, ready to feed the merger.
"""

import sys
import os
import re
import difflib
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gedcom_lib as G


# ------------------------------------------------------------------ profiles

GENERIC_GIVEN = {
    "", "infant", "baby", "son", "daughter", "child", "twin", "stillborn",
    "unknown", "unnamed", "n n", "nn", "male", "female", "living",
}


class Person:
    __slots__ = ("id", "rec", "given", "surn", "full", "sex", "gtok",
                 "by", "bmd", "dy", "dmd", "bplace", "dplace",
                 "famc", "fams", "nfacts")

    def __init__(self, pid, rec):
        self.id = pid
        self.rec = rec
        self.given, self.surn, self.full = G.name_parts(rec)
        self.gtok = [t for t in self.given.split() if t]
        self.sex = rec.kid_value("SEX")[:1].upper()
        by, bm, bd = G.parse_date(G.event_date(rec, "BIRT"))
        dy, dm, dd = G.parse_date(G.event_date(rec, "DEAT"))
        # CHR/BAPM stands in for a missing birth year — usually within a year.
        if by is None:
            for alt in ("CHR", "BAPM"):
                ay, am, ad = G.parse_date(G.event_date(rec, alt))
                if ay:
                    by = ay
                    break
        self.by, self.dy = by, dy
        self.bmd = (bm, bd) if bm and bd else None
        self.dmd = (dm, dd) if dm and dd else None
        self.bplace = G.norm(G.event_place(rec, "BIRT"))
        self.dplace = G.norm(G.event_place(rec, "DEAT"))
        self.famc = G.pointers(rec, "FAMC")
        self.fams = G.pointers(rec, "FAMS")
        self.nfacts = count_facts(rec)

    def key(self):
        """Identity signature used to match relatives across duplicate records."""
        return (self.full, self.by)

    def label(self):
        yrs = "-".join(str(x) for x in (self.by, self.dy) if x)
        return f"{self.full or '(no name)'} [{yrs}] {self.id}"


def count_facts(rec):
    n = 0
    stack = list(rec.children)
    while stack:
        c = stack.pop()
        n += 1
        stack.extend(c.children)
    return n


# ------------------------------------------------------------------ indexing

def build(path):
    recs, roots, meta = G.parse(path)
    indi = {k: v for k, v in recs.items() if v.tag == "INDI"}
    fam = {k: v for k, v in recs.items() if v.tag == "FAM"}
    people = {pid: Person(pid, r) for pid, r in indi.items()}
    return people, fam, recs, roots, meta


def family_index(people, fam):
    """parents[pid] -> set of parent pids; spouses; children; siblings."""
    parents = defaultdict(set)
    children = defaultdict(set)
    spouses = defaultdict(set)
    siblings = defaultdict(set)
    for fid, f in fam.items():
        husb = [c.value for c in f.kids("HUSB") if c.value in people]
        wife = [c.value for c in f.kids("WIFE") if c.value in people]
        kids = [c.value for c in f.kids("CHIL") if c.value in people]
        par = husb + wife
        for k in kids:
            for p in par:
                parents[k].add(p)
                children[p].add(k)
        for a in par:
            for b in par:
                if a != b:
                    spouses[a].add(b)
        for a in kids:
            for b in kids:
                if a != b:
                    siblings[a].add(b)
    return parents, children, spouses, siblings


# ------------------------------------------------------------------ union-find

class DSU:
    def __init__(self, items):
        self.p = {i: i for i in items}
        self.members = {i: [i] for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        # Keep the numerically smaller id as root — stable and reproducible.
        if ra > rb:
            ra, rb = rb, ra
        self.p[rb] = ra
        self.members[ra].extend(self.members.pop(rb))
        return True

    def groups(self):
        g = defaultdict(list)
        for i in self.p:
            g[self.find(i)].append(i)
        return {k: sorted(v) for k, v in g.items() if len(v) > 1}


# ------------------------------------------------------------------ blocking

def relational_blocks(people, ctx):
    """Buckets built from who a person is attached to, not what they are called.

    This is the block that matters most for the duplicated *parents* of an
    already-merged child, and nothing name-based finds them: "Elsa
    Quillane [1840-1920]" and "Elsá Quillaine Fennaway [1841]" share
    no name token and no year, so no name or date bucket ever puts them side by
    side. What they do share is a child — and once that child's two records have
    been merged, both women are parents of the same person, which is strong
    evidence they are the same woman.

    That only becomes visible after some merging has happened, so this is
    recomputed every round and the candidate list grows as the clusters form.
    """
    dsu = ctx["dsu"]
    bk = defaultdict(list)
    for pid in people:
        for c in ctx["children"][pid]:
            bk[("PARENT-OF", dsu.find(c))].append(pid)
        for s in ctx["spouses"][pid]:
            bk[("SPOUSE-OF", dsu.find(s))].append(pid)
    return bk


def blocks(people):
    """Candidate buckets. A pair is only ever compared inside a shared bucket."""
    bk = defaultdict(list)
    for pid, p in people.items():
        if not p.full:
            continue
        sur = p.surn or "?"
        g0 = p.gtok[0] if p.gtok else ""
        # 1. same full name
        bk[("N", p.full)].append(pid)
        # 2. surname + first given + birth decade  (catches date-shifted dupes)
        if p.by:
            bk[("SD", sur, g0, p.by // 10)].append(pid)
            bk[("SY", sur, p.by)].append(pid)
        # 3. surname + first given (no date) — for records missing dates
        if g0:
            bk[("SG", sur, g0)].append(pid)
        # 4. given-name-only block for people whose surname is missing
        if not p.surn and p.by:
            bk[("GY", p.given, p.by)].append(pid)
        # 5. surname-FREE blocks. Without these, two records of one person
        #    spelled Smyth and Smith are never even compared. A full birth date
        #    is selective enough to block on by itself.
        if p.by and p.bmd:
            bk[("FB", p.by, p.bmd)].append(pid)
        if p.dy and p.dmd:
            bk[("FD", p.dy, p.dmd)].append(pid)
        if g0 and p.by:
            bk[("GB", g0, p.by)].append(pid)
    return bk


def candidate_pairs(people, max_block=400, bk=None):
    seen = set()
    oversized = Counter()
    for key, members in (bk or blocks(people)).items():
        if len(members) < 2:
            continue
        if len(members) > max_block:
            # A block this big is a name so common it carries no information
            # (e.g. every "Maria" born in the 1800s). Comparing it pairwise is
            # both quadratic and worthless, so record and skip it rather than
            # silently truncating.
            oversized[key] = len(members)
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a > b:
                    a, b = b, a
                if (a, b) not in seen:
                    seen.add((a, b))
                    yield a, b
    if oversized:
        print(f"  [blocking] skipped {len(oversized)} oversized blocks "
              f"(>{max_block}): {', '.join(str(k) + '=' + str(v) for k, v in oversized.most_common(5))}",
              file=sys.stderr)


# ------------------------------------------------------------------ scoring

def impossible_dates(p):
    """The record's own birth and death cannot both be right."""
    return bool(p.by and p.dy and (p.dy < p.by or p.dy - p.by > 110))


def date_conflict(a, b):
    """True when two dates are positively incompatible (not merely unknown).

    A record whose own birth and death contradict each other has at least one
    wrong date, so its dates cannot veto a merge. This is not hypothetical: the
    kind of tree holds "John (Jack) Henry Doe, 1900-1858" and "John H. Doe,
    1902-1943" — the same man — and the corrupt 1858
    made the two records look like a death-year conflict and blocked the merge.
    The consequence was over a thousand people ending up with more than two parents,
    because their parents stayed split while they themselves were merged.

    So when either record's dates are internally impossible, the hard veto is
    dropped and the pair is decided on names, places and relatives instead.
    Scoring is unaffected: unmatched dates still earn nothing.
    """
    if impossible_dates(a) or impossible_dates(b):
        return None
    if a.by and b.by and abs(a.by - b.by) > 2:
        return "birth years differ"
    if a.dy and b.dy and abs(a.dy - b.dy) > 2:
        return "death years differ"
    return None


def day_month_conflict(a, b):
    """Same year, different day or month — a typo as often as a real difference.

    "Jane Margaret Doe 1824-1896" appears twice with the same
    name, the same years and the same family, differing only in the recorded day
    of birth. Vetoing that outright left her children with four parents. But two
    siblings genuinely born in different months of one year look identical to
    this test, so it is not waved through either: it caps the pair at REVIEW.
    """
    if a.bmd and b.bmd and a.by and b.by and a.by == b.by and a.bmd != b.bmd:
        return "birth day/month differ"
    if a.dmd and b.dmd and a.dy and b.dy and a.dy == b.dy and a.dmd != b.dmd:
        return "death day/month differ"
    return None


def surname_similarity(a, b):
    """0..1 on two normalised surnames.

    Exact matching is too strict for real trees. Nordic records spell one farm
    name several ways (Saltmere / Saltmeer), and people genuinely changed surname
    when they moved farm. German records give Müller / Mueller / Muller.

    So score similarity instead, and let the date and relative evidence decide
    whether a near-miss is enough.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.7           # one record simply has no surname recorded
    ta, tb = set(a.split()), set(b.split())
    if ta & tb:
        return 0.9           # "gronvoldsdatter brændeland" against "brændeland"
    return difflib.SequenceMatcher(None, a, b).ratio()


def name_score(a, b):
    """0..4. Exact normalised match is 4; a different first name is 0."""
    if a.full and a.full == b.full:
        return 4
    ga, gb = set(a.gtok), set(b.gtok)
    if not ga or not gb:
        return 0
    if a.gtok[0] != b.gtok[0]:
        # A middle-name difference is common; a different first name is not.
        if ga & gb:
            given = 1
        elif difflib.SequenceMatcher(None, a.gtok[0], b.gtok[0]).ratio() >= 0.72:
            # Given names drift in this tree the way surnames do: Olav and
            # Olaf, Ingrid and Ingird, Jorgen and Jørgen. Worth 1 point —
            # the date and relative evidence still has to carry the pair.
            given = 1
        else:
            return 0
    else:
        given = 3 if len(ga & gb) >= 2 else 2

    sim = surname_similarity(a.surn, b.surn)
    if sim >= 0.99:
        return given
    if sim >= 0.8:
        return max(1, given - 1)
    if sim >= 0.62:
        return max(1, given - 2)
    # Different surnames entirely. Only worth considering at all when the given
    # name matches; the date evidence has to carry it from here, and it will
    # land in REVIEW rather than being merged automatically.
    return 1 if a.gtok[0] == b.gtok[0] else 0


def place_score(pa, pb):
    if not pa or not pb:
        return 0
    ta, tb = set(pa.split()), set(pb.split())
    if not ta or not tb:
        return 0
    ov = len(ta & tb) / min(len(ta), len(tb))
    return 2 if ov >= 0.8 else (1 if ov >= 0.4 else 0)


def plausibly_same_person(x, y):
    """Loose identity test for two relative records.

    Used only to ask whether two sets of parents can be the same couple. It is
    deliberately forgiving about surnames — a Nordic mother can carry one
    farm name on one record and another farm name on the next, because the
    family moved farm — and strict about given names and
    years, which is where a genuinely different couple shows up.
    """
    if x.by and y.by and abs(x.by - y.by) > 3:
        return False
    gx, gy = set(x.gtok), set(y.gtok)
    if gx & gy:
        return True
    if not x.given or not y.given:
        return True          # nothing to contradict
    # Same surname and same birth year is enough on its own: a mother recorded
    # as "Sophia Louisa Doe" on one record and "Louise Doe" on the other,
    # both born 1842, is one woman written down two ways.
    if (x.by and y.by and x.by == y.by
            and surname_similarity(x.surn, y.surn) >= 0.85):
        return True
    return difflib.SequenceMatcher(None, x.given, y.given).ratio() >= 0.75


def parents_contradict(a_id, b_id, ctx):
    """True when both records name parents and not one of them can be matched.

    Two records of one person disagreeing about *both* parents is the signature
    of cousins named for a shared grandparent, and of the speculative deep
    pedigrees this tree carries above about 1700. It is not proof they are
    different people — one side's parent link may simply be wrong — so this
    only demotes a pair out of automatic merging and into review.
    """
    people = ctx["people"]
    pa = [people[x] for x in ctx["parents"][a_id] if people[x].full]
    pb = [people[x] for x in ctx["parents"][b_id] if people[x].full]
    if not pa or not pb:
        return False
    for x in pa:
        for y in pb:
            if plausibly_same_person(x, y):
                return False
    return True


def relative_overlap(ka, kb):
    """How many identity-keys two relative sets share, ignoring unknown years."""
    a = {k for k in ka if k[0]}
    b = {k for k in kb if k[0]}
    return len(a & b)


def score_pair(a, b, ctx):
    """Return (score, tier, evidence list, blocker or None)."""
    ev = []

    # ---- hard blockers -------------------------------------------------
    if a.sex and b.sex and a.sex != b.sex and "U" not in (a.sex, b.sex):
        return 0, "REJECT", ev, "sex conflict"
    dc = date_conflict(a, b)
    if dc:
        return 0, "REJECT", ev, dc
    ra, rb = ctx["dsu"].find(a.id), ctx["dsu"].find(b.id)
    if b.id in ctx["parents"][a.id] or a.id in ctx["parents"][b.id]:
        return 0, "REJECT", ev, "parent/child of each other"
    if b.id in ctx["spouses"][a.id]:
        return 0, "REJECT", ev, "spouses of each other"
    if b.id in ctx["siblings"][a.id]:
        return 0, "REJECT", ev, "siblings in the same family"

    ns = name_score(a, b)
    if ns == 0:
        return 0, "REJECT", ev, "names too different"
    score = ns
    ev.append(f"name+{ns}")

    # ---- dates ---------------------------------------------------------
    if a.by and b.by:
        if a.by == b.by:
            score += 2
            ev.append("birth year=")
            if a.bmd and b.bmd and a.bmd == b.bmd:
                score += 3
                ev.append("birth full date=")
        else:
            score += 1
            ev.append(f"birth year~{abs(a.by-b.by)}")
    if a.dy and b.dy:
        if a.dy == b.dy:
            score += 2
            ev.append("death year=")
            if a.dmd and b.dmd and a.dmd == b.dmd:
                score += 3
                ev.append("death full date=")
    score += place_score(a.bplace, b.bplace)
    if place_score(a.bplace, b.bplace):
        ev.append("birth place~")
    score += place_score(a.dplace, b.dplace)

    # ---- relatives (the strong signal) ---------------------------------
    keyof = ctx["keyof"]
    pa = {keyof(x) for x in ctx["parents"][a.id]}
    pb = {keyof(x) for x in ctx["parents"][b.id]}
    sa = {keyof(x) for x in ctx["spouses"][a.id]}
    sb = {keyof(x) for x in ctx["spouses"][b.id]}
    ca = {keyof(x) for x in ctx["children"][a.id]}
    cb = {keyof(x) for x in ctx["children"][b.id]}

    np_ = relative_overlap(pa, pb)
    nsp = relative_overlap(sa, sb)
    nch = relative_overlap(ca, cb)

    # Structural overlap: the two records point at the SAME relative record (or
    # the same merge cluster), not merely at two people with the same name.
    # That is much stronger evidence, and it is the signal that identifies a
    # duplicated parent — a child has two parents, so if a third turns up, one
    # of them is a duplicate of another. "Tomas Velkamp Saltmere" and "Tomas
    # Velkampf Saltmere" are both fathers of the same child; they are one man.
    find = ctx["dsu"].find
    co_child = len({find(x) for x in ctx["children"][a.id]}
                   & {find(x) for x in ctx["children"][b.id]})
    co_spouse = len({find(x) for x in ctx["spouses"][a.id]}
                    & {find(x) for x in ctx["spouses"][b.id]})
    if co_child:
        score += 4 * min(co_child, 2)
        ev.append(f"co-parent of the same child x{co_child}")
    if co_spouse:
        score += 4 * min(co_spouse, 2)
        ev.append(f"married to the same person x{co_spouse}")
    if np_:
        score += 3 * min(np_, 2)
        ev.append(f"shared parents x{np_}")
    if nsp:
        score += 4 * min(nsp, 2)
        ev.append(f"shared spouse x{nsp}")
    if nch:
        score += 2 * min(nch, 3)
        ev.append(f"shared children x{nch}")

    # Complementary stubs: one record holds the ancestry, the other the
    # descendants. This is the shape of the bridges that split the original tree.
    if (not a.famc and b.famc and a.fams and not b.fams) or \
       (not b.famc and a.famc and b.fams and not a.fams):
        score += 2
        ev.append("complementary stub")

    # A generic given name ("Infant", "Son") carries no identity.
    if a.given in GENERIC_GIVEN or b.given in GENERIC_GIVEN:
        score -= 4
        ev.append("generic given name")

    # ---- tiers ---------------------------------------------------------
    hard_id = (a.full == b.full and a.by and b.by and a.by == b.by and
               ((a.bmd and b.bmd and a.bmd == b.bmd) or
                (a.dy and b.dy and a.dy == b.dy)))
    strong_rel = (np_ + nsp + nch) >= 2 or nsp >= 1 and np_ >= 1

    if ns == 4 and (hard_id or (strong_rel and a.by and b.by and a.by == b.by)):
        tier = "AUTO"
    elif score >= 14:
        tier = "AUTO"
    elif score >= 8:
        tier = "REVIEW"
    else:
        tier = "WEAK"

    # Being co-parents of one child, or married to one person, always earns a
    # look even when the names and dates are too thin to score. Left in WEAK
    # these never reach a reviewer, and they are the single largest cause of
    # people ending up with three or four parents. Not promoted on a shared
    # PARENT — that is a sibling relation, and same-named siblings are a trap
    # rather than a duplicate.
    if tier == "WEAK" and (co_child or co_spouse):
        tier = "REVIEW"
        ev.append("PROMOTED: shares an actual child or spouse record")

    # However well a pair scores on names and dates, two records that name
    # entirely different parents get looked at rather than merged on sight.
    if tier == "AUTO" and parents_contradict(a.id, b.id, ctx):
        tier = "REVIEW"
        ev.append("DEMOTED: records name different parents")
    dmc = day_month_conflict(a, b)
    if dmc:
        ev.append(f"DEMOTED: {dmc}")
        if tier == "AUTO":
            tier = "REVIEW"
        elif tier == "WEAK":
            tier = "WEAK"
    return score, tier, ev, None


# ------------------------------------------------------------------ driver

def run(path, rounds=4):
    people, fam, recs, roots, meta = build(path)
    print(f"# {len(people)} individuals, {len(fam)} families ({meta['encoding']})")
    parents, children, spouses, siblings = family_index(people, fam)
    dsu = DSU(list(people))

    def keyof(pid):
        """Identity key that follows the current merge clusters."""
        root = dsu.find(pid)
        p = people[root]
        # Use the best-known name/year in the cluster.
        return (p.full, p.by)

    ctx = {"parents": parents, "children": children, "spouses": spouses,
           "siblings": siblings, "dsu": dsu, "keyof": keyof, "people": people}

    all_pairs = list(candidate_pairs(people))
    print(f"# {len(all_pairs):,} candidate pairs after blocking")

    results = {}
    seen_pairs = set(all_pairs)
    for rnd in range(1, rounds + 1):
        # Relational blocks depend on the clusters formed so far, so recompute
        # them each round: merging a child is what reveals that its two mothers
        # are one woman, and that is only visible after the child has merged.
        if rnd > 1:
            fresh = [pr for pr in candidate_pairs(
                people, bk=relational_blocks(people, ctx)) if pr not in seen_pairs]
            if fresh:
                seen_pairs.update(fresh)
                all_pairs.extend(fresh)
                print(f"#   +{len(fresh):,} new candidate pairs from relatives")
        merged_this_round = 0
        for a_id, b_id in all_pairs:
            if dsu.find(a_id) == dsu.find(b_id):
                continue
            a, b = people[a_id], people[b_id]
            sc, tier, ev, blocker = score_pair(a, b, ctx)
            results[(a_id, b_id)] = (sc, tier, ev, blocker)
            if tier == "AUTO":
                # Cluster-level veto: never union two clusters that already
                # contain a parent/child, spouse or sibling relation between
                # them — that would fold a real generation away.
                if cluster_conflict(a_id, b_id, ctx):
                    results[(a_id, b_id)] = (sc, "REJECT", ev, "cluster relation conflict")
                    continue
                if dsu.union(a_id, b_id):
                    merged_this_round += 1
        print(f"# round {rnd}: +{merged_this_round} auto-merges")
        if merged_this_round == 0:
            break

    return people, fam, recs, roots, dsu, results, ctx


def explain_conflict(a_id, b_id, ctx):
    """Say WHY a merge is structurally impossible, in terms of the actual link.

    A refusal on its own is not useful to anyone. These pairs are almost always
    two records that really are one person, sitting behind a corrupt parent link
    in the source tree — a man recorded as his own brother's father, or a
    duplicate record standing in as its own parent. Naming the offending link
    turns the refusal into something fixable on MyHeritage.
    """
    people = ctx["people"]
    dsu = ctx["dsu"]
    if b_id in ctx["parents"][a_id]:
        return f"{people[b_id].label()} is recorded as a parent of {people[a_id].label()}"
    if a_id in ctx["parents"][b_id]:
        return f"{people[a_id].label()} is recorded as a parent of {people[b_id].label()}"
    if b_id in ctx["spouses"][a_id]:
        return "the two records are recorded as married to each other"
    if b_id in ctx["siblings"][a_id]:
        return "the two records are recorded as siblings in the same family"
    # Otherwise it is an ancestry chain through the clusters; name one link.
    ra, rb = dsu.find(a_id), dsu.find(b_id)
    for start, other in ((a_id, rb), (b_id, ra)):
        for m in dsu.members.get(dsu.find(start), [start]):
            for p in ctx["parents"].get(m, ()):
                if dsu.find(p) == other:
                    return (f"{people[p].label()} is recorded as a parent of "
                            f"{people[m].label()}, and is a duplicate of the "
                            f"other record in this pair")
    return "merging them would make someone their own ancestor"


def cluster_conflict(a_id, b_id, ctx):
    """Veto a merge that would fold a real generation away.

    Checking the two records directly is not enough. Once records are being
    merged, A's cluster can already contain B's grandfather, and welding them
    then makes somebody their own ancestor — which is what happened with the
    two records of one man, where one record's father was a duplicate of
    the other record. So walk the ancestry at CLUSTER level, in both
    directions, and refuse if either reaches the other.
    """
    dsu = ctx["dsu"]
    if dsu.find(a_id) == dsu.find(b_id):
        return False
    if b_id in ctx["parents"][a_id] or a_id in ctx["parents"][b_id]:
        return True
    if b_id in ctx["spouses"][a_id] or b_id in ctx["siblings"][a_id]:
        return True
    return ancestry_touches(dsu, ctx["parents"], a_id, b_id)


def ancestry_touches(dsu, parents, a_id, b_id, max_nodes=20000):
    """True if either cluster is an ancestor of the other."""
    ra, rb = dsu.find(a_id), dsu.find(b_id)
    for start, target in ((ra, rb), (rb, ra)):
        seen = {start}
        stack = [start]
        n = 0
        while stack:
            root = stack.pop()
            n += 1
            if n > max_nodes:
                break
            for member in dsu.members.get(root, (root,)):
                for p in parents.get(member, ()):
                    rp = dsu.find(p)
                    if rp == target:
                        return True   # target is an ancestor of start
                    if rp not in seen:
                        seen.add(rp)
                        stack.append(rp)
    return False


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(HERE), "sample", "sample-family.ged")
    people, fam, recs, roots, dsu, results, ctx = run(src)
    groups = dsu.groups()
    print(f"\n# {len(groups)} merge clusters covering "
          f"{sum(len(v) for v in groups.values())} records")
    sizes = Counter(len(v) for v in groups.values())
    print("# cluster sizes:", dict(sorted(sizes.items())))
    tiers = Counter(t for _, t, _, _ in results.values())
    print("# pair tiers:", dict(tiers))
