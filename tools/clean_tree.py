#!/usr/bin/env python3
"""
clean_tree.py — the whole cleanup pipeline, end to end.

    python3 tools/clean_tree.py IN.ged OUT.ged --reports DIR [--accept FILE]

Stages
------
  0  parse (input must already be structurally repaired)
  1  duplicate scan -> merge clusters            (dup_scan)
  2  optional extra merges adjudicated by hand   (--accept)
  3  fold duplicates into survivors, remap pointers
  4  merge the duplicate families that step 3 creates
  5  text and MyHeritage-noise repairs
  6  validate, measure connectivity, write reports

Every stage writes a line to reports/merge-log.tsv, so any change in the output
file can be traced back to the two records it came from.
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
import merge_gedcom as M


def load_accept(path):
    """Extra confirmed duplicate pairs, one '@A@<tab>@B@ # why' per line."""
    pairs = []
    if not path or not os.path.exists(path):
        return pairs
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("@") and parts[1].startswith("@"):
                pairs.append((parts[0], parts[1]))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--reports", required=True)
    ap.add_argument("--accept", help="TSV of extra confirmed duplicate pairs")
    ap.add_argument("--keep-upd", action="store_true",
                    help="keep MyHeritage _UPD timestamps (default: strip)")
    args = ap.parse_args()

    os.makedirs(args.reports, exist_ok=True)
    log = []
    stats = {}

    # ---- stage 1: scan --------------------------------------------------
    print("[1/6] duplicate scan")
    people, fam, recs, roots, dsu, results, ctx = D.run(args.src)
    stats["individuals_in"] = len(people)
    stats["families_in"] = len(fam)
    before_sizes = M.components(roots)
    stats["islands_before"] = len(before_sizes)
    stats["largest_island_before"] = before_sizes[0] if before_sizes else 0

    # ---- stage 2: hand-adjudicated additions ----------------------------
    extra = load_accept(args.accept)
    n_extra = n_vetoed = 0
    for a, b in extra:
        if a not in people or b not in people:
            log.append(f"ACCEPT-SKIP\t{a}\t{b}\tone of the ids is not in this file")
            continue
        # An adjudicated pair is still refused if merging it would make someone
        # their own ancestor. That is a structural impossibility, not a matter
        # of judgment, and it means one of the two records is really the other's
        # parent — a father/son weld that no amount of matching evidence fixes.
        if D.cluster_conflict(a, b, ctx):
            n_vetoed += 1
            log.append(f"ACCEPT-VETO\t{a}\t{b}\t{D.explain_conflict(a, b, ctx)}"
                       f"\tCORRUPT LINK IN THE SOURCE TREE — the two records are "
                       f"probably one person, but merging them as the tree stands "
                       f"would make somebody their own ancestor. Fix the link on "
                       f"MyHeritage, then re-export.")
            continue
        if dsu.union(a, b):
            n_extra += 1
            log.append(f"ACCEPT\t{a}\t{b}\tadjudicated")
    stats["adjudicated_merges"] = n_extra
    stats["adjudicated_vetoed"] = n_vetoed
    print(f"      + {n_extra} adjudicated pairs applied, {n_vetoed} vetoed as impossible")

    clusters = dsu.groups()
    stats["clusters"] = len(clusters)
    stats["records_in_clusters"] = sum(len(v) for v in clusters.values())

    # ---- stage 3: fold individuals --------------------------------------
    print(f"[2/6] folding {len(clusters)} clusters")
    mapping = {}
    drop_indi = set()
    n_daterepair = 0
    for root, members in clusters.items():
        survivor_id = M.pick_survivor(members, people)
        survivor = people[survivor_id].rec
        for pid in members:
            if pid == survivor_id:
                continue
            notes = M.merge_individual(survivor, people[pid].rec, log)
            mapping[pid] = survivor_id
            drop_indi.add(pid)
            log.append(f"INDI\t{pid}\t->\t{survivor_id}\t{people[pid].label()}"
                       f"\t{'; '.join(notes)}")
        if M.reconcile_dates(survivor, members, people, log):
            n_daterepair += 1
    stats["individuals_merged_away"] = len(drop_indi)
    stats["impossible_dates_repaired"] = n_daterepair

    roots = [r for r in roots if not (r.tag == "INDI" and r.xref in drop_indi)]
    n_ptr = M.remap_pointers(roots, mapping)
    stats["pointers_rewritten"] = n_ptr
    print(f"      {len(drop_indi)} records folded, {n_ptr} pointers rewritten")

    for r in roots:
        if r.tag == "INDI":
            M.dedupe_pointer_lists(r, M.PTR_TAGS_INDI)
        elif r.tag == "FAM":
            M.dedupe_pointer_lists(r, M.PTR_TAGS_FAM)

    # ---- stage 4: merge the families that just became duplicates --------
    print("[3/6] merging duplicate families")
    recs2 = {r.xref: r for r in roots if r.xref}
    fmap, fdrop, nfam = M.merge_families(recs2, roots, log)
    roots = [r for r in roots if not (r.tag == "FAM" and r.xref in fdrop)]
    M.remap_pointers(roots, fmap)
    empty = M.drop_empty_families(recs2, roots, log)
    roots = [r for r in roots if not (r.tag == "FAM" and r.xref in empty)]
    dead = fdrop | empty
    for r in roots:
        if r.tag == "INDI":
            r.children = [c for c in r.children
                          if not (c.tag in M.PTR_TAGS_INDI and c.value in dead)]
            M.dedupe_pointer_lists(r, M.PTR_TAGS_INDI)
        elif r.tag == "FAM":
            M.dedupe_pointer_lists(r, M.PTR_TAGS_FAM)
    stats["families_merged"] = nfam
    stats["families_dropped_empty"] = len(empty)
    print(f"      {nfam} duplicate families merged, {len(empty)} empty dropped")

    # ---- stage 4b: rebuild reciprocal links -----------------------------
    print("[4/6] rebuilding reciprocal FAM/INDI links")
    added, dropped_links = rebuild_links(roots, log)
    stats["reciprocal_links_added"] = added
    stats["contradictory_links_dropped"] = dropped_links
    print(f"      {added} missing back-links added, "
          f"{dropped_links} contradictory links dropped")

    # ---- stage 5: repairs -----------------------------------------------
    print("[5/6] text and date repairs")
    n_char, n_nfc = M.normalise_characters(roots, log)
    n_text, n_ids, n_ws = M.repair_text(roots, log)
    n_rec, n_unres = M.repair_replacement_chars(roots, log)
    flagged = M.flag_impossible_dates(roots, log)
    n_noise = M.strip_mh_noise(roots, not args.keep_upd)
    n_lab, n_coll = M.label_tree_sources(roots, log)
    stats["user_tree_sources_labelled"] = n_lab
    stats["real_record_collections"] = n_coll
    n_wrap = M.rewrap_long_lines(roots)
    stats["long_lines_rewrapped"] = n_wrap
    stats["mangled_chars_repaired"] = n_char
    stats["values_renormalised_nfc"] = n_nfc
    stats["corrupt_chars_recovered"] = n_rec
    stats["corrupt_chars_unresolved"] = n_unres
    stats["db_ids_moved_to_REFN"] = n_ids
    stats["name_spacing_tidied"] = n_ws
    stats["dates_flagged"] = len(flagged)
    stats["mh_noise_stripped"] = n_noise
    print(f"      {n_char} mangled characters repaired, {n_nfc} values put into NFC, "
          f"{n_ids} database IDs moved to REFN,")
    print(f"      {len(flagged)} impossible dates flagged, {n_noise} _UPD lines stripped")

    # ---- stage 6: validate and write ------------------------------------
    print("[6/6] validating")
    problems, n_indi, n_fam = M.validate(recs2, roots)
    after_sizes = M.components(roots)
    stats["individuals_out"] = n_indi
    stats["families_out"] = n_fam
    stats["islands_after"] = len(after_sizes)
    stats["largest_island_after"] = after_sizes[0] if after_sizes else 0

    update_header(roots, stats)
    G.write(args.dst, roots)

    write_reports(args.reports, log, results, people, stats, problems,
                  before_sizes, after_sizes, clusters)

    print()
    print(f"wrote {args.dst}")
    for k, v in stats.items():
        print(f"  {k:32} {v:,}" if isinstance(v, int) else f"  {k:32} {v}")
    if problems:
        print("\n  VALIDATION PROBLEMS:")
        for p in problems:
            print(f"    ! {p}")
    else:
        print("\n  validation: clean")


def rebuild_links(roots, log):
    """Make FAM->INDI and INDI->FAM agree. Merging can leave one side behind."""
    indi = {r.xref: r for r in roots if r.tag == "INDI"}
    fams = [r for r in roots if r.tag == "FAM"]
    added = 0
    dropped = 0
    for f in fams:
        for tag, back in (("HUSB", "FAMS"), ("WIFE", "FAMS"), ("CHIL", "FAMC")):
            for c in f.kids(tag):
                p = indi.get(c.value)
                if p is None:
                    continue
                if f.xref not in G.pointers(p, back):
                    node = G.Line(1, None, back, f.xref)
                    p.children.append(node)
                    added += 1
    # And the other direction: an INDI pointing at a family that does not list it.
    fam_by_id = {f.xref: f for f in fams}
    for pid, p in indi.items():
        for c in list(p.children):
            if c.tag not in ("FAMS", "FAMC"):
                continue
            f = fam_by_id.get(c.value)
            if f is None:
                p.children.remove(c)
                dropped += 1
                log.append(f"LINK-DROP\t{pid}\t{c.tag}\t{c.value}"
                           f"\tpoints at a family that no longer exists")
                continue
            if c.tag == "FAMC":
                if pid not in [x.value for x in f.kids("CHIL")]:
                    f.children.append(G.Line(1, None, "CHIL", pid))
                    added += 1
            else:
                listed = [x.value for x in f.kids("HUSB")] + [x.value for x in f.kids("WIFE")]
                if pid not in listed:
                    sex = p.kid_value("SEX")[:1].upper()
                    tag = "WIFE" if sex == "F" else "HUSB"
                    if not f.kids(tag):
                        f.children.append(G.Line(1, None, tag, pid))
                        added += 1
                    else:
                        # This person claims to be a spouse in a family whose
                        # husband and wife slots are both already taken by other
                        # people. One of the two claims is wrong and the family
                        # record is the one with corroborating children, so the
                        # dangling claim goes — but it is logged, not silent.
                        p.children.remove(c)
                        dropped += 1
                        h = [x.value for x in f.kids("HUSB")]
                        w = [x.value for x in f.kids("WIFE")]
                        log.append(f"LINK-DROP\t{pid}\tFAMS\t{c.value}"
                                   f"\tboth spouse slots already filled by {h}/{w}")
    return added, dropped


def update_header(roots, stats):
    for r in roots:
        if r.tag == "HEAD":
            note = G.Line(1, None, "NOTE",
                          "Cleaned copy of a GEDCOM export.")
            for text in [
                f"Duplicate individual records merged: {stats['individuals_merged_away']}.",
                f"Duplicate families merged: {stats['families_merged']}.",
                f"Disconnected groups: {stats['islands_before']} -> {stats['islands_after']}.",
                "Source of truth remains the original tree; this file is derived.",
                "See the reports/ folder beside this file for the full merge log.",
            ]:
                note.children.append(G.Line(2, None, "CONT", text))
            r.children.append(note)
            return


def write_reports(d, log, results, people, stats, problems, before, after, clusters):
    with open(os.path.join(d, "merge-log.tsv"), "w", encoding="utf-8") as fh:
        fh.write("kind\tfrom\tarrow\tto\tdetail\tnotes\n")
        for line in log:
            fh.write(line + "\n")

    with open(os.path.join(d, "review-pairs.tsv"), "w", encoding="utf-8") as fh:
        fh.write("score\ttier\tid_a\tperson_a\tid_b\tperson_b\tevidence\n")
        rows = [(s, t, a, b, e) for (a, b), (s, t, e, _) in results.items()
                if t == "REVIEW"]
        for s, t, a, b, e in sorted(rows, key=lambda r: -r[0]):
            fh.write(f"{s}\t{t}\t{a}\t{people[a].label()}\t{b}\t{people[b].label()}"
                     f"\t{'; '.join(e)}\n")

    # Merges that are almost certainly right but cannot be applied, because the
    # source tree records one of the two people as an ancestor of the other.
    # These are fixable only on MyHeritage, so they get their own short file
    # rather than being left as three lines inside a 40,000-line log.
    vetoes = [l for l in log if l.startswith("ACCEPT-VETO")]
    with open(os.path.join(d, "corrupt-links.txt"), "w", encoding="utf-8") as fh:
        fh.write("Corrupt parent links in the MyHeritage tree\n")
        fh.write("=" * 60 + "\n\n")
        fh.write("Each pair below was judged to be one person — by rule or by two\n")
        fh.write("independent reviewers — but could NOT be merged, because the tree\n")
        fh.write("records one of them as an ancestor of the other. Merging as the tree\n")
        fh.write("stands would make somebody their own ancestor.\n\n")
        fh.write("These are wrong links in the source data, not judgment calls. Fix\n")
        fh.write("them on MyHeritage and re-export, and the merges will go through.\n\n")
        if not vetoes:
            fh.write("None found in this build.\n")
        for line in vetoes:
            f = line.split("\t")
            fh.write(f"- {f[1]} and {f[2]}\n  {f[3] if len(f) > 3 else ''}\n\n")

    with open(os.path.join(d, "summary.txt"), "w", encoding="utf-8") as fh:
        for k, v in stats.items():
            fh.write(f"{k:32} {v}\n")
        fh.write("\nislands before (top 10): " + ", ".join(map(str, before[:10])) + "\n")
        fh.write("islands after  (top 10): " + ", ".join(map(str, after[:10])) + "\n")
        fh.write("\ncluster sizes: " +
                 str(dict(sorted(Counter(len(v) for v in clusters.values()).items()))) + "\n")
        fh.write("\nvalidation:\n")
        for p in problems or ["clean"]:
            fh.write(f"  {p}\n")


if __name__ == "__main__":
    main()
