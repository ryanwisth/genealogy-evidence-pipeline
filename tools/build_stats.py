#!/usr/bin/env python3
"""
build_stats.py — the numbers behind a family tree, computed once for the viewer.

Called by build_tree_view.py. Emits a `stats` block of chart specifications that
the page renders generically, so adding a chart here needs no new drawing code.

Two rules run through all of it:

  * **A band with too few people is not plotted.** Every series carries its own
    sample count and thin bands are dropped rather than drawn as noise.
  * **Censored cohorts are excluded, not shown.** Lifespan can only be measured
    once a cohort has largely died; including recent ones makes lifespan appear
    to collapse, because the only people with a recorded death are those who
    died young. Anything measured from death dates stops at `latest - 100`.

Chart shapes the page knows how to draw:
    line   — one or more series over the same x, points joined
    stack  — series stacked into bands, for composition over time
    bars   — horizontal bars, biggest first
    hist   — a distribution over fixed buckets
"""

import os
import re
import sys
import statistics
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contract import fold as _fold        # the shared fold (PLAN §4)

MIN_BAND = 40          # below this a band is noise, not a measurement
BAND = 50              # years per cohort


def band_of(y):
    return (int(y) // BAND) * BAND


def median_series(buckets, xs, minimum=MIN_BAND):
    """Median per band, aligned to xs, with None where the band is too thin."""
    out = []
    for x in xs:
        v = buckets.get(x, [])
        out.append(round(statistics.median(v), 1) if len(v) >= minimum else None)
    return out


def count_series(buckets, xs):
    return [len(buckets.get(x, [])) for x in xs]


# ---- captions are written FROM the series ----------------------------------
# Every number a chart's note quotes is read off the series that chart draws
# (design rule). Three notes typed by hand drifted from their own charts when
# the tree was rebuilt ("one in six" over a 22.9 % line,
# "forty ... twenty-nine" over 38 → 30, "thirty-five ... twenty-eight" over
# 34 → 29). These helpers keep the sentences short and the numbers honest.
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _n(v):
    """A median or a count as printed: 34, 38.5, 1,204."""
    if v is None:
        return "?"
    v = round(float(v), 1)
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def _pct(v):
    return f"{round(float(v)):.0f}%"


def _decade(x):
    return f"{int(x)}s"


def _pairs(xs, ys):
    """(band, value) for the bands that have a value."""
    return [(int(x), y) for x, y in zip(xs, ys) if y is not None]


def _at(xs, ys, band):
    for x, y in _pairs(xs, ys):
        if x == band:
            return y
    return None


def _ref_bands(xs, early=1800, late=1900):
    """The two bands a "then and now" sentence compares: the 1800s and the
    1900s when the chart has them, else its first and last."""
    have = [int(x) for x in xs]
    if early in have and late in have:
        return early, late
    return (have[0], have[-1]) if have else (None, None)


def _peak(xs, ys, low=False):
    p = _pairs(xs, ys)
    if not p:
        return None, None
    return (min if low else max)(p, key=lambda t: t[1])


def _list(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


CAPTIONS = {}


def caption(cid):
    def reg(fn):
        CAPTIONS[cid] = fn
        return fn
    return reg


def _series(c, split, name):
    return c["splits"].get(split, {}).get(name) or [None] * len(c["x"])


@caption("population")
def _cap_population(c, X):
    reg = c["splits"]["Region"]
    xs = c["x"]
    peaks = []
    for r, ys in reg.items():
        if r == "unrecorded" or sum(ys) < 0.02 * X["people"]:
            continue
        b, _ = _peak(xs, ys)
        peaks.append((b, -sum(ys), r))
    peaks.sort()
    s = ("People per 50 years, stacked by where they were born. Each region "
         "peaks in turn: " + _list(f"{r} in the {_decade(b)}" for b, _, r in peaks)
         + ".")
    us = reg.get("United States")
    if us:
        for k, x in enumerate(xs):
            known = sum(ys[k] for r, ys in reg.items() if r != "unrecorded")
            if known and us[k] / known > 0.5:
                s += (f" From the {_decade(x)} the United States is "
                      f"{_pct(100 * us[k] / known)} of recorded births.")
                break
    s += (f" The grey band is the {X['unrecorded']:,} people whose birthplace "
          "the tree never recorded.")
    return s


@caption("lifespan")
def _cap_lifespan(c, X):
    xs = c["x"]
    men, women = _series(c, "Sex", "Men"), _series(c, "Sex", "Women")
    both = [(int(x), m, w) for x, m, w in zip(xs, men, women)
            if m is not None and w is not None]
    s = "Median age at death, by 50 years of birth."
    cross = None
    for k in range(len(both)):
        if both[k][2] > both[k][1] and all(w >= m for _, m, w in both[k:]):
            cross = k
            break
    if cross:
        before = both[:cross]
        lt = sum(1 for _, m, w in before if w < m)
        eq = sum(1 for _, m, w in before if w == m)
        if lt:
            s += (f" Switch to Sex for the finding: women died younger than men "
                  f"in {lt} of the {len(before)} bands before the "
                  f"{_decade(both[cross][0])}"
                  + (f" (level in {eq})" if eq else "")
                  + f", and outlive them from the {_decade(both[cross][0])} on "
                  "— the cost of childbirth, then its retreat.")
    s += (f" Cohorts born after {X['lifeCut']} are excluded, because when most "
          "of a cohort is still living the only recorded deaths are the early "
          "ones, and lifespan appears to collapse.")
    return s


@caption("familysize")
def _cap_familysize(c, X):
    p = _pairs(c["x"], _series(c, "All", "Every couple"))
    s = "Median, by the 50 years in which the couple married."
    if p:
        s += (f" {_n(p[0][1])} children in the {_decade(p[0][0])}, "
              f"{_n(p[-1][1])} in the {_decade(p[-1][0])}.")
    best = None
    for r, ys in c["splits"].get("Region", {}).items():
        b, v = _peak(c["x"], ys)
        if v is not None and (best is None or v > best[2]):
            best = (r, b, v)
    if best:
        s += f" {best[0]} ran largest: {_n(best[2])} in the {_decade(best[1])}."
    s += (" Read the last band carefully: living children are often left out "
          "of a tree, so some of that final drop is the record, not the family.")
    return s


@caption("marriageage")
def _cap_marriageage(c, X):
    xs = c["x"]
    men, women = _series(c, "Sex", "Men"), _series(c, "Sex", "Women")
    pm = _pairs(xs, men)
    s = "Median age on the wedding day."
    if pm:
        s += (f" Men: between {_n(min(v for _, v in pm))} and "
              f"{_n(max(v for _, v in pm))} in every band from the "
              f"{_decade(pm[0][0])} to the {_decade(pm[-1][0])}.")
    gaps = [(int(x), m - w) for x, m, w in zip(xs, men, women)
            if m is not None and w is not None]
    if gaps and all(g > 0 for _, g in gaps):
        lo = min(gaps, key=lambda t: t[1])
        s += (f" Women married younger in every band, by "
              f"{_n(min(g for _, g in gaps))} to {_n(max(g for _, g in gaps))} "
              f"years; the gap is narrowest in the {_decade(lo[0])}.")
    elif gaps:
        s += (" Women married younger in "
              f"{sum(1 for _, g in gaps if g > 0)} of {len(gaps)} bands.")
    return s


def _months(ys):
    k_hi = max(range(12), key=lambda k: ys[k])
    k_lo = min(range(12), key=lambda k: ys[k])
    return k_hi, k_lo


@caption("birthmonth")
def _cap_birthmonth(c, X):
    ys = _series(c, "All", "Births")
    hi, lo = _months(ys)
    s = (f"{sum(ys):,} people whose birth month is recorded. {MONTHS[hi]} is "
         f"the peak and {MONTHS[lo]} the trough, which puts conceptions most "
         f"often in {MONTHS[(hi - 9) % 12]} and least often in "
         f"{MONTHS[(lo - 9) % 12]}.")
    if 5 <= (lo - 9) % 12 <= 9:        # fewest conceived June to October
        s += (" Fewest children conceived at harvest is the ordinary rhythm "
              "of a farming population, still legible four centuries on.")
    return s


@caption("generation")
def _cap_generation(c, X):
    xs, ys = c["x"], _series(c, "All", "Parent to child")
    s = (f"Median years between a parent's birth and a child's, on "
         f"{X['genPairs']:,} parent-child pairs.")
    hb, hv = _peak(xs, ys)
    later = [(x, y) for x, y in _pairs(xs, ys) if hb is not None and x > hb]
    if later:
        lb, lv = min(later, key=lambda t: t[1])
        s += (f" {_n(hv)} years at the longest, in the {_decade(hb)}; "
              f"{_n(lv)} by the {_decade(lb)}.")
        if lv < hv:
            s += (" A shorter generation is mostly people marrying earlier and "
                  "stopping earlier, and it is why a tree reaches further back "
                  "in years than the number of generations suggests.")
    return s


@caption("motherhood")
def _cap_motherhood(c, X):
    xs = c["x"]
    first, last = _series(c, "All", "At her first"), _series(c, "All", "At her last")
    a, b = _ref_bands(xs)
    s = "Median age."
    pf = [(x, y) for x, y in _pairs(xs, first) if b is None or x <= b]
    if pf:
        s += (f" The first child barely moves: {_n(min(y for _, y in pf))} to "
              f"{_n(max(y for _, y in pf))} from the {_decade(pf[0][0])} to "
              f"the {_decade(pf[-1][0])}.")
    la, lb = _at(xs, last, a), _at(xs, last, b)
    fa, fb = _at(xs, first, a), _at(xs, first, b)
    if None not in (la, lb, fa, fb):
        s += (f" The last child is where the change is: {_n(la)} in the "
              f"{_decade(a)}, {_n(lb)} by the {_decade(b)}. The span between "
              f"them is a woman's childbearing life, and it goes from "
              f"{_n(la - fa)} years to {_n(lb - fb)}.")
        if lb - fb < la - fa:
            s += " That is the fertility transition, in one family."
    return s


@caption("spacing")
def _cap_spacing(c, X):
    p = _pairs(c["x"], _series(c, "All", "Gap"))
    s = f"Median gap, on {X['spacingPairs']:,} consecutive pairs."
    if p:
        lo, hi = min(v for _, v in p), max(v for _, v in p)
        span = f"from the {_decade(p[0][0])} to the {_decade(p[-1][0])}"
        if hi - lo <= 0.5:
            s += (f" It does not move: {_n(lo)} years in every band, {span}. "
                  "Family size fell by having children for fewer years, not by "
                  "spacing them further apart, which is what the mother's-"
                  "last-child chart shows from the other side.")
        else:
            s += f" Between {_n(lo)} and {_n(hi)} years, {span}."
    return s


@caption("agegap")
def _cap_agegap(c, X):
    p = _pairs(c["x"], _series(c, "All", "Husband older by"))
    s = "Median years, by the year of the wedding."
    if p:
        s += f" {_n(p[0][1])} in the {_decade(p[0][0])}"
        late = [v for x, v in p if x >= 1800]
        if late:
            lo, hi = min(late), max(late)
            s += (f"; {_n(lo)} from the 1800s on" if lo == hi
                  else f"; {_n(lo)} to {_n(hi)} from the 1800s on")
        s += "."
        neg = [x for x, v in p if v < 0]
        s += (" A negative band would mean wives were older on average; none is."
              if not neg else
              f" In the {_list(_decade(x) for x in neg)} wives were older on average.")
    return s


@caption("widowhood")
def _cap_widowhood(c, X):
    xs = c["x"]
    f, m = _series(c, "All", "Widows"), _series(c, "All", "Widowers")
    both = [(a, b) for a, b in zip(f, m) if a is not None and b is not None]
    k = sum(1 for a, b in both if a > b)
    s = ("Median years a surviving spouse outlived the other, counting only "
         "couples where both death dates are known.")
    if both:
        s += (f" Widows outlast widowers in "
              + ("every band" if k == len(both) else f"{k} of {len(both)} bands"))
        s += (" — they married younger and lived longer, so they were left "
              "alone for longer." if k > len(both) / 2 else ".")
    return s


@caption("marrmonth")
def _cap_marrmonth(c, X):
    ys = _series(c, "All", "Weddings")
    hi, lo = _months(ys)
    s = (f"{sum(ys):,} weddings with a full date. {MONTHS[hi]} is the peak and "
         f"{MONTHS[lo]} the trough, {ys[hi] / max(1, ys[lo]):.1f} times as many.")
    if hi in (9, 10, 11) and lo in (2, 3):
        s += (" That is the farming year and the church calendar together: "
              "married after the harvest was in, not during Lent.")
    return s


@caption("deathmonth")
def _cap_deathmonth(c, X):
    ys = _series(c, "All", "Deaths")
    hi, lo = _months(ys)
    s = (f"{sum(ys):,} people whose death month is recorded. {MONTHS[hi]} takes "
         f"{_pct(100 * (ys[hi] - ys[lo]) / max(1, ys[lo]))} more than "
         f"{MONTHS[lo]}.")
    if hi in (11, 0, 1, 2) and lo in (5, 6, 7, 8):
        s += (" Winter mortality is one of the most reliable patterns in any "
              "pre-modern population, and it is plainly here.")
    return s


@caption("moved")
def _cap_moved(c, X):
    xs = c["x"]
    far = _series(c, "All", "Over 1,000 km")
    s = (f"Share of a birth cohort who died more than 100 km, and more than "
         f"1,000 km, from where they were born, on {X['movedPeople']:,} people "
         "with both places mapped.")
    hb, hv = _peak(xs, far)
    if hb is not None:
        s += (f" Over 1,000 km peaks at {_pct(hv)} for those born in the "
              f"{_decade(hb)}. Read this as which migration was under way "
              "rather than as whether people were becoming more mobile.")
    if X.get("movedMedian"):
        lo, hi = X["movedMedian"]
        s += (f" A median distance was tried first and was useless: it swung "
              f"between {lo:,} km and {hi:,} km band to band, because the "
              "population changes rather than the behaviour.")
    return s


@caption("namefashion")
def _cap_namefashion(c, X):
    xs = c["x"]
    girls = _series(c, "Sex", "Girls")
    s = ("Share of a birth cohort given one of the ten commonest first names "
         "of that cohort.")
    hb, hv = _peak(xs, girls)
    if hb is not None:
        s += f" In the {_decade(hb)}, {_pct(hv)} of girls got one of ten names"
        top = X["girlTop"].get(hb)
        s += (f", and {top[0]} alone, with its spellings in other languages, "
              f"was {_pct(top[1])}." if top else ".")
        last = _pairs(xs, girls)[-1]
        if last[0] != hb:
            s += f" By the {_decade(last[0])} it is {_pct(last[1])}."
    eras = [(b, X["boyTop"][b]) for b in X["boyEras"] if X["boyTop"].get(b)]
    if len(eras) >= 2:
        s += (" The boys' names trace the migration: "
              + "; then ".join(f"{_list(ns)} in the {_decade(b)}" for b, ns in eras)
              + ".")
    return s


@caption("middlenames")
def _cap_middlenames(c, X):
    p = _pairs(c["x"], _series(c, "All", "Two or more"))
    s = "Share of a birth cohort given two or more given names"
    if p:
        s += (f": {_pct(p[0][1])} in the {_decade(p[0][0])}, "
              f"{_pct(p[-1][1])} by the {_decade(p[-1][0])}")
    return s + (". It rises alongside the fall in name-sharing above, and for "
                "the same reason: families needed a way to tell apart the "
                "fourth Johann.")


@caption("namedafter")
def _cap_namedafter(c, X):
    xs, ys = c["x"], _series(c, "All", "Named for him")
    s = (f"On {X['eldest']:,} eldest sons whose paternal grandfather is also "
         "in the tree.")
    hb, hv = _peak(xs, ys)
    if hb is not None:
        s += f" The custom is strongest in the {_decade(hb)}, at {_pct(hv)}"
        last = _pairs(xs, ys)[-1]
        s += (f", and {_pct(last[1])} by the {_decade(last[0])}."
              if last[0] != hb else ".")
    s += (" Spelling is folded across languages first (Johann, Johannes, Hans, "
          "Jean and John count as one name), without which this measures "
          f"{_pct(X['eldestRawPct'])} instead of {_pct(X['eldestPct'])} overall. "
          "Treat it as a floor: a custom this loose leaves cases no matching "
          "rule will catch.")
    return s


@caption("marrydistance")
def _cap_marrydistance(c, X):
    p = _pairs(c["x"], _series(c, "All", "Over 50 km apart"))
    s = "Share of weddings where the two were born more than 50 km apart."
    if len(p) >= 3:
        lo = min(p[1:-1], key=lambda t: t[1])
        s += (f" {_pct(p[0][1])} in the {_decade(p[0][0])}, {_pct(lo[1])} at "
              f"the lowest ({_decade(lo[0])}), {_pct(p[-1][1])} in the "
              f"{_decade(p[-1][0])}.")
        if lo[1] < p[0][1] and lo[1] < p[-1][1]:
            s += (" The U is not one trend: the early high is immigrants "
                  "marrying other immigrants, the dip is settled communities "
                  "marrying their neighbours, and the late rise is ordinary "
                  "modern mobility.")
    return s + " Only weddings where both birthplaces could be mapped are counted."


@caption("dateprecision")
def _cap_dateprecision(c, X):
    p = _pairs(c["x"], _series(c, "All", "Full date"))
    s = "Share of recorded births carrying a day and a month, not just a year"
    if p:
        s += (f": {_pct(p[0][1])} in the {_decade(p[0][0])}, "
              f"{_pct(p[-1][1])} by the {_decade(p[-1][0])}")
    s += (". Read it beside the completeness chart: that one says whether a "
          "fact exists at all, this one says how sharp it is.")
    if X.get("heapN"):
        s += (f" Separately, {_pct(X['heapPct'])} of {X['heapN']:,} ages at "
              "death end in 0 or 5, against 20% by chance"
              + (", so these ages were copied from documents rather than "
                 "remembered." if X["heapPct"] < 25 else "."))
    return s


@caption("pedigree")
def _cap_pedigree(c, X):
    found, must = _series(c, "All", "Found"), _series(c, "All", "Must exist")
    s = ("Ancestors of the person at the centre, per generation back, against "
         "the number that must exist.")
    k = 0
    while k < len(found) and found[k] is not None and found[k] >= must[k]:
        k += 1
    if found:
        g = min(8, len(found))
        s += (f" The two match for {k} generation{'s' if k != 1 else ''}; by "
              f"generation {g}, {found[g - 1]:,} of {must[g - 1]:,} are known, and "
              "the tree is following a few well-documented lines rather than "
              "the whole fan.")
    over = [str(i + 1) for i in range(len(found))
            if found[i] is not None and found[i] > must[i]]
    if over and X.get("over"):
        s += (f" Generation{'s' if len(over) > 1 else ''} {_list(over)} "
              f"overshoot{'' if len(over) > 1 else 's'} what is arithmetically "
              f"possible because {X['over']} people in this line carry more "
              "than two parents: a contradiction inherited from the source "
              "tree, not a finding.")
    return s


def write_captions(charts, X):
    for c in charts:
        fn = CAPTIONS.get(c["id"])
        if fn:
            c["note"] = fn(c, X)


# ---- findings: a card's title is what the chart shows (audit-ux P10) -------
# The page used to title a card with its measure ("How long they lived") and
# leave the finding for the last, faintest line of the note. Now the finding is
# the title and the measure is the subtitle. Like the captions, every number in
# a finding is read off the series the card draws; a chart with no finding
# function (or one whose data does not support the sentence) keeps its measure
# as the title. The page groups the cards into chapters, in this order.
CHAPTERS = [["happened", "Things that happened"], ["life", "Life & death"],
            ["marriage", "Marriage"], ["children", "Children"],
            ["moving", "Moving"], ["names", "Names"],
            ["quality", "Record quality"]]
CHAPTER_OF = {
    "lifespan": "life", "childmortality": "life", "agedist": "life",
    "deathmonth": "life",
    "marriageage": "marriage", "agegap": "marriage", "marrmonth": "marriage",
    "marrydistance": "marriage", "widowhood": "marriage",
    "familysize": "children", "motherhood": "children", "spacing": "children",
    "generation": "children", "birthmonth": "children",
    "population": "moving", "flows": "moving", "moved": "moving",
    "namefashion": "names", "middlenames": "names", "namedafter": "names",
    "coverage": "quality", "dateprecision": "quality", "pedigree": "quality",
}
FINDINGS = {}


def finding(cid):
    def reg(fn):
        FINDINGS[cid] = fn
        return fn
    return reg


def _up_down(a, b):
    return "fell" if b < a else "rose"


@finding("population")
def _f_population(c, X):
    reg, xs = c["splits"]["Region"], c["x"]
    peaks = sorted((_peak(xs, ys)[0], r) for r, ys in reg.items()
                   if r != "unrecorded" and sum(ys) >= 0.02 * X["people"])
    if not peaks:
        return None
    s = f"{peaks[0][1]} came first ({_decade(peaks[0][0])})"
    us = reg.get("United States")
    if us:
        for k, x in enumerate(xs):
            known = sum(ys[k] for r, ys in reg.items() if r != "unrecorded")
            if known and us[k] / known > 0.5:
                return (s + f"; from the {_decade(x)} the United States is "
                        f"{_pct(100 * us[k] / known)} of recorded births")
    return s + f", {peaks[-1][1]} last ({_decade(peaks[-1][0])})"


@finding("lifespan")
def _f_lifespan(c, X):
    xs = c["x"]
    men, women = _series(c, "Sex", "Men"), _series(c, "Sex", "Women")
    both = [(int(x), m, w) for x, m, w in zip(xs, men, women)
            if m is not None and w is not None]
    for k in range(1, len(both)):
        if both[k][2] > both[k][1] and all(w >= m for _, m, w in both[k:]):
            before = both[:k]
            if all(w <= m for _, m, w in before) and any(w < m for _, m, w in before):
                eq = [x for x, m, w in before if w == m]
                return (f"Women died younger than men until the "
                        f"{_decade(both[k][0])}"
                        + (f" (level in the {_list(_decade(x) for x in eq)})"
                           if eq else "")
                        + ", then outlived them")
            break
    p = _pairs(xs, _series(c, "All", "Everyone"))
    if len(p) >= 2:
        return (f"Median lifespan {_up_down(p[0][1], p[-1][1])} from "
                f"{_n(p[0][1])} to {_n(p[-1][1])} years")
    return None


@finding("familysize")
def _f_familysize(c, X):
    p = _pairs(c["x"], _series(c, "All", "Every couple"))
    if len(p) < 2:
        return None
    return (f"From {_n(p[0][1])} children per couple in the {_decade(p[0][0])} "
            f"to {_n(p[-1][1])} in the {_decade(p[-1][0])}")


@finding("marriageage")
def _f_marriageage(c, X):
    xs = c["x"]
    men, women = _series(c, "Sex", "Men"), _series(c, "Sex", "Women")
    gaps = [m - w for m, w in zip(men, women) if m is not None and w is not None]
    if not gaps:
        return None
    if all(g > 0 for g in gaps):
        return (f"Women married {_n(min(gaps))} to {_n(max(gaps))} years younger "
                "than men, in every band")
    return (f"Women married younger than men in {sum(1 for g in gaps if g > 0)} "
            f"of {len(gaps)} bands")


@finding("childmortality")
def _f_childmortality(c, X):
    xs, ys = c["x"], _series(c, "All", "Everyone")
    hb, hv = _peak(xs, ys)
    if hb is None:
        return None
    s = f"At least {_pct(hv)} died before eighteen in the {_decade(hb)}"
    last = _pairs(xs, ys)[-1]
    if last[0] != hb:
        s += f"; {_pct(last[1])} by the {_decade(last[0])}"
    return s


@finding("agedist")
def _f_agedist(c, X):
    xs, ys = c["x"], _series(c, "All", "Everyone")
    if not ys or not any(ys):
        return None
    top = max(range(len(ys)), key=lambda k: ys[k])
    step = (int(xs[1]) - int(xs[0])) if len(xs) > 1 else 5
    s = f"Deaths peak at {xs[top]}–{int(xs[top]) + step - 1}"
    # the other local peak, when there is one (children, in any population
    # before modern medicine)
    loc = [k for k in range(len(ys)) if k != top and abs(k - top) > 1
           and (k == 0 or ys[k] > ys[k - 1]) and (k == len(ys) - 1 or ys[k] > ys[k + 1])]
    if loc:
        k = max(loc, key=lambda j: ys[j])
        where = (f"under {int(xs[k]) + step}" if k == 0
                 else f"at {xs[k]}–{int(xs[k]) + step - 1}")
        s += f", with a second peak {where} ({ys[k]:,} people)"
    return s


def _f_month(c, name, what, verb):
    ys = _series(c, "All", name)
    if not ys or not any(ys):
        return None
    hi, lo = _months(ys)
    return f"{MONTHS[hi]} {verb} the most {what}, {MONTHS[lo]} the fewest"


@finding("birthmonth")
def _f_birthmonth(c, X):
    return _f_month(c, "Births", "births", "has")


@finding("marrmonth")
def _f_marrmonth(c, X):
    ys = _series(c, "All", "Weddings")
    if not ys or not any(ys):
        return None
    hi, lo = _months(ys)
    return (f"{MONTHS[hi]} had {ys[hi] / max(1, ys[lo]):.1f} times the weddings "
            f"of {MONTHS[lo]}")


@finding("deathmonth")
def _f_deathmonth(c, X):
    ys = _series(c, "All", "Deaths")
    if not ys or not any(ys):
        return None
    hi, lo = _months(ys)
    return (f"{MONTHS[hi]} took {_pct(100 * (ys[hi] - ys[lo]) / max(1, ys[lo]))} "
            f"more lives than {MONTHS[lo]}")


@finding("flows")
def _f_flows(c, X):
    ys = _series(c, "All", "Moved")
    if not c["x"] or not ys or ys[0] is None:
        return None
    return f"The biggest move: {ys[0]:,} people, {c['x'][0]}"


@finding("generation")
def _f_generation(c, X):
    xs, ys = c["x"], _series(c, "All", "Parent to child")
    hb, hv = _peak(xs, ys)
    later = [(x, y) for x, y in _pairs(xs, ys) if hb is not None and x > hb]
    if not later:
        return None
    lb, lv = min(later, key=lambda t: t[1])
    return (f"A generation took {_n(hv)} years in the {_decade(hb)}, "
            f"{_n(lv)} by the {_decade(lb)}")


@finding("motherhood")
def _f_motherhood(c, X):
    xs = c["x"]
    first, last = _series(c, "All", "At her first"), _series(c, "All", "At her last")
    a, b = _ref_bands(xs)
    la, lb, fa, fb = _at(xs, last, a), _at(xs, last, b), _at(xs, first, a), _at(xs, first, b)
    if None in (la, lb, fa, fb):
        return None
    word = "shrank" if lb - fb < la - fa else "grew"
    return (f"A mother's childbearing years {word} from {_n(la - fa)} to "
            f"{_n(lb - fb)}, {_decade(a)} to {_decade(b)}")


@finding("spacing")
def _f_spacing(c, X):
    p = _pairs(c["x"], _series(c, "All", "Gap"))
    if not p:
        return None
    lo, hi = min(v for _, v in p), max(v for _, v in p)
    if hi - lo <= 0.5:
        return (f"{_n(lo)} years between one child and the next, in every band "
                f"for {p[-1][0] - p[0][0]} years")
    return f"Between {_n(lo)} and {_n(hi)} years from one child to the next"


@finding("agegap")
def _f_agegap(c, X):
    p = _pairs(c["x"], _series(c, "All", "Husband older by"))
    if not p:
        return None
    late = [v for x, v in p if x >= 1800 and x > p[0][0]]
    if not late:
        return f"Husbands were {_n(p[0][1])} years older in the {_decade(p[0][0])}"
    lo, hi = min(late), max(late)
    return (f"Husbands were {_n(p[0][1])} years older in the {_decade(p[0][0])}, "
            + (f"{_n(lo)}" if lo == hi else f"{_n(lo)} to {_n(hi)}")
            + " from the 1800s on")


@finding("widowhood")
def _f_widowhood(c, X):
    f, m = _series(c, "All", "Widows"), _series(c, "All", "Widowers")
    both = [(a, b) for a, b in zip(f, m) if a is not None and b is not None]
    if not both:
        return None
    k = sum(1 for a, b in both if a > b)
    return ("Widows spent longer alone than widowers, in "
            + ("every band" if k == len(both) else f"{k} of {len(both)} bands"))


@finding("moved")
def _f_moved(c, X):
    hb, hv = _peak(c["x"], _series(c, "All", "Over 1,000 km"))
    if hb is None:
        return None
    return (f"{_pct(hv)} of those born in the {_decade(hb)} died over "
            "1,000 km from their birthplace")


@finding("namefashion")
def _f_namefashion(c, X):
    xs, girls = c["x"], _series(c, "Sex", "Girls")
    hb, hv = _peak(xs, girls)
    if hb is None:
        return None
    s = f"In the {_decade(hb)}, {_pct(hv)} of girls got one of ten names"
    last = _pairs(xs, girls)[-1]
    return s + (f"; by the {_decade(last[0])}, {_pct(last[1])}" if last[0] != hb else "")


@finding("middlenames")
def _f_middlenames(c, X):
    p = _pairs(c["x"], _series(c, "All", "Two or more"))
    if len(p) < 2:
        return None
    return (f"Two or more given names: {_pct(p[0][1])} in the {_decade(p[0][0])}, "
            f"{_pct(p[-1][1])} by the {_decade(p[-1][0])}")


@finding("namedafter")
def _f_namedafter(c, X):
    hb, hv = _peak(c["x"], _series(c, "All", "Named for him"))
    if hb is None:
        return None
    return (f"Naming the eldest son for his grandfather peaked at {_pct(hv)}, "
            f"in the {_decade(hb)}")


@finding("marrydistance")
def _f_marrydistance(c, X):
    p = _pairs(c["x"], _series(c, "All", "Over 50 km apart"))
    if len(p) < 3:
        return None
    lo = min(p[1:-1], key=lambda t: t[1])
    if lo[1] < p[0][1] and lo[1] < p[-1][1]:
        return (f"Couples born over 50 km apart: {_pct(p[0][1])} in the "
                f"{_decade(p[0][0])}, {_pct(lo[1])} in the {_decade(lo[0])}, "
                f"{_pct(p[-1][1])} in the {_decade(p[-1][0])}")
    return (f"Couples born over 50 km apart {_up_down(p[0][1], p[-1][1])} from "
            f"{_pct(p[0][1])} to {_pct(p[-1][1])}")


@finding("dateprecision")
def _f_dateprecision(c, X):
    p = _pairs(c["x"], _series(c, "All", "Full date"))
    if len(p) < 2:
        return None
    return (f"Exact birth dates: {_pct(p[0][1])} in the {_decade(p[0][0])}, "
            f"{_pct(p[-1][1])} by the {_decade(p[-1][0])}")


@finding("pedigree")
def _f_pedigree(c, X):
    found, must = _series(c, "All", "Found"), _series(c, "All", "Must exist")
    if not found:
        return None
    k = 0
    while k < len(found) and found[k] is not None and found[k] >= must[k]:
        k += 1
    g = min(8, len(found))
    return (f"Complete for {k} generation{'s' if k != 1 else ''}; by generation "
            f"{g}, {found[g - 1]:,} of {must[g - 1]:,} ancestors are known")


@finding("coverage")
def _f_coverage(c, X):
    xs = c["x"]
    cut = X.get("lifeCut")
    rows = []
    for k, x in enumerate(xs):
        vs = [ys[k] for ys in c["splits"]["All"].values()]
        if cut is not None and int(x) > cut:
            continue                 # the living: "death recorded" falls by design
        if all(v is not None for v in vs):
            rows.append((int(x), sum(vs) / len(vs)))
    if len(rows) < 2:
        return None
    hi, lo = max(rows, key=lambda t: t[1]), min(rows, key=lambda t: t[1])
    return (f"Records are most complete for the {_decade(hi[0])} births "
            f"({_pct(hi[1])}) and thinnest for the {_decade(lo[0])} ({_pct(lo[1])})")


def _tile(c):
    """A chart whose one line does not move is a number, not a chart."""
    if c.get("kind") != "line" or len(c["splits"]) != 1:
        return None
    (ser,) = c["splits"].values()
    if len(ser) != 1:
        return None
    (name, ys), = ser.items()
    p = _pairs(c["x"], ys)
    if len(p) < 4:
        return None
    lo, hi = min(v for _, v in p), max(v for _, v in p)
    if hi - lo > 0.5:
        return None
    return {"value": _n(statistics.median(v for _, v in p)), "unit": c.get("unit", ""),
            "from": p[0][0], "to": p[-1][0]}


# ---- surnames: one row per family name, spellings folded -----------------
# research/audit-data.md §3.4 and genealogy-apps #17. The surname is tree-data's
# `surn` (display_name() in build_tree_view.py), so the page can map every person
# to a row by the exact spelling it already holds. Spellings are grouped by a
# conservative skeleton (German umlauts spelled out, then the shared fold, then
# ae/oe/ue, ck, th, dt, tz, ph, y and ie reduced, doubled letters collapsed, a
# German-reading transliteration for Cyrillic records). A skeleton that is too eager joins different families, so the
# exceptions below keep apart pairs it would wrongly join; each was
# checked by eye against the group list. Extend it for your own tree.
SURN_KEEP_APART = {
    "smith": "Smith (English), not Smit (Dutch)",
}
_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})
_CYR = {"а": "a", "б": "b", "в": "w", "г": "h", "д": "d", "е": "e", "ё": "o", "ж": "sch",
        "з": "s", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "ch", "ц": "z",
        "ч": "tsch", "ш": "sch", "щ": "sch", "ъ": "", "ы": "i", "ь": "", "э": "e",
        "ю": "u", "я": "ja"}
_SUFFIX = re.compile(r"[,\s]+(ii|iii|iv|jr|sr)\.?\s*$", re.I)


def surname_key(s):
    """The grouping key for a display surname ("" when it has no letters)."""
    s = _SUFFIX.sub("", str(s or ""))
    k = _fold(s.translate(_UMLAUT))
    k = "".join(_CYR.get(ch, ch) for ch in k)
    k = re.sub(r"[^a-z]", "", k)
    if k in SURN_KEEP_APART:
        return "=" + k
    for a, b in (("ae", "e"), ("oe", "o"), ("ue", "u"), ("ck", "k"), ("th", "t"),
                 ("dt", "t"), ("tz", "z"), ("ph", "f"), ("y", "i"), ("ie", "i")):
        k = k.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", k)


def _me_from_argv():
    """build_tree_view.py's --me, for callers that do not pass me= (the viewer)."""
    a = sys.argv
    for k, v in enumerate(a):
        if v == "--me" and k + 1 < len(a):
            return a[k + 1]
        if v.startswith("--me="):
            return v[5:]
    return None


def _display_surnames(people, group):
    """tree-data's surn for each person: build_tree_view.display_name()."""
    fn = getattr(sys.modules.get("__main__"), "display_name", None)
    if fn is None:
        try:
            from build_tree_view import display_name as fn
        except Exception:          # run outside the pipeline: the GEDCOM SURN
            fn = None
    if fn is None:
        return {p: (people[p].surn or "").strip() for p in group}
    return {p: fn(people[p].rec)[1] for p in group}


def surname_block(people, fam, group, year, parents, region_of, event_place,
                  me, surn=None):
    """statsView.surnames: every surname carried by two or more people.

    rows[]: s the spelling most of them carry · n people · y [first, last]
    recorded birth year · h births per century (centuries[]) · r top three
    recorded birth regions [[name, n]] · line how many are the viewer's direct
    ancestors · blood how many are blood relatives of the viewer (descended from
    one of their ancestors) · v [[spelling, n]] every spelling folded into the
    row, exactly as tree-data's surn has it (only when more than one). Fields
    that would be empty or 0 are left out.
    """
    surn = surn if surn is not None else _display_surnames(people, group)
    # the viewer's blood line: their ancestors, and everyone descended from one
    kids = defaultdict(set)
    for f in fam.values():
        par = [c.value for c in list(f.kids("HUSB")) + list(f.kids("WIFE"))]
        for ch in f.kids("CHIL"):
            for q in par:
                kids[q].add(ch.value)
    anc = set()
    if me in group:
        todo = [me]
        while todo:
            q = todo.pop()
            for r in parents.get(q, ()):
                if r in group and r not in anc:
                    anc.add(r)
                    todo.append(r)
    blood = set(anc) | ({me} if me in group else set())
    todo = list(blood)
    while todo:
        q = todo.pop()
        for r in kids.get(q, ()):
            if r in group and r not in blood:
                blood.add(r)
                todo.append(r)

    groups = defaultdict(list)
    for p in sorted(group):
        s = (surn.get(p) or "").strip()
        k = surname_key(s) if s else ""
        if k:
            groups[k].append(p)
    by = {p: people[p].by for p in group}
    cent = sorted({(by[p] // 100) * 100 for p in group if by[p]})
    c0 = cent[0] if cent else 1400
    c1 = cent[-1] if cent else 2000
    ncent = (c1 - c0) // 100 + 1
    rows, singles = [], 0
    for k, mem in groups.items():
        if len(mem) < 2:
            singles += 1
            continue
        sp = Counter(surn[p].strip() for p in mem)
        yrs = sorted(by[p] for p in mem if by[p])
        h = [0] * ncent
        for y in yrs:
            h[y // 100 - c0 // 100] += 1
        regs = Counter(region_of(event_place(people[p].rec, "BIRT")) or "" for p in mem)
        regs.pop("", None)
        row = {"s": sp.most_common(1)[0][0],       # the spelling most of them carry
               "n": len(mem), "h": h}
        if yrs:
            row["y"] = [yrs[0], yrs[-1]]              # first and last recorded birth year
        if regs:
            row["r"] = [[r, n] for r, n in regs.most_common(3)]
        line = sum(1 for p in mem if p in anc)
        kin = sum(1 for p in mem if p in blood)
        if line:
            row["line"] = line
        if kin:
            row["blood"] = kin
        if len(sp) > 1:
            row["v"] = [[s, n] for s, n in sp.most_common()]
        rows.append(row)
    rows.sort(key=lambda r: (-r["n"], _fold(r["s"])))
    return {
        "rows": rows,
        "centuries": [c0 + 100 * i for i in range(ncent)],
        "groups": len(groups), "singles": singles,
        "people": sum(len(m) for m in groups.values()),
        "spellings": len({(surn.get(p) or "").strip() for p in group} - {""}),
        "me": me if me in group else None,
        "lineSize": len(anc), "bloodSize": len(blood),
        "keptApart": [[k, why] for k, why in SURN_KEEP_APART.items()],
        "rule": ("Spellings are grouped when they differ only by accents, "
                 "umlauts written out (ö = oe), doubled letters, ck/k, th/t, dt/t, "
                 "tz/z, ph/f, y/i or ie/i, or by the Cyrillic alphabet."),
    }


def write_findings(charts, X):
    for c in charts:
        c["chapter"] = CHAPTER_OF.get(c["id"], "quality")
        fn = FINDINGS.get(c["id"])
        try:
            f = fn(c, X) if fn else None
        except (KeyError, IndexError, ValueError, TypeError, ZeroDivisionError):
            f = None                   # a missing split keeps the measure as title
        if f:
            c["finding"] = f
        t = _tile(c)
        if t:
            c["tile"] = t


def build(people, fam, group, year, idx, parents, root,
          region_of, event_place, parse_date, event_date, places=None,
          me=None, surn=None):
    """Return the stats block.

    `group` is the connected set being shown; `idx` maps a person id to their
    position in the viewer's arrays, so a record row can select that person in
    the 3-D scene rather than being a dead line of text.

    `me` is the viewer (the viewer) for the surnames' "on your line" counts; without
    it, build_tree_view.py's own --me is used, then nobody. `surn` maps a
    person id to tree-data's display surname; without it display_name() is
    called again.
    """
    P = {p: people[p] for p in group}
    latest = max(int(year[p]) for p in group)
    life_cut = latest - 100

    # ---------------------------------------------------------------- gather
    born, lifespan, young, marr_age, fam_size, months = ({} for _ in range(6))
    born = defaultdict(list)
    lifespan_all, lifespan_sex, lifespan_reg = defaultdict(list), defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
    young_all, young_reg = defaultdict(list), defaultdict(lambda: defaultdict(list))
    born_reg = defaultdict(lambda: defaultdict(int))
    month = Counter()
    age_hist_m, age_hist_f = Counter(), Counter()
    regions = Counter()

    for p in group:
        pr = P[p]
        b = band_of(year[p])
        born[b].append(p)
        r = region_of(event_place(pr.rec, "BIRT")) or "unrecorded"
        born_reg[b][r] += 1
        regions[r] += 1
        _, m, _ = parse_date(event_date(pr.rec, "BIRT"))
        if m:
            month[m] += 1
        if pr.by and pr.dy:
            a = pr.dy - pr.by
            if 0 <= a <= 110:
                if b <= life_cut:
                    lifespan_all[b].append(a)
                    if pr.sex in ("M", "F"):
                        lifespan_sex[pr.sex][b].append(a)
                    if r != "unrecorded":
                        lifespan_reg[r][b].append(a)
                    young_all[b].append(1 if a < 18 else 0)
                    if r != "unrecorded":
                        young_reg[r][b].append(1 if a < 18 else 0)
                if pr.sex == "M":
                    age_hist_m[(a // 5) * 5] += 1
                elif pr.sex == "F":
                    age_hist_f[(a // 5) * 5] += 1

    marr_all, marr_sex = defaultdict(list), defaultdict(lambda: defaultdict(list))
    size_all, size_reg = defaultdict(list), defaultdict(lambda: defaultdict(list))
    flows = Counter()
    for f in fam.values():
        m = f.kid("MARR")
        my = None
        if m and m.kid("DATE"):
            mm = re.search(r"\b(\d{3,4})\b", m.kid("DATE").full_value())
            my = int(mm.group(1)) if mm else None
        if my:
            mb = band_of(my)
            kids = len([c for c in f.kids("CHIL") if c.value])
            size_all[mb].append(kids)
            for tag in ("HUSB", "WIFE"):
                for c in f.kids(tag):
                    q = people.get(c.value)
                    if not q or q.id not in group:
                        continue
                    if q.by and 12 <= my - q.by <= 60:
                        marr_all[mb].append(my - q.by)
                        marr_sex["M" if tag == "HUSB" else "F"][mb].append(my - q.by)
                    if tag == "HUSB":
                        rr = region_of(event_place(q.rec, "BIRT"))
                        if rr:
                            size_reg[rr][mb].append(kids)
    for p in group:
        pr = P[p]
        rb = region_of(event_place(pr.rec, "BIRT"))
        rd = region_of(event_place(pr.rec, "DEAT"))
        if rb and rd and rb != rd:
            flows[(rb, rd)] += 1

    # ---------------------------------------------------------------- axes
    xs_birth = [x for x in sorted(born) if len(born[x]) >= MIN_BAND]
    xs_life = [x for x in xs_birth if x <= life_cut and len(lifespan_all.get(x, [])) >= MIN_BAND]
    xs_marr = [x for x in sorted(size_all) if len(size_all[x]) >= MIN_BAND]
    top_regions = [r for r, _ in regions.most_common() if r != "unrecorded"][:6]

    def by_region(src, xs, kind="median"):
        out = {}
        for r in top_regions:
            s = (median_series(src[r], xs) if kind == "median"
                 else [round(100 * statistics.mean(src[r][x]), 1) if len(src[r].get(x, [])) >= MIN_BAND else None for x in xs])
            if sum(1 for v in s if v is not None) >= 3:
                out[r] = s
        return out

    charts = []

    charts.append({
        "id": "population", "kind": "stack",
        "title": "How many were born, and where",
        "note": "People per 50 years, stacked by birth region. The grey band is everyone whose birthplace the tree never recorded.",
        "x": xs_birth, "unit": "people", "xlab": "born",
        "splits": {"Region": {r: [born_reg[x].get(r, 0) for x in xs_birth]
                              for r in top_regions + ["unrecorded"]}},
    })

    charts.append({
        "id": "lifespan", "kind": "line",
        "title": "How long they lived",
        "note": f"Median age at death, by 50 years of birth; switch to Sex to compare women and men. Cohorts born after {life_cut} are excluded, because when most of a cohort is still living the only recorded deaths are the early ones, and lifespan appears to collapse.",
        "x": xs_life, "unit": "years", "zero": False, "xlab": "born",
        "splits": {
            "All": {"Everyone": median_series(lifespan_all, xs_life)},
            "Sex": {"Men": median_series(lifespan_sex["M"], xs_life),
                    "Women": median_series(lifespan_sex["F"], xs_life)},
            "Region": by_region(lifespan_reg, xs_life),
        },
    })

    charts.append({
        "id": "familysize", "kind": "line",
        "title": "Children per couple",
        "note": "Median, by the 50 years in which the couple married. Read the last band carefully: living children are often left out of a tree, so some of any final drop is the record, not the family.",
        "x": xs_marr, "unit": "children", "zero": True, "xlab": "married",
        "splits": {
            "All": {"Every couple": median_series(size_all, xs_marr)},
            "Region": by_region(size_reg, xs_marr),
        },
    })

    charts.append({
        "id": "marriageage", "kind": "line",
        "title": "Age at marriage",
        "note": "Median age on the wedding day, women and men.",
        "x": xs_marr, "unit": "years old", "zero": False, "xlab": "married",
        "splits": {
            "Sex": {"Men": median_series(marr_sex["M"], xs_marr),
                    "Women": median_series(marr_sex["F"], xs_marr)},
            "All": {"Everyone": median_series(marr_all, xs_marr)},
        },
    })

    charts.append({
        "id": "childmortality", "kind": "line",
        "title": "Died before eighteen",
        "note": "Share of a birth cohort dying as a child. This is a floor, not a "
                "rate: infants who died were often never written down at all, so "
                "the real figure was higher.",
        "x": xs_life, "unit": "%", "zero": True, "xlab": "born",
        "splits": {
            "All": {"Everyone": [round(100 * statistics.mean(young_all[x]), 1)
                                 if len(young_all.get(x, [])) >= MIN_BAND else None
                                 for x in xs_life]},
            "Region": by_region(young_reg, xs_life, kind="pct"),
        },
    })

    hb = sorted(set(list(age_hist_m) + list(age_hist_f)))
    charts.append({
        "id": "agedist", "kind": "hist",
        "title": "Age at death, everyone",
        "note": "The whole distribution rather than its middle. The bar at the "
                "far left is children; the hump on the right is everyone who got "
                "through childhood.",
        "x": [f"{b}" for b in hb], "unit": "people", "zero": True, "xlab": "age at death",
        "splits": {"Sex": {"Men": [age_hist_m.get(b, 0) for b in hb],
                           "Women": [age_hist_f.get(b, 0) for b in hb]},
                   "All": {"Everyone": [age_hist_m.get(b, 0) + age_hist_f.get(b, 0) for b in hb]}},
    })

    MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    charts.append({
        "id": "birthmonth", "kind": "hist",
        "title": "What month they were born",
        "note": f"{sum(month.values()):,} people whose birth month is recorded, by month.",
        "x": MON, "unit": "births", "zero": True,
        "splits": {"All": {"Births": [month.get(i, 0) for i in range(1, 13)]}},
    })

    charts.append({
        "id": "flows", "kind": "bars",
        "title": "Where they went",
        "note": "People who died in a different region from the one they were "
                "born in. Only those with both places recorded can be counted.",
        "x": [f"{a} → {b}" for (a, b), _ in flows.most_common(10)],
        "unit": "people",
        "splits": {"All": {"Moved": [n for _, n in flows.most_common(10)]}},
    })

    # --- how much of the line has actually been found ---------------------
    # Every generation back doubles the number of ancestors you *have*. Plotting
    # the ones the tree has found against that doubling shows exactly where the
    # research stops being complete and starts being a few lucky lines.
    gens, seen_anc, frontier = [], {root}, {root}
    g = 0
    while frontier and g < 16:
        nxt = set()
        for q in frontier:
            for m in parents.get(q, ()):
                if m in group and m not in seen_anc:
                    nxt.add(m)
        if not nxt:
            break
        g += 1
        seen_anc |= nxt
        gens.append(len(nxt))
        frontier = nxt
    over = sum(1 for q in seen_anc if len(parents.get(q, ())) > 2)
    # ---- eight more, added 2026-09-17 -----------------------------------
    # Each was measured before it was written: anything whose bands came out
    # flat, or whose trend turned out to be the population changing rather
    # than behaviour, is noted rather than drawn. Three candidates were
    # dropped outright and are listed at the foot of this block.
    import math as _math

    # build() is handed people/fam/group/year, not a child index or the
    # geocoded places, so both are derived here rather than changing every
    # caller. `places` is optional: without it the distance chart is skipped
    # instead of the whole block failing.
    kids_of = defaultdict(list)
    for _f in fam.values():
        _par = [c.value for c in list(_f.kids("HUSB")) + list(_f.kids("WIFE"))
                if c.value in people]
        for _c in _f.kids("CHIL"):
            if _c.value in people:
                for _p in _par:
                    kids_of[_p].append(_c.value)
    PLACES = places or {}

    def band(v):
        return (v // 50) * 50

    def med_of(pairs, unit_min=MIN_BAND):
        """pairs of (year, value) -> (x list, median list) on 50-year bands."""
        by = defaultdict(list)
        for y, v in pairs:
            by[band(y)].append(v)
        xs = [x for x in sorted(by) if len(by[x]) >= unit_min]
        return xs, [round(statistics.median(by[x]), 1) for x in xs], by

    def pct_of(pairs, test):
        by = defaultdict(list)
        for y, v in pairs:
            by[band(y)].append(1 if test(v) else 0)
        xs = [x for x in sorted(by) if len(by[x]) >= MIN_BAND]
        return xs, [round(100 * statistics.mean(by[x]), 1) for x in xs]

    # gather the raw pairs
    gen_pairs, spacing, first_child, last_child = [], [], [], []
    widow = {"M": [], "F": []}
    agegap, marr_month, death_month, moved = [], Counter(), Counter(), []
    marriage_km = []

    def pr_of(pid):
        return people[pid].rec

    def _km(a, b):
        A, B = PLACES.get(a), PLACES.get(b)
        if not A or not B:
            return None
        la1, lo1, la2, lo2 = map(_math.radians, (A[0], A[1], B[0], B[1]))
        h = (_math.sin((la2 - la1) / 2) ** 2 + _math.cos(la1) * _math.cos(la2)
             * _math.sin((lo2 - lo1) / 2) ** 2)
        return 6371 * 2 * _math.asin(min(1, _math.sqrt(h)))
    MONI = {m: i + 1 for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
         "OCT", "NOV", "DEC"])}
    _full = re.compile(r"\s*\d{1,2}\s+([A-Za-z]{3,})\s+(\d{4})")

    for p in group:
        b = year.get(p)
        if not b:
            continue
        kb = sorted(year[k] for k in kids_of[p] if year.get(k))
        for k in kb:
            if 14 < k - b < 60:
                gen_pairs.append((b, k - b))
        if kb and people[p].sex == "F":
            if 12 < kb[0] - b < 55:
                first_child.append((b, kb[0] - b))
            if 12 < kb[-1] - b < 55:
                last_child.append((b, kb[-1] - b))
        d = people[p].dy
        dd = event_date(people[p].rec, "DEAT")
        m = _full.match(dd or "")
        if m and m.group(1)[:3].upper() in MONI:
            death_month[MONI[m.group(1)[:3].upper()]] += 1
        bp = event_place(people[p].rec, "BIRT")
        dp = event_place(people[p].rec, "DEAT")
        a, c = PLACES.get(bp), PLACES.get(dp)
        if a and c:
            la1, lo1, la2, lo2 = map(_math.radians, (a[0], a[1], c[0], c[1]))
            h = (_math.sin((la2 - la1) / 2) ** 2 + _math.cos(la1) * _math.cos(la2)
                 * _math.sin((lo2 - lo1) / 2) ** 2)
            moved.append((b, 6371 * 2 * _math.asin(min(1, _math.sqrt(h)))))

    for f in fam.values():
        hs = [c.value for c in f.kids("HUSB") if c.value in people]
        ws = [c.value for c in f.kids("WIFE") if c.value in people]
        mm = f.kid("MARR")
        md = (mm.kid_value("DATE") if mm else "") or ""
        my = parse_date(md)[0]
        kb = sorted(year[c.value] for c in f.kids("CHIL")
                    if c.value in people and year.get(c.value))
        for a, b2 in zip(kb, kb[1:]):
            if 0 < b2 - a < 15:
                spacing.append((a, b2 - a))
        mo = _full.match(md)
        if mo and mo.group(1)[:3].upper() in MONI:
            marr_month[MONI[mo.group(1)[:3].upper()]] += 1
        if hs and ws:
            hb, wb = year.get(hs[0]), year.get(ws[0])
            if hb and wb and my:
                agegap.append((my, wb - hb))
            if my:
                _d = _km(event_place(people[hs[0]].rec, "BIRT"),
                         event_place(people[ws[0]].rec, "BIRT"))
                if _d is not None:
                    marriage_km.append((my, _d))
            dh, dw = people[hs[0]].dy, people[ws[0]].dy
            if dh and dw and dh != dw:
                if dw > dh and wb:
                    widow["F"].append((wb, dw - dh))
                elif dh > dw and hb:
                    widow["M"].append((hb, dh - dw))

    x_gen, y_gen, _ = med_of(gen_pairs)
    charts.append({
        "id": "generation", "kind": "line",
        "title": "How long a generation took",
        "note": f"Median years between a parent's birth and a child's, on {len(gen_pairs):,} parent-child pairs. A shorter generation is mostly people marrying earlier and stopping earlier.",
        "x": x_gen, "unit": "years", "zero": False, "xlab": "parent born",
        "splits": {"All": {"Parent to child": y_gen}},
    })

    xf, yf, _ = med_of(first_child)
    xl, yl, byl = med_of(last_child)
    xs_moth = [x for x in xf if x in set(xl)]
    charts.append({
        "id": "motherhood", "kind": "line",
        "title": "A mother's first child and her last",
        "note": "Median age of the mother at her first and last child. The span between them is the childbearing life.",
        "x": xs_moth, "unit": "years old", "zero": False, "xlab": "mother born",
        "splits": {"All": {
            "At her first": [yf[xf.index(x)] for x in xs_moth],
            "At her last": [yl[xl.index(x)] for x in xs_moth],
        }},
    })

    x_sp, y_sp, _ = med_of(spacing)
    charts.append({
        "id": "spacing", "kind": "line",
        "title": "Years between one child and the next",
        "note": f"Median gap, on {len(spacing):,} consecutive pairs of siblings.",
        "x": x_sp, "unit": "years", "zero": True, "xlab": "earlier child born",
        "splits": {"All": {"Gap": y_sp}},
    })

    # agegap holds (wife's birth year - husband's birth year), which is already
    # "how many years older the husband was" — positive when he was the elder.
    # Negating it made every band negative, and on a zero-based axis the line
    # was drawn below the plot and escaped the card.
    x_ag, y_ag, _ = med_of(agegap)
    charts.append({
        "id": "agegap", "kind": "line",
        "title": "How much older the husband was",
        "note": "Median years between husband and wife, by the year of the wedding. A negative band would mean wives were older on average.",
        "x": x_ag, "unit": "years", "zero": True, "xlab": "married",
        "splits": {"All": {"Husband older by": y_ag}},
    })

    xw_f, yw_f, _ = med_of(widow["F"])
    xw_m, yw_m, _ = med_of(widow["M"])
    xs_w = [x for x in xw_f if x in set(xw_m)]
    charts.append({
        "id": "widowhood", "kind": "line",
        "title": "Years spent widowed",
        "note": "Median years a surviving spouse outlived the other, counting only couples where both death dates are known.",
        "x": xs_w, "unit": "years", "zero": True, "xlab": "survivor born",
        "splits": {"All": {
            "Widows": [yw_f[xw_f.index(x)] for x in xs_w],
            "Widowers": [yw_m[xw_m.index(x)] for x in xs_w],
        }},
    })

    MON2 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    charts.append({
        "id": "marrmonth", "kind": "hist",
        "title": "What month they married",
        "note": f"{sum(marr_month.values()):,} weddings with a full date, by month.",
        "x": MON2, "unit": "weddings", "zero": True,
        "splits": {"All": {"Weddings": [marr_month.get(i, 0) for i in range(1, 13)]}},
    })

    charts.append({
        "id": "deathmonth", "kind": "hist",
        "title": "What month they died",
        "note": f"{sum(death_month.values()):,} people whose death month is recorded, by month.",
        "x": MON2, "unit": "deaths", "zero": True,
        "splits": {"All": {"Deaths": [death_month.get(i, 0) for i in range(1, 13)]}},
    })

    if moved:
        x_mv, y_mv = pct_of(moved, lambda d: d > 100)
        _, y_mv2 = pct_of(moved, lambda d: d > 1000)
        charts.append({
        "id": "moved", "kind": "line",
        "title": "How far from home they died",
        "note": f"Share of a birth cohort who died more than 100 km \u2014 and more than 1,000 km \u2014 from where they were born, on {len(moved):,} people with both places mapped. Read this as which migration was under way rather than as whether people were becoming more mobile.",
        "x": x_mv, "unit": "%", "zero": True, "xlab": "born",
        "splits": {"All": {"Over 100 km": y_mv, "Over 1,000 km": y_mv2}},
        })

    # Measured and NOT drawn, so nobody spends the afternoon rediscovering it:
    #   marriage to first child   median is 1 year in every band, for 350 years
    #   patronymic surnames       peaks at 12% and the trend is inside the noise;
    #                             -sen is also a fixed surname, so it cannot be
    #                             separated from the patronymic by suffix alone
    #   occupation inheritance    10% of 1,316 pairs, but occupations are free
    #                             text in six languages, so an exact match
    #                             undercounts by an unknown and large amount

    # ---- five more, 2026-09-17 (second round) ---------------------------

    def _given(pr):
        n = pr.kid("NAME")
        g = (n.kid_value("GIVN") if n else "") or ""
        if not g:
            raw = (n.full_value() if n else "")
            mm = re.match(r"^(.*?)/", raw)
            g = mm.group(1) if mm else raw
        return [w for w in re.sub(r"[^A-Za-z\u00c0-\u00ff ]", " ", g).split() if w]

    # The same name in six languages. Without this fold, Johann / Johannes /
    # Hans / Jean / John look like five different names, and the naming custom
    # below measures 11% instead of 26%.
    _VAR = {}
    for _grp in (
            "johann johannes john hans jan jean johan jon",
            "peter pieter pierre petter per", "jacob jakob jacques jaap james",
            "heinrich henrich henry hendrik henri", "maria marie mary mari",
            "anna anne ann annie hanna", "katharina catharina catherine katherine",
            "helena helene helen lena", "wilhelm william willem guillaume",
            "margaretha margaret marguerite margrethe margrete",
            "elisabeth elizabeth elisabet lisbeth", "andreas andrew andre anders",
            "nikolaus nicolas nicholas niels nils", "georg george jorgen jurgen",
            "michael michel mikael mikkel", "franz francois francis frank",
            "olaus ole olav oluf", "gerhard gerard gerrit"):
        _ws = _grp.split()
        for _w in _ws:
            _VAR[_w] = _ws[0]

    def _key(n):
        # the shared fold: an ASCII-only fold dropped ø outright, so Jørgen
        # became "jrgen" and never met Jorgen / Georg in the table above
        n = _fold(n)
        return _VAR.get(n, n)

    def _vset(pr):
        return {_key(g) for g in _given(pr)}

    def _rawset(pr):
        # the same names with no cross-language folding, for the caption's
        # "without which this measures ..." comparison
        return {_fold(g) for g in _given(pr)}

    firsts = {"M": defaultdict(list), "F": defaultdict(list)}
    firsts_raw = {"M": defaultdict(list), "F": defaultdict(list)}
    eldest_raw = []
    ngiven, precision, eldest = [], [], []
    for p in group:
        pr = people[p].rec
        g = _given(pr)
        bd = event_date(pr, "BIRT") or ""
        # These three are about what the RECORD says, so they must be banded by
        # the recorded birth year, not the estimated one. `year` fills a missing
        # year from relatives; a person with an estimated year has no birth date
        # at all by definition, so counting them here answered "how precise are
        # the dates" with a population that has none — which dragged the modern
        # bands from 77% down to 41% and looked like a privacy effect. It was
        # not: recent records are the most precise in the file.
        b = parse_date(bd)[0]
        if not b:
            continue
        if g:
            ngiven.append((b, len(g)))
            sx = people[p].sex
            if sx in firsts:
                firsts[sx][band(b)].append(_key(g[0]))
                firsts_raw[sx][band(b)].append(g[0].title())
        precision.append((b, 1 if re.search(r"\d{1,2}\s+[A-Za-z]{3,}\s+\d{4}", bd) else 0))

    # eldest son and his father's father
    for p in group:
        if people[p].sex != "M":
            continue
        b = year.get(p)
        fathers = [q for q in parents[p] if q in people and people[q].sex == "M"]
        if not b or not fathers:
            continue
        bros = [k for k in kids_of[fathers[0]]
                if k in people and people[k].sex == "M" and year.get(k)]
        if not bros or b != min(year[k] for k in bros):
            continue
        gf = [q for q in parents[fathers[0]] if q in people and people[q].sex == "M"]
        if not gf:
            continue
        mine, his = _vset(pr_of(p)), _vset(pr_of(gf[0]))
        if not mine or not his:
            continue
        eldest.append((b, 1 if mine & his else 0))
        eldest_raw.append(1 if _rawset(pr_of(p)) & _rawset(pr_of(gf[0])) else 0)

    # share of a cohort given one of that cohort's ten commonest names
    def top_share(byband):
        xs, ys = [], []
        for x in sorted(byband):
            v = byband[x]
            if len(v) < MIN_BAND:
                continue
            c = Counter(v)
            xs.append(x)
            ys.append(round(100 * sum(n for _, n in c.most_common(10)) / len(v), 1))
        return xs, ys

    xb, yb = top_share(firsts["M"])
    xg, yg = top_share(firsts["F"])
    xs_nm = [x for x in xb if x in set(xg)]
    charts.append({
        "id": "namefashion", "kind": "line",
        "title": "How many shared the same few names",
        "note": "Share of a birth cohort given one of the ten commonest first names of that cohort.",
        "x": xs_nm, "unit": "%", "zero": True, "xlab": "born",
        "splits": {"Sex": {"Boys": [yb[xb.index(x)] for x in xs_nm],
                           "Girls": [yg[xg.index(x)] for x in xs_nm]}},
    })

    x_ng, y_ng = pct_of(ngiven, lambda n: n >= 2)
    charts.append({
        "id": "middlenames", "kind": "line",
        "title": "Who had more than one given name",
        "note": "Share of each birth cohort recorded with a middle name.",
        "x": x_ng, "unit": "%", "zero": True, "xlab": "born",
        "splits": {"All": {"Two or more": y_ng}},
    })

    x_el, y_el = pct_of(eldest, lambda v: v == 1)
    charts.append({
        "id": "namedafter", "kind": "line",
        "title": "Eldest sons named for their father's father",
        "note": f"On {len(eldest):,} eldest sons whose paternal grandfather is also in the tree: the share named after him. Spelling is folded across languages first \u2014 Johann, Johannes, Hans, Jean and John count as one name. Treat it as a floor.",
        "x": x_el, "unit": "%", "zero": True, "xlab": "son born",
        "splits": {"All": {"Named for him": y_el}},
    })

    if marriage_km:
        x_mk, y_mk = pct_of(marriage_km, lambda d: d > 50)
        charts.append({
            "id": "marrydistance", "kind": "line",
            "title": "How far away they found a spouse",
            "note": "Share of weddings where the two were born more than 50 km apart. Only weddings where both birthplaces could be mapped are counted.",
            "x": x_mk, "unit": "%", "zero": True, "xlab": "married",
            "splits": {"All": {"Over 50 km apart": y_mk}},
        })

    x_pr, y_pr = pct_of(precision, lambda v: v == 1)
    charts.append({
        "id": "dateprecision", "kind": "line",
        "title": "How exactly the birth dates are known",
        "note": "Share of recorded births carrying a day and a month, not just a year. Read it beside the completeness chart: that one says whether a fact exists at all, this one says how sharp it is.",
        "x": x_pr, "unit": "%", "zero": True, "xlab": "born",
        "splits": {"All": {"Full date": y_pr}},
    })

    charts.append({
        "id": "pedigree", "kind": "line", "log": True,
        "title": "How far back the line is complete",
        "note": "Ancestors of the person at the centre, per generation back, against the number that must exist. "
                + (f"Generations three and four overshoot what is arithmetically "
                   f"possible because {over} people in this line carry more than "
                   f"two parents: a contradiction inherited from the source tree, "
                   f"not a finding." if over else ""),
        "x": [f"{i+1}" for i in range(len(gens))], "unit": "ancestors (log scale)", "xlab": "generations back",
        "splits": {"All": {"Found": gens,
                           "Must exist": [2 ** (i + 1) for i in range(len(gens))]}},
    })

    # --- where the tree is thin -------------------------------------------
    cov = {k: defaultdict(list) for k in
           ("Exact birth date", "Death recorded", "Birthplace", "Both parents")}
    for p in group:
        pr = P[p]
        b = band_of(year[p])
        y, m, d = parse_date(event_date(pr.rec, "BIRT"))
        cov["Exact birth date"][b].append(1 if (y and m and d) else 0)
        cov["Death recorded"][b].append(1 if pr.dy else 0)
        cov["Birthplace"][b].append(1 if event_place(pr.rec, "BIRT") else 0)
        cov["Both parents"][b].append(1 if len(parents.get(p, ())) >= 2 else 0)
    charts.append({
        "id": "coverage", "kind": "line",
        "title": "How complete the records are",
        "note": "Share of each birth cohort with the fact recorded. Read this one "
                "before trusting any other chart: where a line runs low, that "
                "century is thin, and a trend measured across it is measuring "
                "the record-keeping as much as the family. The two exceptions "
                "are the last bands, where 'death recorded' falls because those "
                "people are alive.",
        "x": xs_birth, "unit": "%", "zero": True, "max": 100, "xlab": "born",
        "splits": {"All": {k: [round(100 * statistics.mean(v[x]), 1)
                               if len(v.get(x, [])) >= MIN_BAND else None
                               for x in xs_birth]
                           for k, v in cov.items()}},
    })

    # ---------------------------------------------------------------- records
    def label(p):
        pr = P[p]
        yrs = "–".join(str(x) for x in (pr.by, pr.dy) if x)
        return {"name": pr.full.title() or "(no name)", "when": yrs,
                "i": idx.get(p, -1)}

    longest = sorted((p for p in group
                      if P[p].by and P[p].dy and 0 < P[p].dy - P[p].by <= 110),
                     key=lambda p: -(P[p].dy - P[p].by))[:6]
    kids_count = Counter()
    for f in fam.values():
        n = len([c for c in f.kids("CHIL") if c.value])
        for tag in ("HUSB", "WIFE"):
            for c in f.kids(tag):
                if c.value in group:
                    kids_count[c.value] += n
    most_kids = [p for p, _ in kids_count.most_common(6)]

    records = [
        {"title": "Longest lived",
         "note": "An age this high is as often a copied-wrong year as a real "
                 "record-breaker. Shown as the file has them.",
         "rows": [dict(label(p), value=f"{P[p].dy - P[p].by} years") for p in longest]},
        {"title": "Most children",
         "note": "Counting every marriage, so a widower who remarried appears "
                 "with both families summed.",
         "rows": [dict(label(p), value=f"{kids_count[p]} children") for p in most_kids]},
    ]

    occ = Counter()
    for p in group:
        for o in P[p].rec.kids("OCCU"):
            v = (o.full_value() or "").strip().lower()
            if v:
                occ[v] += 1

    tables = {
        "occupations": [[o.title(), n] for o, n in occ.most_common(14)],
        "surnames": [[s.title(), n] for s, n in
                     Counter(P[p].surn for p in group if P[p].surn).most_common(14)],
        "regions": [[r, n] for r, n in regions.most_common()],
    }

    # ---- the numbers the captions quote that are not on the chart itself --
    def _top_name(sx, b):
        keys = firsts[sx].get(b) or []
        if not keys:
            return None
        k, n = Counter(keys).most_common(1)[0]
        spell = Counter(r for r, kk in zip(firsts_raw[sx][b], keys) if kk == k)
        return (spell.most_common(1)[0][0], round(100 * n / len(keys), 1))

    have = [int(x) for x in xs_nm]
    eras = [e for e in (1650, 1800, 1900) if e in have] or (
        [have[0], have[len(have) // 2], have[-1]] if have else [])
    moved_med = None
    if moved:
        _xm, _ym, _ = med_of(moved)
        if _ym:
            moved_med = (int(round(min(_ym))), int(round(max(_ym))))
    ages = [P[p].dy - P[p].by for p in group
            if P[p].by and P[p].dy and 1 <= P[p].dy - P[p].by <= 110]
    X = {
        "people": len(group), "unrecorded": regions.get("unrecorded", 0),
        "lifeCut": life_cut, "genPairs": len(gen_pairs),
        "spacingPairs": len(spacing), "movedPeople": len(moved),
        "movedMedian": moved_med, "over": over,
        "girlTop": {int(b): _top_name("F", b) for b in firsts["F"]},
        "boyTop": {int(b): [n for n, _ in Counter(firsts_raw["M"][b]).most_common(3)]
                   for b in firsts_raw["M"]},
        "boyEras": eras,
        "eldest": len(eldest),
        "eldestPct": 100 * sum(v for _, v in eldest) / max(1, len(eldest)),
        "eldestRawPct": 100 * sum(eldest_raw) / max(1, len(eldest_raw)),
        "heapN": len(ages),
        "heapPct": 100 * sum(1 for a in ages if a % 5 == 0) / max(1, len(ages)),
    }
    write_captions(charts, X)
    write_findings(charts, X)

    by_id = {c["id"]: c for c in charts}
    life_line = by_id["lifespan"]["splits"]["All"]["Everyone"]
    headline = {
        "people": len(group),
        "families": len(fam),
        "span": [min(int(year[p]) for p in group), latest],
        "lifespanFrom": next((v for v in life_line if v), None),
        "lifespanTo": next((v for v in reversed(life_line) if v), None),
        "moved": sum(flows.values()),
    }
    # The legend colours by region INHERITED from relatives where a person's
    # own birthplace is missing; the "Where they were born" table counts
    # recorded birthplaces only. Both are right and neither said so (F28).
    unrec = regions.get("unrecorded", 0)
    region_note = {
        "recorded": len(group) - unrec, "inferred": unrec,
        "legend": (f"Where they were born — or, for the {unrec:,} people with "
                   "no recorded birthplace, where their family was."),
        "table": (f"Recorded birthplaces only, grouped into regions. "
                  f"The {unrec:,} people in the unrecorded row are coloured by "
                  "their family's region in the model, which is why the legend's "
                  "counts are larger."),
    }
    me = me or _me_from_argv() or ""
    surnames = surname_block(people, fam, group, year, parents, region_of,
                             event_place, me, surn)
    return {"charts": charts, "records": records, "tables": tables,
            "headline": headline, "lifeCut": life_cut,
            "regionNote": region_note, "chapters": CHAPTERS,
            "surnames": surnames}
