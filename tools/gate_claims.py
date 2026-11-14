#!/usr/bin/env python3
"""
gate_claims.py RESULTS OUTDIR [--research DIR] [--view DIR] [--fetch] [--max-fetch N] [--as-of LEDGER]

The free step between a research round's two waves. It reads the finders'
proposals and does in Python what round 1 paid an Opus skeptic to do by hand:

  RESULTS   the wave-1 workflow's journal.jsonl (harvested offline, never by
            resuming the workflow), or a JSON file {results: [{label, proposal}]}
  OUTDIR    research/roundN/gated/ — one <lane>.json per lane for the skeptics,
            and SUMMARY.md

The round directory is OUTDIR's parent (research/roundN/): its roster.json and
pages/ (record pages saved as plain text by a separate, earlier step) are read
from there. The ledger comes from --research, the tree from --view.

Structural checks (a failure DROPS the claim, with the reason written down):
  * every claim cites a source that exists and is a record, not a user tree;
  * a parent link lists at least two identifiers;
  * a fact is not already in the ledger (same person, tag and YEAR: round 2's
    skeptics rejected "BET 1752 AND 1752" as a duplicate of "20 FEB 1752");
  * a new person is not already in the tree: the named parents' existing
    children are matched on folded first name (Wm = William) and year within 3,
    and a match drops the person and its parent link (43 of round 2's 67
    rejections were duplicates like this);
  * a new person is not living (born after 1925 with no death).
A parent link that re-states a link the tree already has is KEPT: a record for
an existing link is what a link audit is for, and merge_round.py cites it on the
family without linking the child twice.

Quote check (FLAGS, never drops): the quoted text of each cited source is looked
for in the saved pages first; with --fetch (off by default) a source not found
there is fetched once, politely, and checked too. "quote-found" carries the excerpt around the match, so the
skeptic can judge identity without re-fetching; "quote-missing" and
"fetch-failed" tell it to re-open that source first; images and headstone photos
are "image": only a reader can check those. Without --fetch a source not in the
saved pages is "not-fetched". A page that renders by script can hide a true quote
from a plain HTTP fetch, which is why a miss flags rather than drops.
"""
import difflib
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workspace as W

NICK = {"wm": "william", "chas": "charles", "jno": "john", "geo": "george", "fred": "frederick",
        "fredk": "frederick", "thos": "thomas", "jas": "james", "jos": "joseph", "wilhelm": "william",
        "johann": "john", "johannes": "john", "jean": "john", "jan": "john", "heinrich": "henry",
        "henri": "henry", "friedrich": "frederick", "carl": "charles", "karl": "charles",
        "maria": "mary", "marie": "mary", "catharina": "catherine", "katharina": "catherine",
        "katherine": "catherine", "kattie": "catherine", "elisabeth": "elizabeth", "lizzie": "elizabeth"}
RECORD_KINDS = {"parish register transcription", "census transcription", "record image", "index",
                "headstone photo", "newspaper", "secondary compilation"}
IMAGE_KINDS = {"record image", "headstone photo"}


def load_results(path):
    """-> {label: proposal}. Journal: 'started' lines map agentId -> label,
    'result' lines carry the return value."""
    if path.endswith(".jsonl"):
        label_of, out = {}, {}
        for line in open(path, encoding="utf-8"):
            d = json.loads(line)
            if d.get("type") == "started":
                label_of[d["agentId"]] = d.get("label", "")
            elif d.get("type") == "result" and isinstance(d.get("result"), dict):
                lab = label_of.get(d["agentId"], d["agentId"]).removeprefix("research:")
                out[lab] = d["result"]
        return out
    d = json.load(open(path, encoding="utf-8"))
    return {r["label"]: r["proposal"] for r in d.get("results", d) if r and r.get("proposal")}


FOLD = str.maketrans({"ø": "o", "æ": "ae", "ß": "ss", "œ": "oe", "ð": "d", "þ": "th"})


def norm(s):
    s = unicodedata.normalize("NFKD", html.unescape(s or "").casefold().translate(FOLD))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ſ", "s")
    return re.sub(r"[^\w]+", " ", s).strip()


USER_AGENT = "genealogy-evidence-pipeline/1.0 (quote check; one request per cited page)"
FETCH_DELAY = 3.0          # seconds between requests to any site


def page_text(url, cache, budget):
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    fn = os.path.join(cache, key)
    if not os.path.exists(fn + ".txt"):
        if budget["left"] <= 0:
            return None, "not-fetched"
        budget["left"] -= 1
        time.sleep(FETCH_DELAY)
        r = subprocess.run(["curl", "-sL", "--max-time", "30", "-A", USER_AGENT, "-o", fn, url])
        if r.returncode or not os.path.exists(fn) or os.path.getsize(fn) < 200:
            return None, "fetch-failed"
        head = open(fn, "rb").read(5)
        if head.startswith(b"%PDF"):
            subprocess.run(["pdftotext", "-layout", fn, fn + ".txt"])
        else:
            raw = open(fn, encoding="utf-8", errors="replace").read()
            raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
            open(fn + ".txt", "w", encoding="utf-8").write(re.sub(r"<[^>]+>", " ", raw))
    if not os.path.exists(fn + ".txt"):
        return None, "fetch-failed"
    return open(fn + ".txt", encoding="utf-8", errors="replace").read(), "ok"


LABELS = set("""age born birth year date place residence residential status family position marital
occupation parish church burial buried death died baptism baptised christened serial no role name given last
gender remarks reported cause doctor persons involved h and the of in from to with""".split())


def quote_status(quote, text):
    """Found when at least 80% of the quote's distinctive words (not field labels,
    at least 3 characters or a number) fall inside one window of the page, after
    folding case, accents, long s and punctuation. A window, not a phrase: record
    pages lay values out as 'Label: value' pairs, so a faithful quote of the values
    is rarely a contiguous string on the page."""
    q, t = norm(quote), norm(text)
    want = [w for w in dict.fromkeys(q.split()) if w not in LABELS and (len(w) >= 3 or w.isdigit())]
    if not want:
        return "quote-empty", ""
    tw = t.split()
    pos = {}
    for k, w in enumerate(tw):
        pos.setdefault(w, []).append(k)
    present = [w for w in want if w in pos]
    if len(present) < 0.8 * len(want):
        return "quote-missing", ""
    anchor = min(present, key=lambda w: len(pos[w]))          # rarest word on the page
    span = max(40, 3 * len(want))
    best, at = 0, 0
    for k in pos[anchor][:200]:
        win = set(tw[max(0, k - span): k + span])
        c = sum(1 for w in want if w in win)
        if c > best:
            best, at = c, k
    if best >= 0.8 * len(want):
        return "quote-found", " ".join(tw[max(0, at - span): at + span])[:900]
    return "quote-missing", ""


def year(s):
    m = re.search(r"\b(1[4-9]\d\d|20\d\d)\b", s or "")
    return int(m.group(1)) if m else None


def first_name(name):
    w = norm(name).split()
    return NICK.get(w[0], w[0]) if w else ""


def same_first(a, b, loose=False):
    """William = Wm, Hjalmar = Hjalmer: nickname table, else the first four letters.
    loose (for flags only, never drops): a similarity of 0.6, which is what
    old spelling variants of one given name (Gunnvald / Gonval) need."""
    a, b = first_name(a), first_name(b)
    if not a or not b:
        return False
    if a == b or (len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]):
        return True
    return loose and difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def main(results_path, outdir, research, view, fetch=False, max_fetch=400, as_of=None):
    RD = os.path.dirname(os.path.abspath(outdir))
    roster = {r["label"]: r for r in json.load(open(os.path.join(RD, "roster.json"), encoding="utf-8"))}
    # --as-of LEDGER replays an old round against the ledger as it stood then:
    # people added after it (@IR numbers past its length) are invisible.
    ledger = json.load(open(as_of or os.path.join(research, "additions.json"), encoding="utf-8"))
    later = {f"@IR{n:04d}@" for n in range(len(ledger["people"]) + 1, 10000)} if as_of else set()
    have = {(f["on"], f["tag"], year(f["date"])) for f in ledger["facts"] if year(f["date"])}
    # events inside people an earlier round added count too, under both their
    # ledger tid and their built id (round 3 re-proposed six of them)
    for n_, p_ in enumerate(ledger["people"], 1):
        for e in p_.get("events", []):
            if year(e.get("date")):
                have |= {(p_["tid"], e["tag"], year(e["date"])), (f"@IR{n_:04d}@", e["tag"], year(e["date"]))}
    T = json.load(open(os.path.join(view, "tree-data.json"), encoding="utf-8"))
    tix = {x: i for i, x in enumerate(T["ids"])}
    pg_dir = os.path.join(RD, "pages")
    saved = {f: open(os.path.join(pg_dir, f), encoding="utf-8", errors="replace").read()
             for f in sorted(os.listdir(pg_dir) if os.path.isdir(pg_dir) else []) if f.endswith(".txt")}
    props = load_results(results_path)
    os.makedirs(os.path.join(outdir, "cache"), exist_ok=True)
    budget = {"left": max_fetch if fetch else 0}

    lanes, summary = {}, []
    for label, P in sorted(props.items()):
        lane = roster.get(label, {}).get("lane") or label.rsplit("-", 1)[0]
        L = lanes.setdefault(lane, {"lane": lane, "labels": [], "dossier_ids": [], "targets": [],
                                    "sources": [], "new_people": [], "parent_links": [], "contests": [],
                                    "facts_on_existing": [], "tree_fixes": [], "gate": {}, "dropped": []})
        L["labels"].append(label)
        L["dossier_ids"] += roster.get(label, {}).get("ids", [])
        L["targets"] += P.get("targets", [])
        src = {s["sid"]: s for s in P.get("sources", [])}
        rec = {k for k, s in src.items() if s.get("kind") in RECORD_KINDS and s.get("url") and s.get("quote")}
        L["sources"] += list(src.values())

        # A new parent named only in a child's baptism carries its citation on the
        # parent link, not on itself; a census-only person cites through "other".
        link_sids = {}
        for x in P.get("parent_links", []):
            for who in (x.get("father"), x.get("mother"), x.get("child")):
                if who:
                    link_sids.setdefault(who, []).extend(x.get("sids") or [])

        # Finders sometimes write a parent as a name, not an id: resolve it
        # against the people this agent was given and their families.
        near = set()
        for x in roster.get(label, {}).get("ids", []):
            if x in tix:
                i = tix[x]
                near |= {i} | set(T["spouses"][i]) | set(T["children"][i]) | set(T["parents"][i] or [])
        by_name = {norm(T["names"][i]): T["ids"][i] for i in near}

        def as_id(v):
            return v if (v in tix or not v) else by_name.get(norm(v), v)

        # Duplicate people: a new child of an existing parent who is already one
        # of that parent's children, by first name and year within 1. A child who
        # died in infancy is exempt: the next child was often given the same name
        # (a daughter b. 1765, d. 1765, beside a sister of the same name b. 1766).
        dup_of = {}
        for x in P.get("parent_links", []):
            kid = next((p for p in P.get("new_people", []) if p.get("tid") == x.get("child")), None)
            if not kid:
                continue
            ky = year(((kid.get("birth") or kid.get("baptism") or {}).get("date")))
            kd = year(((kid.get("death") or kid.get("burial") or {}).get("date")))
            if ky and kd and kd - ky <= 5:
                continue
            for par_id in (as_id(x.get("father")), as_id(x.get("mother"))):
                if par_id not in tix:
                    continue
                for c in T["children"][tix[par_id]]:
                    if T["ids"][c] in later:
                        continue
                    cy = T["birth"][c]
                    if same_first(T["names"][c], kid.get("given", "")) and (not ky or not cy or abs(cy - ky) <= 1):
                        dup_of[kid["tid"]] = f"{T['names'][c]} {T['ids'][c]}"

        # A new PARENT who may already be in the tree (round 3 added a woman's father
        # under one spelling although the same man, b. 1810, was there already under
        # another, which cut a whole branch off). Flag, never drop:
        # the skeptic decides, and a flagged parent should be linked, not added.
        parents_new = {x.get(k) for x in P.get("parent_links", []) for k in ("father", "mother")}
        by_first = {}
        for p_ in P.get("new_people", []):
            if p_.get("tid") not in parents_new:
                continue
            py = year(((p_.get("birth") or p_.get("baptism") or {}).get("date")))
            sur = norm(p_.get("surname", "")).split()[:1]
            for j, nm_ in enumerate(T["names"]):
                if T["ids"][j] in later:
                    continue
                w = norm(nm_).split()
                if len(w) < 2 or not same_first(nm_, p_.get("given", ""), loose=True):
                    continue
                if sur and not any(x[:4] == sur[0][:4] for x in w[1:]):
                    continue
                if py and T["birth"][j] and abs(T["birth"][j] - py) > 2:
                    continue
                if not py or not T["birth"][j]:
                    continue
                by_first.setdefault(p_["tid"], []).append(f"{nm_} {T['ids'][j]} (b. {T['birth'][j]})")
        for tid_, hits in by_first.items():
            for p_ in P.get("new_people", []):
                if p_.get("tid") == tid_:
                    p_["possible_existing"] = hits[:5]

        def keep(kind, item, who):
            sids = item.get("sids") or [x for e in ("birth", "baptism", "death", "burial")
                                         for x in (item.get(e) or {}).get("sids", [])]
            if kind == "new_people":
                sids = sids + [x for o in item.get("other") or [] for x in o.get("sids") or []] \
                    + link_sids.get(item.get("tid"), [])
            why = None
            if not [s for s in sids if s in rec]:
                why = "cites no record (missing source, or a user tree only)"
            elif kind == "parent_links" and sum(
                    len([c for c in re.split(r"[;,]| and ", x) if c.strip()]) for x in item.get("identifiers") or []) < 2:
                # count clauses, not list entries: "Baptism names both parents as
                # Pierre Martin and Anne Dubois, same parish" is three
                why = "fewer than two identifiers"
            elif kind == "facts_on_existing" and (item["on"], item["tag"], year(item["date"])) in have:
                why = "already in the ledger (same person, event and year)"
            elif kind == "new_people" and item.get("tid") in dup_of:
                why = f"already in the tree as {dup_of[item['tid']]}"
            elif kind == "new_people" and (year((item.get("birth") or item.get("baptism") or {}).get("date")) or 0) > 1925 \
                    and not (item.get("death") or item.get("burial")):
                why = "living (born after 1925, no death recorded)"
            elif kind == "parent_links" and item.get("child") in dup_of:
                why = f"the child is already in the tree as {dup_of[item['child']]}"

            if why:
                L["dropped"].append({"label": label, "kind": kind, "item": item, "why": why})
            else:
                L[kind].append(item)

        for p in P.get("new_people", []):
            keep("new_people", p, p.get("tid"))
        for x in P.get("parent_links", []):
            keep("parent_links", x, x.get("child"))
        for x in P.get("contests", []):
            keep("contests", x, x.get("child"))
        for x in P.get("facts_on_existing", []):
            keep("facts_on_existing", x, x.get("on"))
        for x in P.get("tree_fixes", []):
            keep("tree_fixes", x, x.get("on"))
        # a new person whose only parent link was dropped would enter the tree
        # attached to nobody: drop it too (round 3 nearly added nine that way)
        lost = {d["item"].get("child") for d in L["dropped"] if d["kind"] == "parent_links" and d["label"] == label}
        linked = {x.get("child") for x in L["parent_links"]} | {x.get(k) for x in L["parent_links"] for k in ("father", "mother")}
        for p_ in [p_ for p_ in L["new_people"] if p_.get("tid") in lost and p_.get("tid") not in linked]:
            L["new_people"].remove(p_)
            L["dropped"].append({"label": label, "kind": "new_people", "item": p_, "why": "its only parent link was dropped"})

    for L in lanes.values():
        cited = {s for k in ("new_people", "parent_links", "contests", "facts_on_existing", "tree_fixes")
                 for it in L[k] for s in (it.get("sids") or []) +
                 [x for e in ("birth", "baptism", "death", "burial") for x in (it.get(e) or {}).get("sids", [])] +
                 [x for o in it.get("other") or [] for x in o.get("sids") or []]}
        for s in L["sources"]:
            if s["sid"] not in cited:
                continue
            if s.get("kind") in IMAGE_KINDS:
                L["gate"][s["sid"]] = {"status": "image"}
                continue
            # saved pages first: a page named for the source id, then any page
            found = None
            own = s["sid"].split(":")[-1] + ".txt"
            for fn_, txt in sorted(saved.items(), key=lambda kv: kv[0] != own):
                st_, ex_ = quote_status(s.get("quote", ""), txt)
                if st_ == "quote-found":
                    found = {"status": "quote-found", "excerpt": ex_, "from": "pages/" + fn_}
                    break
            if found:
                L["gate"][s["sid"]] = found
                continue
            if own in saved:
                L["gate"][s["sid"]] = {"status": "quote-missing", "excerpt": "", "from": "pages/" + own}
                continue
            if not fetch or not s.get("url", "").startswith(("http://", "https://")):
                L["gate"][s["sid"]] = {"status": "not-fetched"}
                continue
            text, how = page_text(s["url"], os.path.join(outdir, "cache"), budget)
            if text is None:
                L["gate"][s["sid"]] = {"status": how}
                continue
            st, ex = quote_status(s.get("quote", ""), text)
            L["gate"][s["sid"]] = {"status": st, "excerpt": ex}
        json.dump(L, open(os.path.join(outdir, f"{L['lane']}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        review_sheet(L, os.path.join(outdir, f"{L['lane']}.md"), T, tix)
        st = {}
        for g in L["gate"].values():
            st[g["status"]] = st.get(g["status"], 0) + 1
        n = sum(len(L[k]) for k in ("new_people", "parent_links", "contests", "facts_on_existing", "tree_fixes"))
        summary.append(f"| {L['lane']} | {len(L['new_people'])} | {len(L['parent_links'])} | {len(L['contests'])} | "
                       f"{len(L['facts_on_existing'])} | {n} | {len(L['dropped'])} | "
                       + ", ".join(f"{k} {v}" for k, v in sorted(st.items())) + " |")

    open(os.path.join(outdir, "SUMMARY.md"), "w", encoding="utf-8").write(
        "# Gate summary\n\n| lane | new people | links | contests | facts | kept | dropped | sources |\n"
        "|---|---|---|---|---|---|---|---|\n" + "\n".join(summary) + "\n")
    print("\n".join(summary))
    print(f"lanes {len(lanes)}  fetches left {budget['left']}" + ("" if fetch else "  (offline: saved pages only)"))


def review_sheet(L, path, T, tix):
    """The skeptic's reading copy: each kept claim on a few lines, with its
    sources' quotes and the gate's excerpt. Round 2's skeptics spent 53% of their
    context printing raw gated JSON; this is the part they actually judge."""
    src = {s["sid"]: s for s in L["sources"]}

    def who(x):
        return f"{T['names'][tix[x]]} {x} ({T['birth'][tix[x]] or '?'}-{T['death'][tix[x]] or '?'})" if x in tix else str(x)

    def cites(sids):
        out = []
        for sid in sids or []:
            s = src.get(sid, {})
            g = L["gate"].get(sid, {})
            out.append(f"    - {sid} [{s.get('kind', '?')}; gate: {g.get('status', 'not checked')}] {s.get('title', '')[:110]}"
                       f"\n      {s.get('url', '')}\n      quote: {s.get('quote', '')[:300]}"
                       + (f"\n      page around it: {g['excerpt'][:350]}" if g.get("excerpt") else ""))
        return "\n".join(out)

    lines = [f"# Lane {L['lane']} — claims to judge", "",
             f"Tree people: {', '.join(who(x) for x in L['dossier_ids'])}", ""]
    for p in L["new_people"]:
        ev = [f"{k} {p[k]['date']} {p[k]['place']}" for k in ("birth", "baptism", "death", "burial") if p.get(k)]
        sids = [x for k in ("birth", "baptism", "death", "burial") for x in (p.get(k) or {}).get("sids", [])]
        if p.get("possible_existing"):
            lines += [f"## CHECK FIRST: {p['tid']} may already be in the tree as: {'; '.join(p['possible_existing'])}. "
                      "If so, reject the new person and link the existing record instead."]
        lines += [f"## new_people:{p['tid']} — {p['given']} {p['surname']} ({p.get('confidence')})",
                  f"  {'; '.join(ev)}", f"  why: {p.get('reasoning', '')[:500]}", cites(sids), ""]
    for x in L["parent_links"]:
        lines += [f"## parent_links:{x['child']} — child {who(x['child'])}; father {who(x.get('father'))}; "
                  f"mother {who(x.get('mother'))}", f"  identifiers: {'; '.join(x.get('identifiers') or [])}",
                  f"  why: {x.get('reasoning', '')[:400]}", cites(x.get("sids")), ""]
    for x in L["contests"]:
        lines += [f"## contests:{x['child']} — {who(x['child'])} supports {x['supports']}",
                  f"  why: {x.get('reasoning', '')[:500]}", cites(x.get("sids")), ""]
    for x in L["facts_on_existing"]:
        lines += [f"## facts_on_existing:{x['on']}:{x['tag']}:{x['date']} — {who(x['on'])}",
                  f"  {x['tag']} {x['date']} {x.get('place', '')} {x.get('detail', '')[:200]}",
                  f"  vs tree: {x.get('new_vs_tree', '')[:200]}", cites(x.get("sids")), ""]
    for x in L["tree_fixes"]:
        lines += [f"## tree_fixes:{x['on']} — {who(x['on'])}", f"  {x['fix'][:500]}", cites(x.get("sids")), ""]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    argv = sys.argv[1:]
    as_of = W.pop_opt(argv, "--as-of")
    mf = int(W.pop_opt(argv, "--max-fetch", "400"))
    research, view = W.research_dir(argv), W.view_dir(argv)
    a = [x for x in argv if not x.startswith("--")]
    if len(a) != 2:
        sys.exit(__doc__)
    main(a[0], a[1], research, view, fetch="--fetch" in argv, max_fetch=mf, as_of=as_of)
