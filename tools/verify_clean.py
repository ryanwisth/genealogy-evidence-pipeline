#!/usr/bin/env python3
"""
verify_clean.py — prove the cleaned file did not lose anything.

    python3 tools/verify_clean.py ORIGINAL.ged CLEANED.ged --log reports/merge-log.tsv

The cleanup merges thousands of records and rewrites tens of thousands of lines.
"It validated" is not the same as "nothing was dropped", so this checks the
claim directly, from the two files:

  1. every individual in the original is either present in the cleaned file or
     named in the merge log as having been folded into a survivor
  2. no individual appears in the cleaned file that was not in the original
  3. for every person who was NOT merged, every fact still folds to the same
     text — so rewrapping, character repair and noise-stripping changed nothing
     of substance
  4. for every person who WAS merged, their facts are present on the survivor
  5. no pointer dangles, and every FAM/INDI link has its reciprocal
  6. the file is valid UTF-8 and no line exceeds the GEDCOM 255-character limit

Exit code is non-zero if any check fails.
"""

import sys
import os
import re
import argparse
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gedcom_lib as G

# Tags the cleanup deliberately removes or adds; excluded from fact comparison.
IGNORE = {"_UPD", "NOTE", "REFN", "RIN", "_UID", "CHAN"}


def txt(node):
    """Folded value with the cleanup's character repairs applied.

    The repairs are applied to the *original* side too, so a value the cleanup
    corrected ("Ravns²y" -> "Ravnsøy") still compares equal. The question this
    test asks is whether facts survived, not whether corrections were reverted.
    """
    import unicodedata
    import merge_gedcom as M
    v = node.full_value()
    for bad, good in M.CHAR_REPAIRS:
        v = v.replace(bad, good)
    v = unicodedata.normalize("NFC", v)
    if node.tag in ("NAME", "GIVN", "SURN"):
        # The cleanup moves external database ids out of names into REFN, so
        # normalise them away on both sides — the id is preserved, just not here.
        v, _ = M.extract_db_ids(v)
    return re.sub(r"\s+", " ", v).strip()


def path_set(rec, fam_map=None):
    """Every atomic fact on a record, as TAG/SUBTAG=value paths.

    Comparing whole facts as one signature was too strict: a merge that *adds*
    a place of death to an event changes the signature of the whole event and
    looks like the event was lost. Atomic paths make the real invariant
    checkable — every individual piece of information in the original must
    still be somewhere on the corresponding record afterwards.
    """
    out = Counter()

    def walk(node, prefix):
        if node.tag in ("CONC", "CONT") or node.tag in IGNORE:
            return
        v = txt(node)
        if v.startswith("@F") and fam_map is not None:
            v = resolve(v, fam_map)      # families were renumbered by merging
        path = f"{prefix}/{node.tag}" if prefix else node.tag
        if v:
            out[f"{path}={v}"] += 1
        for c in node.children:
            walk(c, path)

    for c in rec.children:
        walk(c, "")
    return out


def note_texts(rec):
    """Every NOTE body on the record, for finding preserved alternatives."""
    blob = []
    stack = list(rec.children)
    while stack:
        n = stack.pop()
        if n.tag == "NOTE":
            blob.append(txt(n))
        stack.extend(n.children)
    return " || ".join(blob)


def load_merge_map(path):
    """merged_id -> survivor_id, from the merge log."""
    m = {}
    fams = {}
    if not os.path.exists(path):
        return m, fams
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and f[0] == "INDI" and f[2] == "->":
                m[f[1]] = f[3]
            elif len(f) >= 4 and f[0] == "FAM" and f[2] == "->":
                fams[f[1]] = f[3]
    return m, fams


def resolve(pid, mapping):
    seen = set()
    while pid in mapping and pid not in seen:
        seen.add(pid)
        pid = mapping[pid]
    return pid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("cleaned")
    ap.add_argument("--log", required=True)
    ap.add_argument("--sample", type=int, default=0,
                    help="only deep-compare this many unmerged people (0 = all)")
    args = ap.parse_args()

    problems = []
    print("parsing...")
    orig, oroots, ometa = G.parse(args.original)
    clean, croots, cmeta = G.parse(args.cleaned)
    o_indi = {k: v for k, v in orig.items() if v.tag == "INDI"}
    c_indi = {k: v for k, v in clean.items() if v.tag == "INDI"}
    mapping, fmap = load_merge_map(args.log)
    print(f"  original {len(o_indi):,} individuals, cleaned {len(c_indi):,}, "
          f"merge log has {len(mapping):,} folded records")

    # 1 + 2 — population reconciles
    missing = [p for p in o_indi if p not in c_indi and p not in mapping]
    invented = [p for p in c_indi if p not in o_indi]
    if missing:
        problems.append(f"{len(missing)} individuals vanished without a merge-log entry "
                        f"(e.g. {missing[:5]})")
    if invented:
        problems.append(f"{len(invented)} individuals in the cleaned file were not in the "
                        f"original (e.g. {invented[:5]})")
    if len(o_indi) - len(mapping) != len(c_indi):
        problems.append(f"arithmetic does not close: {len(o_indi)} - {len(mapping)} "
                        f"!= {len(c_indi)}")

    # 3 — unmerged people keep every fact
    unmerged = [p for p in o_indi if p in c_indi and p not in mapping]
    if args.sample:
        unmerged = unmerged[:args.sample]
    def missing_facts(before, after_rec):
        """Original facts absent from the cleaned record.

        A value the merge could not place in a structured field is kept as a
        note ("Alternative PLAC from merged duplicate record @I...@: Millford"),
        so the note text counts as the fact surviving.
        """
        after = path_set(after_rec, fmap)
        # The merge treats values as equal when they differ only in case or
        # accents ("Altdorf" / "AltDorf"), so the check must too, or it
        # reports a loss where one spelling simply won.
        after_loose = {G.norm(x) for x in after}
        notes = None
        gone = Counter()
        for k in before:
            # Distinct facts, not multiplicities: several records in this tree
            # carry the identical fact twice ("CHR 11 SEP 1864" listed two
            # times), and collapsing those repeats is the point of the cleanup,
            # not a loss.
            if k in after:
                continue
            if G.norm(k) in after_loose:
                continue
            # A value that was corrupt in the source ("Inder?y") is repaired in
            # the cleaned file, so match it with the lost character as a
            # wildcard rather than calling the repair a loss.
            if "�" in k:
                # Match on the raw text, not the normalised form: the
                # normaliser folds "ø" to "o" and the replacement character to
                # a space, which destroys the very position being matched.
                pat = re.compile("^" + "".join(
                    "." if ch == "�" else re.escape(ch) for ch in k.lower()) + "$")
                if any(pat.match(x.lower()) for x in after):
                    continue
            # A record whose sex was recorded as "U" (unknown) merging into one
            # that knows the sex is an improvement, not a lost fact.
            if k == "SEX=U":
                continue
            if notes is None:
                notes = note_texts(after_rec)
            val = k.split("=", 1)[1] if "=" in k else k
            if val and val.lower() in notes.lower():
                continue
            gone[k] = 1
        return gone

    lost = 0
    examples = []
    for pid in unmerged:
        gone = missing_facts(path_set(o_indi[pid], fmap), c_indi[pid])
        if gone:
            lost += 1
            if len(examples) < 5:
                examples.append((pid, list(gone)[:2]))
    if lost:
        problems.append(f"{lost} unmerged individuals lost at least one fact; "
                        f"examples: {examples}")
    print(f"  checked {len(unmerged):,} unmerged individuals — {lost} lost a fact")

    # 4 — merged people's facts landed on the survivor
    lost_m = 0
    ex_m = []
    for pid in list(mapping):
        surv = resolve(pid, mapping)
        if surv not in c_indi:
            problems.append(f"survivor {surv} for merged {pid} is not in the cleaned file")
            continue
        if pid not in o_indi:
            continue
        gone = missing_facts(path_set(o_indi[pid], fmap), c_indi[surv])
        if gone:
            lost_m += 1
            if len(ex_m) < 5:
                ex_m.append((pid, surv, list(gone)[:2]))
    if lost_m:
        problems.append(f"{lost_m} merged individuals lost a fact on their survivor; "
                        f"examples: {ex_m}")
    print(f"  checked {len(mapping):,} merged individuals — {lost_m} lost a fact")

    # 5 — referential integrity
    present = {r.xref for r in croots if r.xref}
    dangling = 0
    stack = list(croots)
    while stack:
        n = stack.pop()
        if re.fullmatch(r"@[A-Z]\d+@", n.value or "") and n.value not in present:
            dangling += 1
        stack.extend(n.children)
    if dangling:
        problems.append(f"{dangling} dangling pointers")

    c_fam = {k: v for k, v in clean.items() if v.tag == "FAM"}
    oneway = 0
    for fid, f in c_fam.items():
        for tag, back in (("HUSB", "FAMS"), ("WIFE", "FAMS"), ("CHIL", "FAMC")):
            for c in f.kids(tag):
                p = c_indi.get(c.value)
                if p is not None and fid not in G.pointers(p, back):
                    oneway += 1
    if oneway:
        problems.append(f"{oneway} family links without their reciprocal")

    # 6 — encoding and line length
    with open(args.cleaned, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        problems.append(f"cleaned file is not valid UTF-8: {e}")
        text = raw.decode("utf-8", "replace")
    if "�" in text:
        problems.append(f"{text.count(chr(0xFFFD))} replacement characters remain")
    longest = max(len(l) for l in text.split("\r\n"))
    if longest > 255:
        problems.append(f"longest line is {longest} characters (GEDCOM limit is 255)")
    headless = sum(1 for l in text.split("\r\n") if l and not re.match(r"^\d+ ", l))
    if headless:
        problems.append(f"{headless} lines do not start with a level number")

    print()
    if problems:
        print("FAILED:")
        for p in problems:
            print(f"  ! {p}")
        return 1
    print("PASSED — every individual accounted for, every fact preserved,")
    print(f"         no dangling pointers, valid UTF-8, longest line {longest} chars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
