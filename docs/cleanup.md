# The cleanup half: duplicates and lossless merging

The research half needs a tree it can trust. The export it started from held the same people as several unmerged records, which split the tree into islands: run cold, the tools said one man was unrelated to his own father.

| Step | Tool | What it does |
|---|---|---|
| Parse | `repair_structure.py` | Makes a MyHeritage export parseable without losing bytes: raw line breaks inside values, multi-byte characters split across CONC lines, caption text that looks like a GEDCOM line, a stripped byte that was half a character. |
| Score | `dup_scan.py` | Scores pairs on name, dates, places and relatives, and runs to a fixed point: merge the certain pairs, recompute relatives through the merged clusters, re-score. Hard blockers veto a pair outright (different sex, conflicting full dates, parent/child, spouses, siblings in one family). |
| Decide | `adjudicate.py`, `make_verify_pack.py`, `collect_verdicts.py` | Settles REVIEW pairs by rule where it can and writes self-contained dossiers for a judgment pass where it cannot; collects the verdicts into a merge list. |
| Merge | `merge_gedcom.py`, `clean_tree.py` | Most complete record survives (penalized if its own dates are impossible); every distinct fact and name spelling is carried over; impossible date pairs are repaired only from a coherent duplicate; user-tree "sources" get a note saying they are not documents. |
| Prove | `verify_clean.py` | Checks that every individual is accounted for, every fact preserved, no pointer dangles, the file is valid UTF-8 and no line is too long. |
| Inspect | `tree-audit.py`, `relationship-path.py` | Islands, duplicate candidates, who cites a source, and the step-by-step blood/marriage path between two people. |

Design choice: a wrong merge corrupts the tree invisibly, while a missed merge only leaves a duplicate. So the scanner is conservative, the uncertain band goes to review, and the verifier proves nothing was lost.
