# Part 8 — The Critic

> Historical design walkthrough. As of 2026-09-18, the active critic has four
> checks: canon assertions, knowledge, possession, and possible commitment payoffs.
> Planner-dependent checks and location-claim checking were removed because they
> had no working production path. See the [current architecture](../architecture.html#critic).


*Part 8 of the Continuum series. [Part 7](07-search.md) finished the query side: we can
look facts up exactly and find passages fuzzily. Everything so far is passive — you have
to ask. This post builds the part that speaks up on its own.*

---

## The goal

Chapter 31 says Aelric drew the silver dagger. The database says Mira has held it since
chapter 12.

We want the system to notice, unprompted, and say something like:

```
FAIL  location_possession
      Character Aelric is shown holding object 'silver dagger', but no active
      possession edge exists entering chapter 31.
      quote: "Aelric drew the silver dagger from his belt."
```

Not "this chapter feels off." A specific claim, a specific contradiction, a specific
quote, and enough evidence to check the verdict yourself.

That last requirement — **you must be able to check the verdict** — turns out to
determine the entire architecture.

---

## Attempt 1: just ask a model

The obvious thing. Paste the previous chapters and the new one, and ask:

```
Here is a novel so far, and a new chapter. Does the new chapter
contradict anything established earlier? List any continuity errors.
```

Let's take it seriously. It is what most people build, and for small inputs it works
surprisingly well. Here's why it doesn't scale to a novel.

**It doesn't fit.** Post 1, first principle. Seventy chapters is 530,000 tokens.

**Its recall is terrible even when it does fit.** Remember the numbers from Post 1: on
benchmarks that test tracking *who knows what* in a narrative, the best model reaches
69% against a human baseline of 92%, and one widely-used model performs at chance. And
knowledge tracking is precisely the error class we most want caught. You'd be delegating
your hardest check to the thing that's worst at it.

**It's non-deterministic.** Run it twice, get two different lists. Which is right? Is the
difference a real finding or sampling noise? You cannot build a regression test against
a component that answers differently each time, which means you cannot tell whether a
change improved it.

**It cannot be audited.** "The chapter contradicts Aelric's established location." Says
who? Based on which earlier chapter? A finding you can't trace is a finding you can't
act on, and after two or three unverifiable false alarms a human stops reading them
entirely. An unreliable alarm is worse than no alarm — it trains people to ignore it.

**It costs a fortune.** A full-context call per chapter, on every draft revision.

**And the deepest problem: it can't tell you what it *didn't* check.** Silence from an
LLM critic means "I found nothing," which could equally mean "I didn't look." There is
no coverage story. You never know if the system is working.

### The reframe

Here's the move that fixes all six at once.

A continuity error is not a *literary* judgement. It is a **contradiction between a claim
the chapter makes and a fact the database holds.** And we've spent four posts building a
database of facts.

So don't ask a model to *judge*. Ask it to *extract claims*, then check the claims with
SQL.

```
   ┌──────────────────────────────────────────────────────────────────┐
   │  ONE LLM CALL:  draft prose ──▶ typed claims                     │
   │                                                                  │
   │   "Aelric drew the silver dagger from his belt."                 │
   │                        ↓                                         │
   │   possession_claim: {character: "Aelric", object: "silver dagger",│
   │                      quote: "Aelric drew the silver dagger…"}    │
   └──────────────────────────────────────────────────────────────────┘
                                   │
   ┌───────────────────────────────▼──────────────────────────────────┐
   │  DETERMINISTIC SQL:  claim  ⋈  database  ──▶ finding             │
   │                                                                  │
   │   SELECT … FROM possesses_edges                                  │
   │    WHERE character_id = Aelric AND object_id = dagger            │
   │      AND since_chapter < 31                                      │
   │      AND (until_chapter IS NULL OR until_chapter >= 31)          │
   │   → no rows → FINDING                                            │
   └──────────────────────────────────────────────────────────────────┘
```

The LLM does reading comprehension — turning prose into structured claims — which it's
genuinely good at. The *judgement* is a SQL query, which is deterministic, cheap,
explainable, and testable.

This is the same trick as Post 4's grammatical anchor and Post 5's typed deltas, and by
now it should feel like a principle:

> **Use the LLM to produce structure. Use code to make decisions about the structure.**

Every finding now comes with the row it contradicts. Every check has a name, so you can
say exactly which checks ran. And the whole thing is testable: seed a known violation,
assert it fires.

---

## The five checks

Continuum's critic runs five typed checks. Each is a pure function of `(database
snapshot, draft claims)` returning a list of findings.

```python
class ContinuityCritic:
    """Run all 5 typed checks against a DraftChapter."""

    def critique(self, draft: DraftChapter) -> CritiqueReport:
        report = CritiqueReport(novel_id=draft.novel_id,
                                chapter_number=draft.chapter_number)
        report.findings.extend(check_entity_mentions(...))
        report.findings.extend(check_location_possession(...))
        report.findings.extend(check_knowledge_state(...))
        report.findings.extend(check_commitments(...))
        report.findings.extend(check_thread_coverage(...))
        return report
```

The input is a `DraftChapter` — the claims, already extracted and already resolved to
database ids:

```python
@dataclass
class DraftChapter:
    novel_id: str
    chapter_number: int
    text: str
    mentions: list[dict]           # [{entity_id, predicate, claimed_value, quote}]
    knowledge_claims: list[dict]   # [{character_id, fact_description, learned_this_chapter}]
    location_claims: list[dict]    # [{character_id, location_id, quote}]
    possession_claims: list[dict]  # [{character_id, object_id, quote}]
    events: list[dict]
    planned_thread_ids: list[str]
    planned_commitment_ids: list[str]
```

Note that claims arrive with **ids**, not names. Name→id resolution happened earlier, and
critically it happened **read-only**:

```python
"""Extract the critic's claim shapes from draft prose, then resolve names to
ids READ-ONLY (unknown names drop the claim; critiquing a draft must never
create entities)."""
```

Checking a draft must not mutate the world. If a draft mentions a character who doesn't
exist, the claim is dropped — we do not create a character as a side effect of *checking*
one. Post 2's reference-only principle, applied to a third place.

### Severity

```python
class Severity(str, Enum):
    FAIL = "FAIL"  # blocks chapter commit
    WARN = "WARN"  # note for next chapter / author
    INFO = "INFO"  # observation

@property
def passed(self) -> bool:
    return len(self.fails) == 0
```

A report passes if and only if there are no FAILs. WARNs don't block; they're notes.

Getting the FAIL/WARN split right is more important than it looks, and it's mostly about
**false-positive tolerance**. A check that FAILs must be one where a finding is almost
certainly a real error, because a FAIL is meant to stop you. A check that's right 70% of
the time must WARN, or people learn to ignore the whole system. We'll see both calls
below.

---

### Check 1: entity mentions vs canon

Some facts about a story world shouldn't change: eye colour, a home town, who someone's
sibling is. The `canon_facts` table holds them as subject-predicate-value triples,
extracted by pass 13:

```sql
CREATE TABLE canon_facts (
    novel_id          UUID,
    kind              TEXT,          -- physical | relational | world_rule | backstory
    subject_entity_id UUID,
    predicate         TEXT,          -- 'eye_color', 'home_town', 'sibling_of'
    value             TEXT,
    source_chapter    INTEGER,
    confidence        FLOAT,
    locked            BOOLEAN DEFAULT false,
    UNIQUE(novel_id, subject_entity_id, predicate)
);
```

That `locked` bit is a human control. Lock "Aelric's eye colour is grey" and a
contradiction becomes a FAIL. Leave it unlocked and it's a WARN.

The check:

```python
for m in mentions:
    canonical = fact_index.get((str(m["entity_id"]), m["predicate"]))
    if canonical is None:
        continue                                  # no canon on this predicate
    if _canon_equivalent(m["claimed_value"], str(canonical["value"])):
        continue                                  # agrees
    sev = Severity.FAIL if canonical["locked"] else Severity.WARN
    findings.append(Finding(
        check="entity_mention",
        severity=sev,
        message=(f"Mention contradicts {'locked ' if canonical['locked'] else ''}"
                 f"canon fact for entity {eid} ({pred}): "
                 f"draft says '{claimed}', canon says '{canonical['value']}' "
                 f"(set in chapter {canonical.get('source_chapter')})."),
        quote=m.get("quote"),
        suggested_fix=(f"Change '{claimed}' to '{canonical['value']}' "
                       f"or update canon (if facts have evolved and the fact is unlocked)."),
        context={...},
    ))
```

Look at what that finding contains: the claimed value, the canonical value, **the chapter
the canonical value came from**, the quote from the draft, and a suggested fix. A human
can adjudicate it in five seconds without opening the manuscript.

This check had two bugs, both instructive.

#### Bug: the check compared against its own output

On the ingestion path, the critic runs *after* the chapter is persisted. Which means
`persist_canon_facts()` has already written **this chapter's** canon facts.

So the check would:

1. Read the chapter's claim: *"Aelric's eyes are amber."*
2. Query canon for `(Aelric, eye_color)`.
3. Get back... `amber`. The row it wrote thirty milliseconds ago.
4. They match. No finding.

The check could never disagree with itself. It was structurally incapable of firing on
the ingestion path, and it did so silently — a passing check and a check that can't fail
look identical from outside.

The fix is one predicate:

```sql
-- Compare against canon established BEFORE this chapter. On the ingestion
-- spine persist_canon_facts has already written this chapter's own facts by
-- the time the critique runs, so without this bound every mention is
-- compared against the row it just produced and the check can never
-- disagree with itself.
AND (%(chapter)s::int IS NULL OR source_chapter IS NULL OR source_chapter < %(chapter)s::int)
```

`source_chapter < chapter_number`. Strictly earlier. Now chapter 31's claims are checked
against what the book established in chapters 1–30, which is what "contradicts prior
canon" means.

The general shape of this bug — **a validator that runs after the thing it validates has
already been written, so it validates its own output** — is worth watching for anywhere
you have a write-then-check pipeline.

#### Bug: it failed a chapter over a hyphen

The comparison was exact string equality. So:

```
canon: "storm-grey"
draft: "storm grey"
       ──▶ FAIL. Chapter blocked.
```

That is not a continuity error. That is a hyphen.

And remember this check emits FAIL, the severity that's supposed to stop you. Every
false FAIL costs credibility that is very hard to earn back.

```python
def _canon_equivalent(a: str, b: str) -> bool:
    """Compare two canon values for practical equality.

    This check emits Severity.FAIL, so exact string equality made it fail a
    chapter on punctuation alone — canon "storm-grey" versus a draft's "storm
    grey" is not a continuity violation. Fold separators and surrounding
    punctuation before comparing.
    """
    def fold(value: str) -> str:
        collapsed = re.sub(r"[-_/]+", " ", normalize_text(value))
        return " ".join(re.sub(r"[^\w\s]", "", collapsed).split())
    return fold(a) == fold(b)
```

Separators (`-`, `_`, `/`) become spaces; remaining punctuation is stripped; whitespace
collapses. `"storm-grey"`, `"storm grey"`, and `"Storm/Grey"` all fold to `"storm grey"`.

Note the restraint. It doesn't do stemming, or synonyms, or fuzzy matching. Those would
start accepting "slate grey" as equal to "storm grey", which *is* a continuity error. The
folding is exactly wide enough to cover typographic variation and no wider. **The
severity of the check bounds how loose the comparison is allowed to be** — same principle
as Post 4's per-type evidence rules.

---

### Check 2: location and possession

Two related checks in one module.

**Two places at once.** If the draft asserts a character is in more than one distinct
location, without a transition:

```python
for cid, claims in by_char.items():
    distinct_locs = {str(c["location_id"]) for c in claims}
    if len(distinct_locs) > 1:
        findings.append(Finding(
            check="location_possession",
            severity=Severity.FAIL,
            message=(f"Character {cid} is asserted in {len(distinct_locs)} "
                     f"locations within the same chapter without a transition event."),
            quote=claims[0].get("quote"),
            context={"character_id": cid, "locations": sorted(distinct_locs)},
        ))
```

**Holding something they don't have:**

```sql
-- Possession edges active entering this chapter: replay closes an edge
-- at the loss chapter, so an edge with until_chapter = N-1 (lost last
-- chapter) is NOT held entering chapter N — require until >= N.
SELECT character_id, object_id, since_chapter, until_chapter
  FROM possesses_edges
 WHERE character_id = ANY(%s::uuid[]) AND object_id = ANY(%s::uuid[])
   AND since_chapter < %s
   AND (until_chapter IS NULL OR until_chapter >= %s)
```

That comment is an inclusive/exclusive boundary being pinned down. Post 5 established
that `until_chapter` is *inclusive* — an edge with `until_chapter = 30` means the
character held it through chapter 30. So entering chapter 31, they no longer do. The
predicate must be `>= 31`, not `>= 30`. Off by one here and every object flips from
"lost" to "held" for exactly one chapter.

And this one WARNs rather than FAILs, for a reason spelled out in the message itself:

```python
message=(f"Character {cid} is shown holding object {oid}, but "
         f"no active possession edge exists entering chapter "
         f"{chapter_number}. If the chapter introduces the pickup "
         f"this is fine; otherwise flag."),
```

A character picking something up *for the first time* legitimately has no prior edge. The
check can't distinguish "picked it up in this chapter" from "was never given it" without
understanding the prose — which is exactly what we've forbidden it from doing. So it
warns and tells the human what to look at. Honest about its own limits, in the finding
text.

#### Bug: it failed everyone who walked anywhere

The claims extractor emitted one location claim per assertion in the draft. A chapter
where Mira leaves the archive and walks to the docks produces two claims: archive, docks.

The check counts distinct locations, sees two, and FAILs.

**Every character who moved during a chapter failed.** Which is most characters, in most
chapters. And this ran on the MCP pre-save gate — the one path where a FAIL actually
blocks something.

The bug is a modelling error, not a coding one. "Two location claims" was being read as
"asserted to be in two places simultaneously", when it actually meant "moved from one
place to another", which is the most normal thing in fiction.

The fix collapses claims to one per character — the *last* location:

```python
# One claim per character: the LAST location the draft puts them in.
# check_location_possession FAILs when a character has >1 distinct location
# claim in a chapter, so emitting one claim per LLM assertion failed every
# character who simply walked somewhere — which is most chapters. The
# claim that matters is where the character ends up, since that is what
# gets compared against prior state.
#
# Keyed on the RESOLVED character_id, not the surface name: two spellings
# that resolve to one character (an alias the resolver already knows) must
# collapse together, or the check FAILs on a name variant.
location_by_char: dict[str, dict] = {}
for c in raw_claims.get("location_claims", []):
    cid = _char(str(c.get("character_name", "")))
    loc = _find("location", str(c.get("location_name", "")))
    if cid is None or loc is None:
        continue
    location_by_char[str(cid)] = {"character_id": cid, "location_id": loc[0],
                                  "quote": c.get("quote")}
location_claims = list(location_by_char.values())
```

The second half of that comment is the subtle part. Key the collapse on the **resolved
character id**, not the name string. Otherwise a draft that says "Mira" in one paragraph
and "Mira Solen" in another produces two dictionary keys, two surviving claims, two
locations — and the check FAILs on an alias the resolver already knew about.

That's Post 4's work being consumed correctly. Identity resolution isn't a preprocessing
step you do once; it's a property every downstream component has to actually *use*.

---

### Check 3: knowledge state

The one the benchmarks say is hardest, and the one this architecture is best at.

`knows_edges` records who learned what, when, and how:

```sql
CREATE TABLE knows_edges (
    character_id     UUID NOT NULL REFERENCES characters(id),
    fact_description TEXT NOT NULL,
    learned_chapter  INTEGER NOT NULL,
    source_event_id  UUID,
    source_type      TEXT CHECK (source_type IN
                       ('dialogue','observation','inference','witnessed','told','assumed')),
    certainty        FLOAT DEFAULT 1.0,
    shared_with      UUID[] DEFAULT '{}',
    superseded_by_id UUID REFERENCES knows_edges(id)
);
```

The check asks: for every claim that a character *acts on* some knowledge, do they have
an active `knows_edge` for it from before this chapter?

```python
rows = db.fetchall("""
    SELECT character_id, fact_description, learned_chapter, source_type
      FROM knows_edges
     WHERE character_id = ANY(%s::uuid[])
       AND learned_chapter < %s
       AND superseded_by_id IS NULL
""", (char_ids, chapter_number), dict_rows=True)

for k in knowledge_claims:
    if k.get("learned_this_chapter", True):
        continue                      # acquired in this draft — fine
    if not _fact_is_known(fact, known.get(cid, set())):
        findings.append(Finding(
            check="knowledge_state",
            severity=Severity.FAIL,
            message=(f"Character {cid} acts on knowledge they have not been "
                     f"shown to acquire: '{fact}'. No prior knows_edges row, "
                     f"no learn-this-chapter event."),
            quote=k.get("quote"),
            suggested_fix=("Either add a prior scene where the character learns this, "
                           "or rephrase the passage so the knowledge is acquired here."),
        ))
```

#### The matching problem

Two independent LLM calls describe the same fact. The ingest pass wrote:

> *"the iron compass points to the sunken vault"*

The draft claims extractor wrote:

> *"the compass indicates the location of the sunken vault"*

Same fact. Zero string equality.

Exact matching gives false FAILs on every paraphrase. Embedding similarity would be
better but costs a model call per comparison inside a check that's supposed to be cheap
and deterministic. The compromise is content-word overlap:

```python
def words_overlap(a: str, b: str, *, min_words: int, ratio: float) -> set[str]:
    """Shared fuzzy match between two normalize_text'd prose fragments: when
    the content-word overlap reaches max(min_words, ratio * |content(a)|),
    return the overlapping words (the evidence); otherwise return an empty set."""
    a_words = content_words(a)
    b_words = content_words(b)
    overlap = a_words & b_words
    if len(overlap) >= max(min_words, int(len(a_words) * ratio)):
        return overlap
    return set()
```

For the knowledge check, `min_words=2, ratio=0.5`: at least half the claim's content
words, and at least two.

```
claim:  {compass, indicates, location, sunken, vault}     (5 content words)
known:  {iron, compass, points, sunken, vault}
overlap: {compass, sunken, vault}                          3 ≥ max(2, 2.5) ✓  match
```

Note it **returns the overlapping words**, not a boolean. Those words go into the
finding's evidence, so a human can see *why* two phrasings were considered the same. A
matcher that shows its work.

#### Bug: function words matched everything

The first version compared all words, not just content words. Which produces:

```
"The blade will return to the sea"   ∩   "the ship will sail to the harbor"
   = {the, will, to}                      3 words → match!
```

Two completely unrelated sentences, "matched", with `{the, will, to}` cited as the
evidence. So a stopword list was added:

```python
# Function words carry no evidence of subject-matter overlap. Without this,
# "The blade will return to the sea" and "the ship will sail to the harbor"
# match on {the, will, to} alone and the finding cites those three words as
# its evidence.
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those of in on at to
    for from by with without into onto over under again further once is are
    was were be been being am do does did doing have has had having will would
    shall should can could may might must it its he she they them his her
    their there here as not no nor so too very just about after before""".split()
)
```

The comment is doing something clever: it names the *symptom* that would tell you the
stopword list was removed. The evidence field would fill with `{the, will, to}`. Future
readers get a diagnostic, not just a rule.

#### Bug: the `source_type` allowlist

The check needs to know whether a claim is knowledge the character is *acquiring* here
(fine) or *acting on* from before (checkable). The first version inferred it from
`source_type`, exempting a hardcoded list — `observation`, `witnessed`, `told`.

Which means `inference` and `assumed` were **not** exempt. But deducing something is a
perfectly good way to learn it. A character who works out the killer's identity in
chapter 20 was flagged for "acting on knowledge they have not been shown to acquire" —
in the very chapter where they acquire it.

The fix: make it an explicit field the claims extractor fills.

```python
"learned_this_chapter": "boolean — true only if the character acquires this fact
                         within this draft; false if they act on knowledge from before"
```

```python
# learned_this_chapter is the authoritative signal that the character
# acquires this fact within this draft, regardless of source_type —
# inference/assumed are valid ways to learn something too, and gating
# the exemption on a source_type allowlist FAILed those spuriously.
if k.get("learned_this_chapter", True):
    continue
```

And a defensive coercion where the claim is built:

```python
learned = k.get("learned_this_chapter")
knowledge_claims.append({
    ...
    # Lenient when the model omitted the flag; a non-bool must not
    # silently disable the knowledge check via truthiness.
    "learned_this_chapter": learned if isinstance(learned, bool) else True,
})
```

Why `isinstance` rather than plain truthiness? Because a model might emit the *string*
`"false"`, which is truthy in Python. `if k.get("learned_this_chapter")` would treat
`"false"` as `True` and skip the check. Explicit type checking at the LLM boundary is
not paranoia; it's the boundary where types are guarantees you don't have.

---

### Check 4: commitments (Chekhov's gun)

A **commitment** is a promise the story makes: a locked door, a prophecy, an ominous
warning. The formalism comes from a 2026 paper (CFPG) that models narrative debt as
(Foreshadow, Trigger, Payoff) triples, which map straight onto a table:

```sql
CREATE TABLE commitments (
    novel_id           UUID,
    foreshadow_text    TEXT NOT NULL,
    foreshadow_chapter INTEGER NOT NULL,
    trigger_predicate  JSONB,
    payoff_text        TEXT,
    payoff_chapter     INTEGER,
    status             TEXT CHECK (status IN ('pending','satisfied','broken','abandoned')),
    weight             FLOAT DEFAULT 1.0,
    related_entity_ids UUID[]
);
```

The half of this check that runs looks for **accidental payoffs** — draft events that
look like they resolve a pending commitment nobody planned to resolve:

```sql
-- foreshadow_chapter <= this chapter: a commitment planted in a LATER
-- chapter does not exist yet in story time, so a draft event cannot
-- accidentally pay it off. Without this bound, re-processing an early
-- chapter warns about foreshadows from the end of the book.
SELECT id, foreshadow_text
  FROM commitments
 WHERE novel_id = %s AND status = 'pending'
   AND (foreshadow_chapter IS NULL OR foreshadow_chapter <= %s)
```

That's the chapter-cutoff discipline showing up *inside* the critic. Re-critique chapter
3 of a finished novel and, without the bound, you'd get warnings about foreshadowing from
chapter 60 — which hasn't happened yet from chapter 3's point of view. Spoiler-safety
isn't only a read-layer concern.

Matching uses the same `words_overlap`, with looser thresholds (`min_words=3, ratio=0.4`)
because foreshadow text and event descriptions are longer and share less vocabulary. And
it emits WARN, not FAIL — an accidental payoff is a judgement call about craft, not a
factual contradiction.

The other half of this check — "did the foreshadows you *planned* to plant actually get
planted?" — gates on `planned_commitment_ids`, which every current caller passes as `[]`.
More on that in a moment.

---

### Check 5: thread coverage

```python
def check_thread_coverage(db, planned_thread_ids, events):
    if not planned_thread_ids:
        return []
    ...
```

First line: if nothing was planned, return nothing.

Every current caller passes `planned_thread_ids=[]`.

**This check has never produced a finding, and cannot.** Let's talk about why that's not
simply a bug.

---

## The audit: three of five checks couldn't fire

August 2026. Someone traced each check end-to-end from `pipeline.py` rather than reading
it in isolation. The result was uncomfortable.

The critic gets its claims from an adapter. On the ingestion path, that adapter reused
the chapter's already-extracted passes rather than paying for a second LLM call — sensible,
frugal. But look at what it filled in:

```python
def build_draft_from_extraction(db, *, novel_id, chapter_number, text, extracted):
    raw_claims = {
        "mentions": [...canon facts...],
        "knowledge_claims": [
            {
                "character_name": l.get("character_name"),
                "fact_description": l.get("fact_description"),
                "learned_this_chapter": True,          # ← hardcoded
                ...
            }
            for l in extracted.get("learnings", [])
        ],
        "location_claims": [...collapsed to one per character...],
        "possession_claims": [],                        # ← hardcoded empty
        "events": [...],
    }
    return build_draft_chapter(db, ..., planned_thread_ids=[],       # ← hardcoded
                                        planned_commitment_ids=[])   # ← hardcoded
```

Trace each hardcode to the check it disables:

| Hardcoded value | Check it silences | How |
|---|---|---|
| `learned_this_chapter: True` | `knowledge_state` | every claim hits the `continue` on line 2 |
| `possession_claims: []` | `location_possession` (possession half) | nothing to check |
| `planned_thread_ids: []` | `thread_coverage` | returns `[]` on its first line |
| `planned_commitment_ids: []` | `commitments` (planned half) | the block never runs |

**Three of five checks were structurally incapable of emitting a finding on the ingestion
path.** And `critique_reports.passed` — the pass/fail badge shown in the UI — was
therefore *effectively a constant `true`*.

The most uncomfortable detail: **every one of those hardcodes was a defensible fix for a
real false positive.**

- `learned_this_chapter: True` — the extraction pass records what characters *learn*.
  Every entry is by definition learned this chapter. Marking them otherwise would have
  FAILed every single one.
- `possession_claims: []` — mapping possession *gains* to possession *claims* made the
  check WARN on every acquisition, since a fresh pickup has no prior edge by definition.
- `planned_*_ids: []` — nothing in an analyzer produces a plan. There is genuinely no
  plan to check against.

Each was locally correct. Cumulatively they removed the signal along with the noise, and
nothing in the codebase held the *aggregate* view. The tests were green: each check has
unit tests that pass claims in directly and assert it fires correctly. The checks work.
Nothing was ever *feeding* them.

> **Unit tests verify a component works. They cannot verify it is reachable.** If your
> tests construct the input by hand, you have tested the component and not the system.
> "Does anything in production ever produce this input?" is a separate question that
> needs a separate answer.

### The fix that was already sitting there

The missing inputs didn't need a new extraction pass. There was already a second adapter
— `extract_draft_claims` — built for the MCP path, where an agent asks "check this draft
before I save it." It reads prose directly and produces exactly the missing fields:

```python
_CLAIMS_SCHEMA = {
    "mentions": [...],
    "knowledge_claims": [{
        "character_name": "string",
        "fact_description": "string",
        "source_type": "dialogue|observation|inference|witnessed|told|assumed",
        "learned_this_chapter": "boolean — true only if the character acquires this fact "
                                "within this draft; false if they act on knowledge from before",
        "quote": "string",
    }],
    "location_claims": [{"character_name": "string", "location_name": "string", "quote": "string"}],
    "possession_claims": [{"character_name": "string", "object_name": "string", "quote": "string"}],
    "events": [...],
}
```

Cost: **one LLM call per chapter** — not per chunk, per chapter. Against thirteen passes
per chunk, that's a rounding error.

So the ingestion path now uses it by default, behind a setting:

```python
critique_claims: str = field(
    default_factory=lambda: os.getenv("CRITIQUE_CLAIMS", "extract").strip().lower()
)
```

```
"extract" — one extra LLM call per chapter against the chapter text,
            producing real learned_this_chapter flags and possession
            claims. All five checks can fire.
"reuse"   — reuse the chapter's already-extracted passes, no extra
            call. Cheaper, but the spine has no "acts on prior
            knowledge" or possession claim to offer, so the knowledge
            and location/possession checks cannot produce a finding.
```

And a third case with a sharp edge:

```python
# Mock runs always fall back to "reuse": extract_draft_claims returns empty
# claims without a real LLM, which would make the critic pass vacuously.
```

Think about why that matters. In mock mode, `extract_draft_claims` returns empty lists.
An empty draft has no claims. No claims means no findings. No findings means
`passed = True`.

**The critic would pass everything, confidently, in every offline test run.** Which would
make the critic eval meaningless — it'd be measuring a component that always says yes.
So mock mode deliberately takes the *cheaper, less capable* path, because a check that's
honestly quiet beats a check that's vacuously green.

Same idea, one level up, in `extract_draft_claims` itself:

```python
except Exception as exc:
    # Empty claims would make the critic pass vacuously and let an
    # unchecked draft through the ingest gate — fail loudly instead.
    raise RuntimeError(f"draft_claims: extraction failed: {exc}") from exc
if not data:
    raise RuntimeError("draft_claims: extraction returned unparseable JSON")
```

Most of this codebase catches exceptions and degrades. This one raises. The difference is
that here, degrading produces a *false assurance* — a green pass badge on a chapter
nobody checked. A loud failure is strictly better than a quiet false negative.

> **Ask of every fallback: does the degraded state look like success?** If yes, it must
> not be silent.

### Where the checks stand now

| Check | Reads | Spine (`extract`, default) | Spine (`reuse`) | MCP pre-save |
|---|---|---|---|---|
| `entity_mention` | `canon_facts` | **Fires** | Fires | Fires |
| `knowledge_state` | `knows_edges` | **Fires** | No | Fires |
| `location_possession` | `possesses_edges` | **Fires** | No | Fires |
| `commitments` | `commitments` | Partly (accidental-payoff half only) | Partly | Partly |
| `thread_coverage` | `thread_events` | **No** | No | No |

`thread_coverage` remains dead. And the project's notes are careful about the
distinction:

> *This is dormancy by design, not breakage: an analyzer-only system produces no plan to
> check a draft against.*

The check exists because an earlier version of the project *did* have a planner — a
component that decided in advance which threads a chapter should advance. That planner
was deleted (Post 11). The check survives, correct and unreachable.

With a diagnostic attached, because there's a latent bug in it too:

> *Note `thread_coverage` also builds `event_ids` from an `id` field neither adapter
> attaches, so if a planner were wired up today it would warn on every thread
> unconditionally — fix that at the same time.*

And an explicit exit condition:

> *Done when: either a planner supplies the ids and the `id` plumbing is fixed, or both
> branches are deleted rather than left as latent false positives.*

Not "TODO: fix thread coverage." A statement of what is true, why, what would have to
change, and the trap waiting for whoever changes it. Dead code with a maintained
explanation is a defensible state; dead code that nobody has audited is not.

---

## Where the critic sits

Two decisions about *when* it runs, both non-obvious.

### It doesn't block saving

`analyze_chapter` runs the critic as phase 5, after the chapter is committed, and a
failure doesn't roll anything back:

```python
want_critic = settings.critic_enabled if run_critic is None else run_critic
if want_critic:
    try:
        critique_summary = critique_chapter(client, ...)
    except Exception:
        logger.exception("critique failed for chapter %s of novel %s; re-run "
                         "pipeline.critic.cli", chapter_number, novel_id)
```

You might expect the opposite — surely a chapter that fails continuity shouldn't be
saved? Three reasons it works this way.

**The chapter is data, not a claim of correctness.** Storing chapter 31 means "chapter 31
exists and here's what's in it." Whether it contains an error is a separate fact, and the
system records both.

**Errors are often intentional.** A character *lying* about where they were will trip the
location check. A first-person narrator who is mistaken will trip the knowledge check.
Refusing to store the chapter would make the system unusable for exactly the kind of
fiction that most needs it.

**Refusing to save loses information.** If the chapter isn't stored, the critique has
nowhere to live either. You'd throw away both the chapter and the finding.

So: save always, critique always, report the result. The finding is attached to the
chapter for a human to adjudicate.

For agents that *want* a gate, there's a separate tool that critiques without saving:

```python
@mcp.tool()
def check_continuity(novel_id: str, chapter_number: int, draft_text: str) -> Any:
    """Run the continuity critic on a draft WITHOUT saving it. Returns
    passed/fails/warns with quotes and suggested fixes. Always run this
    before save_chapter."""
```

Note "Always run this before save_chapter" is *advice in a docstring*, not enforcement.
Nothing makes a caller check before saving. That's stated plainly rather than
oversold:

> *"Validate before saving" is a separate step, not automatic. Nothing forces a caller to
> check before saving — the capability exists but isn't a gate.*

### It's optional and separately re-runnable

Later work made the critic fully detachable:

```python
critic_enabled: bool = field(default_factory=lambda: _bool_env("CRITIC_ENABLED", True))
```

with a CLI to run it after the fact:

```python
"""Run the continuity critic over already-ingested chapters.

The critic is decoupled from ingestion, so this is how you critique a chapter
that was ingested with CRITIC_ENABLED=false, re-run one whose critique failed
transiently, or re-judge a back catalogue after changing a check.

Re-running replaces the chapter's previous report (persist_critique deletes
and rewrites), so it is safe to run repeatedly.

    python -m pipeline.critic.cli --novel-id <uuid>               # every chapter
    python -m pipeline.critic.cli --novel-id <uuid> --chapter 12  # just chapter 12
"""
```

That third use case is the valuable one: **change a check, re-judge the entire back
catalogue** without re-paying for extraction. It's the same idea as event sourcing from
Post 5 — because the expensive derived data (`knows_edges`, `possesses_edges`,
`canon_facts`) is stored, the cheap judgement over it can be recomputed at will.

It's idempotent by the crudest possible means:

```python
def persist_critique(db, *, chapter_id, report):
    with db.transaction() as cur:
        cur.execute("DELETE FROM critique_reports WHERE chapter_id = %s", (chapter_id,))
        cur.execute("""INSERT INTO critique_reports (chapter_id, passed, stats)
                       VALUES (%s, %s, %s::jsonb) RETURNING id""", ...)
```

Delete then insert, in one transaction. The `critique_findings` rows cascade away with
their report. Same delete-and-rebuild philosophy as the state materializer, for the same
reason: it's obviously correct.

And the batch CLI is deliberately forgiving:

```python
except Exception as exc:
    # Keep going: one bad chapter should not abandon the rest of a
    # back-catalogue run. The non-zero exit code reports it.
    failed += 1
    results.append({"chapter": number, "error": str(exc)})
```

Continue on error, count failures, exit non-zero. A batch job over 70 chapters that dies
on chapter 3 is much less useful than one that does 69 and tells you which one broke.

---

## Does it work? (Partly, and the eval knows it)

There's an eval. It seeds four known violations plus one clean draft against a fixture
novel:

```python
two_places = DraftChapter(
    text="Aria haggled at Fogmere Docks at noon; at the same hour she read in the Sable Archive.",
    location_claims=[
        {"character_id": char_a, "location_id": loc_a, "quote": "Aria haggled at Fogmere Docks at noon"},
        {"character_id": char_a, "location_id": loc_b, "quote": "at the same hour she read in the Sable Archive"},
    ],
)

unknown_knowledge = DraftChapter(
    text="Borin recalled that the iron compass points to the sunken vault.",
    knowledge_claims=[{"character_id": char_b,
                       "fact_description": "the iron compass points to the sunken vault",
                       "learned_this_chapter": False, ...}],
)

wrong_possessor = DraftChapter(
    text="Borin turned the Iron Compass over in his hands, as he had for years.",
    possession_claims=[{"character_id": char_b, "object_id": obj_id, ...}],
)

canon_contradiction = DraftChapter(
    text="Aria's amber eyes caught the lamplight.",
    mentions=[{"entity_id": char_a_eid, "predicate": "eye_color", "claimed_value": "amber", ...}],
)

clean = DraftChapter(
    text="Aria stood alone in the Sable Archive, compass in hand, sure of its secret.",
    # correct location, correct possessor, knowledge she genuinely has
)
```

The assertions:

```python
assert metrics["recall"] == 1.0            # every seeded violation is caught
assert metrics["false_positives"] == 0     # the clean draft produces nothing
assert metrics["precision"] == 1.0
```

Exact 1.0 on both, deterministically — no LLM in this loop, so the numbers are exact
rather than approximate.

And now the honesty, from the project's own open-gaps list:

> ***Critic precision is not a precision measurement.*** *`critic_eval`'s precision cannot
> fall below 0.8 by construction. It scores 4 positive cases and 1 negative, and only the
> single clean draft can produce a false positive; a positive case where the critic emits
> findings from the **wrong** check counts as a false negative, never a false positive.
> So a degenerate "flag everything" critic scores precision 0.8 / recall 1.0.*

Work through that. Five cases: four should flag, one shouldn't. A critic that flags
*everything* gets all four positives (recall 1.0) and one false positive (the clean
draft), giving precision 4/5 = 0.8. So the metric's floor is 0.8 no matter how bad the
critic is. It cannot distinguish "correct" from "paranoid."

That's a real limitation of a real metric, written down in the project's own
documentation, with an exit criterion:

> *Done when: scoring is per finding — each emitted finding a TP if it matches a seeded
> violation on `(check, character, quote)` and an FP otherwise — against a negative set
> materially larger than one clean draft.*

And a reason it hasn't been fixed yet:

> *Deliberately left until after the 2026-08-17 `CRITIQUE_CLAIMS` change, so the rescoring
> is done against checks that can actually fire rather than tuned against three inert
> ones.*

Which is exactly right. Rebuilding the metric while three of the five checks were
structurally silent would have produced a metric tuned to measure silence.

---

## What the critic is

Five deterministic SQL checks over structured claims, with an LLM used only to turn prose
into claims. Every finding cites the row it contradicts and quotes the text that
triggered it. Three of five checks fire in production; one fires partly; one is dormant
by design and documented as such.

It won't catch a plot hole, a boring chapter, or a character acting out of character.
Those are literary judgements and this is not a literary system.

It will catch: a character in two places at once, a character holding an object they gave
away nineteen chapters ago, a character knowing something they were never told, and a
sentence that contradicts a locked fact — with the contradicting row attached, every
time, for the same input.

That's a smaller promise than "AI reviews your novel." It is a promise that can actually
be kept, and — more importantly — one whose failures you can *see*.

---

## Where we are

Seven posts of machinery: extraction, identity, time, world-modelling, search, checking.
All of it lives
behind a Python API that nobody can use.

Two audiences need it. A human wants to browse a wiki of their novel with a slider that
says "show me the world as of chapter 12." An AI agent writing chapter 31 wants the same
data through a tool interface, and must **never** see chapter 32.

Those are the same query with the same constraint. Building them twice is how they drift
apart — and the project did build them twice before consolidating.

---

*Next: [Part 9 — Two Audiences, One Read Layer](09-serving-it.md) — the cutoff contract,
a test that walks the codebase and fails the build if you forget it, and what it takes to
hand a database to an autonomous agent without leaking the ending.*
