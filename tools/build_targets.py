#!/usr/bin/env python3
"""
build_targets.py VIEW_DIR OUT.json --root ID --config LANES.json [--research DIR] [--all]

Pick a research round's targets and write a compact dossier for each, so no
research agent has to decode the view payloads.

    python3 tools/build_targets.py work/view work/research/round1/targets.json \
        --root @I46@ --config sample/lanes.json --research work/research

Round 1's agents each decoded the full details payload and walked tree-data
themselves; most of their tokens went on that and on re-learning each archive.
This does both once, in Python, for nothing:

  * the pool is the root person's direct ancestors with fewer than two parents
    (dead ends);
  * each is placed in a LANE by the record system that could open it (its own
    places, else its on-line child's, else its spouse's), with explicit
    overrides where a place string misleads;
  * people who cannot be researched from open records are EXCLUDED with the
    reason (a paywalled next step, implausible depth, a blank with nothing to
    match on, a living person);
  * the rest are ranked by how many independent identifiers a record could be
    matched on, and each lane takes its QUOTA; the remainder is written out as
    overflow, never dropped silently;
  * a dossier carries the facts, the record citations, spouse, children, what
    earlier rounds already searched, and the source URLs the ledger already holds.

A second pool, the LINK AUDIT, is for accuracy rather than size: ancestors in
the record-rich era whose parents are in the tree, ranked by how many ancestors
sit above them, so a wrong link low in the tree is found before anyone researches
the branch it carries. A third, DATE-FIX, takes the newest lint_tree.py report.
Living people are never targets: born after 1925 with no recorded death.

LANES.json holds everything that is specific to one tree (see sample/lanes.json):
  lanes          {key: [model, threads this round, targets per agent, route text]}
  regions        [[lane, regex over lower-cased place strings], ...] first match wins
  us_regex       places where the link audit applies (record-rich era)
  lane_override  {id: lane}            where a place string misleads
  blocked        {id: reason}          not researched this round
  contest_real   {id: description}     two parent pairs, a record can decide
  contest_merge  {id: description}     the same couple entered twice: a merge, not research
"""
import base64
import collections
import gzip
import json
import os
import re
import glob
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workspace as W

CFG = {}
LANES = REGION_RX = LANE_OVERRIDE = ROUND1_BLOCKED = CONTEST_REAL = CONTEST_MERGE = None
US_RX = None
BLANK_RX = re.compile(r"^\(no name\)$|^[^ ]+$")          # one word or no name at all


def configure(path):
    global LANES, REGION_RX, LANE_OVERRIDE, ROUND1_BLOCKED, CONTEST_REAL, CONTEST_MERGE, US_RX
    c = json.load(open(path, encoding="utf-8"))
    LANES = {k: tuple(v) for k, v in c["lanes"].items()}
    REGION_RX = [tuple(x) for x in c.get("regions", [])]
    LANE_OVERRIDE = c.get("lane_override", {})
    ROUND1_BLOCKED = c.get("blocked", {})
    CONTEST_REAL = c.get("contest_real", {})
    CONTEST_MERGE = c.get("contest_merge", {})
    US_RX = re.compile(c.get("us_regex", r"united states"))


def load(view):
    t = json.load(open(os.path.join(view, "tree-data.json"), encoding="utf-8"))
    D = json.loads(gzip.decompress(base64.b64decode(
        open(os.path.join(view, "tree-data-details.b64")).read())))
    if D.get("stamp") != t.get("stamp"):
        sys.exit("build_targets: details payload stamp does not match tree-data; rebuild the view")
    return t, D


def ancestors(t, root):
    par = t["parents"]
    gen, stack = {}, [(root, 0)]
    while stack:
        i, g = stack.pop()
        for q in par[i] or []:
            if q not in gen or gen[q] > g + 1:
                gen[q] = g + 1
                stack.append((q, g + 1))
    return gen


def rel_words(g):
    return {1: "parent", 2: "grandparent", 3: "great-grandparent"}.get(g, f"{g - 2}x-great-grandparent")


def main(view, out, ME, research):
    t, D = load(view)
    ids, names, par, kids, sps = t["ids"], t["names"], t["parents"], t["children"], t["spouses"]
    births, deaths = t["birth"], t["death"]
    idx = {v: i for i, v in enumerate(ids)}
    rows, titles = D["rows"], D["srcTitles"]
    me = idx[ME]
    # Cut every link a record has disproved or shown to be a duplicate copy
    # (research/disputes.json): counts, targets and pruning follow the records.
    dpath = os.path.join(research, "disputes.json")
    disputes = json.load(open(dpath, encoding="utf-8"))["disputes"] if os.path.exists(dpath) else []
    cut = {(d["child"], d["parent"]) for d in disputes}
    cut_why = collections.defaultdict(list)
    for d in disputes:
        if d["parent"] in idx:
            cut_why[d["child"]].append(f"{names[idx[d['parent']]]}: {d['kind']}. {d['why']}")
    pointer_gen = ancestors(t, me)
    par = [[q for q in (p or []) if (ids[i], ids[q]) not in cut] for i, p in enumerate(t["parents"])]
    te = dict(t, parents=par)
    gen = ancestors(te, me)
    dad_side = (set(ancestors(te, par[me][0])) | {par[me][0]}) if par[me] else set()
    # Every-path pruning: an ancestor reachable only through an OPEN contest's
    # links may not be an ancestor at all; it waits (round 2 researched a couple
    # two generations above a contest, because only direct children were checked).
    open_kids = {idx[c] for c in CONTEST_REAL if c in idx}
    reach_open = set(ancestors(dict(te, parents=[[] if i in open_kids else p for i, p in enumerate(par)]), me))
    waits = {i: next((names[c] for c in open_kids if i in ancestors(te, c)), "an open contest")
             for i in gen if i not in reach_open and i not in open_kids}

    ledger = json.load(open(os.path.join(research, "additions.json"), encoding="utf-8"))
    src_url = {s["sid"]: s.get("url", "") for s in ledger["sources"]}
    tid_urls = {p["tid"]: sorted({src_url.get(s, "") for e in p.get("events", []) for s in e.get("sids", [])} - {""})
                for p in ledger["people"]}
    for f in ledger["families"]:
        fam_urls = {src_url.get(x, "") for x in f.get("sids", [])} - {""}
        for who in (f.get("husb"), f.get("wife")):
            if who in tid_urls:
                tid_urls[who] = sorted(set(tid_urls[who]) | fam_urls)
    tid_of_id = {f"@IR{n:04d}@": p["tid"] for n, p in enumerate(ledger["people"], 1)}
    new_by_name = collections.defaultdict(list)
    for p in ledger["people"]:
        new_by_name[f"{p.get('given', '')} {p.get('surname', '')}".strip().lower()].append(p["tid"])
    urls_on = collections.defaultdict(set)
    for f in ledger["facts"]:
        for s in f.get("sids", []):
            if src_url.get(s):
                urls_on[f["on"]].add(src_url[s])
    notes_on = collections.defaultdict(list)
    for n in ledger["notes"]:
        notes_on[n["on"]].append(n["text"][:300])
    tally = {}
    # optional: a plain-text tally of what round 1 searched ("  TARGET name: summary")
    tpath = os.path.join(research, "verified", "round1-tally.txt")
    for line in (open(tpath, encoding="utf-8") if os.path.exists(tpath) else []):
        m = re.match(r"\s+TARGET (.+?): (.+)$", line)
        if m:
            tally[m.group(1).strip().lower()] = m.group(2)[:400]

    prior = {}
    jpath = os.path.join(research, "round2", "wave1-journal.jsonl")
    if os.path.exists(jpath):
        for line in open(jpath, encoding="utf-8"):
            d = json.loads(line)
            if d.get("type") == "result" and isinstance(d.get("result"), dict):
                for tg in d["result"].get("targets", []):
                    prior[tg["id"]] = {"round": 2, "outcome": tg["outcome"], "summary": tg["summary"][:400],
                                       "searched": [x[:140] for x in tg.get("searched", [])][:8]}

    def facts(i):
        return (rows[i] or {}).get("f", []) if rows[i] else []

    def places(i):
        return " | ".join(x[2] for x in facts(i) if x[2]).lower()

    def first(i, labels):
        for lab in labels:
            for x in facts(i):
                if x[0].startswith(lab) and (x[1] or x[2]):
                    return x
        return None

    def region(i):
        for cand in [i] + [c for c in kids[i] if c in gen or c == me] + list(sps[i]):
            p = places(cand)
            for lane, rx in REGION_RX:
                if p and re.search(rx, p):
                    return lane, cand != i
        return None, False

    def living(i):
        return (births[i] or 0) > 1925 and not deaths[i]


    def short_person(i):
        b = first(i, ("Born", "Christened", "Baptized"))
        d = first(i, ("Died", "Buried"))
        s = names[i]
        s += f" ({births[i] or '?'}-{deaths[i] or '?'})"
        if b and b[2]:
            s += f", b. {b[1]} {b[2]}"
        if d and d[2]:
            s += f"; d. {d[1]} {d[2]}"
        return s

    def dossier(i, lane, task):
        f = facts(i)
        recs = []
        for c in (rows[i] or {}).get("c", []) if rows[i] else []:
            if c[4] == 0:                                   # class 'record'
                recs.append(f"{titles[c[0]][:90]} | {str(c[2])[:80]} | {str(c[3])[:160]}")
        on_line = [c for c in kids[i] if c in gen or c == me]
        others = [c for c in kids[i] if c not in on_line]
        nm = names[i].lower()
        r1 = [tally[k] for k in tally if k == nm]
        tids = new_by_name.get(nm, [])
        return {
            "id": ids[i], "name": names[i], "task": task, "lane": lane,
            "relation": f"the root person's {rel_words(gen[i])} ({'father' if i in dad_side else 'mother'}'s side)",
            "facts": [f"{x[0]}: {x[1]} {x[2]}".strip() + (f" [{x[3][:60]}]" if x[3] else "") for x in f[:14]],
            "record_citations": recs[:4],
            "parents_known": [short_person(p) for p in par[i] or []],
            "spouses": [short_person(s) for s in sps[i]][:3],
            "child_on_line": [short_person(c) for c in on_line][:2],
            "other_children": [short_person(c) for c in others][:20],
            "round1": (r1[0] if r1 else "") ,
            "round1_notes": notes_on.get(ids[i], [])[:3],
            "ledger_urls": sorted(urls_on.get(ids[i], set()) | set(tid_urls.get(tid_of_id.get(ids[i]), []))
                                  | {u for tid in tids for u in tid_urls.get(tid, [])})[:6],
            "contest": CONTEST_REAL.get(ids[i], ""),
            "disproved_parents": cut_why.get(ids[i], []),
            "prior": prior.get(ids[i]),
        }

    def score(i, via_relative):
        s = 0
        s += 1 if births[i] else 0
        s += 1 if first(i, ("Born", "Christened")) and first(i, ("Born", "Christened"))[2] else 0
        s += 1 if sps[i] and not BLANK_RX.match(names[sps[i][0]]) else 0
        s += 1 if any(first(c, ("Born", "Christened")) and first(c, ("Born", "Christened"))[1][-4:].isdigit()
                      for c in kids[i]) else 0
        s += 1 if first(i, ("Died", "Buried")) and first(i, ("Died", "Buried"))[2] else 0
        s += 1 if any(c[4] == 0 for c in ((rows[i] or {}).get("c", []) if rows[i] else [])) else 0
        s += 2 if urls_on.get(ids[i]) or new_by_name.get(names[i].lower()) else 0   # round 1 already found the register
        s -= 1 if via_relative else 0
        if ids[i] in tid_of_id and tid_urls.get(tid_of_id[ids[i]]):
            s += 4                                  # added by research: the register is already found
        pr = prior.get(ids[i]) or {}
        s -= {"nothing found": 3, "candidates only (ambiguous)": 1}.get(pr.get("outcome"), 0)
        return s

    pool, excluded = collections.defaultdict(list), []
    unread = [ids[i] for i in gen if len(par[i] or []) > 2
              and ids[i] not in CONTEST_REAL and ids[i] not in CONTEST_MERGE]
    if unread:
        sys.exit(f"build_targets: new contested ancestors not yet classified: {unread}")
    for i, g in gen.items():
        contested = ids[i] in CONTEST_REAL
        if ids[i] in CONTEST_MERGE:
            excluded.append((ids[i], names[i], "merge in the source tree, not research: " + CONTEST_MERGE[ids[i]]))
            continue
        if len(par[i] or []) >= 2 and not contested:
            continue
        if i in waits:
            excluded.append((ids[i], names[i], f"waits on the open contest at {waits[i]}: every path to the root person runs through it"))
            continue
        if living(i):
            excluded.append((ids[i], names[i], "living"))
            continue
        if ids[i] in ROUND1_BLOCKED:
            excluded.append((ids[i], names[i], ROUND1_BLOCKED[ids[i]]))
            continue
        lane, via = region(i)
        lane = LANE_OVERRIDE.get(ids[i], lane)
        by = births[i] or min([births[c] - 30 for c in kids[i] if births[c]] or [0])
        if by and by < 1550:
            excluded.append((ids[i], names[i], f"implausible depth: born ~{by}, no open record reaches it"))
            continue
        blank = BLANK_RX.match(names[i].strip()) is not None
        if blank and not (sps[i] and kids[i]):
            excluded.append((ids[i], names[i], "a blank with no spouse or child to match a record on"))
            continue
        if lane is None:
            excluded.append((ids[i], names[i], "no place on the person, their child or spouse: nothing to route on"))
            continue
        task = ("decide from a record which of the competing parent pairs is right" if contested else
                "name this blank from a spouse's or child's record" if blank else
                "find the second parent" if len(par[i] or []) == 1 else
                "find the parents")
        pool[lane].append((score(i, via) - (2 if blank else 0) + (1 if contested else 0), g, i, task))

    # A thread is a target plus any of its own parents that are also dead ends in
    # the same lane: a target's missing mother is often on the father's
    # marriage entry, in the register the same agent already has open. Quotas
    # count threads; a thread is never split across agents.
    lanes_out, overflow = [], []
    # The link audit: record-rich-era ancestors (gens 1-6) whose two parents are in the tree,
    # weighted by how many ancestors sit above them. A wrong link at great-
    # grandparent level puts every ancestor above it on the wrong branch.
    above = {}
    for i in gen:
        seen, st = set(), [i]
        while st:
            x = st.pop()
            for q in par[x] or []:
                if q not in seen:
                    seen.add(q)
                    st.append(q)
        above[i] = len(seen)
    for i, g in gen.items():
        if i in waits:
            continue                                    # its links hang on an open contest
        if (prior.get(ids[i]) or {}).get("outcome") in ("link confirmed", "link contradicted"):
            continue                                    # audited in round 2; retry only its misses
        if (len(par[i] or []) == 2 and g <= 6 and not living(i) and (births[i] or 0) >= 1800
                and US_RX.search(places(i))):
            pool["link-audit"].append((above[i], g, i,
                                       "link audit: confirm (or refute) the recorded parents from a record that names them"))

    # Date fixes: impossible dates tools/lint_tree.py found on the direct line
    # (gens 1-9; older data rests on nothing a record reaches), skipping people a
    # research note already covers and duplicate or generation-slip muddles,
    # which are merges in the source tree.
    lint = sorted(glob.glob(os.path.join(research, "LINT*.md")))
    if lint:
        seen_ = set()
        for m in re.finditer(r"^\| (\d+) \| .*? `(@[^`]+@)` \| (.+?) \|$", open(lint[-1], encoding="utf-8").read(), re.M):
            g_, x, what = int(m.group(1)), m.group(2), m.group(3)
            if x in seen_ or x not in idx or "research note already covers" in what or g_ > 9:
                continue
            if x in CONTEST_MERGE or re.search(r"was \d+ at the birth|married \d+ at about -?\d+$", what):
                continue                                    # a duplicate or generation muddle, not a date to fix
            seen_.add(x)
            i = idx[x]
            if i in gen and not living(i):
                pool["date-fix"].append((10 - g_, gen[i], i, f"date check: {what}; find the record that shows which date is right"))

    for key, (model, quota, per, route) in LANES.items():
        cand = sorted(pool.get(key, []), key=lambda x: (-x[0], x[1]))
        if "--all" in sys.argv:                     # for bulk pulls: every researchable person, no quota
            quota, per = len(cand), max(1, len(cand))
        by_i = {c[2]: c for c in cand}
        threads, used = [], set()
        for c in cand:
            if len(threads) >= quota:
                break
            if c[2] in used:
                continue
            # a thread also takes the target's spouse when both are dead ends in
            # the lane: a marriage act names both sets of parents
            th = [c] + ([] if key in ("link-audit", "date-fix") else
                        [by_i[p] for p in list(par[c[2]] or []) + list(sps[c[2]]) if p in by_i and p not in used])
            used.update(x[2] for x in th)
            threads.append(th)
        overflow += [{"id": ids[i], "name": names[i], "lane": key, "score": s}
                     for s, g, i, task in cand if i not in used]
        agents, cur = [], []
        for th in threads:
            if cur and len(cur) + len(th) > per + 1:
                agents.append(cur)
                cur = []
            cur += [dossier(i, key, task) | {"score": s} for s, g, i, task in th]
            if len(cur) >= per:
                agents.append(cur)
                cur = []
        if cur:
            agents.append(cur)
        lanes_out.append({"key": key, "model": model, "route": route, "quota": quota,
                          "pool": len(cand), "threads": len(threads), "agents": agents})

    doc = {
        "built_from": os.path.relpath(os.path.join(view, "tree-data.json")),
        "stamp": t.get("stamp"),
        "me": ME,
        "ancestors": len(gen),
        "ancestors_by_pointer": len(pointer_gen),
        "cut_links": len(cut),
        "dead_ends": sum(1 for i in gen if len(par[i] or []) < 2),
        "lanes": lanes_out,
        "overflow": overflow,
        "excluded": [{"id": a, "name": b, "why": c} for a, b, c in excluded],
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(doc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # The roster is what the fleet script takes as args: small, so the launch call
    # stays small; each agent reads its own dossiers from OUT by id.
    roster = [{"lane": L["key"], "model": L["model"], "route": L["route"],
               "label": f"{L['key']}-{k + 1}", "ids": [d["id"] for d in a]}
              for L in lanes_out for k, a in enumerate(L["agents"])]
    json.dump(roster, open(os.path.join(os.path.dirname(out), "roster.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    n_t = sum(len(a) for L in lanes_out for a in L["agents"])
    n_a = sum(len(L["agents"]) for L in lanes_out)
    print(f"ancestors {doc['ancestors']} (by pointer {doc['ancestors_by_pointer']}, {len(cut)} cut links)")
    print(f"ancestors {doc['ancestors']}  dead ends {doc['dead_ends']}  targets {n_t} in {n_a} agents  "
          f"overflow {len(overflow)}  excluded {len(excluded)}")
    for L in lanes_out:
        print(f"  {L['key']:16s} {L['model']:6s} pool {L['pool']:3d}  take {sum(len(a) for a in L['agents'])}  "
              f"agents {len(L['agents'])}")
        for a in L["agents"]:
            for d in a:
                print(f"      [{d['score']}] {d['name']} — {d['task']} — {d['relation']}")
    why = collections.Counter(e[2].split(":")[0] for e in excluded)
    print("  excluded:", dict(why))


if __name__ == "__main__":
    argv = sys.argv[1:]
    me, cfg = W.pop_opt(argv, "--root"), W.pop_opt(argv, "--config")
    research = W.research_dir(argv)
    a = [x for x in argv if not x.startswith("--")]
    if len(a) != 2 or not me or not cfg:
        sys.exit(__doc__)
    configure(cfg)
    main(a[0], a[1], me, research)
