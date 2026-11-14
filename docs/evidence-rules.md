# Evidence rules and the ledger contract

## The rules

1. **Records, not trees.** A claim must cite a record: a register entry, a census line, an index entry, a headstone photo, a newspaper notice. Other people's online family trees are leads only. A finder may follow a tree to the archive it names, but it cites the archive. `gate_claims.py` drops any claim whose only citations are trees or missing.
2. **Two identifiers.** A new person or a parent link needs a record that names them and at least two independent identifiers tying that record to the tree person (parish + year + father's name, or age + place + spouse). The gate counts clauses, not list entries.
3. **Quote it.** Every source carries the exact text relied on, copied from the page. The gate checks each quote against the saved page before any skeptic sees it.
4. **Refute or reject.** A second model, the skeptic, re-opens the sources and tries to refute each claim. When it cannot confirm, it rejects.
5. **Nothing is deleted.** The base GEDCOM is never edited. Accepted research goes into an append-only ledger, and a new GEDCOM is built from the base plus the ledger. A disproved parent link is recorded in `disputes.json` and cut in every later count. It is not removed from the file.
6. **No living people.** Anyone born after 1925 with no recorded death is never a target and never added.

## What the gate does for free

`tools/gate_claims.py` reads the finders' journal and runs the checks that need no judgment.

| Check | Effect |
|---|---|
| Cites no record (missing source, or a user tree only) | drop |
| Parent link with fewer than two identifiers | drop |
| Fact already in the ledger (same person, tag and year) | drop |
| New child already among the named parents' children (folded first name, year within 1; infant deaths exempt) | drop the person and its link |
| New person living | drop |
| New person whose only parent link was dropped | drop |
| New parent who may already be in the tree under another spelling | flag "CHECK FIRST" for the skeptic |
| Quote found on the saved page | `quote-found`, with the page text around it |
| Quote not on the saved page | `quote-missing`: the skeptic must re-open the source |
| No saved page, fetch off | `not-fetched` |
| Scan or headstone photo | `image`: only a reader can check it |

Quote checks flag and never drop. A page that renders by script can hide a true quote from a plain fetch.

The quote match is a window, not a phrase. Record pages lay values out as `Label: value` pairs, so a faithful quote of the values is rarely a contiguous string. The check folds case, accents, the long s and punctuation, ignores field labels, and passes when 80 % of the quote's distinctive words fall inside one window of the page.

Fetching is off by default. `--fetch` turns it on, with a user agent that names this project, a 3-second delay between requests, and a per-run cap (`--max-fetch`).

## Ledger shape

`research/additions.json`:

```
{"sources":  [{sid, title, repository, url, kind, detail, quote}],
 "people":   [{tid, given, surname, sex, events:[{tag, date, place, detail?, sids}], note?, added, entry}],
 "families": [{husb, wife, children:[...], marriage:{date, place, sids}?, sids, added, entry}],
 "facts":    [{on, tag, date, place, detail?, spouse?, sids, added, entry}],
 "notes":    [{on, text, added, entry}]}
```

`husb`, `wife`, `children`, `on` and `spouse` are existing GEDCOM ids (`@I46@`) or ledger tids. `merge_round.py` prefixes every tid and sid of round N with `rN:`, stamps each item with its date and entry id, refuses references that do not resolve and source kinds that are not records, and proves the merged ledger builds before writing it.

`apply_additions.py` writes new ids with an R prefix (`@IR0001@`, `@FR0001@`, `@SR0001@`) so they never collide with the base file's ids and stay stable across runs. It checks that every base line survives in order, every pointer resolves, no id repeats, and no line exceeds 255 characters.

`research/disputes.json`: `{"disputes": [{child, parent, kind: "disproved" | "duplicate", why, added, round}]}`.
