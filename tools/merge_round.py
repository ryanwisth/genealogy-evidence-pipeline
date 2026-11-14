#!/usr/bin/env python3
"""
merge_round.py RESULTS DATE --base BASE.ged [--research DIR] [--view DIR] [--round N] [--write]

Fold a research round's skeptic output into the ledger, research/additions.json.

  RESULTS  the wave-2 workflow's journal.jsonl (harvested offline), or a JSON
           file {results: [{lane, verdict}]}
  DATE     the ledger date stamp, e.g. 2026-09-27
  --write  actually append; without it this is a dry run that validates only

What it does, and refuses:
  * prefixes every tid and sid of round N (default 2) with "rN:" (rewriting every reference),
    so nothing can collide with round 1's ids;
  * refuses a reference that is neither an existing tree id nor a tid in the
    batch, a citation to a sid that is not in the batch, and a source kind
    that is not a record;
  * stamps each item {added: DATE, entry: "DATE/rN-<lane>-NN"};
  * proves the result builds: runs tools/apply_additions.py into a scratch
    GEDCOM before it touches the ledger, and keeps research/roundN/
    additions.before-roundN.json as the ledger it started from;
  * appends the skeptics' "disputes" (parent links a record shows are wrong) to
    research/disputes.json, refusing any that name an id not in the tree;
  * writes each lane's raw verdicts to research/verified/roundN-<lane>.json.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workspace as W

RN = 2                                          # set from --round
RESEARCH = LEDGER = BASE = VIEW = None           # set from the command line
RECORD_KINDS = {"parish register transcription", "census transcription", "record image", "index",
                "headstone photo", "newspaper", "secondary compilation"}
ID_RX = re.compile(r"^@I[^@]+@$")


def fail(msg):
    sys.exit("merge_round: " + msg)


def load(path):
    if path.endswith(".jsonl"):
        lane_of, out = {}, []
        for line in open(path, encoding="utf-8"):
            d = json.loads(line)
            if d.get("type") == "started":
                lane_of[d["agentId"]] = d.get("label", "").removeprefix("verify:")
            elif d.get("type") == "result" and isinstance(d.get("result"), dict):
                out.append({"lane": lane_of.get(d["agentId"], d["agentId"]), "verdict": d["result"]})
        return out
    d = json.load(open(path, encoding="utf-8"))
    return [r for r in d.get("results", d) if r and r.get("verdict")]


def fold(name):
    name = unicodedata.normalize("NFKD", name or "").casefold()
    return {w for w in re.sub(r"[^a-z ]", " ", "".join(c for c in name if not unicodedata.combining(c))).split()
            if len(w) > 2}


def couples(ged):
    """Every base family as ({husband, wife}, {children}): a MARR fact can only go
    on one, and a child it already holds must not be linked to it twice."""
    out, cur = [], None
    for line in open(ged, encoding="utf-8", errors="replace"):
        if line.startswith("0 "):
            parts = line.split()
            cur = (set(), set()) if len(parts) > 2 and parts[2] == "FAM" else None
            if cur is not None:
                out.append(cur)
        elif cur is not None and line[:7] in ("1 HUSB ", "1 WIFE "):
            cur[0].add(line.split()[2])
        elif cur is not None and line.startswith("1 CHIL "):
            cur[1].add(line.split()[2])
    return out


def family_heads(ged, fids):
    """{@F…@: husband id, else wife id} for the family ids a note is on: the
    builder attaches notes to people only, so a family note goes on its head."""
    out, cur = {}, None
    for line in open(ged, encoding="utf-8", errors="replace"):
        if line.startswith("0 "):
            parts = line.split()
            cur = parts[1] if len(parts) > 2 and parts[2] == "FAM" and parts[1] in fids else None
        elif cur and line[:7] in ("1 HUSB ", "1 WIFE "):
            if line.startswith("1 HUSB ") or cur not in out:
                out[cur] = line.split()[2]
    return out


def main(path, date, write):
    results = load(path)
    T = json.load(open(os.path.join(VIEW, "tree-data.json"), encoding="utf-8"))
    tree_ids = set(T["ids"])
    tix = {x: i for i, x in enumerate(T["ids"])}
    base_fams = couples(BASE)
    pairs = [hw for hw, _ in base_fams]
    marr_notes = []
    ledger = json.load(open(LEDGER, encoding="utf-8"))
    ir_tid = {f"@IR{n:04d}@": p["tid"] for n, p in enumerate(ledger["people"], 1)}
    ledger_pairs = [{f.get("husb"), f.get("wife")} - {None} for f in ledger["families"]]
    old_tids = {p["tid"] for p in ledger["people"]}
    new = {k: [] for k in ("sources", "people", "families", "facts", "notes")}
    counts = []

    for r in results:
        lane, V = r["lane"], r["verdict"]
        L = V["ledger"]
        tids = {p["tid"] for p in L["people"]}
        sids = {s["sid"] for s in L["sources"]}

        def ref(x, what):
            if x is None or x == "":
                return x
            m = re.match(r"^@IR(\d{4})@$", x or "")
            if m:
                # a person an earlier round added: the built GEDCOM numbers them
                # in ledger order (apply_additions.py), the ledger knows their tid
                k = int(m.group(1))
                if not 1 <= k <= len(ledger["people"]):
                    fail(f"{lane}: {what} points at {x}, beyond the ledger's {len(ledger['people'])} people")
                return ledger["people"][k - 1]["tid"]
            if ID_RX.match(x):
                if x not in tree_ids and x not in old_tids:
                    fail(f"{lane}: {what} points at {x}, which is not in the tree")
                return x
            if x in tids:
                return f"r{RN}:" + x
            if x in old_tids:
                return x
            fail(f"{lane}: {what} points at {x!r}, neither a tree id nor a tid in this batch")

        def cites(ss, what):
            for s in ss or []:
                if s not in sids:
                    fail(f"{lane}: {what} cites {s!r}, which is not among the lane's sources")
            return [f"r{RN}:" + s for s in ss or []]

        n = 0

        def stamp(item):
            nonlocal n
            n += 1
            item["added"], item["entry"] = date, f"{date}/r{RN}-{lane}-{n:02d}"
            return item

        for s in L["sources"]:
            if s.get("kind") not in RECORD_KINDS:
                fail(f"{lane}: source {s['sid']} has kind {s.get('kind')!r}; only records are cited")
            new["sources"].append(dict(s, sid=f"r{RN}:" + s["sid"]))
        for p in L["people"]:
            evs = [dict(e, sids=cites(e.get("sids"), f"person {p['tid']}")) for e in p.get("events", [])]
            new["people"].append(stamp(dict(p, tid=f"r{RN}:" + p["tid"], events=evs)))
        for f in L["families"]:
            g = dict(f, husb=ref(f.get("husb"), "family husb"), wife=ref(f.get("wife"), "family wife"),
                     children=[ref(c, "family child") for c in f.get("children", [])],
                     sids=cites(f.get("sids"), "family"))
            if f.get("marriage"):
                g["marriage"] = dict(f["marriage"], sids=cites(f["marriage"].get("sids"), "marriage"))
            same = next((ch for hw, ch in base_fams if g.get("husb") and g.get("wife")
                         and hw == {g["husb"], g["wife"]}), None)
            if same is not None:
                # the record confirms a child the family already holds: cite, do not re-link
                g["children"] = [c for c in g["children"] if c not in same]
            new["families"].append(stamp({k: v for k, v in g.items() if v not in (None, "")}))
        src_of = {x["sid"]: x for x in L["sources"]}
        for f in L["facts"]:
            if f["tag"] == "MARR":
                # The builder hangs a marriage on the base family that joins the
                # couple. A spouse written as a name is resolved against the
                # person's recorded spouses; anything that cannot be placed on a
                # base family becomes a note naming the record, never a guess.
                on, sp = f["on"], f.get("spouse") or ""
                if sp and not ID_RX.match(sp) and on in tix:
                    hits = [T["ids"][j] for j in T["spouses"][tix[on]]
                            if len(fold(T["names"][j]) & fold(sp)) >= 2
                            or (fold(T["names"][j]) & fold(sp) and len(T["spouses"][tix[on]]) == 1)]
                    sp = hits[0] if len(hits) == 1 else sp
                on_t, sp_t = ir_tid.get(on, on), ir_tid.get(sp, sp)
                if ID_RX.match(on) and ID_RX.match(sp) and (any({on, sp} <= c for c in pairs)
                                                           or any({on_t, sp_t} <= c for c in ledger_pairs)):
                    f = dict(f, spouse=sp)
                else:
                    recs = "; ".join(f"{src_of[x]['title']} — {src_of[x].get('detail', '')}" for x in f.get("sids", [])
                                     if x in src_of)
                    text = (f"Marriage {f['date']}, {f.get('place', '')}, to {f.get('spouse') or 'the spouse named'}"
                            + (f" ({f['detail']})" if f.get("detail") else "") + f". Record: {recs}.")
                    new["notes"].append(stamp({"on": ref(on, "marriage note"), "text": text}))
                    marr_notes.append(f"{lane}: {on} + {f.get('spouse')}")
                    continue
            g = dict(f, on=ref(f["on"], "fact"), sids=cites(f.get("sids"), f"fact on {f['on']}"))
            sp = f.get("spouse")
            if sp and not ID_RX.match(sp) and sp not in tids and sp not in old_tids:
                # a spouse the record names but who is not in the tree: keep the
                # name in the detail rather than invent a person for it
                g.pop("spouse")
                g["detail"] = "; ".join(x for x in (f.get("detail"), f"spouse: {sp}") if x)
            elif sp:
                g["spouse"] = ref(sp, "fact spouse")
            new["facts"].append(stamp(g))
        fams = family_heads(BASE, {x["on"] for x in L["notes"] if x["on"].startswith("@F")})
        for x in L["notes"]:
            if x["on"] in fams:
                x = dict(x, text=f"(On family {x['on']}.) {x['text']}", on=fams[x["on"]])
            new["notes"].append(stamp(dict(x, on=ref(x["on"], "note"))))
        counts.append(f"{lane}: {len(L['people'])} people, {len(L['families'])} families, "
                      f"{len(L['facts'])} facts, {len(L['notes'])} notes, {len(L['sources'])} sources")

    if marr_notes:
        counts.append(f"marriages kept as notes (no base family joins the couple): {len(marr_notes)} — "
                      + "; ".join(marr_notes))
    dpath = os.path.join(RESEARCH, "disputes.json")
    dfile = json.load(open(dpath, encoding="utf-8")) if os.path.exists(dpath) else {"disputes": []}
    have_d = {(d["child"], d["parent"]) for d in dfile["disputes"]}
    new_d = []
    for r in results:
        for d in r["verdict"].get("disputes", []):
            if d.get("child") not in tree_ids or d.get("parent") not in tree_ids:
                fail(f"{r['lane']}: dispute names an id not in the tree: {d.get('child')} / {d.get('parent')}")
            if (d["child"], d["parent"]) not in have_d:
                have_d.add((d["child"], d["parent"]))
                new_d.append(dict(d, added=date, round=f"r{RN}"))
    counts.append(f"disputes (cut links) added: {len(new_d)}")
    merged = {k: ledger[k] + new[k] for k in ledger}
    with tempfile.TemporaryDirectory() as tmp:
        lp, gp = os.path.join(tmp, "ledger.json"), os.path.join(tmp, "out.ged")
        json.dump(merged, open(lp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        r = subprocess.run([sys.executable, os.path.join(HERE, "apply_additions.py"), BASE, lp, gp],
                           capture_output=True, text=True)
        if r.returncode:
            fail("the merged ledger does not build:\n" + r.stderr[-2000:])
    print("\n".join(counts))
    print(f"total new: {len(new['people'])} people, {len(new['families'])} families, {len(new['facts'])} facts, "
          f"{len(new['notes'])} notes, {len(new['sources'])} sources")
    if not write:
        print("dry run: validated, ledger untouched (add --write)")
        return
    keep = os.path.join(RESEARCH, f"round{RN}", f"additions.before-round{RN}.json")
    os.makedirs(os.path.dirname(keep), exist_ok=True)
    os.makedirs(os.path.join(RESEARCH, "verified"), exist_ok=True)
    if not os.path.exists(keep):
        shutil.copyfile(LEDGER, keep)
    json.dump(merged, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if new_d:
        dfile["disputes"] += new_d
        json.dump(dfile, open(dpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in results:
        json.dump(r["verdict"], open(os.path.join(RESEARCH, "verified", f"round{RN}-{r['lane']}.json"), "w",
                                     encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"ledger written; the one it started from is {os.path.basename(keep)}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    RN = int(W.pop_opt(argv, "--round", "2"))
    BASE = W.pop_opt(argv, "--base")
    RESEARCH, VIEW = W.research_dir(argv), W.view_dir(argv)
    LEDGER = os.path.join(RESEARCH, "additions.json")
    a = [x for x in argv if not x.startswith("--")]
    if len(a) != 2 or not BASE:
        sys.exit(__doc__)
    main(a[0], a[1], "--write" in argv)
