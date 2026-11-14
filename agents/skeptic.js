export const meta = {
  name: 'genealogy-research-skeptics',
  description: 'Research wave 2: one skeptic agent per lane tries to refute every gated claim and writes the survivors as ledger entries',
  whenToUse: 'Launched by hand after tools/gate_claims.py, with args = {workspace, round, lanes: [{lane, path}]} for lanes that kept at least one claim. Never resume it.',
  phases: [
    { title: 'Verify', detail: 'one opus skeptic per lane', model: 'opus' },
  ],
}

// Claude Code workflow script. args: {workspace: absolute path of the workspace
// folder, round: N, lanes: [{lane, path}]}; path is research/roundN/gated/<lane>.json
// written by tools/gate_claims.py (its review sheet is the same path with .md).
const W = args && args.workspace
const N = (args && args.round) || 1
const lanes = (args && Array.isArray(args.lanes)) ? args.lanes : []
if (!W) throw new Error('args.workspace must be the absolute path of the workspace folder')
if (!lanes.length) throw new Error('args.lanes must be [{lane, path}] for the gated lanes')

const PROMPT = (l) => `You are an adversarial genealogy verifier. Finders have proposed additions to a family tree; whatever you accept is written into its GEDCOM with its citation, so a wrong one poisons the tree for good. Your job is to REFUTE what cannot be proven. When you cannot confirm, reject.

READ FIRST:
- The review sheet for lane "${l.lane}": ${l.path.replace(/\.json$/, '.md')} (Read tool). Each claim is listed with its sources' quotes and the gate's result. Open the full gated JSON (${l.path}) only for a field the sheet lacks; round 2's skeptics spent half their context printing that JSON.
  The gate already dropped, for free: claims citing no record, facts already in the ledger (same person, event and year), new people already among the named parents' children, and living people. Don't re-check those categories.
  Each source carries a "gate" status:
  quote-found = the gate fetched the page and found the quote; its "excerpt" is the page text around it. Judge identity from the excerpt; re-open the page only if the excerpt does not settle it.
  quote-missing / fetch-failed / not-fetched = re-open the source yourself FIRST; if the quoted text is not there, reject every claim resting on it.
  image = a scan or headstone photo: open it and read it yourself; compare with the finder's transcription.
  "dropped" lists what the gate already threw out and why; do not revive those.
- The dossiers for the tree people involved (facts, spouses, children with years and places):
  python3 -c "import json,sys; d=json.load(open('${W}/research/round${N}/targets.json')); ids=set(sys.argv[1:]); [print(json.dumps(x,ensure_ascii=False,indent=1)) for L in d['lanes'] for a in L['agents'] for x in a if x['id'] in ids]" <ids from "dossier_ids">
Open access only, no logins, no downloads, no contacting anyone. WebFetch loads with ToolSearch "select:WebSearch,WebFetch"; curl is fine. Never print a whole page: extract the lines you need.

FOR EVERY CLAIM:
- Is the quoted text really in the record, and does it say what is claimed?
- Identity: do at least two independent identifiers tie the record to the tree person? Given names repeat heavily in some communities and eras, so a name plus a year alone is not enough.
- Competition: one targeted search for a second candidate that fits as well (another child of that name in that parish and decade, a namesake cousin). If one exists and nothing separates them, reject.
- Consistency: ages at marriage, at death, and of parents at a child's birth; dates against the dossier.
- Contests: accept a "supports" verdict only when the record names this person's parents outright.
- Link audits: accept a confirmation only when the record names both the person and the parents the tree gives (allow spelling variants); a record naming different parents becomes a note saying exactly what it shows.
- Children added from a household or baptism run: accept only children the record itself names as children of that couple, and not already in the tree.
- Never add a living person: born after 1925 with no recorded death.

THEN WRITE THE LEDGER ENTRIES for what survives, in their final corrected form (you are the last reader before the tree):
- sources: only records (never a user tree); keep sid, title, repository, url, kind, detail, quote exactly as verified; correct the quote if the record reads differently.
- people: new ancestors, {tid (keep the finder's tid), given, surname, sex, events: [{tag, date, place, detail?, sids}], note: why this record is this person, naming the identifiers}. Medium confidence goes in the note.
- families: {husb, wife, children: [ids or tids], marriage?: {date, place, sids}, sids}; omit husb or wife when the record does not name them.
- facts: {on, tag, date, place, detail?, spouse?, sids} on existing people (ids like @I46@) or new tids.
- disputes: {child, parent, kind: "disproved" | "duplicate", why} for every parent link a record shows is wrong (the record names different parents) or a duplicate copy, with the record named in why. These cut the link for all future counting and research.
- notes: {on, text} for each contest settled (which pair the record supports, naming it), each tree error a record shows, each duplicate: these never delete anything, they record what to fix in the source tree.
Dates in GEDCOM style. Tags from BIRT CHR BAPM CONF MARR DEAT BURI CENS RESI OCCU EMIG IMMI NATU.

Your final answer is the structured verdict.`

const SOURCE = { type: 'object', properties: {
  sid: { type: 'string' }, title: { type: 'string' }, repository: { type: 'string' }, url: { type: 'string' },
  kind: { type: 'string', enum: ['parish register transcription', 'census transcription', 'record image', 'index', 'headstone photo', 'newspaper', 'secondary compilation'] },
  detail: { type: 'string' }, quote: { type: 'string' },
}, required: ['sid', 'title', 'repository', 'url', 'kind', 'detail', 'quote'] }
const EV = { type: 'object', properties: {
  tag: { type: 'string', enum: ['BIRT', 'CHR', 'BAPM', 'CONF', 'MARR', 'DEAT', 'BURI', 'CENS', 'RESI', 'OCCU', 'EMIG', 'IMMI', 'NATU'] },
  date: { type: 'string' }, place: { type: 'string' }, detail: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } },
}, required: ['tag', 'date', 'place', 'sids'] }
const VERDICT = { type: 'object', properties: {
  verdicts: { type: 'array', items: { type: 'object', properties: {
    ref: { type: 'string', description: 'new_people:<tid> | parent_links:<child> | contests:<child> | facts_on_existing:<on>:<tag>:<date> | tree_fixes:<on>' },
    verdict: { type: 'string', enum: ['accept', 'accept with edit', 'reject'] },
    reason: { type: 'string' }, source_checked: { type: 'string', description: 'what you opened (or the gate excerpt you relied on) and what it says' },
  }, required: ['ref', 'verdict', 'reason', 'source_checked'] } },
  ledger: { type: 'object', properties: {
    sources: { type: 'array', items: SOURCE },
    people: { type: 'array', items: { type: 'object', properties: {
      tid: { type: 'string' }, given: { type: 'string' }, surname: { type: 'string' }, sex: { type: 'string', enum: ['M', 'F', 'U'] },
      events: { type: 'array', items: EV }, note: { type: 'string' },
    }, required: ['tid', 'given', 'surname', 'sex', 'events'] } },
    families: { type: 'array', items: { type: 'object', properties: {
      husb: { type: 'string' }, wife: { type: 'string' }, children: { type: 'array', items: { type: 'string' } },
      marriage: { type: 'object', properties: { date: { type: 'string' }, place: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } } }, required: ['date', 'place', 'sids'] },
      sids: { type: 'array', items: { type: 'string' } },
    }, required: ['children', 'sids'] } },
    facts: { type: 'array', items: { type: 'object', properties: {
      on: { type: 'string' }, tag: EV.properties.tag, date: { type: 'string' }, place: { type: 'string' },
      detail: { type: 'string' }, spouse: { type: 'string' }, sids: { type: 'array', items: { type: 'string' } },
    }, required: ['on', 'tag', 'date', 'place', 'sids'] } },
    notes: { type: 'array', items: { type: 'object', properties: { on: { type: 'string' }, text: { type: 'string' } }, required: ['on', 'text'] } },
  }, required: ['sources', 'people', 'families', 'facts', 'notes'] },
  disputes: { type: 'array', items: { type: 'object', properties: {
    child: { type: 'string', description: '@I…@ id' }, parent: { type: 'string', description: '@I…@ id of the parent the record shows is wrong' },
    kind: { type: 'string', enum: ['disproved', 'duplicate'] }, why: { type: 'string', description: 'the record, and what it says' },
  }, required: ['child', 'parent', 'kind', 'why'] } },
  missed: { type: 'array', items: { type: 'string' }, description: 'competing candidates or contradicting records the finders missed' },
}, required: ['verdicts', 'ledger', 'disputes', 'missed'] }

const run = l =>
  agent(PROMPT(l), { label: `verify:${l.lane}`, phase: 'Verify', model: 'opus', effort: 'high', schema: VERDICT })
    .then(v => ({ lane: l.lane, verdict: v }))
    .catch(() => null)
phase('Verify')
const out = await parallel(lanes.map(l => () => run(l)))
return { results: out.filter(Boolean), missing: lanes.filter(l => !out.find(o => o && o.lane === l.lane && o.verdict)).map(l => l.lane) }
