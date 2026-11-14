#!/usr/bin/env python3
"""
repair_structure.py — make a MyHeritage export parseable, without losing bytes.

    python3 tools/repair_structure.py IN.ged OUT.ged

It replaces an earlier, simpler cleaner script and fixes two defects that one
missed.

The four defects
----------------
1. **Raw line breaks inside values.** MyHeritage pastes whole HTML tables from
   its Record Match pages into NOTE/TEXT values and leaves the line breaks in.
   GEDCOM forbids a line break inside a value, so importers either abort
   (webtrees) or silently discard the tail (Gramps: tens of thousands of "line ignored").
   Fix: a lone LF or CR becomes a space; a CRLF-terminated line that does not
   start with a level number is folded back onto the line above.

2. **Multi-byte characters split across a CONC boundary.**  *(missed by the
   older script)*  MyHeritage measures its CONC chunks in bytes, not
   characters, so a two-byte "Ø" (C3 98) can be cut in half: the C3 ends one
   line and the 98 begins the next `CONC`. 167 characters in the 2026-05-01
   export were broken this way, all of them Nordic and German letters (ø, å, ä, ö).

   Concatenating the CONC restores them, so a parser that folds CONC first is
   fine. But anything that decodes the file as text *before* parsing — every
   grep, every editor, every encoding sniffer — sees an invalid byte and
   replaces it, and the letter is gone. Fix: move the orphaned continuation
   bytes back up to finish the character they belong to.

3. **Value text that looks like a GEDCOM line.** One photo caption in a
   Nordic language begins "3 av ...", so the folder in defect 1 reads "3 av ..."
   as level 3, tag "av" and leaves it as a bogus record line. Fix: fold any
   line whose tag is not a known GEDCOM tag back into the value above it.

4. **A stripped non-breaking space that was really a continuation byte.** The
   older script strips b" \\t\\xc2\\xa0" from a fragment before folding it.
   Those are stripped as *individual bytes*, and both C2 and A0 are legal
   UTF-8 continuation bytes, so the strip can eat half a character. Fix: strip
   the non-breaking space as the two-byte unit it is.

Nothing is deleted: every record, tag and value survives, and the script
refuses to write a file whose record counts do not match the input.
"""

import re
import sys

LEVEL = re.compile(rb"^\d+ ")
STRAY_BREAK = re.compile(rb"((?<!\r)\n|\r(?!\n))[ \t]*")
CONC_SPLIT = re.compile(rb"([\xc2-\xf4][\x80-\xbf]*)\r\n(\d+) CONC ([\x80-\xbf]+)")

RECORD_PATTERNS = {
    "individuals": re.compile(rb"^0 @I\d+@ INDI"),
    "families": re.compile(rb"^0 @F\d+@ FAM"),
    "sources": re.compile(rb"^0 @S\d+@ SOUR"),
    "notes": re.compile(rb"^0 @N\d+@ NOTE"),
}

# Enough of GEDCOM 5.5.1 to tell a real tag from note text that looks like one.
KNOWN_TAGS = set("""
HEAD TRLR INDI FAM SOUR NOTE OBJE REPO SUBM SUBN GEDC CHAR LANG DEST FILE COPR
NAME GIVN SURN NPFX NSFX SPFX NICK SEX ALIA ASSO RELA ANCI DESI RFN AFN REFN RIN
BIRT DEAT BURI CREM CHR CHRA BAPM BARM BASM BLES CONF FCOM ORDN NATU EMIG IMMI
CENS PROB WILL GRAD RETI ADOP EVEN FACT CAST DSCR EDUC IDNO NATI NCHI NMR OCCU
PROP RELI RESI SSN TITL MARR MARB MARC MARL MARS ENGA DIV DIVF ANUL
DATE PLAC ADDR ADR1 ADR2 ADR3 CITY STAE POST CTRY PHON EMAIL FAX WWW
AGE AGNC CAUS TYPE ROLE QUAY DATA TEXT PAGE AUTH PUBL ABBR MEDI FORM VERS
FAMC FAMS HUSB WIFE CHIL PEDI STAT CONC CONT CHAN TIME CORP UID
""".split())


def count_records(lines):
    return {name: sum(1 for ln in lines if pat.match(ln))
            for name, pat in RECORD_PATTERNS.items()}


def repair_split_chars(data):
    """Move orphaned UTF-8 continuation bytes back onto the character above."""
    fixed = 0

    def join(m):
        nonlocal fixed
        head, level, tail = m.group(1), m.group(2), m.group(3)
        need = needed_continuations(head)
        if need <= 0:
            return m.group(0)
        take, rest = tail[:need], tail[need:]
        fixed += 1
        if not rest:
            # The whole CONC value was continuation bytes; drop the empty line.
            return head + take
        return head + take + b"\r\n" + level + b" CONC " + rest

    out = CONC_SPLIT.sub(join, data)
    return out, fixed


def needed_continuations(chunk):
    """How many more bytes the trailing UTF-8 sequence in `chunk` still wants."""
    for back in range(1, 5):
        if back > len(chunk):
            return 0
        b = chunk[-back]
        if b < 0x80:
            return 0
        if b >= 0xC0:
            want = (2 if b < 0xE0 else 3 if b < 0xF0 else 4)
            have = back
            return max(0, want - have)
    return 0


def clean(data):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    before = count_records(data.split(b"\r\n"))

    data, n_split = repair_split_chars(data)
    data, n_stray = STRAY_BREAK.subn(b" ", data)

    folded, n_fold, n_faketag = [], 0, 0
    for line in data.split(b"\r\n"):
        real = False
        if LEVEL.match(line):
            m = re.match(rb"^\d+ (?:@[^@]*@ )?([A-Za-z0-9_]+)", line)
            tag = m.group(1).decode("ascii", "replace").upper() if m else ""
            real = bool(m) and (tag in KNOWN_TAGS or tag.startswith("_"))
            if m and not real:
                n_faketag += 1
        if real or not folded:
            folded.append(line)
            continue
        # \xc2\xa0 is a non-breaking space MyHeritage uses for indentation.
        # Strip it as a unit — stripping the bytes separately eats characters.
        fragment = line
        while fragment.startswith(b"\xc2\xa0"):
            fragment = fragment[2:]
        fragment = fragment.lstrip(b" \t")
        if fragment:
            folded[-1] = folded[-1].rstrip() + b" " + fragment
        n_fold += 1

    cleaned = b"\r\n".join(folded)
    after = count_records(folded)

    bad = [ln for ln in folded if ln and not LEVEL.match(ln)]
    assert not bad, f"{len(bad)} lines still malformed, e.g. {bad[0][:80]!r}"
    assert before == after, f"record counts changed: {before} -> {after}"
    try:
        cleaned.decode("utf-8")
        enc_ok = True
    except UnicodeDecodeError:
        enc_ok = False

    return cleaned, {
        "split_characters_rejoined": n_split,
        "stray_breaks_repaired": n_stray,
        "orphan_lines_folded": n_fold,
        "fake_tag_lines_folded": n_faketag,
        "valid_utf8_after": enc_ok,
        "records": after,
        "longest_line": max(len(ln) for ln in folded),
    }


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: repair_structure.py IN.ged OUT.ged")
    with open(sys.argv[1], "rb") as fh:
        cleaned, stats = clean(fh.read())
    with open(sys.argv[2], "wb") as fh:
        fh.write(cleaned)
    print(f"wrote {sys.argv[2]}")
    for k, v in stats.items():
        print(f"  {k:28} {v:,}" if isinstance(v, int) else f"  {k:28} {v}")


if __name__ == "__main__":
    main()
