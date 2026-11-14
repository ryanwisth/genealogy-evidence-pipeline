"""
contract.py — the two definitions every view payload and the page must share.

docs/payload-stamp.md ("Data contract v2") fixes both exactly. They live here once so the
build tools cannot drift apart the way the page and build_search.py did
(audit-functional F10: ø and æ split words because only one side folded them).

    stamp_of(ids)   which person order an index-keyed payload was built for
    fold(text)      the text fold for name search, record search and grouping

The JavaScript side (view-templates/template3d) must implement the same fold
character for character; tools/check_payloads.py tests the Python half.
"""

import hashlib
import re
import unicodedata


def stamp_of(ids):
    """sha1 of the person ids in order, first 16 hex digits.

    Every payload that addresses people by array index carries this. Two
    payloads with different stamps were built for different person orders and
    must never be folded into one page.
    """
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()[:16]


def tree_stamp(tree):
    """The stamp of a loaded tree-data.json: its own if it carries one, else
    computed from its ids with the same formula (builds before contract v2)."""
    s = tree.get("stamp")
    if isinstance(s, str) and s:
        return s
    ids = tree.get("ids")
    return stamp_of(ids) if isinstance(ids, list) and ids else None


# Letters NFKD does not decompose. Without this map "Ravnsøy" is indexed as
# "ravns" + "y" and "Bjørn" as "bj" + "rn". Order is the PLAN's, exactly.
_TRANSLIT = str.maketrans({
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "þ": "th", "Þ": "Th",
    "ı": "i",
})
_MARKS = re.compile("[̀-ͯ]")


def fold(text):
    """Map the letters above, NFKD, drop combining marks U+0300–U+036F,
    lower-case. Ravnsøy, bodo and RAVNSØY all give "ravnsoy"."""
    s = str(text or "").translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = _MARKS.sub("", s)
    return s.lower()


WORD = re.compile(r"[a-z0-9']+")


def words(text):
    """The search tokens of a string: folded, then runs of a-z, 0-9 and '."""
    return WORD.findall(fold(text))


if __name__ == "__main__":
    # the PLAN's own examples, as a self-test
    for raw, want in (("Ravnsøy", "ravnsoy"), ("ravnsoy", "ravnsoy"),
                      ("RAVNSØY", "ravnsoy"), ("Hansdatter", "hansdatter"),
                      ("Gårdbruker", "gardbruker"), ("Preußen", "preussen"),
                      ("Łódź", "lodz"), ("Þórr", "thorr"), ("Ærø", "aero")):
        got = fold(raw)
        assert got == want, (raw, got, want)
    assert words("Dalsvik, Ravnsøy, Norge") == ["dalsvik", "ravnsoy", "norge"]
    assert stamp_of(["@I1@", "@I2@"]) == hashlib.sha1(b"@I1@\n@I2@").hexdigest()[:16]
    print("contract.py self-test passed")
