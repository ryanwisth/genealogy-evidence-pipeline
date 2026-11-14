#!/usr/bin/env python3
"""
build_details.py — the full record behind every point in the view.

The layout builder emits what is needed to *draw* every person: name, years,
coordinates, family links. That is a few bytes each. This emits what is needed
to *read* one of them — every event, attribute, name variant, note and source
citation the GEDCOM holds — which is two orders of magnitude more.

Nothing is summarised away. What it does to the text:

  - transcripts: MyHeritage's glued field labels are split onto their own
    lines ("DoeGender: MaleResidence:" -> "Doe / Gender: Male /
    Residence:"), with any leftover markup stripped;
  - labels: raw tags become words (EVEN TYPE "MARR" -> "Married"; Gift,
    Ekteskap, Heirat, Huwelijk -> "Married"; DIV -> "Divorced" …);
  - places: an address's two halves (ADR1, ADR2) are kept apart — the most
    specific one is the place and the other is noted "also recorded at";
    census "Label: value" prefixes move from the place into the detail;
    "Ship; Port" becomes place = port, detail = "ship …";
  - marriages: one per person per date. The richest copy is kept; a copy at a
    genuinely different place is noted "also recorded at …";
  - citations: exact duplicates are dropped; the "Added by confirming a Smart
    Match" boilerplate rows (other users' trees) fold into one row with a count.

Shape (the details payload; `rows` is index-aligned with tree-data `ids`):

    srcTitles   ["1940 United States Federal Census", ...]
    srcTypes    ["Collection", "Smart Matching", ...]   MyHeritage's own label
    kinds       ["other", "birth", ...]                 fact kind codes, f[6]
    citeClasses ["record", "user tree", "Discovery", "Smart Match"]   c[4]
    schema      a description of every column (SCHEMA below)
    rows        [ {n, f, c, x} | 0, ... ]

Columns are only ever APPENDED, so a page written for an older payload still
reads the ones it knows. Facts carry an empty string rather than a missing
value, because the renderer tests truthiness and a sparse row costs more JSON.
"""

import os
import re
import sys
import html as _html
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence as E

SCHEMA = {
    "rows": "one entry per person, index-aligned with tree-data.json ids; "
            "0 = nothing recorded beyond the name and the family links",
    "n": "[type, text] — other names: 'Recorded as' (the raw first NAME, "
         "only when the display name was cleaned), 'Also recorded as', "
         "'Married name', 'Nickname' …",
    "f": "[label, date, place, detail, note, cits, kind, sortKey] — facts. "
         "label: human words, 'Married — Spouse' on family events. date: the "
         "GEDCOM date string as written. cits: citations on the fact (same "
         "shape as c; empty in this export). kind: index into kinds. sortKey: "
         "yyyymmdd of the first date in the string, 00 for an unknown month or "
         "day; BEF is one less, AFT sorts after the stated period; 0 = undated",
    "c": "[titleIdx, typeIdx, page, transcript, class, count?, titleIdxs?] — "
         "citations. class: index into citeClasses (evidence.py). count and "
         "titleIdxs: present only on a folded row that stands for `count` "
         "boilerplate Smart-Match or Discovery citations (\"Added by confirming "
         "a Smart Match\"); titleIdx is then a synthetic title, page reads "
         "'N trees' (distinct trees), the transcript lists them one per line "
         "('Tree — name there'), and titleIdxs holds their srcTitles indices",
    "x": "[label, text] — notes on the person",
    "kinds": "fact kind codes; 'identifier' is a reference number or id that "
             "a page may collapse",
    "citeClasses": "0 record · 1 user tree · 2 Discovery · 3 Smart Match",
}

# Every level-1 tag that says something about a person, with the words to put
# in front of it. Anything not listed still comes through — see LABEL fallback —
# so a tag nobody anticipated shows up as itself rather than vanishing.
LABEL = {
    "BIRT": "Born",            "DEAT": "Died",          "BURI": "Buried",
    "CREM": "Cremated",        "CHR":  "Christened",    "BAPM": "Baptised",
    "CHRA": "Adult christening",
    "CONF": "Confirmed",       "FCOM": "First communion",
    "RESI": "Lived",           "CENS": "Census",        "OCCU": "Occupation",
    "IMMI": "Immigrated",      "EMIG": "Emigrated",     "NATU": "Naturalised",
    "NATI": "Nationality",     "RELI": "Religion",      "EDUC": "Education",
    "GRAD": "Graduated",       "DSCR": "Description",   "PROB": "Probate",
    "WILL": "Will",            "TITL": "Title",         "NCHI": "Children born",
    "NMR":  "Marriages",       "ADOP": "Adopted",       "RETI": "Retired",
    "ORDN": "Ordained",        "CAST": "Caste",         "IDNO": "Id number",
    "SSN":  "Social security", "PROP": "Property",      "MARR": "Married",
    "DIV":  "Divorced",        "ENGA": "Engaged",       "MARB": "Marriage banns",
    "MARC": "Marriage contract", "MARL": "Marriage licence",
    "ANUL": "Annulled",        "EVEN": "Event",         "REFN": "Reference number",
    "AFN":  "Ancestral File number", "_FSFTID": "FamilySearch id",
    "_MILT": "Military service",
}

# Tags handled elsewhere in the record, or pure plumbing with nothing to read.
SKIP = {"NAME", "SEX", "FAMC", "FAMS", "CHAN", "RIN", "_UID", "SOUR", "NOTE",
        "OBJE", "REFN", "_PROJECT_GUID", "_EXPORTED_FROM_SITE_ID", "_RTLSAVE"}

NAME_LABEL = {"_MARNM": "Married name", "NICK": "Nickname", "NPFX": "Prefix",
              "NSFX": "Suffix", "_FORMERNAME": "Former name", "_AKA": "Also known as",
              "TYPE": "Kind"}

# ---- fact kinds ---------------------------------------------------------
KINDS = ["other", "birth", "baptism", "confirmation", "marriage", "divorce",
         "residence", "census", "occupation", "education", "migration",
         "military", "religion", "death", "burial", "identifier"]
K = {k: i for i, k in enumerate(KINDS)}

TAG_KIND = {
    "BIRT": "birth", "CHR": "baptism", "BAPM": "baptism", "CHRA": "baptism",
    "BAPL": "baptism", "CONF": "confirmation", "FCOM": "confirmation",
    "MARR": "marriage", "ENGA": "marriage", "MARB": "marriage",
    "MARC": "marriage", "MARL": "marriage", "MARS": "marriage",
    "DIV": "divorce", "DIVF": "divorce", "ANUL": "divorce",
    "RESI": "residence", "CENS": "census", "OCCU": "occupation",
    "RETI": "occupation", "EDUC": "education", "GRAD": "education",
    "IMMI": "migration", "EMIG": "migration", "NATU": "migration",
    "_MILT": "military", "RELI": "religion", "DEAT": "death",
    "BURI": "burial", "CREM": "burial",
    "IDNO": "identifier", "SSN": "identifier", "REFN": "identifier",
    "AFN": "identifier", "_FSFTID": "identifier", "RFN": "identifier",
}

# An EVEN says what it is in TYPE, in whatever language the tree was typed in.
# First match wins; matched from the start of the TYPE, ignoring case.
EVEN_KIND = [(k, re.compile(p, re.I)) for k, p in (
    ("identifier", r"(reference number|ancestral file number|familysearch id|"
                   r"find ?a ?grave|find-a-grave|findagrave|gw$|_fsftid|idno$|"
                   r"refn$|afn$)"),
    ("divorce",    r"(div$|divorce|skilt|skilsmisse|scheidung)"),
    ("marriage",   r"(marr|marriage|gift|ekteskap|heirat|huwelijk|giftemål|"
                   r"wedding|other marriages|civil$|alternate marri|mariage|"
                   r"trauung|vielse|engage|marb$|marc$|marl$)"),
    # birth and death only as the whole TYPE: "Death Notice of Marie … (wife
    # of Andrew)" is about somebody else, and "Death of Spouse" too
    ("birth",      r"(alternate birth|birth registration|birth|født|geburt|"
                   r"naissance)$"),
    ("baptism",    r"(dåp|dop$|døbt|døpt|hjemmedøbt|taufe|baptism|baptême|"
                   r"christening|chrest|alternate baptism|chr$|bapm$)"),
    ("confirmation", r"(konfirm|komfirm|confirmation|conf$|fcom$)"),
    ("census",     r"(cens$|census|folketelling|volkstelling|volkszählung)"),
    ("residence",  r"(resided|residence|resi$|bosted|bosatt|bopel|family address|"
                   r"move$|flytting|umzug|settlement|address)"),
    ("occupation", r"(employer|yrke|occupation|occu$|retired|beruf)"),
    ("education",  r"(degree|education|educ$|graduat)"),
    ("migration",  r"(arrival|departure|migration|destination|rückwanderung|"
                   r"emigra|immigra|utvandr|innvandr|auswanderung|einwanderung|"
                   r"naturali)"),
    ("military",   r"(military|milit|draft|enlist)"),
    ("religion",   r"(religion|church$|konfession)"),
    ("death",      r"(alternate death|death registration|death|død|dødsfall|tod|"
                   r"décès)$"),
    ("burial",     r"(funeral|gruftbestattung|obituary|gravlagt|begravelse|"
                   r"beerdigung|bisettelse|alternate burial|cemetary|cemetery|"
                   r"burial|buri$|crem)"),
)]

# The EVEN TYPE words that are the whole label, put into plain English. Only
# exact matches: "Heirat mit Anna geb. Doe" keeps its words (it names
# the spouse); its kind is still marriage.
_MARRIED = ("marr", "marriage", "gift", "gift 1. gang", "gift 2. gang",
            "gift 3. gang", "ekteskap", "heirat", "huwelijk", "giftemål",
            "wedding", "mariage", "trauung", "vielse")
EVEN_LABEL = dict({t: "Married" for t in _MARRIED}, **{
    "div": "Divorced", "skilt": "Divorced", "prop": "Property",
    "cens": "Census", "folketelling": "Census", "volkstelling": "Census",
    "idno": "Id number", "marl": "Marriage licence", "marc": "Marriage contract",
    "marb": "Marriage banns", "enga": "Engaged",
    "dåp": "Baptised", "dop": "Baptised", "døbt": "Baptised", "døpt": "Baptised",
    "taufe": "Baptised", "konfirmasjon": "Confirmed", "konfirmert": "Confirmed",
    "komfirmert": "Confirmed", "bosted": "Lived", "bosatt": "Lived",
    "bopel": "Lived", "gravlagt": "Buried", "begravelse": "Buried",
    "beerdigung": "Buried", "utvandret": "Emigrated", "yrke": "Occupation",
    "reference number": "Reference number",
    "myheritage:rel_partners": "Partners", "myheritage:rel_friends": "Friends",
    "myheritage:rel_unknown": "Relationship", "myheritage:rel_other": "Relationship",
})
_GENERIC_EVEN = re.compile(r"^(custom event|misc|personal facts|event-misc|"
                           r"unspecified|event|fact( \d+)?)$", re.I)

# ---- text ---------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")
# The cleanup pass records what a merge discarded as a note on the fact, one
# note per record it absorbed. A person rebuilt from seven duplicates ends up
# with seven near-identical sentences on their birth, which buries the birth.
# This pulls the distinct alternative VALUES out and drops the record ids.
_ALT = re.compile(r"Alternative (\w+) from merged duplicate record @[^@]+@:\s*(.+)")
# A physical description stored as pseudo-XML (older GEDCOMs; cleanup2 now
# writes the labelled form itself). Same output as cleanup2: "Hair: Brown · …".
_DSCR = re.compile(r"(?i)<(HAIR|EYES|HEIGHT|WEIGHT|BUILD|COMPLEXION|MARKS)>(.*?)</\1>")
# The field labels MyHeritage uses in record transcripts. Only these are broken
# on: a general "capital word before a colon" rule would split ordinary prose
# and place names ("Washington: ...") that happen to look the same.
_FIELDS = ("Gender", "Birth", "Death", "Marriage", "Marriages", "Residence",
           "Residence in", "Age", "Race", "Occupation", "Father", "Mother",
           "Fathers", "Mothers", "Parent", "Parents", "Wife", "Husband",
           "Spouse", "Spouses", "Child", "Children", "Sibling", "Siblings",
           "Census", "Burial", "Baptism", "Christening", "Immigration",
           "Emigration", "Naturalization", "Arrival", "Departure", "Ship",
           "Marital status", "Alias name", "Nationality", "Birthplace",
           "Household", "Relation to head", "Enum. District", "Roll", "Line",
           "State", "County", "Township", "Page", "Family", "Date",
           "Mother's maiden name", "Place", "Origin", "Ethnicity", "Religion",
           "Birth name", "Birth names", "Other names", "Alias names",
           "Also known as", "Possible relatives", "Residences",
           "Phone numbers", "Cause of death", "Military service",
           "Civil occupation", "Death place", "Burial place", "Last residence")
# A label glued to the word before it: MyHeritage exported these transcripts
# with no separator at all ("John DoeGender: MaleResidence: …"). The
# look-behind is any character that can end a value — a lower-case letter was
# not enough ("USAAge:", "Doe)Parent:") — but not an opening bracket, a
# dash or a slash, which start rather than end something.
_RUNON = re.compile(r"(?<=[^\s(\[{/\-])(" + "|".join(
    re.escape(f) for f in sorted(_FIELDS, key=len, reverse=True)) + r"):")
_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")


def fold(s):
    """The shared text fold (PLAN §4): ø→o … then NFKD, no marks, lower."""
    s = s or ""
    for a, b in (("ø", "o"), ("Ø", "O"), ("æ", "ae"), ("Æ", "AE"), ("œ", "oe"),
                 ("Œ", "OE"), ("ß", "ss"), ("ł", "l"), ("Ł", "L"), ("đ", "d"),
                 ("Đ", "D"), ("þ", "th"), ("Þ", "Th"), ("ı", "i")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not ("̀" <= c <= "ͯ")).lower()


def _key(s):
    """fold, then letters and digits only — for 'is this the same text'."""
    return re.sub(r"[^a-z0-9]+", " ", fold(s)).strip()


def detag(s):
    """MyHeritage transcript text -> plain text with one field per line.

    Some of this text has been through an HTML encoder more than once, so it
    arrives as `&amp;lt;p&amp;gt;` — unescaping once leaves `&lt;p&gt;` on the
    page. Unescape and strip in a loop until it stops changing instead. The
    label splitter runs on EVERY value: the old early return for text without
    markup meant it never ran once cleanup2 had removed the markup, and 2,344
    transcripts showed their labels glued together.
    """
    if not s:
        return ""
    if "<" in s or "&" in s:
        s = _DSCR.sub(lambda m: m.group(1).title() + ": " + m.group(2).strip() + " · ", s)
        s = re.sub(r"(?i)</?(DSCR|DESCRIPTION)>", "", s)
        s = re.sub(r"\s*·\s*(?=$|\n)", "", s.strip())
        for _ in range(4):
            before = s
            s = re.sub(r"(?i)<br\s*/?>", "\n", s)
            s = re.sub(r"(?i)</(p|div|tr|li|h\d)>", "\n", s)
            s = re.sub(r"(?i)</t[dh]>", "  ", s)
            s = _TAG.sub("", s)
            s = _html.unescape(s)
            if s == before:
                break
    s = _RUNON.sub(r"\n\1:", s)
    s = _WS.sub(" ", s)
    s = _NL.sub("\n\n", s)
    return "\n".join(ln.strip() for ln in s.split("\n")).strip()


# ---- dates --------------------------------------------------------------
_MON = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
_MON.update({m: i for i, m in enumerate(
    "JANUARY FEBRUARY MARCH APRIL MAY JUNE JULY AUGUST SEPTEMBER OCTOBER "
    "NOVEMBER DECEMBER".split(), 1)})
_MON["SEPT"] = 9
_YEAR = re.compile(r"(?<![\d/])(\d{3,4})(?![\d])")


def parse_date(s):
    """(y, m, d) of the FIRST date in a GEDCOM-ish date string, 0 = unknown.

    Handles '3 JUL 1679', 'JUL 1679', '1679', 'July 3 1679', 'ABT 1850',
    'BET 1763 AND 1852', 'FROM MAY 1981 TO 1 FEB 2021', '1700/01'.
    """
    s = (s or "").upper()
    m = _YEAR.search(s)
    if not m:
        return (0, 0, 0)
    y, pre = int(m.group(1)), s[:m.start()].rstrip(" ,.")
    mo = d = 0
    a = re.search(r"(?:\b(\d{1,2})\s+)?\b([A-Z]{3,9})\.?$", pre)
    b = re.search(r"\b([A-Z]{3,9})\.?\s+(\d{1,2})$", pre)
    if a and a.group(2) in _MON:
        mo, d = _MON[a.group(2)], int(a.group(1) or 0)
    elif b and b.group(1) in _MON:
        mo, d = _MON[b.group(1)], int(b.group(2))
    if not 1 <= d <= 31:
        d = 0
    return (y, mo, d)


def sort_key(s):
    """yyyymmdd of the first date; BEF one less, AFT after the period; 0 = none."""
    y, mo, d = parse_date(s)
    if not y:
        return 0
    k = y * 10000 + mo * 100 + d
    head = (s or "").strip().upper()
    if head.startswith("BEF"):
        return k - 1
    if head.startswith("AFT"):
        return y * 10000 + (mo or 12) * 100 + (d or 31) + 1
    return k


# ---- places -------------------------------------------------------------
# Census fields that MyHeritage wrote in FRONT of the town in an address —
# "Age: 24; Marital Status: Married; Relation to Head of House: Wife,
# Ward 20, …", sometimes glued ("Marital Status: SingleRelation to
# Head: Son, Springfield, …"). Only these labels are moved out; a place that
# happens to contain a colon ("Lutheran Cemetery: Springfield, …") is left.
_CLABELS = ["Relation to Head of House", "Relative Relation to Head",
            "Relation to Head", "RelationToHead", "Military Marital Status",
            "Residence Marital Status", "Marital Status", "MaritalStatus",
            "Residence Post Office", "Residence Able To Speak English",
            "Residence Age", "Enumeration District", "EnumerationDistrict",
            "Attended School", "AttendedSchool", "Able To Speak English",
            "AbleToSpeakEnglish", "Can Read", "CanRead", "Can Write", "CanWrite",
            "Number Of Children Born", "NumberOfChildrenBorn",
            "Number Of Children Living", "NumberOfChildrenLiving",
            "Years Married", "YearsMarried", "Employment Code", "EmploymentCode",
            "Employment Details", "EmploymentDetails", "Employment History",
            "EmploymentHistory", "Grade Completed", "GradeCompleted",
            "Income Other Sources", "IncomeOtherSources", "Income", "Is Employed",
            "IsEmployed", "Public Emergency Work", "PublicEmergencyWork",
            "Seeking Work", "SeekingWork", "Weeks Worked", "WeeksWorked",
            "Months UnEmployed Past Year", "MonthsUnEmployedPastYear",
            "Occupation", "Spouse", "Ektefelle", "Ehepartner", "Marriage to",
            "ID", "Age"]
_CLAB = r"(?:Age in \d{4}|" + "|".join(
    re.escape(l) for l in sorted(_CLABELS, key=len, reverse=True)) + r")"
_CPFX = re.compile(r"^\s*(" + _CLAB + r")\s*:\s*", re.I)
_CEND = re.compile(r"\s*;\s*|\s*,\s*|(?<=[a-z0-9)])(?=" + _CLAB + r"\s*:)")
_SAME = re.compile(r"^same\s+(?:house|place|address|city|town)\b[^-–]*[-–]\s*", re.I)
_NOT_PLACE = re.compile(r"^(same (house|place|address|city|town)|census|unknown|"
                        r"none|n/?a|private|\[private\]|\?+|-+)$", re.I)
_SHIP = re.compile(r"^(?:S\.\s*S\.\s*|SS\s+)?([^;,]+?)\s*;\s*(?:arrived\s+)?(.+)$", re.I)


_ANYLAB = re.compile(r"^([A-Z][A-Za-z']*(?: [A-Za-z']+){0,6}):\s*(.*)$", re.S)
_GLUED_C = re.compile(r"(?<=[A-Za-z0-9)])(?=(?:Age|Marital Status|Relation to Head|"
                      r"MaritalStatus|RelationToHead|Residence [A-Z][a-z]+)\b[^:;,]{0,30}:)")


def strip_census(v):
    """(place, ['Marital Status: Single', …], [other places]) from one line.

    Two shapes. A single known label in front of the town ("Marital Status:
    Single, Pinehurst, …", sometimes glued: "SingleRelation to Head: Son, …")
    is peeled off label by label. A run of ';'-separated "Label: value"
    segments is a census row whatever its labels are ("StreetAddress: Main
    St; Age: 27; …; RelationToHead: Wife, Springfield, Clark, Ohio"): every
    segment becomes detail, and what follows the last value is the place. A
    place in FRONT of such a run ("Route 9, Springfield, Ohio,
    USAAge: 23; …") is kept as another place.
    """
    bits, also = [], []
    if ";" in v:
        segs = [x.strip() for x in _GLUED_C.sub("; ", v).split(";")]
        labelled = [x for x in segs if _ANYLAB.match(x)]
        if len(labelled) >= 2:
            place = ""
            for k, seg in enumerate(segs):
                m = _ANYLAB.match(seg)
                if not m:
                    if "," in seg:
                        also.append(seg)
                    elif seg:
                        bits.append(seg)
                    continue
                val = m.group(2)
                if k == len(segs) - 1 and "," in val:
                    val, place = val.split(",", 1)
                if val.strip():
                    bits.append(m.group(1) + ": " + val.strip())
            place = place.strip(" ,")
            if not place and also:
                place = also.pop()
            return place, bits, also
    while True:
        m = _CPFX.match(v)
        if not m:
            return v, bits, also
        rest = v[m.end():]
        e = _CEND.search(rest)
        val, v = (rest[:e.start()], rest[e.end():]) if e else (rest, "")
        val = val.strip()
        if val:
            bits.append(m.group(1) + ": " + val)


def _parts(v):
    return [p for p in (_key(x) for x in v.split(",")) if p]


def same_place(a, b):
    """One place written two ways: equal, contained, or one a suffix of the other."""
    if not a or not b:
        return True
    ka, kb = _key(a), _key(b)
    if ka == kb or ka in kb or kb in ka:
        return True
    pa, pb = _parts(a), _parts(b)
    n = min(len(pa), len(pb))
    return n > 0 and pa[-n:] == pb[-n:]


def place_of(ev):
    """(place, detail bits, other places) for an event.

    A PLAC is the place. Failing that, an ADDR: MyHeritage fills ADR1 and ADR2
    inconsistently, and they are often two DIFFERENT residences from two merged
    sources — joining them fused "12 Main St., Springfield" with
    "Lake Township, Clark, Ohio" into one string no geocoder can place. So
    they stay apart: the line with the most comma components is the place, and
    the rest are returned to be noted "also recorded at".
    """
    lines = []
    p = ev.kid("PLAC")
    if p is not None and (p.full_value() or "").strip():
        lines = [p.full_value()]
    else:
        a = ev.kid("ADDR")
        if a is not None:
            if (a.full_value() or "").strip():
                lines += a.full_value().split("\n")
            for t in ("ADR1", "ADR2", "ADR3"):
                lines += [c.full_value() or "" for c in a.kids(t)]
            comp = [(c.full_value() or "").strip()
                    for t in ("CITY", "STAE", "POST", "CTRY") for c in a.kids(t)]
            comp = [c for c in comp if c]
            if comp:
                # a bare street line belongs in front of its own town
                street = [ln for ln in lines if ln.strip() and "," not in ln]
                lines = [ln for ln in lines if ln not in street]
                lines.append(", ".join([s.strip() for s in street] + comp))
    bits, out = [], []
    for ln in lines:
        for v in ln.split(" · "):   # cleanup2 writes two different halves as "A · B"
            v = " ".join(v.split())
            v, b, more = strip_census(v)
            bits += b
            for x in [v] + more:
                x = _SAME.sub("", x).strip(" ,;")
                if x and not _NOT_PLACE.match(x):
                    out.append(x)
    uniq = []
    for v in out:
        if any(_key(v) == _key(u) or _key(v) in _key(u) for u in uniq):
            continue
        uniq = [u for u in uniq if _key(u) not in _key(v)] + [v]
    if not uniq:
        return "", bits, []
    best = max(range(len(uniq)), key=lambda i: (len(_parts(uniq[i])), -i))
    return uniq[best], bits, [u for i, u in enumerate(uniq) if i != best]


def split_ship(place):
    """'Harwick; New York' -> ('New York', 'Harwick'); else (place, '')."""
    if ";" not in place:
        return place, ""
    m = _SHIP.match(place)
    if not m:
        return place, ""
    return m.group(2).strip(), m.group(1).strip()


def detail_of(ev, skip_type=False):
    """The odds and ends hanging off an event: age, cause, agency, type."""
    bits = []
    v = (ev.full_value() or "").strip()
    if v and v not in ("Y",):
        bits.append(detag(v))
    for tag, pre in (("TYPE", ""), ("AGE", "age "), ("CAUS", "cause: "),
                     ("AGNC", ""), ("RELI", ""), ("STAT", ""), ("STAE", "")):
        if tag == "TYPE" and skip_type:
            continue
        for c in ev.kids(tag):
            t = (c.full_value() or "").strip()
            if tag == "AGE":            # "age About 24-25" -> "age about 24-25"
                t = re.sub(r"(?i)^(about|abt\.?|circa|ca\.)\s+", "about ", t)
            if t:
                bits.append(pre + detag(t))
    return " · ".join(dict.fromkeys(b for b in bits if b))


def _join(*parts, sep=" · "):
    out = []
    for p in parts:
        for b in (p.split(sep) if p else []):
            if b and b not in out:
                out.append(b)
    return sep.join(out)


def collapse_notes(notes):
    """Fold the merge trail into one line per field, keeping every value."""
    alts, plain, said = {}, [], set()
    for n in notes:
        if not n:
            continue
        m = _ALT.match(n.strip())
        if m:
            field, val = m.group(1), m.group(2).strip()
            seen = alts.setdefault(field, [])
            if val not in seen:
                seen.append(val)
        else:
            # a merge can bring the same note twice (in one real tree, 161
            # facts); one copy is the whole of what it says
            k = " ".join(n.split())
            if k not in said:
                said.add(k)
                plain.append(n)
    out = []
    for field, vals in alts.items():
        word = {"PLAC": "place", "DATE": "date", "AGE": "age",
                "CAUS": "cause", "TYPE": "type"}.get(field, field.lower())
        lead = ("a merged record also gave this " + word + ": ") if len(vals) == 1 \
               else ("merged records also gave this " + word + " as: ")
        out.append(lead + " · ".join(vals))
    return "\n".join(plain + out)


def fact_label(tag, ev):
    """EVEN carries its real name in TYPE; everything else is a known tag."""
    if tag == "EVEN":
        t = " ".join((ev.kid_value("TYPE") or "").split())
        if not t or _GENERIC_EVEN.match(t):
            return "Event"
        if re.fullmatch(r"_?[A-Z]{3,7}", t) and t in LABEL:
            return LABEL[t]
        return EVEN_LABEL.get(t.lower(), t[:60])
    return LABEL.get(tag, tag.replace("_", " ").strip().title())


def fact_kind(tag, ev):
    if tag == "EVEN":
        t = " ".join((ev.kid_value("TYPE") or "").split())
        for k, rx in EVEN_KIND:
            if rx.match(t):
                return K[k]
        return K["other"]
    return K[TAG_KIND.get(tag, "other")]


# The spouse, when a person-level marriage names them in its value.
_SPOUSE = re.compile(r"^(?:spouses?|conjointe?|ektefelle|ehepartner(?:in)?|ehefrau|"
                     r"ehemann|husband|wife|partner|echtgeno(?:ot|te)|époux|épouse|"
                     r"married to|marriage to)\s*:\s*(.+)$", re.I)


def _take_spouse(detail):
    """(spouse, detail without it) from a 'Spouse: X · …' detail."""
    keep, who = [], ""
    for b in detail.split(" · ") if detail else []:
        m = _SPOUSE.match(b.strip())
        if m and not who and "\n" not in b:
            who = m.group(1).strip()
        else:
            keep.append(b)
    return who, " · ".join(keep)


# ---- citations ----------------------------------------------------------
_BOILER = re.compile(r"^(individual )?added (by confirming|via) an? (smart match|"
                     r"person discovery|instant discovery\W*)\.?$", re.I)
FOLD_TITLE = {E.SMART_MATCH: "Smart Matches with other users' family trees",
              E.DISCOVERY: "Discovery matches with other users' family trees"}


def citations(rec, srcmap, reg):
    """Every SOUR pointer directly under a record, with its transcript.

    A citation is `2 SOUR @S123@` plus a PAGE and a DATA/TEXT transcript; the
    title and kind live on the @S123@ record, which srcmap resolves.
    """
    out = []
    for s in rec.kids("SOUR"):
        title, stype = srcmap.get(s.value, ("", ""))
        page = detag((s.kid("PAGE").full_value() if s.kid("PAGE") else "") or "")
        text = ""
        data = s.kid("DATA")
        if data is not None:
            chunks = [d.full_value() for d in data.kids("TEXT")]
            text = detag("\n".join(c for c in chunks if c))
            dt = data.kid_value("DATE")
            if dt and not text:
                text = dt
        if not text:
            t2 = s.kid("TEXT")
            if t2 is not None:
                text = detag(t2.full_value())
        out.append([reg.title(title), reg.type(stype), page, text,
                    E.source_class(title, stype)])
    return out


def tidy_citations(rows, reg):
    """Drop exact repeats; fold boilerplate Smart-Match/Discovery rows."""
    seen, uniq = set(), []
    for r in rows:
        k = (r[0], r[1], r[2], r[3])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    stats = {"dupes": len(rows) - len(uniq), "folded": 0}
    out, groups = [], {}
    for r in uniq:
        if r[4] in FOLD_TITLE and _BOILER.match(r[3] or ""):
            if r[4] not in groups:
                groups[r[4]] = [len(out), []]
                out.append(None)
            groups[r[4]][1].append(r)
        else:
            out.append(r)
    for cls, (pos, rs) in groups.items():
        if len(rs) == 1:
            out[pos] = rs[0]
            continue
        lines = []
        for r in rs:
            t = reg.titles[r[0]] if r[0] >= 0 else "an unnamed tree"
            ln = t + (" — " + r[2] if r[2] and not r[2].startswith("http") else "")
            if ln not in lines:
                lines.append(ln)
        tis = list(dict.fromkeys(r[0] for r in rs if r[0] >= 0))
        nt = max(len(tis), 1)
        out[pos] = [reg.title(FOLD_TITLE[cls]), rs[0][1],
                    f"{nt} tree{'s' if nt != 1 else ''}",
                    "\n".join(lines), cls, len(rs), tis]
        stats["folded"] += len(rs) - 1
    return [r for r in out if r is not None], stats


class _Reg:
    """The shared title and type tables."""
    def __init__(self):
        self.titles, self.types, self.ti, self.yi = [], [], {}, {}

    def title(self, t):
        if not t:
            return -1
        if t not in self.ti:
            self.ti[t] = len(self.titles)
            self.titles.append(t)
        return self.ti[t]

    def type(self, t):
        if not t:
            return -1
        if t not in self.yi:
            self.yi[t] = len(self.types)
            self.types.append(t)
        return self.yi[t]


# ---- marriages ----------------------------------------------------------
def _base(label):
    return label.split(" — ")[0]


def merge_marriages(facts, meta):
    """One 'Married' per person per date: the richest copy, others noted.

    Five mechanisms restate one wedding (audit-data §1.5): a person-level
    EVEN TYPE MARR restating the family's MARR, one family holding two MARR
    events, the person-level event restated 2–4 times, two families for the
    same couple, and a spouseless family copying the real one. They share a
    date, so that is what they are grouped on. The copy kept has the spouse,
    comes from the family record, and has the most specific place; a copy
    whose place is genuinely different is kept as "also recorded at …".
    """
    groups = {}
    for i, f in enumerate(facts):
        if f[6] == K["marriage"] and _base(f[0]) == "Married" and f[1].strip():
            groups.setdefault(" ".join(f[1].upper().split()), []).append(i)
    drop, merged = set(), 0
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        def rank(i):
            f, m = facts[i], meta[i]
            return (bool(m["spouse"]), m["src"] == "fam", len(_parts(f[2])),
                    len(f[2]), len(f[3]) + len(f[4]), len(f[5]), -i)
        best = max(idxs, key=rank)
        B = facts[best]
        others = [i for i in idxs if i != best]
        if not B[2]:
            withp = [i for i in others if facts[i][2]]
            if withp:
                B[2] = facts[max(withp, key=lambda i: len(_parts(facts[i][2])))][2]
        also, spouses = [], []
        for i in others:
            f = facts[i]
            if f[2] and not same_place(f[2], B[2]) and \
                    not any(same_place(f[2], a) for a in also):
                also.append(f[2])
            sp = meta[i]["spouse"]
            if sp and meta[best]["spouse"] and _key(sp) != _key(meta[best]["spouse"]) \
                    and _key(sp) not in [_key(s) for s in spouses]:
                spouses.append(sp)
            B[3] = _join(B[3], f[3])
            # line by line, so a note both copies carry is kept once
            for ln in f[4].split("\n"):
                if ln.strip() and ln not in B[4].split("\n"):
                    B[4] = (B[4] + "\n" + ln).strip()
            for c in f[5]:
                if c not in B[5]:
                    B[5].append(c)
            drop.add(i)
            merged += 1
        extra = []
        if also:
            extra.append("also recorded at: " + "; ".join(also))
        if spouses:
            extra.append("also recorded with spouse: " + "; ".join(spouses))
        if extra:
            B[4] = (B[4] + "\n" + "\n".join(extra)).strip()
    return [f for i, f in enumerate(facts) if i not in drop], merged


def _names(rec, display):
    """Other names: 'Recorded as' when cleaning changed the name, then the rest."""
    rows = []
    for k, nm in enumerate(rec.kids("NAME")):
        raw = _WS.sub(" ", (nm.full_value() or "").replace("/", " ")).strip()
        if k == 0:
            if display and raw and _key(raw) != _key(display):
                rows.append(["Recorded as", raw])
        elif raw:
            rows.append(["Also recorded as", raw])
        for t, lab in NAME_LABEL.items():
            for c in nm.kids(t):
                v = _WS.sub(" ", (c.full_value() or "").replace("/", " ")).strip()
                if v:
                    rows.append([lab, v])
    out, seen = [], set()
    dk = _key(display) if display else None
    for lab, v in rows:
        k = (lab, _key(v))
        if k in seen or (lab == "Also recorded as" and _key(v) == dk):
            continue
        seen.add(k)
        out.append([lab, v])
    return out, len(rows) - len(out)


def build(people, fam, recs, display=None, stats=None):
    """people: {pid: Person}; fam: {fid: Line}; recs: every record by id.

    display: {pid: cleaned display name} from build_tree_view — used for the
    spouse named on a family event and to tell whether the raw name differs.
    stats: a dict that receives before/after counts for the build log.
    """
    display = display or {}
    st = stats if stats is not None else {}
    for k in ("marriages merged", "citation exact repeats dropped",
              "boilerplate citations folded", "name rows deduped",
              "places split (ADR halves)", "census prefixes moved",
              "ships split from port", "identifier facts",
              "raw-tag labels humanised"):
        st.setdefault(k, 0)
    srcmap = E.sources(recs)
    reg = _Reg()
    out = {}

    def fact(tag, ev, spouse_of=None):
        date = " ".join(((ev.kid("DATE").full_value() if ev.kid("DATE") else "")
                         or "").split())
        kind = fact_kind(tag, ev)
        label = fact_label(tag, ev)
        if tag == "EVEN" and label != (ev.kid_value("TYPE") or "").strip()[:60] \
                and label != "Event":
            st["raw-tag labels humanised"] += 1
        place, pbits, also = place_of(ev)
        if also:
            st["places split (ADR halves)"] += 1
        if pbits:
            st["census prefixes moved"] += 1
        det = detail_of(ev, skip_type=(tag == "EVEN"))
        if kind == K["migration"]:
            place, ship = split_ship(place)
            if ship:
                det = _join("ship " + ship, det)
                st["ships split from port"] += 1
        det = _join(det, *pbits)
        note = collapse_notes([detag(n.full_value()) for n in ev.kids("NOTE")])
        if also:
            note = (note + "\nalso recorded at: " + "; ".join(also)).strip()
        cits = citations(ev, srcmap, reg)
        if not (date or place or det or note or cits):
            return None, None
        spouse = spouse_of or ""
        if kind == K["marriage"] and _base(label) == "Married" and not spouse_of:
            spouse, det = _take_spouse(det)
        if spouse and not label.endswith(" — " + spouse):
            label += " — " + spouse
        if kind == K["identifier"]:
            st["identifier facts"] += 1
        return ([label, date, place, det, note, cits, kind, sort_key(date)],
                {"src": "fam" if spouse_of is not None else "indi",
                 "spouse": spouse})

    for pid, per in people.items():
        rec = per.rec if hasattr(per, "rec") else per
        names, nd = _names(rec, display.get(pid))
        st["name rows deduped"] += nd
        facts, meta, extra = [], [], []

        # --- facts ---------------------------------------------------------
        for ev in rec.children:
            if ev.tag in SKIP or ev.level != 1:
                continue
            f, m = fact(ev.tag, ev)
            if f:
                facts.append(f)
                meta.append(m)
        # Reference numbers are identifiers, not notes: the page collapses them.
        for n in rec.kids("REFN"):
            v = (n.full_value() or "").strip()
            if v:
                facts.append(["Reference number", "", "", v, "", [],
                              K["identifier"], 0])
                meta.append({"src": "indi", "spouse": ""})
                st["identifier facts"] += 1

        # --- marriages, divorces: they live on the FAM record --------------
        for fs in rec.kids("FAMS"):
            f = fam.get(fs.value)
            if f is None:
                continue
            other = [c.value for c in list(f.kids("HUSB")) + list(f.kids("WIFE"))
                     if c.value != pid]
            who = ""
            if other:
                who = display.get(other[0], "")
                p2 = people.get(other[0])
                if not who and p2 is not None:
                    r2 = p2.rec if hasattr(p2, "rec") else p2
                    nm = r2.kid("NAME")
                    who = _WS.sub(" ", (nm.full_value() if nm else "").replace("/", " ")).strip()
            for ev in f.children:
                if ev.tag not in ("MARR", "DIV", "ENGA", "MARB", "MARC", "MARL",
                                  "ANUL", "EVEN", "CENS", "RESI"):
                    continue
                fct, m = fact(ev.tag, ev, spouse_of=who)
                if fct:
                    facts.append(fct)
                    meta.append(m)

        facts, nm_ = merge_marriages(facts, meta)
        st["marriages merged"] += nm_

        # --- record-level notes --------------------------------------------
        for n in rec.kids("NOTE"):
            v = detag(n.full_value())
            if v:
                extra.append(["Note", v])

        # Merged trees restate the same fact. An exact repeat is dropped; so
        # is a barer copy of one already held — same event, same day, same
        # place, but missing the age or cause the other carries. What is kept
        # is a genuine disagreement (two different places for one wedding),
        # because that is a thing worth seeing rather than hiding.
        # Cleaning places makes more copies meet here ("Same House - 3927 N
        # Seventh St" and "3927 N Seventh St"), so what the barer copy carries
        # that the kept one lacks — a different age, a note — is folded in
        # rather than dropped.
        best, order = {}, []
        for f in facts:
            k = (f[0], f[1], f[2])
            weight = len(f[3]) + len(f[4]) + len(f[5]) * 100
            if k not in best:
                best[k] = (weight, f)
                order.append(k)
                continue
            keep, other = (f, best[k][1]) if weight > best[k][0] else (best[k][1], f)
            keep[3] = _join(keep[3], other[3])
            for ln in (other[4] or "").split("\n"):
                if ln and ln not in keep[4]:
                    keep[4] = (keep[4] + "\n" + ln).strip()
            for c in other[5]:
                if c not in keep[5]:
                    keep[5].append(c)
            best[k] = (max(weight, best[k][0]), keep)
            st["repeated facts folded"] = st.get("repeated facts folded", 0) + 1
        facts = [best[k][1] for k in order]

        person_cits, cs = tidy_citations(citations(rec, srcmap, reg), reg)
        st["citation exact repeats dropped"] += cs["dupes"]
        st["boilerplate citations folded"] += cs["folded"]
        row = {}
        if names:
            row["n"] = names
        if facts:
            row["f"] = facts
        if person_cits:
            row["c"] = person_cits
        if extra:
            row["x"] = extra
        if row:
            out[pid] = row

    return {"srcTitles": reg.titles, "srcTypes": reg.types, "kinds": KINDS,
            "citeClasses": E.CLASS_NAMES, "schema": SCHEMA, "people": out}
