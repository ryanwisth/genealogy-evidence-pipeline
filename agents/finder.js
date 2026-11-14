export const meta = {
  name: 'genealogy-research-finders',
  description: 'Research wave 1: one finder agent per roster entry proposes record-backed additions (new ancestors, parent links, contests, dated facts); proposals only, nothing is written to the tree',
  whenToUse: 'Launched by hand with args = {workspace, round, roster} where roster is research/roundN/roster.json from tools/build_targets.py. Never resume it; harvest journal.jsonl instead.',
  phases: [
    { title: 'Research', detail: 'one finder per roster entry, model pinned per lane' },
  ],
}

// Claude Code workflow script (uses the workflow runtime's agent(), parallel(),
// phase() and log()). args: {workspace: absolute path of the workspace folder,
// round: N, roster: [{lane, model, route, label, ids}]} as written by
// tools/build_targets.py.
const W = args && args.workspace
const N = (args && args.round) || 1
const roster = (args && Array.isArray(args.roster)) ? args.roster : []
if (!W) throw new Error('args.workspace must be the absolute path of the workspace folder')
if (!roster.length) throw new Error('args.roster must be the roster array from research/roundN/roster.json')
for (const r of roster) {
  if (r.model !== 'opus' && r.model !== 'sonnet') throw new Error(`roster entry ${r.label} has no pinned model`)
}

const CONTEXT = `You are researching a family tree to make it BIGGER and MORE ACCURATE. Your targets are on the root person's direct line and are one of four kinds (the dossier's "task" says which):
- DEAD END ("find the parents" / "find the second parent" / "name this blank"): records that add new ancestors.
- CONTEST ("decide from a record which of the competing parent pairs is right"): two online trees give different parents; find the record that names this person's parents.
- DATE CHECK ("date check: …"): the tree holds two dates that cannot both be right (e.g. a birth after the baptism); find the record that shows which is right, add it as a fact, and say in tree_fixes which date to change. Do not research further.
- LINK AUDIT ("confirm (or refute) the recorded parents"): the tree already gives two parents; find a record that NAMES this person's parents (a census household with them as child, a marriage or death record listing parents, an obituary). If it agrees, add that record as facts: the link is now evidenced. If it disagrees, say exactly how in tree_fixes. Do not research further up.
- In every kind, add dated, placed, sourced facts from the records you open.
GROW THE TREE from records you already have open (first check the dossier's children lists: a child already there under any spelling, Wm for William, is not new; round 2 proposed 40 such duplicates): children of the root person's ancestor couples named in a census household, a baptism run, or an obituary, who are not already in the dossier's children lists, go in as new_people with a parent_link to the couple (at most 10 per family, each with the fact from that record). Do not go searching for them separately.
A separate skeptic will re-open every source you cite before anything reaches the tree, so a claim you cannot quote is worth nothing. Precision beats volume.

YOUR DOSSIERS (read these first; they replace decoding the view payloads):
  python3 -c "import json,sys; d=json.load(open('${W}/research/round${N}/targets.json')); ids=set(sys.argv[1:]); [print(json.dumps(x,ensure_ascii=False,indent=1)) for L in d['lanes'] for a in L['agents'] for x in a if x['id'] in ids]" IDS
Each dossier has the person's facts, spouses and children (with years and places: your matching identifiers), and:
  - "prior": what round 2 already searched for this person and found. Do NOT repeat those searches; start from what they did not try.
  - "ledger_urls": the records earlier rounds found this person in. For an ancestor round 2 added, that register is usually where their own baptism or marriage is: start there.
  - "disproved_parents": parents a record has shown are NOT this person's. Never propose them back.
  python3 -c "import json,base64,gzip,sys; V='${W}/view/'; t=json.load(open(V+'tree-data.json')); D=json.loads(gzip.decompress(base64.b64decode(open(V+'tree-data-details.b64').read()))); i=t['ids'].index(sys.argv[1]); print(t['names'][i], json.dumps(D['rows'][i],ensure_ascii=False)[:4000])" @I...@
Never write anywhere in the workspace.

TOKEN DISCIPLINE (this fleet is sized to a budget; round 1 spent most of its tokens on page dumps):
- Never print a whole web page. Fetch with curl into a temp file, then grep or python out the rows you need, 60 lines at most per call. WebFetch is fine for one focused question about one page.
- About 20 tool calls per target, 50 for your whole assignment (round 2's finders averaged 78 turns, and every turn re-reads everything before it, so turns are the real cost). When a target is not yielding, write a clean negative (exactly what you searched) and move on. A clean negative is a valid, useful result.
- At most 8 facts_on_existing per target, and only from records you already have open for the main question. Do not go looking for censuses of every relative.

WHERE RECORDS ARE (open access only: no login, no account, no downloads beyond reading pages, no contacting anyone):
- Your lane's route (below) names the archives and indexes that cover your targets' places. Start there.
- When the route names an index you have not used before, probe it once; if it is closed or needs a login, say so in "searched" and do not retry.
- Paywalled or login-only sites: do not try. Other users' trees (any site's family-tree pages) are LEADS only: follow one to the archive it names, then cite the archive. Never cite a tree.
- Find a Grave style memorials: a headstone PHOTO you actually see is a record; the memorial text written by users is not.
- Before about 1780 many baptisms name only the father: the mother comes from the father's MARRIAGE entry, in the same parish's marriage list. Patronymic surnames change every generation.

EVIDENCE RULES (hard):
- Propose a new person or a parent link ONLY when a record names them AND at least two independent identifiers tie that record to the tree person (parish + year + father's name; or age + farm + spouse). List those identifiers in "identifiers". If two records fit equally, propose nothing and report both as candidates.
- At most two generations above each target.
- Quote the exact text you rely on, copied verbatim from the page (the values as they appear, not your summary: a script checks every quote against the page before any skeptic sees it), and give its URL, plus the register / page / entry when shown. For a scan, say "my reading of the image" and transcribe what you read, marking doubtful letters with [?].
- Dates in GEDCOM style ("14 NOV 1754", "ABT 1754", "BET 1758 AND 1765"); places as the record gives them plus the modern parish/county.
- CONTEST targets ("decide from a record which of the competing parent pairs is right"): find the record that names this person's parents (baptism, marriage, death, census household), and report which pair it supports in "contests". Do not research the parents of either pair.
- Never look up a living person: anyone born after 1925 with no recorded death. A living person named in a record you are reading is skipped, not added.
- Tree errors you can show from a record (a wrong date, a duplicate, a conflated person) go in "tree_fixes" with the record, never as a silent change.

Your final answer is the structured proposal.`

const SOURCE = { type: 'object', properties: {
  sid: { type: 'string', description: 'short unique id, prefixed with your label, e.g. lane-a-1:S3' },
  title: { type: 'string' }, repository: { type: 'string' }, url: { type: 'string' },
  kind: { type: 'string', enum: ['parish register transcription', 'census transcription', 'record image', 'index', 'headstone photo', 'newspaper', 'secondary compilation', 'user tree (lead only)'] },
  detail: { type: 'string', description: 'register / volume / page / entry / line' },
  quote: { type: 'string', description: 'the exact text relied on, as it appears' },
}, required: ['sid', 'title', 'repository', 'url', 'kind', 'detail', 'quote'] }
const EVENT = { type: 'object', properties: { date: { type: 'string' }, place: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } } }, required: ['date', 'place', 'sids'] }
const PROPOSAL = { type: 'object', properties: {
  targets: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, name: { type: 'string' },
    outcome: { type: 'string', enum: ['new ancestors found', 'contest settled', 'link confirmed', 'link contradicted', 'facts added', 'candidates only (ambiguous)', 'nothing found', 'duplicate record'] },
    summary: { type: 'string' }, searched: { type: 'array', items: { type: 'string' } },
  }, required: ['id', 'name', 'outcome', 'summary', 'searched'] } },
  sources: { type: 'array', items: SOURCE },
  new_people: { type: 'array', items: { type: 'object', properties: {
    tid: { type: 'string', description: 'temporary id prefixed with your label, e.g. lane-a-1:N1' },
    given: { type: 'string' }, surname: { type: 'string' }, sex: { type: 'string', enum: ['M', 'F', 'U'] },
    birth: EVENT, baptism: EVENT, death: EVENT, burial: EVENT,
    other: { type: 'array', items: { type: 'object', properties: { tag: { type: 'string', enum: ['CONF', 'RESI', 'CENS', 'OCCU', 'EMIG', 'IMMI'] }, date: { type: 'string' }, place: { type: 'string' }, detail: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } } }, required: ['tag', 'date', 'place', 'sids'] } },
    confidence: { type: 'string', enum: ['high', 'medium'] }, reasoning: { type: 'string' },
  }, required: ['tid', 'given', 'surname', 'sex', 'confidence', 'reasoning'] } },
  parent_links: { type: 'array', items: { type: 'object', properties: {
    child: { type: 'string', description: '@I…@ id or a tid' },
    father: { type: 'string', description: 'the @I…@ id from the dossier, or your tid; never a name' },
    mother: { type: 'string', description: 'the @I…@ id from the dossier, or your tid; never a name' },
    marriage: EVENT, sids: { type: 'array', items: { type: 'string' } },
    identifiers: { type: 'array', items: { type: 'string' }, description: 'the independent identifiers tying the record to the tree person (at least two)' },
    confidence: { type: 'string', enum: ['high', 'medium'] }, reasoning: { type: 'string' },
  }, required: ['child', 'sids', 'identifiers', 'confidence', 'reasoning'] } },
  contests: { type: 'array', items: { type: 'object', properties: {
    child: { type: 'string' }, supports: { type: 'string', description: 'which pair the record supports, by the parent ids, or "undecided"' },
    sids: { type: 'array', items: { type: 'string' } }, reasoning: { type: 'string' },
  }, required: ['child', 'supports', 'sids', 'reasoning'] } },
  facts_on_existing: { type: 'array', items: { type: 'object', properties: {
    on: { type: 'string' }, tag: { type: 'string', enum: ['BIRT', 'CHR', 'CONF', 'MARR', 'DEAT', 'BURI', 'CENS', 'RESI', 'OCCU', 'EMIG', 'IMMI', 'NATU'] },
    date: { type: 'string' }, place: { type: 'string' }, detail: { type: 'string' }, spouse: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } },
    new_vs_tree: { type: 'string', description: 'new / confirms (adds a source) / contradicts (say how)' }, reasoning: { type: 'string' },
  }, required: ['on', 'tag', 'date', 'place', 'sids', 'new_vs_tree', 'reasoning'] } },
  tree_fixes: { type: 'array', items: { type: 'object', properties: { on: { type: 'string' }, fix: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } } }, required: ['on', 'fix', 'sids'] } },
}, required: ['targets', 'sources', 'new_people', 'parent_links', 'contests', 'facts_on_existing', 'tree_fixes'] }

const run = r =>
  agent(`${CONTEXT}\n\nYOUR LABEL: ${r.label}\nYOUR LANE: ${r.lane} — ${r.route}\nYOUR TARGETS (dossier ids; read them first with the command above): ${r.ids.join(' ')}`,
    { label: `research:${r.label}`, phase: 'Research', model: r.model, effort: 'medium', schema: PROPOSAL })
    .then(prop => ({ label: r.label, lane: r.lane, model: r.model, ids: r.ids, proposal: prop }))
    .catch(() => null)

phase('Research')
log(`${roster.length} finders: ${roster.filter(r => r.model === 'opus').length} opus, ${roster.filter(r => r.model === 'sonnet').length} sonnet`)
const done = (await parallel(roster.map(r => () => run(r)))).filter(Boolean)
const missing = roster.filter(r => !done.find(d => d.label === r.label && d.proposal)).map(r => r.label)
if (missing.length) log(`no proposal from: ${missing.join(', ')}`)
return { results: done, missing }
