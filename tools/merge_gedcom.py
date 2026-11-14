#!/usr/bin/env python3
"""
merge_gedcom.py — collapse duplicate records and re-emit a clean GEDCOM.

Runs `dup_scan` to get merge clusters, then:

  1. picks a survivor per cluster and folds the other records into it,
  2. rewrites every pointer in the file to the survivor,
  3. merges the duplicate FAM records that step 2 creates,
  4. repairs the known text and date defects,
  5. validates the result and writes the merge log.

Merge policy
------------
Nothing is thrown away. Facts that exist on only one record are carried over;
facts that exist on both are kept once. Where the two records disagree on a
single-valued fact the survivor's value wins and the loser's is preserved as an
alternate (extra NAME lines, or an EVEN with the original tag), so a later
reader can still see the conflict instead of inheriting a silent choice.

Survivor choice: the record with the most facts wins, ties broken by the lower
xref number. The survivor keeps its own xref, so IDs already quoted in
`findings/` and `aliases.tsv` still resolve.
"""

import sys
import os
import re
import argparse
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gedcom_lib as G
import dup_scan as D

# Tags that may appear only once on an individual. Everything else is a
# repeatable fact and is unioned.
SINGLETON = {"SEX", "BIRT", "DEAT", "BURI", "CREM", "CHR", "BAPM", "RIN", "_UID"}
# Carried but never duplicated — MyHeritage bookkeeping.
MH_NOISE = {"_UPD"}

PTR_TAGS_INDI = {"FAMC", "FAMS"}
PTR_TAGS_FAM = {"HUSB", "WIFE", "CHIL"}


# ------------------------------------------------------------------ merging

def impossible(by, dy):
    """A birth/death year pair that cannot describe a real life."""
    if by and dy and (dy < by or dy - by > 110):
        return True
    return False


def pick_survivor(cluster, people):
    """Most complete record wins; ties go to the lowest xref number.

    Completeness is penalised heavily when the record's own dates are
    impossible. Otherwise the most *detailed* corrupt record — "Edward Saltmere,
    1900-1858", which has a full day and month on both — would beat the
    correct but sparser one and carry the corruption into the merged tree.
    """
    def rank(pid):
        p = people[pid]
        completeness = (
            p.nfacts
            + (5 if p.bmd else 0) + (3 if p.by else 0)
            + (5 if p.dmd else 0) + (3 if p.dy else 0)
            + 4 * len(p.famc) + 2 * len(p.fams)
        )
        if impossible(p.by, p.dy):
            completeness -= 40
        return (-completeness, xref_num(pid))
    return sorted(cluster, key=rank)[0]


def reconcile_dates(survivor, members, people, log):
    """Repair an impossible birth/death pair from the duplicates being merged.

    Where a cluster holds several records of one person, they often disagree,
    and one of the versions is coherent. "Edward (Ned) Saltmere" reads
    1900-1858 on one record and 1899 / 1 Jan 1943 on two others. Take the
    coherent combination rather than keeping an impossible one.

    Only ever replaces a pair that is *impossible*; a merely different date is
    left alone, with the survivor's value standing.
    """
    sy, _, _ = G.parse_date(G.event_date(survivor, "BIRT"))
    dy, _, _ = G.parse_date(G.event_date(survivor, "DEAT"))
    if not impossible(sy, dy):
        return None

    births, deaths = [], []
    for pid in members:
        p = people[pid]
        if p.by:
            births.append((p.by, pid))
        if p.dy:
            deaths.append((p.dy, pid))
    best = None
    for by, bsrc in births:
        for dyy, dsrc in deaths:
            if impossible(by, dyy):
                continue
            # Prefer the pair that keeps the survivor's own value where it can.
            score = (by == sy) + (dyy == dy)
            if best is None or score > best[0]:
                best = (score, by, dyy, bsrc, dsrc)
    if best is None:
        log.append(f"DATE-UNFIXED\t{survivor.xref}\t{sy}-{dy}\t"
                   f"no coherent pair among {len(members)} merged records")
        return None

    _, by, dyy, bsrc, dsrc = best
    changed = []
    if by != sy:
        swap_event_date(survivor, "BIRT", people[bsrc].rec)
        changed.append(f"BIRT {sy}->{by} from {bsrc}")
    if dyy != dy:
        swap_event_date(survivor, "DEAT", people[dsrc].rec)
        changed.append(f"DEAT {dy}->{dyy} from {dsrc}")
    if changed:
        log.append(f"DATE-REPAIR\t{survivor.xref}\t{sy}-{dy} was impossible\t"
                   f"{'; '.join(changed)}")
        return changed
    return None


def swap_event_date(survivor, tag, donor_rec):
    """Replace the survivor's DATE on `tag` with the donor's, keeping the old
    one visible as a note so the disagreement is not hidden."""
    ev = survivor.kid(tag)
    dev = donor_rec.kid(tag)
    if ev is None or dev is None:
        return
    new = dev.kid("DATE")
    if new is None:
        return
    old = ev.kid("DATE")
    if old is not None:
        note = G.Line(2, None, "NOTE",
                      f"Date in the MyHeritage export was {old.full_value()!r}, "
                      f"which is impossible against the other date on this "
                      f"person; replaced from duplicate record {donor_rec.xref}.")
        ev.children.remove(old)
        ev.children.insert(0, note)
    ev.children.insert(0, new.clone())


def xref_num(x):
    m = re.search(r"(\d+)", x or "")
    return int(m.group(1)) if m else 0


def merge_individual(survivor, loser, log):
    """Fold `loser`'s lines into `survivor`. Returns list of note strings."""
    notes = []
    have = {c.signature() for c in survivor.children}
    have_tags = Counter(c.tag for c in survivor.children)

    for child in loser.children:
        tag = child.tag
        sig = child.signature()

        if tag in MH_NOISE:
            continue
        if sig in have:
            continue  # identical fact already present

        if tag == "NAME":
            # Keep every distinct spelling — they are genuine alternates and
            # this tree's names vary a lot (Muller / Müller / Mueller).
            survivor.children.append(child.clone())
            have.add(sig)
            notes.append(f"alt NAME: {child.full_value()}")
            continue

        if tag in SINGLETON and have_tags.get(tag):
            existing = survivor.kid(tag)
            merged = merge_event(existing, child, loser.xref)
            if merged:
                notes.append(f"{tag}: {merged}")
            continue

        survivor.children.append(child.clone())
        have.add(sig)
        have_tags[tag] += 1

    return notes


def merge_event(existing, incoming, donor_xref):
    """Fill gaps in a single-valued event from the duplicate's copy.

    Only *adds* sub-facts the survivor lacks (a PLAC where there was none, a
    source citation). A conflicting DATE or PLAC is left alone and reported,
    never overwritten — the survivor was chosen as the more complete record.
    """
    added = []
    have = {c.signature() for c in existing.children}
    have_tags = {c.tag for c in existing.children}
    for c in incoming.children:
        if c.signature() in have:
            continue
        if c.tag in ("DATE", "PLAC", "AGE", "CAUS", "TYPE") and c.tag in have_tags:
            cur = existing.kid(c.tag)
            if G.norm(cur.full_value()) != G.norm(c.full_value()):
                # The duplicate disagrees. The survivor's value stands — it was
                # chosen as the more complete record — but the alternative is
                # kept as a note rather than thrown away. Usually the two are
                # the same event at different precision ("Millford" against
                # "St Anne's Hospital, Eastbrook"), and which one a
                # researcher wants is not ours to decide.
                existing.children.append(G.Line(
                    existing.level + 1, None, "NOTE",
                    f"Alternative {c.tag} from merged duplicate record "
                    f"{donor_xref}: {c.full_value()}"))
                added.append(f"alt {c.tag} kept as note: {c.full_value()!r}")
            continue
        existing.children.append(c.clone())
        have.add(c.signature())
        have_tags.add(c.tag)
        if c.tag in ("DATE", "PLAC"):
            added.append(f"filled {c.tag}={c.full_value()!r}")
    if not existing.value and incoming.value:
        existing.value = incoming.value
    return "; ".join(added)


# ------------------------------------------------------------------ pointers

def remap_pointers(roots, mapping):
    """Rewrite every @Ixxx@ pointer value through `mapping`."""
    n = 0
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node.value.startswith("@") and node.value in mapping:
            new = mapping[node.value]
            if new != node.value:
                node.value = new
                n += 1
        stack.extend(node.children)
    return n


def dedupe_pointer_lists(rec, tags):
    """After remapping, the same pointer can appear twice (e.g. CHIL)."""
    removed = 0
    seen = defaultdict(set)
    keep = []
    for c in rec.children:
        if c.tag in tags and c.value.startswith("@"):
            if c.value in seen[c.tag]:
                removed += 1
                continue
            seen[c.tag].add(c.value)
        keep.append(c)
    rec.children = keep
    return removed


# ------------------------------------------------------------------ families

def family_key(fam):
    h = [c.value for c in fam.kids("HUSB") if c.value]
    w = [c.value for c in fam.kids("WIFE") if c.value]
    return (tuple(sorted(h)), tuple(sorted(w)))


def merge_families(recs, roots, log):
    """Two FAM records with the same couple are the same family."""
    fams = [r for r in roots if r.tag == "FAM"]
    by_key = defaultdict(list)
    for f in fams:
        k = family_key(f)
        if k == ((), ()):
            continue  # childless, parentless husk — handled separately
        by_key[k].append(f)

    mapping = {}
    dropped = set()
    merged = 0
    for k, group in by_key.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda f: (-D.count_facts(f), xref_num(f.xref)))
        keep = group[0]
        have = {c.signature() for c in keep.children}
        for other in group[1:]:
            for c in other.children:
                if c.signature() in have:
                    continue
                if c.tag in MH_NOISE:
                    continue
                keep.children.append(c.clone())
                have.add(c.signature())
            mapping[other.xref] = keep.xref
            dropped.add(other.xref)
            merged += 1
            log.append(f"FAM\t{other.xref}\t->\t{keep.xref}\tsame couple {k}")
        dedupe_pointer_lists(keep, PTR_TAGS_FAM)
    return mapping, dropped, merged


def drop_empty_families(recs, roots, log):
    """A FAM with no spouses and fewer than two children carries nothing."""
    dropped = set()
    for f in [r for r in roots if r.tag == "FAM"]:
        h = [c for c in f.kids("HUSB") if c.value]
        w = [c for c in f.kids("WIFE") if c.value]
        ch = [c for c in f.kids("CHIL") if c.value]
        if not h and not w and len(ch) < 2:
            dropped.add(f.xref)
            log.append(f"FAM\t{f.xref}\tdropped\tno spouses and {len(ch)} child(ren)")
    return dropped


# ------------------------------------------------------------------ repairs

# Character repairs, in order. Every entry was confirmed by reading the actual
# occurrences in this file — none is a generic guess.
#
#   Ã¸ family : a UTF-8 string that was once read as latin-1 and re-saved.
#               Seen in MyHeritage site titles.
#   ²         : stands for ø in an older Nordic source, dozens of times —
#               including one that also upper-cased the next letter (²R).
#   †         : stands for å, a few dozen times.
#   e´        : a detached acute — e´ is é.
#   ° and ¸   : one-off substitutions; each is listed literally rather than
#               given a rule, because ° stands for ø in one place and å in the
#               other, and ¸ is standing in for a comma.
#   U+200E    : a left-to-right mark sitting inside words (inside a surname).
CHAR_REPAIRS = [
    ("Ã¸", "ø"), ("Ã¦", "æ"),
    ("Ã¥", "å"), ("Ã¶", "ö"),
    ("Ã¼", "ü"), ("Ã¤", "ä"),
    ("Ã", "ß"), ("Ã©", "é"),
    ("Ã", "Ø"), ("Ã", "Ä"),
    ("Ã", "Ö"), ("Ã", "Ü"),
    ("²R", "ør"), ("²", "ø"),
    ("†", "å"),
    ("e´", "é"),
    # one-off literal substitutions (a ° that stood for ø in one word and å in
    # another) were listed here for the original export; add your own.
    ("François¸", "François,"),
    ("‎", ""),
    (" ", " "),
]


def normalise_characters(roots, log):
    """Apply CHAR_REPAIRS and put the whole file into NFC.

    The export mixes Unicode normalisation forms: 250 characters are written
    decomposed (NFD) — "Göteborg" as G-o-combining-diaeresis — while the rest of
    the file uses the precomposed form. They look identical and compare unequal,
    so a search for Göteborg silently misses them, and so does any sort. NFC is
    the form every other record already uses.
    """
    import unicodedata
    repaired = renormalised = 0
    counts = Counter()

    def walk(node, rec):
        nonlocal repaired, renormalised
        v = node.value
        if v:
            out = v
            for bad, good in CHAR_REPAIRS:
                if bad in out:
                    counts[f"{bad!r}->{good!r}"] += out.count(bad)
                    out = out.replace(bad, good)
            if out != v:
                repaired += 1
                if node.tag in ("NAME", "GIVN", "SURN", "PLAC", "OCCU", "TITL"):
                    log.append(f"CHAR-FIX\t{rec.xref}\t{node.tag}\t{v!r}\t->\t{out!r}")
            nfc = unicodedata.normalize("NFC", out)
            if nfc != out:
                renormalised += 1
                out = nfc
            if out != v:
                node.value = out
        for c in node.children:
            walk(c, rec)

    for r in roots:
        walk(r, r)
    for k, n in counts.most_common():
        log.append(f"CHAR-SUMMARY\t-\t-\t{k}\tx{n}")
    return repaired, renormalised
# External database IDs that the tree owner typed into the name field. Most are
# numbers from a community genealogy registry, on some surnames
# of the tree. They are real references and are kept — but moved to a REFN line, so the
# name reads as a name.
#
# Deliberately NOT matched: bare small numerals such as "Ingrid 1 /Berg/",
# "N N nr.2 /Lindvik/" and "Marta 2 Hansdatter". Those are the tree owner's
# way of telling two same-named people apart and carry meaning.
DB_ID_PATTERNS = [
    re.compile(r"#\s*(\d{3,7})\]?"),                 # Surname #10928
    re.compile(r",\s*(\d{3,7})\s*(?=/|$)"),          # Becker, 1995  (an ID, not a year)
    re.compile(r"\[\s*(\d{3,7})\s*\]"),              # Weber [123456]
    re.compile(r"(?<=/)\s*(\d{4,7})\s+(?=[A-Za-zÀ-ÿ])"),  # Johann /12345 Berg/
]


def extract_db_ids(value):
    """Return (clean_value, [ids]) — pull database IDs out of a name string."""
    ids = []
    out = value
    for pat in DB_ID_PATTERNS:
        while True:
            m = pat.search(out)
            if not m:
                break
            ids.append(m.group(1))
            out = out[:m.start()] + " " + out[m.end():]
    out = re.sub(r"\s*\[\s*#?\s*\]", "", out)     # empty bracket left behind
    out = re.sub(r",\s*(?=/|$)", "", out)          # dangling comma
    return tidy_name(out), ids


def tidy_name(s):
    """Normalise spacing around the GEDCOM surname slashes.

    "Anna M R /Saltmere /" -> "Anna M R /Saltmere/", without inventing a
    leading space when the given name is empty ("/Berg/" stays "/Berg/").
    """
    m = re.match(r"^(.*?)/(.*?)/(.*)$", s)
    if not m:
        return re.sub(r"\s{2,}", " ", s).strip()
    given = re.sub(r"\s{2,}", " ", m.group(1)).strip()
    surn = re.sub(r"\s{2,}", " ", m.group(2)).strip()
    suffix = re.sub(r"\s{2,}", " ", m.group(3)).strip()
    out = (given + " " if given else "") + "/" + surn + "/"
    if suffix:
        out += " " + suffix
    return out


def repair_text(roots, log):
    """Repair mangled letters; move database IDs from NAME into REFN.

    Nothing is deleted: every digit removed from a name reappears as a REFN on
    the same individual. Whitespace-only tidying is counted but not logged line
    by line — 369 of those would bury the real changes.
    """
    fixed = 0
    moved = 0
    whitespace = 0
    for r in roots:
        if r.tag != "INDI":
            continue
        found_ids = []
        for nm in r.kids("NAME"):
            for node in [nm] + nm.children:
                if node.tag not in ("NAME", "GIVN", "SURN", "CONC", "CONT"):
                    continue
                before = node.value
                after = before
                if node.tag in ("NAME", "GIVN", "SURN"):
                    stripped, ids = extract_db_ids(after)
                    if ids:
                        found_ids.extend(ids)
                        log.append(f"TEXT-DBID\t{r.xref}\t{node.tag}\t{after!r}\t->"
                                   f"\t{stripped!r}\tids={','.join(ids)}")
                        moved += 1
                        after = stripped
                    else:
                        tidy = re.sub(r"\s{2,}", " ", after).strip()
                        if tidy != after:
                            whitespace += 1
                            after = tidy
                if after != before:
                    node.value = after
        for i in dict.fromkeys(found_ids):
            refn = G.Line(1, None, "REFN", i)
            refn.children.append(G.Line(2, None, "TYPE", "external database id (moved from NAME)"))
            r.children.append(refn)
    log.append(f"TEXT-WHITESPACE\t-\t-\t{whitespace} name values tidied (spacing only)")
    return fixed, moved, whitespace


def repair_replacement_chars(roots, log):
    """Recover U+FFFD characters that are corrupt in MyHeritage's own data.

    Eight values in the tree already contained a replacement character before
    the export was written (two place names and a surname). The
    byte is gone upstream, so it cannot be decoded back.

    It can be *looked up*, though: the same file spells those places correctly
    elsewhere (the same place name and surname, intact). So build a vocabulary
    of every clean word in the file and substitute only when exactly one word
    matches the damaged one with a single character filled in. One candidate is
    a recovery; two would be a guess, and a guess is left alone and reported.
    """
    vocab = Counter()
    damaged = []

    def walk(node, rec):
        v = node.value
        if v:
            if "�" in v:
                damaged.append((rec, node))
            for tok in re.findall(r"[^\s,;()/\[\]]+", v):
                if len(tok) > 2 and "�" not in tok:
                    vocab[tok] += 1
        for c in node.children:
            walk(c, rec)

    for r in roots:
        walk(r, r)
    if not damaged:
        return 0, 0

    def score(word):
        """Vocabulary support for a candidate, allowing a grammatical ending.

        A word with a Nordic definite-article ending (-a) may be missing
        from the file while its stem is present. So a candidate is also supported by its own
        stem, at a lower weight than an exact hit.
        """
        return (10 * vocab.get(word, 0)
                + 2 * vocab.get(word[:-1], 0)
                + vocab.get(word[:-2], 0))

    fixed = unresolved = 0
    for rec, node in damaged:
        before = node.value
        out = before
        for tok in set(re.findall(r"[^\s,;()/\[\]]*�[^\s,;()/\[\]]*", before)):
            pat = re.compile("^" + "".join(
                "." if ch == "�" else re.escape(ch) for ch in tok) + "$")
            # The lost byte was invalid UTF-8, so the character it stood for
            # cannot have been ASCII. That rules out "Ostfold" as the
            # source of "?stfold" — only the ø spelling could have
            # produced a bad byte — and settles an otherwise close call.
            def non_ascii_fill(w):
                return any(ord(w[i]) > 127
                           for i, ch in enumerate(tok) if ch == "�")

            cands = Counter({w: score(w) for w in vocab
                             if pat.match(w) and non_ascii_fill(w)})
            # The letter is gone from the bytes, so also try the letters that
            # actually go missing in this tree: Nordic and German vowels.
            for ch in "øöåäüéæØÖÅ":
                w = tok.replace("�", ch)
                s = score(w)
                if s:
                    cands[w] = max(cands.get(w, 0), s)
            ranked = [(w, s) for w, s in cands.most_common() if s > 0]
            # Accept only a clear winner — 3x the runner-up. Anything closer is
            # a guess, and a guess in someone's family tree is worse than a gap.
            if ranked and (len(ranked) == 1 or ranked[0][1] >= 3 * ranked[1][1]):
                out = out.replace(tok, ranked[0][0])
            else:
                unresolved += 1
                log.append(f"TEXT-UNRESOLVED\t{rec.xref}\t{node.tag}\t{tok!r}"
                           f"\t{[w for w, _ in ranked[:4]]}")
        if out != before:
            node.value = out
            fixed += 1
            log.append(f"TEXT-RECOVER\t{rec.xref}\t{node.tag}\t{before!r}\t->\t{out!r}")
    return fixed, unresolved


def flag_impossible_dates(roots, log, annotate=True):
    """Find deaths before births and impossible lifespans.

    The dates are never changed — there is nothing in the file to correct them
    from, and inventing one would be worse than leaving it wrong. Instead each
    affected individual gets a NOTE, so the problem is visible in Gramps or
    webtrees at the point where someone would otherwise copy the bad date out.
    """
    flagged = []
    for r in roots:
        if r.tag != "INDI":
            continue
        by, bm, bd = G.parse_date(G.event_date(r, "BIRT"))
        dy, dm, dd = G.parse_date(G.event_date(r, "DEAT"))
        why = None
        if by and dy:
            if dy < by:
                why = "death before birth"
            elif dy - by > 110:
                why = f"lifespan of {dy - by} years"
        if by and (by < 1000 or by > 2026):
            why = "birth year out of range"
        if not why:
            continue
        flagged.append((r.xref, why, f"{by}-{dy}"))
        log.append(f"DATE\t{r.xref}\t{why}\t{by}-{dy}")
        if annotate:
            note = G.Line(1, None, "NOTE",
                          f"DATA WARNING: the dates on this person are impossible "
                          f"({by}-{dy}: {why}). This came from the MyHeritage tree "
                          f"and could not be corrected from any other record in the "
                          f"file. Do not rely on either date without checking a source.")
            r.children.append(note)
    return flagged


def label_tree_sources(roots, log):
    """Mark the 'sources' that are really other people's family trees.

    In the export this was written for, about nine in ten SOUR records were
    MyHeritage user trees: tagged `_TYPE Smart Matching` or `_TYPE Discovery`,
    every one of whose descriptions reads "MyHeritage family tree Family site:
    ...". Only the remaining tenth are actual record collections — the SSDI, California County Marriages, the U.S.
    Public Records Index.

    Imported anywhere else, all of them render identically as "sources", which
    makes the tree look evidenced when nine in ten citations are somebody else's
    unsourced guess agreeing with this one. Two trees agreeing is not evidence;
    it is frequently the same error copied twice. So each of those records gets
    a note saying what it is, at the point where someone would otherwise take it
    for a document.
    """
    labelled = collection = 0
    for r in roots:
        if r.tag != "SOUR":
            continue
        kind = r.kid_value("_TYPE")
        if kind not in ("Smart Matching", "Discovery"):
            collection += 1
            continue
        author = r.kid_value("AUTH") or "another MyHeritage user"
        r.children.append(G.Line(1, None, "NOTE",
            f"NOT A DOCUMENT. This 'source' is another MyHeritage user's family "
            f"tree ({author}), matched to this one by MyHeritage's "
            f"{kind} feature. It records that two trees agree, which is not "
            f"evidence — the same mistake is often copied between trees. Only "
            f"the sources of type 'Collection' in this file are record "
            f"collections."))
        labelled += 1
    log.append(f"SOURCE-LABEL\t-\t-\t{labelled} user-tree sources labelled, "
               f"{collection} record collections left alone")
    return labelled, collection


MAX_LINE = 200   # GEDCOM 5.5.1 caps a line at 255; leave room for level and tag


def rewrap_long_lines(roots, width=MAX_LINE):
    """Split over-long values into CONC continuations, as the spec requires.

    Folding the raw line breaks out of MyHeritage's embedded HTML leaves some
    values thousands of characters long, and GEDCOM 5.5.1 allows 255 per line.
    Importers vary from truncating to refusing the file.

    Splitting happens on CHARACTER boundaries. Splitting on byte boundaries is
    exactly the bug that cut 170 Nordic letters in half in the original
    export, and it is not worth reproducing.
    """
    wrapped = 0

    def visit(node):
        nonlocal wrapped
        # Pull the value and its existing CONC/CONT chain into plain segments.
        segments = [node.value]
        others = []
        for c in node.children:
            if c.tag == "CONC":
                segments[-1] += c.value
            elif c.tag == "CONT":
                segments.append(c.value)
            else:
                others.append(c)
        visit_all(others)

        overhead = len(f"{node.level} ") + len(node.tag) + 1
        if all(len(s) + overhead <= width for s in segments) and len(segments) == 1:
            return
        if all(len(s) + overhead <= width for s in segments):
            return  # already properly chunked

        new_children = []
        first = True
        for seg in segments:
            chunks = [seg[i:i + width] for i in range(0, len(seg), width)] or [""]
            for ci, chunk in enumerate(chunks):
                if first:
                    node.value = chunk
                    first = False
                elif ci == 0:
                    new_children.append(G.Line(node.level + 1, None, "CONT", chunk))
                else:
                    new_children.append(G.Line(node.level + 1, None, "CONC", chunk))
        wrapped += 1
        node.children = new_children + others

    def visit_all(nodes):
        for n in list(nodes):
            visit(n)

    for r in roots:
        visit_all(r.children)
    return wrapped


def strip_mh_noise(roots, enabled):
    if not enabled:
        return 0
    n = 0
    for r in roots:
        keep = []
        for c in r.children:
            if c.tag in MH_NOISE:
                n += 1
                continue
            keep.append(c)
        r.children = keep
    return n


# ------------------------------------------------------------------ validate

def validate(recs, roots):
    """Structural checks on the finished file."""
    problems = []
    indi = {r.xref: r for r in roots if r.tag == "INDI"}
    fam = {r.xref: r for r in roots if r.tag == "FAM"}
    present = {r.xref for r in roots if r.xref}

    # dangling pointers
    dangling = 0
    stack = list(roots)
    while stack:
        n = stack.pop()
        if re.fullmatch(r"@[A-Z]\d+@", n.value or "") and n.value not in present:
            dangling += 1
            if dangling <= 5:
                problems.append(f"dangling pointer {n.value} under {n.tag}")
        stack.extend(n.children)
    if dangling:
        problems.append(f"{dangling} dangling pointers total")

    # reciprocal links
    missing_link = 0
    for fid, f in fam.items():
        for tag in ("HUSB", "WIFE"):
            for c in f.kids(tag):
                p = indi.get(c.value)
                if p is not None and fid not in G.pointers(p, "FAMS"):
                    missing_link += 1
        for c in f.kids("CHIL"):
            p = indi.get(c.value)
            if p is not None and fid not in G.pointers(p, "FAMC"):
                missing_link += 1
    if missing_link:
        problems.append(f"{missing_link} FAM->INDI links without the reciprocal INDI->FAM link")

    # self-referential families
    for fid, f in fam.items():
        h = {c.value for c in f.kids("HUSB")}
        w = {c.value for c in f.kids("WIFE")}
        ch = {c.value for c in f.kids("CHIL")}
        if h & w:
            problems.append(f"{fid}: same person is husband and wife")
        if (h | w) & ch:
            problems.append(f"{fid}: a parent is also listed as a child")

    # ancestry cycles — a person being their own ancestor
    parents = defaultdict(set)
    for fid, f in fam.items():
        par = [c.value for c in f.kids("HUSB")] + [c.value for c in f.kids("WIFE")]
        for c in f.kids("CHIL"):
            parents[c.value].update(p for p in par if p)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = defaultdict(int)
    cycles = []

    def visit(start):
        stack = [(start, iter(parents.get(start, ())))]
        colour[start] = GREY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if colour[nxt] == GREY:
                    cycles.append(nxt)
                elif colour[nxt] == WHITE:
                    colour[nxt] = GREY
                    stack.append((nxt, iter(parents.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()

    for pid in indi:
        if colour[pid] == WHITE:
            visit(pid)
    if cycles:
        problems.append(f"{len(set(cycles))} ancestry cycles (person is their own ancestor): "
                        + ", ".join(sorted(set(cycles))[:5]))

    return problems, len(indi), len(fam)


def components(roots):
    """Connected components over parent/child/spouse edges."""
    indi = {r.xref for r in roots if r.tag == "INDI"}
    adj = defaultdict(set)
    for f in [r for r in roots if r.tag == "FAM"]:
        par = [c.value for c in f.kids("HUSB")] + [c.value for c in f.kids("WIFE")]
        par = [p for p in par if p in indi]
        kids = [c.value for c in f.kids("CHIL") if c.value in indi]
        for k in kids:
            for p in par:
                adj[k].add(p)
                adj[p].add(k)
        for a in par:
            for b in par:
                if a != b:
                    adj[a].add(b)
    seen, sizes = set(), []
    for pid in indi:
        if pid in seen:
            continue
        stack, n = [pid], 0
        seen.add(pid)
        while stack:
            x = stack.pop()
            n += 1
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        sizes.append(n)
    sizes.sort(reverse=True)
    return sizes
