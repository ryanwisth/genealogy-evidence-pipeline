#!/usr/bin/env python3
"""
make_demo_research.py [OUTDIR] — build the invented demo inputs for the quick start.

Everything here is made up. It starts from sample/sample-family.ged (420
invented people) and writes, into OUTDIR (default: sample/):

  demo-messy.ged                 the sample tree with three duplicate person
                                 records injected, so the cleanup half has work
  research/additions.json        an empty evidence ledger
  research/disputes.json         no disproved links yet
  research/round1/roster.json    one finder lane, three dead-end ancestors
  research/round1/journal.jsonl  a finder journal in the shape gate_claims.py reads
  research/round1/pages/*.txt    saved "record pages" as plain text: two support
                                 their claims, one contradicts its claim, and one
                                 cited source has no saved page at all
  research/round1/skeptic-verdicts.json
                                 a FIXTURE standing in for the skeptic agents'
                                 output (the real step needs Claude; the demo does
                                 not call any model)

Deterministic: a fixed seed and fixed content, so two runs write the same files.
"""
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SEED = 20260926
DUPLICATES = ["@I36@", "@I22@", "@I12@"]     # people copied as second records


def inject_duplicates(src, dst, rng):
    """Copy three people as second records, the way an accepted online match
    imports them: the copy arrives inside its own copy of the parents' family
    (same husband and wife, a new family id), and some exact dates come back
    as 'ABT year'. The scanner must find them through the shared parents."""
    text = open(src, encoding="utf-8").read()
    recs = re.split(r"(?m)^(?=0 )", text)
    by_id = {r.split()[1]: r for r in recs if r.startswith("0 @")}
    extra, add_fams = [], {}
    for k, pid in enumerate(DUPLICATES, 1):
        new, nf = f"@I{9000 + k}@", f"@F{9000 + k}@"
        body = [ln for ln in by_id[pid].splitlines()[1:] if not ln.startswith(("1 FAMS", "1 FAMC"))]
        famc = [ln.split()[2] for ln in by_id[pid].splitlines() if ln.startswith("1 FAMC")][0]
        body = [re.sub(r"^(2 DATE )\d{1,2} [A-Z]{3} (\d{4})$", r"\1ABT \2", ln) if rng.random() < 0.3 else ln
                for ln in body]
        extra.append(f"0 {new} INDI\n" + "\n".join(body) + f"\n1 FAMC {nf}\n")
        couple = [ln for ln in by_id[famc].splitlines() if ln.startswith(("1 HUSB", "1 WIFE"))]
        extra.append(f"0 {nf} FAM\n" + "\n".join(couple) + f"\n1 CHIL {new}\n")
        for ln in couple:
            add_fams.setdefault(ln.split()[2], []).append(nf)
    out = []
    for r in recs:
        if r.startswith("0 TRLR"):
            out += extra
        if r.startswith("0 @I") and r.split()[1] in add_fams:
            r = r.rstrip("\n") + "\n" + "".join(f"1 FAMS {f}\n" for f in add_fams[r.split()[1]])
        out.append(r)
    open(dst, "w", encoding="utf-8").write("".join(out))
    return len(DUPLICATES)


LABEL, LANE = "lane-a-1", "lane-a"
URL = "https://records.example.org/"

SOURCES = [
    {"sid": f"{LABEL}:S1", "title": "Baptisms 1852, St. Andrew's (invented)", "repository": "Example Regional Archive",
     "url": URL + "bap/1852-031", "kind": "parish register transcription", "detail": "vol. 4, p. 31, entry 12",
     "quote": "14 March 1852 Louisa, daughter of Henri Druvelle, cooper, and Jeanne Saltmere, born 12 March"},
    {"sid": f"{LABEL}:S2", "title": "Census 1880, district 7 (invented)", "repository": "Example National Census Index",
     "url": URL + "census/1880/d7-p112", "kind": "census transcription", "detail": "district 7, sheet 112, line 18",
     "quote": "Jacob Orbeline head 41 farmer; Marta Orbeline wife 38; Anna Orbeline daughter 4"},
    {"sid": f"{LABEL}:S3", "title": "Baptisms 1782, St. Clement's (invented)", "repository": "Example Regional Archive",
     "url": URL + "bap/1782-009", "kind": "parish register transcription", "detail": "vol. 1, p. 9",
     "quote": "Mary daughter of Louis Castellune and Agnes Thornquay"},
    {"sid": f"{LABEL}:S4", "title": "Burials 1834 (invented)", "repository": "Example Regional Archive",
     "url": URL + "bur/1834-044", "kind": "parish register transcription", "detail": "p. 44",
     "quote": "Mary Varnquist widow aged 52 buried 9 June 1834"},
    {"sid": f"{LABEL}:S5", "title": "An online family tree (invented)", "repository": "a user-contributed tree site",
     "url": URL + "trees/varnquist", "kind": "user tree (lead only)", "detail": "",
     "quote": "Louisa Druvelle died 1920"},
]

PAGES = {   # saved page text, named for the source id it was saved for
    "S1.txt": """Example Regional Archive - Baptisms, St. Andrew's, 1852
Register vol. 4, page 31
Entry 12
Date of baptism: 14 March 1852
Name: Louisa
Born: 12 March 1852
Father: Henri Druvelle, cooper
Mother: Jeanne Saltmere
Sponsors: Paul Druvelle; Marie Saltmere
""",
    "S2.txt": """Example National Census Index - 1880 - District 7 - Sheet 112
Line | Name | Relation | Age | Occupation
17 | Jacob Orbeline | Head | 41 | Farmer
18 | Marta Orbeline | Wife | 38 | Keeping house
19 | Anna Orbeline | Daughter | 4 |
20 | Karl Orbeline | Son | 2 |
""",
    # the claim says Louis; the register says Pierre, with a different mother
    "S3.txt": """Example Regional Archive - Baptisms, St. Clement's, 1782
Register vol. 1, page 9
Date: 3 May 1782
Child: Mary
Father: Pierre Castellune, weaver
Mother: Ursula Brimsworth
""",
}


def ev(date, place, *s):
    return {"date": date, "place": place, "sids": [f"{LABEL}:{x}" for x in s]}


def proposal():
    t = lambda n: f"{LABEL}:{n}"
    S = lambda *x: [f"{LABEL}:{y}" for y in x]
    return {
        "targets": [
            {"id": "@I35@", "name": "Louisa Druvelle", "outcome": "new ancestors found",
             "summary": "Her baptism names both parents.", "searched": ["Baptisms 1850-1854, St. Andrew's"]},
            {"id": "@I41@", "name": "Anna Orbeline", "outcome": "new ancestors found",
             "summary": "The 1880 census shows her in her parents' household.", "searched": ["1880 census, district 7"]},
            {"id": "@I25@", "name": "Mary Castellune", "outcome": "new ancestors found",
             "summary": "A 1782 baptism of a Mary Castellune.", "searched": ["Baptisms 1780-1784, St. Clement's"]},
        ],
        "sources": SOURCES,
        "new_people": [
            {"tid": t("N1"), "given": "Henri", "surname": "Druvelle", "sex": "M", "confidence": "high",
             "reasoning": "Named as father in the baptism that matches Louisa's birth date and parish.",
             "other": [{"tag": "OCCU", "date": "1852", "place": "", "detail": "cooper", "sids": S("S1")}]},
            {"tid": t("N2"), "given": "Jeanne", "surname": "Saltmere", "sex": "F", "confidence": "high",
             "reasoning": "Named as mother in the same baptism."},
            {"tid": t("N3"), "given": "Jacob", "surname": "Orbeline", "sex": "M", "confidence": "high",
             "birth": ev("ABT 1839", "", "S2"), "reasoning": "Head of the 1880 household that lists Anna, 4."},
            {"tid": t("N4"), "given": "Marta", "surname": "Fennaway", "sex": "F", "confidence": "medium",
             "birth": ev("ABT 1842", "", "S2"),
             "reasoning": "Wife in the same household; maiden name from a later marriage index (not cited here)."},
            {"tid": t("N5"), "given": "Louis", "surname": "Castellune", "sex": "M", "confidence": "medium",
             "reasoning": "Father in the 1782 baptism."},
            {"tid": t("N6"), "given": "Peter", "surname": "Varnquist", "sex": "M", "confidence": "high",
             "birth": ev("1876", "", "S1"), "reasoning": "A son of Louisa, from the same register."},
            {"tid": t("N7"), "given": "Laura", "surname": "Druvelle", "sex": "F", "confidence": "medium",
             "birth": ev("1951", "", "S1"), "reasoning": "A later relative named in a note."},
        ],
        "parent_links": [
            {"child": "@I35@", "father": t("N1"), "mother": t("N2"), "sids": S("S1"),
             "identifiers": ["baptism gives birth 12 March 1852, the tree's birth year",
                             "given name Louisa and surname Druvelle match"], "confidence": "high",
             "reasoning": "Birth date, name and surname all agree."},
            {"child": "@I41@", "father": t("N3"), "mother": t("N4"), "sids": S("S2"),
             "identifiers": ["Anna aged 4 in 1880 fits the tree's birth year 1876", "surname Orbeline"],
             "confidence": "high", "reasoning": "Only Anna Orbeline of that age in the district."},
            {"child": "@I25@", "father": t("N5"), "sids": S("S3"),
             "identifiers": ["baptism year 1782 matches", "given name Mary"], "confidence": "medium",
             "reasoning": "The only Mary Castellune baptized that year."},
            {"child": t("N6"), "father": "@I34@", "mother": "@I35@", "sids": S("S1"),
             "identifiers": ["parents' names", "parish"], "confidence": "high", "reasoning": "From the register."},
            {"child": t("N7"), "father": t("N1"), "sids": S("S1"),
             "identifiers": ["surname", "parish"], "confidence": "medium", "reasoning": "Same family."},
        ],
        "contests": [],
        "facts_on_existing": [
            {"on": "@I35@", "tag": "CHR", "date": "14 MAR 1852", "place": "St. Andrew's", "sids": S("S1"),
             "new_vs_tree": "new", "reasoning": "The baptism itself."},
            {"on": "@I41@", "tag": "CENS", "date": "1880", "place": "District 7", "sids": S("S2"),
             "new_vs_tree": "new", "reasoning": "Listed as daughter, 4."},
            {"on": "@I25@", "tag": "BURI", "date": "9 JUN 1834", "place": "", "sids": S("S4"),
             "new_vs_tree": "confirms (adds a source)", "reasoning": "Burial of a widow aged 52."},
            {"on": "@I35@", "tag": "DEAT", "date": "1920", "place": "", "sids": S("S5"),
             "new_vs_tree": "confirms (adds a source)", "reasoning": "An online tree gives 1920."},
        ],
        "tree_fixes": [],
    }


def verdicts():
    """The fixture: what a skeptic lane returned for the gated claims above,
    written by hand to show the shape. It accepts the two claims whose quotes the
    gate found on the saved pages, rejects the baptism whose page names a
    different father, and rejects the burial it could not re-open."""
    S = lambda *x: [f"{LABEL}:{y}" for y in x]
    t = lambda n: f"{LABEL}:{n}"
    keep = [s for s in SOURCES if s["sid"] in S("S1", "S2")]
    return {"results": [{"lane": LANE, "verdict": {
        "verdicts": [
            {"ref": f"new_people:{t('N1')}", "verdict": "accept", "reason": "father named in the baptism",
             "source_checked": "gate excerpt of S1"},
            {"ref": f"new_people:{t('N2')}", "verdict": "accept", "reason": "mother named in the baptism",
             "source_checked": "gate excerpt of S1"},
            {"ref": "parent_links:@I35@", "verdict": "accept", "reason": "birth date and names agree",
             "source_checked": "gate excerpt of S1"},
            {"ref": f"new_people:{t('N3')}", "verdict": "accept", "reason": "household head",
             "source_checked": "gate excerpt of S2"},
            {"ref": f"new_people:{t('N4')}", "verdict": "accept with edit",
             "reason": "the census gives only her married surname; maiden name not proven here",
             "source_checked": "gate excerpt of S2"},
            {"ref": "parent_links:@I41@", "verdict": "accept", "reason": "age and surname agree",
             "source_checked": "gate excerpt of S2"},
            {"ref": f"new_people:{t('N5')}", "verdict": "reject",
             "reason": "the register names Pierre Castellune and Ursula Brimsworth, not the quoted parents",
             "source_checked": "saved page S3 (gate: quote-missing)"},
            {"ref": "parent_links:@I25@", "verdict": "reject", "reason": "rests on the misquoted baptism",
             "source_checked": "saved page S3"},
            {"ref": "facts_on_existing:@I35@:CHR:14 MAR 1852", "verdict": "accept", "reason": "the baptism",
             "source_checked": "gate excerpt of S1"},
            {"ref": "facts_on_existing:@I41@:CENS:1880", "verdict": "accept", "reason": "listed as daughter",
             "source_checked": "gate excerpt of S2"},
            {"ref": "facts_on_existing:@I25@:BURI:9 JUN 1834", "verdict": "reject",
             "reason": "could not re-open the source; no saved page", "source_checked": "S4 (gate: not-fetched)"},
        ],
        "ledger": {
            "sources": keep,
            "people": [
                {"tid": t("N1"), "given": "Henri", "surname": "Druvelle", "sex": "M",
                 "events": [{"tag": "OCCU", "date": "1852", "place": "", "detail": "cooper", "sids": S("S1")}],
                 "note": "Father in Louisa's baptism: birth date 12 March 1852 and both names match."},
                {"tid": t("N2"), "given": "Jeanne", "surname": "Saltmere", "sex": "F", "events": [],
                 "note": "Mother in Louisa's baptism."},
                {"tid": t("N3"), "given": "Jacob", "surname": "Orbeline", "sex": "M",
                 "events": [{"tag": "BIRT", "date": "ABT 1839", "place": "", "sids": S("S2")}],
                 "note": "Head of the 1880 household listing Anna, 4."},
                {"tid": t("N4"), "given": "Marta", "surname": "", "sex": "F",
                 "events": [{"tag": "BIRT", "date": "ABT 1842", "place": "", "sids": S("S2")}],
                 "note": "Wife in the same household. Maiden name not proven by this record (medium confidence)."},
            ],
            "families": [
                {"husb": t("N1"), "wife": t("N2"), "children": ["@I35@"], "sids": S("S1")},
                {"husb": t("N3"), "wife": t("N4"), "children": ["@I41@"], "sids": S("S2")},
            ],
            "facts": [
                {"on": "@I35@", "tag": "CHR", "date": "14 MAR 1852", "place": "St. Andrew's", "sids": S("S1")},
                {"on": "@I41@", "tag": "CENS", "date": "1880", "place": "District 7", "sids": S("S2")},
            ],
            "notes": [
                {"on": "@I25@", "text": "A 1782 baptism of a Mary Castellune names Pierre Castellune and Ursula "
                                        "Brimsworth as parents; it was proposed as hers with other names and rejected. "
                                        "Identity with this Mary is not established."},
            ],
        },
        "disputes": [],
        "missed": ["S4 burial: no saved page and no fetch; re-open before proposing again"],
    }}]}


def main(outdir):
    rng = random.Random(SEED)
    os.makedirs(outdir, exist_ok=True)
    n = inject_duplicates(os.path.join(REPO, "sample", "sample-family.ged"),
                          os.path.join(outdir, "demo-messy.ged"), rng)
    rd = os.path.join(outdir, "research")
    r1 = os.path.join(rd, "round1")
    os.makedirs(os.path.join(r1, "pages"), exist_ok=True)
    json.dump({k: [] for k in ("sources", "people", "families", "facts", "notes")},
              open(os.path.join(rd, "additions.json"), "w"), indent=1)
    json.dump({"disputes": []}, open(os.path.join(rd, "disputes.json"), "w"), indent=1)
    json.dump([{"lane": LANE, "model": "sonnet", "route": "Example Regional Archive registers and census index",
                "label": LABEL, "ids": ["@I35@", "@I41@", "@I25@"]}],
              open(os.path.join(r1, "roster.json"), "w"), indent=1)
    with open(os.path.join(r1, "journal.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "started", "agentId": "a1", "label": f"research:{LABEL}"}) + "\n")
        fh.write(json.dumps({"type": "result", "agentId": "a1", "result": proposal()}, ensure_ascii=False) + "\n")
    for fn, txt in PAGES.items():
        open(os.path.join(r1, "pages", fn), "w", encoding="utf-8").write(txt)
    json.dump(verdicts(), open(os.path.join(r1, "skeptic-verdicts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"demo inputs in {outdir}: {n} duplicate records injected, "
          f"{len(proposal()['new_people']) + len(proposal()['parent_links']) + len(proposal()['facts_on_existing'])} "
          f"claims in the journal, {len(PAGES)} saved pages, 1 fixture verdict lane")


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "sample")))
