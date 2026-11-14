#!/usr/bin/env python3
"""
gedcom_lib.py — a small, lossless GEDCOM record model.

Why this exists
---------------
The project already had `relationship-path.py`, which parses just enough of the
file to walk the graph (names, dates, FAMC/FAMS). That is read-only by design.
Merging duplicate records needs something different: every line of every record
has to survive a round trip, because a merge rewrites pointers and re-emits the
file. Anything the parser quietly drops would be deleted from the tree.

So this model keeps the *whole* record as a tree of Line nodes and never
discards a tag it does not understand. `parse()` then `serialise()` on an
untouched record reproduces it byte for byte (modulo CRLF normalisation).

Encoding
--------
MyHeritage declares `1 CHAR UTF-8` and then ships bytes that are not valid
UTF-8 (353 of them in the 2026-05-01 export). Decoding as latin-1 is a trap:
it turns "Müller" into "MÃ¼ller" and makes accented names unfindable. We honour
the declared charset and replace the bad bytes, exactly as the other tools do.

Structural precondition
-----------------------
This assumes the input has already been through `clean-gedcom.py`, i.e. every
line starts with a level number. `parse()` raises if that is not true, rather
than silently dropping tens of thousands of lines of note text.
"""

import re
import unicodedata

LINE_RE = re.compile(r"^(\d+)\s+(?:(@[^@]*@)\s+)?([A-Za-z0-9_]+)(?:\s(.*))?$")


class Line:
    """One GEDCOM line plus its children."""

    __slots__ = ("level", "xref", "tag", "value", "children")

    def __init__(self, level, xref, tag, value):
        self.level = level
        self.xref = xref
        self.tag = tag
        self.value = value or ""
        self.children = []

    def kids(self, tag):
        return [c for c in self.children if c.tag == tag]

    def kid(self, tag):
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def kid_value(self, tag):
        c = self.kid(tag)
        return c.value if c else ""

    def full_value(self):
        """Value with CONC/CONT children folded back in, as GEDCOM defines them."""
        out = self.value
        for c in self.children:
            if c.tag == "CONC":
                out += c.value
            elif c.tag == "CONT":
                out += "\n" + c.value
        return out

    def emit(self, out, level=None):
        lv = self.level if level is None else level
        parts = [str(lv)]
        if self.xref:
            parts.append(self.xref)
        parts.append(self.tag)
        if self.value:
            parts.append(self.value)
        out.append(" ".join(parts))
        for c in self.children:
            c.emit(out, None if level is None else lv + 1)

    def clone(self):
        n = Line(self.level, self.xref, self.tag, self.value)
        n.children = [c.clone() for c in self.children]
        return n

    def signature(self):
        """Structural fingerprint — used to spot identical facts when merging."""
        inner = "|".join(c.signature() for c in self.children)
        return f"{self.tag}={self.value}({inner})"

    def __repr__(self):
        return f"<Line {self.level} {self.tag} {self.value[:30]!r} +{len(self.children)}>"


def smart_decode(raw):
    """Decode a file that is UTF-8 *except* for a few stray single-byte chars.

    This export declares UTF-8 and is UTF-8 for 40 MB, apart from 327 bytes that
    were written as cp1252 — they are all Norwegian and German letters that came
    in from an older source: 0xF8 ø, 0xD8 Ø, 0xF6 ö, 0xE5 å.

    Decoding the whole file as latin-1 is the trap the project notes warn about:
    it turns "Müller" into "MÃ¼ller" and makes accented names unfindable.
    Decoding as UTF-8 with errors='replace' is the other trap: it throws the
    letter away and leaves U+FFFD, so "Ravnsøy" becomes "Ravns?y".

    Decoding per byte recovers the real letter: take UTF-8 as far as it is
    valid, decode only the offending bytes as cp1252, and carry on.
    """
    parts, i, fixed = [], 0, 0
    while True:
        try:
            parts.append(raw[i:].decode("utf-8"))
            break
        except UnicodeDecodeError as e:
            parts.append(raw[i:i + e.start].decode("utf-8"))
            bad = raw[i + e.start:i + e.end]
            parts.append(bad.decode("cp1252", errors="replace"))
            fixed += e.end - e.start
            i += e.end
    return "".join(parts), fixed


def read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    head = raw[:2048].decode("latin-1", "replace").upper()
    if "CHAR UTF-8" in head or "CHAR UNICODE" in head:
        try:
            return raw.decode("utf-8"), "utf-8", 0
        except UnicodeDecodeError:
            text, fixed = smart_decode(raw)
            return text, f"utf-8 (+{fixed} cp1252 bytes recovered)", fixed
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc, 0
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace"), "latin-1", 0


def parse(path):
    """Return (records, order, meta).

    records : {xref -> root Line} for every level-0 record that has an xref
    order   : the level-0 roots in file order (includes HEAD/TRLR, which have none)
    """
    text, enc, badbytes = read_text(path)
    roots, stack, bad = [], [], 0

    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        m = LINE_RE.match(raw)
        if not m:
            bad += 1
            if bad <= 5:
                print(f"  !! unparseable line {n}: {raw[:90]!r}")
            continue
        level = int(m.group(1))
        node = Line(level, m.group(2), m.group(3).upper(), m.group(4))

        if level == 0:
            roots.append(node)
            stack = [node]
            continue
        while len(stack) > level:
            stack.pop()
        if not stack:
            continue
        stack[-1].children.append(node)
        stack.append(node)

    if bad:
        raise ValueError(
            f"{bad} unparseable lines — run clean-gedcom.py on this file first."
        )

    records = {r.xref: r for r in roots if r.xref}
    return records, roots, {"encoding": enc, "bad_bytes": badbytes}


def serialise(roots):
    out = []
    for r in roots:
        r.emit(out, 0)
    return "\r\n".join(out) + "\r\n"


def write(path, roots):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(serialise(roots))


# ------------------------------------------------------------------ helpers

def norm(s):
    """Lowercase, accent- and punctuation-free. Müller == Muller == MUELLER."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "ss").replace("ø", "o").replace("æ", "ae").replace("å", "a")
    s = s.replace("Ø", "o").replace("Æ", "ae").replace("Å", "a")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    # Collapse runs of spaces. Dropping punctuation leaves doubles behind
    # ("Gerhard (George) Doe" -> "gerhard  george  doe"), and without this
    # two spellings of one name compare unequal over nothing but whitespace.
    return re.sub(r"\s+", " ", s).strip()


YEAR_RE = re.compile(r"\b(\d{3,4})\b")


def year(datestr):
    m = YEAR_RE.search(datestr or "")
    return int(m.group(1)) if m else None


MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def parse_date(datestr):
    """Return (y, m, d) with None for missing parts. Handles ABT/BET/BEF/AFT."""
    if not datestr:
        return (None, None, None)
    s = datestr.upper()
    s = re.sub(r"\b(ABT|ABOUT|EST|CAL|BEF|BEFORE|AFT|AFTER|FROM|TO|BET|AND|CIR|CIRCA)\b", " ", s)
    y = m = d = None
    ym = YEAR_RE.search(s)
    if ym:
        y = int(ym.group(1))
    mm = re.search(r"\b(" + "|".join(MONTHS) + r")\b", s)
    if mm:
        m = MONTHS[mm.group(1)]
    dm = re.search(r"\b(\d{1,2})\b", s)
    if dm and (not ym or dm.start() < ym.start()):
        v = int(dm.group(1))
        if 1 <= v <= 31:
            d = v
    return (y, m, d)


def name_parts(indi):
    """(given, surname, full) from the first NAME line, accent-normalised."""
    nm = indi.kid("NAME")
    if nm is None:
        return "", "", ""
    raw = nm.full_value()
    given = nm.kid_value("GIVN")
    surn = nm.kid_value("SURN")
    if not given or not surn:
        m = re.match(r"^(.*?)/(.*?)/(.*)$", raw)
        if m:
            given = given or (m.group(1) + " " + m.group(3)).strip()
            surn = surn or m.group(2).strip()
        else:
            given = given or raw
    full = f"{given} {surn}".strip()
    return norm(given), norm(surn), norm(full)


def event_date(indi, tag):
    e = indi.kid(tag)
    if e is None:
        return ""
    d = e.kid("DATE")
    return d.value if d else ""


def event_place(indi, tag):
    e = indi.kid(tag)
    if e is None:
        return ""
    p = e.kid("PLAC")
    return p.full_value() if p else ""


def pointers(rec, tag):
    return [c.value for c in rec.kids(tag) if c.value.startswith("@")]
