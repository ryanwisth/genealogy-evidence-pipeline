#!/usr/bin/env python3
"""
apply_additions.py BASE.ged LEDGER.json OUT.ged — write verified research into a new GEDCOM.

    python3 tools/apply_additions.py work/clean.ged \
        work/research/additions.json work/research/tree-research.ged

The cleaned GEDCOM stays as it is. Research found in records — a baptism that
names a dead end's father, a census household, a burial — goes into ONE ledger,
research/additions.json, each item carrying the record it came from, and this
tool folds the whole ledger into a fresh copy. Every research session appends
to the ledger; the output is regenerated, never edited by hand.

What it guarantees, and checks before it writes:
  * append-only: every line of BASE survives, in order (a subsequence check);
    existing records only GAIN lines (new facts, FAMC/FAMS pointers);
  * every added fact cites a source record, with the page and the quoted text
    of the record it rests on, and a NOTE naming the ledger entry and its date;
  * new ids are prefixed R (@IR0001@, @FR0001@, @SR0001@) so they can never
    collide with MyHeritage's, and are stable across runs (ledger order);
  * every pointer resolves, no id is used twice, no line exceeds 255 characters
    (long quotes are continued with CONC), and the output parses.

Ledger shape (see docs/evidence-ledger.md for the full contract):
  {"sources":  [{sid, title, repository, url, kind, detail, quote}],
   "people":   [{tid, given, surname, sex, events:[{tag,date,place,detail?,sids}], note?, added, entry}],
   "families": [{husb, wife, children:[…], marriage:{date,place,sids}?, sids, added, entry}],
   "facts":    [{on, tag, date, place, detail?, spouse?, sids, added, entry}],
   "notes":    [{on, text, added, entry}]}
husb/wife/children/on/spouse are existing ids ("@I46@") or ledger tids ("N1").
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gedcom_lib as G

EVENT_TAGS = {"NAME", "BIRT", "CHR", "BAPM", "CONF", "MARR", "DEAT", "BURI", "CENS",
              "RESI", "OCCU", "EMIG", "IMMI", "NATU"}
RECORD_KINDS = {"parish register transcription", "census transcription",
                "record image", "index", "headstone photo", "newspaper",
                "secondary compilation", "certified record held by the family"}
MAXLEN = 240                                 # stay well inside GEDCOM's 255


def fail(msg):
    sys.exit("apply_additions: " + msg)


def text_lines(level, tag, text):
    """One GEDCOM value: CONT for its line breaks, CONC for length. A CONC
    split never falls next to a space (some readers trim it and join words)."""
    out = []
    for k, part in enumerate(str(text).split("\n")):
        first = True
        while True:
            head = k == 0 and first
            lead = (f"{level} {tag} " if head
                    else f"{level + 1} {'CONT' if first else 'CONC'} ")
            room = MAXLEN - len(lead)
            if len(part) > room:
                cut = room
                while cut > 1 and (part[cut - 1] == " " or part[cut] == " "):
                    cut -= 1
                chunk, part = part[:cut], part[cut:]
            else:
                chunk, part = part, ""
            out.append((lead + chunk).rstrip())
            first = False
            if not part:
                break
    return out


def main(base, ledger_path, out_path):
    L = json.load(open(ledger_path, encoding="utf-8"))
    raw, enc, fixed = G.read_text(base)
    if fixed:
        fail(f"base is not clean UTF-8 ({enc}); apply to a cleaned GEDCOM, never the raw export")
    base_lines = raw.splitlines()

    # level-0 records: xref -> (start, end) line range
    recs, order = {}, []
    cur = None
    for i, ln in enumerate(base_lines):
        if ln.startswith("0 "):
            if cur:
                recs[cur[0]] = (cur[1], i)
            parts = ln.split(" ", 2)
            xref = parts[1] if len(parts) > 1 and parts[1].startswith("@") else None
            cur = (xref or f"__{i}", i)
            order.append(cur[0])
    if cur:
        recs[cur[0]] = (cur[1], len(base_lines))
    trlr = next((i for i, ln in enumerate(base_lines) if ln.strip() == "0 TRLR"), None)
    if trlr is None:
        fail("base has no 0 TRLR")

    # ---- ids ----------------------------------------------------------------
    sid_map, tid_map = {}, {}
    for n, s in enumerate(L.get("sources", []), 1):
        if s["sid"] in sid_map:
            fail(f"source {s['sid']} twice in the ledger")
        sid_map[s["sid"]] = f"@SR{n:04d}@"
        if s.get("kind") not in RECORD_KINDS:
            fail(f"source {s['sid']} has kind {s.get('kind')!r}: only records are cited "
                 f"(a user tree is a lead, never a source for an addition)")
    for n, p in enumerate(L.get("people", []), 1):
        if p["tid"] in tid_map:
            fail(f"person {p['tid']} twice in the ledger")
        tid_map[p["tid"]] = f"@IR{n:04d}@"
    for x in list(sid_map.values()) + list(tid_map.values()):
        if x in recs:
            fail(f"{x} already exists in the base")

    def ref(x, what):
        if x in tid_map:
            return tid_map[x]
        if isinstance(x, str) and x.startswith("@") and x in recs:
            return x
        fail(f"{what}: {x!r} is neither a ledger tid nor an INDI in the base")

    def cite_lines(level, sids):
        out = []
        if not sids:
            fail("an addition with no source")
        for s in sids:
            if s not in sid_map:
                fail(f"unknown source {s}")
            src = next(x for x in L["sources"] if x["sid"] == s)
            out.append(f"{level} SOUR {sid_map[s]}")
            if src.get("detail"):
                out += text_lines(level + 1, "PAGE", src["detail"])
            if src.get("quote"):
                out.append(f"{level + 1} DATA")
                out += text_lines(level + 2, "TEXT", src["quote"])
        return out

    def event_lines(ev, stamp):
        tag = ev["tag"]
        if tag not in EVENT_TAGS:
            fail(f"event tag {tag} not allowed")
        out = [f"1 {tag}"]
        if ev.get("date"):
            out.append(f"2 DATE {ev['date']}")
        if ev.get("place"):
            out += text_lines(2, "PLAC", ev["place"])
        if ev.get("detail"):
            out += text_lines(2, "NOTE", ev["detail"])
        out += cite_lines(2, ev.get("sids") or [])
        out += text_lines(2, "NOTE", stamp)
        return out

    def stamp_of(item):
        return (f"Added {item.get('added', '?')} from research "
                f"(research/additions.json, entry {item.get('entry', '?')}).")

    inserts = {}                                   # existing xref -> [lines]
    new_records = []                               # [(xref, lines)] in output order
    new_by = {}                                    # xref -> the same lines list

    # ---- people ---------------------------------------------------------------
    for p in L.get("people", []):
        x = tid_map[p["tid"]]
        rec = [f"0 {x} INDI", f"1 NAME {p.get('given', '').strip()} /{p.get('surname', '').strip()}/"]
        if p.get("given"):
            rec.append(f"2 GIVN {p['given'].strip()}")
        if p.get("surname"):
            rec.append(f"2 SURN {p['surname'].strip()}")
        rec.append(f"1 SEX {p.get('sex', 'U')}")
        for ev in p.get("events", []):
            rec += event_lines(ev, stamp_of(p))
        if p.get("note"):
            rec += text_lines(1, "NOTE", p["note"])
        rec += text_lines(1, "NOTE", stamp_of(p))
        new_records.append((x, rec))
        new_by[x] = rec

    # ---- families (parent links) -------------------------------------------------
    fams_of = {}                                   # base FAM -> {HUSB, WIFE ids}
    for x in order:
        if x in recs and base_lines[recs[x][0]].endswith(" FAM"):
            s0, e0 = recs[x]
            fams_of[x] = {ln.split()[2] for ln in base_lines[s0:e0] if ln.startswith(("1 HUSB", "1 WIFE"))}
    n = 0
    new_fams = {}                                  # FAMs the ledger creates: fx -> (record lines, {spouses})
    for f in L.get("families", []):
        # A child found for a couple the tree already has joins THAT family —
        # a second FAM for the same couple would draw them as two families.
        husb = ref(f["husb"], "family husb") if f.get("husb") else None
        wife = ref(f["wife"], "family wife") if f.get("wife") else None
        if husb and wife and husb in recs and wife in recs:
            fam = next((fx for fx, ptrs in fams_of.items() if ptrs == {husb, wife}), None)
            if fam:
                for c in f.get("children", []):
                    who = ref(c, "family child")
                    inserts.setdefault(fam, []).append(f"1 CHIL {who}")
                    (new_by[who] if who in new_by else inserts.setdefault(who, [])).append(f"1 FAMC {fam}")
                # setdefault: a record can add only a marriage to an existing couple, no child
                if f.get("marriage"):
                    inserts.setdefault(fam, []).extend(event_lines(dict(f["marriage"], tag="MARR"), stamp_of(f)))
                inserts.setdefault(fam, []).extend(cite_lines(1, f.get("sids") or []) + text_lines(1, "NOTE", stamp_of(f)))
                continue
        # the same couple an earlier ledger entry already joined: add to that
        # family, never a second one (round 3 re-states round 2's couples)
        if husb and wife:
            same = next((v for v in new_fams.values() if v[1] == {husb, wife}), None)
            if same:
                rec0 = same[0]
                have_kids = {ln.split()[2] for ln in rec0 if ln.startswith("1 CHIL ")}
                fx0 = rec0[0].split()[1]
                for c in f.get("children", []):
                    who = ref(c, "family child")
                    if who in have_kids:
                        continue
                    rec0.append(f"1 CHIL {who}")
                    (new_by[who] if who in new_by else inserts.setdefault(who, [])).append(f"1 FAMC {fx0}")
                if f.get("marriage"):
                    rec0.extend(event_lines(dict(f["marriage"], tag="MARR"), stamp_of(f)))
                rec0.extend(cite_lines(1, f.get("sids") or []) + text_lines(1, "NOTE", stamp_of(f)))
                continue
        n += 1
        fx = f"@FR{n:04d}@"
        if fx in recs:
            fail(f"{fx} already exists")
        rec = [f"0 {fx} FAM"]
        new_fams[fx] = (rec, {ref(f[r], "family " + r) for r in ("husb", "wife") if f.get(r)})
        for role in ("husb", "wife"):
            if f.get(role):
                who = ref(f[role], f"family {n} {role}")
                rec.append(f"1 {role.upper()} {who}")
                line = f"1 FAMS {fx}"
                if who in tid_map.values():
                    new_by[who].append(line)
                else:
                    inserts.setdefault(who, []).append(line)
        for c in f.get("children", []):
            who = ref(c, f"family {n} child")
            rec.append(f"1 CHIL {who}")
            line = f"1 FAMC {fx}"
            if who in tid_map.values():
                new_by[who].append(line)
            else:
                inserts.setdefault(who, []).append(line)
        if f.get("marriage"):
            m = dict(f["marriage"], tag="MARR")
            rec += event_lines(m, stamp_of(f))
        rec += cite_lines(1, f.get("sids") or [])
        rec += text_lines(1, "NOTE", stamp_of(f))
        new_records.append((fx, rec))

    # ---- facts on existing people --------------------------------------------------
    for f in L.get("facts", []):
        who = ref(f["on"], "fact")
        if f["tag"] == "NAME":
            # a name a record gives someone the tree has only as a blank or a
            # first name: a second NAME line, cited
            lines = [f"1 NAME {f.get('given', '').strip()} /{f.get('surname', '').strip()}/"]
            if f.get("given"):
                lines.append(f"2 GIVN {f['given'].strip()}")
            if f.get("surname"):
                lines.append(f"2 SURN {f['surname'].strip()}")
            lines += cite_lines(2, f.get("sids") or []) + text_lines(2, "NOTE", stamp_of(f))
            (new_by[who] if who in new_by else inserts.setdefault(who, [])).extend(lines)
            continue
        if f["tag"] == "MARR":
            sp = ref(f.get("spouse"), "marriage spouse")
            fam = next((fx for fx, ptrs in fams_of.items() if {who, sp} <= ptrs), None)
            if fam:
                inserts.setdefault(fam, []).extend(event_lines(f, stamp_of(f)))
            else:
                # a couple an earlier ledger entry joined (two people research added)
                nf = next((rec for rec, ptrs in new_fams.values() if {who, sp} <= ptrs), None)
                if nf is None:
                    fail(f"no family joins {who} and {sp} for a MARR fact")
                nf.extend(event_lines(f, stamp_of(f)))
        elif who in tid_map.values():
            new_by[who].extend(event_lines(f, stamp_of(f)))
        else:
            inserts.setdefault(who, []).extend(event_lines(f, stamp_of(f)))
    for nt in L.get("notes", []):
        who = ref(nt["on"], "note")
        lines = text_lines(1, "NOTE", nt["text"] + " " + stamp_of(nt))
        # a note on a person an earlier round added goes in that new record, as facts do
        (new_by[who] if who in new_by else inserts.setdefault(who, [])).extend(lines)

    # ---- sources ---------------------------------------------------------------------
    for s in L.get("sources", []):
        rec = [f"0 {sid_map[s['sid']]} SOUR"]
        rec += text_lines(1, "TITL", s["title"])
        if s.get("repository"):
            rec += text_lines(1, "AUTH", s["repository"])
        rec.append("1 _TYPE Collection")
        if s.get("url"):
            rec += text_lines(1, "NOTE", "URL: " + s["url"])
        rec += text_lines(1, "NOTE", f"Kind: {s['kind']}. Cited by the research ledger (research/additions.json).")
        new_records.append((sid_map[s["sid"]], rec))

    # ---- write ---------------------------------------------------------------------------
    out = []
    for i, ln in enumerate(base_lines):
        if i == trlr:
            for _, rec in new_records:
                out += rec
        out.append(ln)
    # insert gained lines at the end of their records (walk backwards so indices hold)
    final = list(out)
    ends = sorted(((recs[x][1], x) for x in inserts if x in recs), reverse=True)
    for e, x in ends:
        # index e in base_lines == index e in `out` as long as e <= trlr (new records sit at trlr)
        final[e:e] = inserts[x]
    if any(x not in recs for x in inserts):
        fail("an insert targets a record the base does not have")

    # ---- checks ----------------------------------------------------------------------------
    it = iter(final)
    if not all(any(ln == y for y in it) for ln in base_lines):
        fail("the output lost or reordered a line of the base")
    long = [ln for ln in final if len(ln) > 255]
    if long:
        fail(f"{len(long)} lines over 255 characters")
    xrefs = [ln.split()[1] for ln in final if ln.startswith("0 @")]
    if len(xrefs) != len(set(xrefs)):
        fail("an id is used twice")
    defined = set(xrefs)
    import re
    for ln in final:
        for ptr in re.findall(r" (@[A-Z0-9_]+@)$", ln):
            if not ln.startswith("0 ") and ptr not in defined:
                fail(f"dangling pointer {ptr} in: {ln}")
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(final) + "\n")
    G.parse(tmp)                                    # it must read back
    os.replace(tmp, out_path)
    print(f"wrote {out_path}: +{len(L.get('people', []))} people, +{len(L.get('families', []))} "
          f"families, +{len(L.get('sources', []))} sources, "
          f"{sum(len(v) for v in inserts.values())} lines added to {len(inserts)} existing records")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
