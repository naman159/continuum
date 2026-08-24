# Part 5 — Time Travel Without Snapshots

*Part 5 of the Continuum series. [Part 4](04-identity.md) made names resolve to stable
entity ids. Now we can finally attack the question the series opened with: does Aelric
still have the dagger in chapter 31?*

---

## The question, one more time

```
chapter 4:   Aelric takes a silver dagger from a dead soldier.
chapter 12:  Aelric gives the dagger to Mira for safekeeping.
chapter 31:  Aelric draws the dagger.
```

Chapter 31 is wrong. We want a system that says so, automatically, with certainty.

Notice what that requires. Not "search the book for dagger" — we did that in Post 1 and
got 94 hits including both of the first two lines, with no way to tell which one is
current. We need to answer:

> **Who holds object X at chapter N?**

for any X and any N, exactly, with no LLM in the loop, in milliseconds.

That's a data-modelling problem, and it has a beautiful answer that a lot of systems
outside this domain have converged on independently. Let's derive it rather than
announce it.

---

## Attempt 1: store the answer (and why that's a trap)

The MVP's `character_states` table stores, for each character and each chapter, what was
true then:

```sql
CREATE TABLE character_states (
    character_id    UUID,
    chapter_id      UUID,
    location_id     UUID,
    emotional_state TEXT,
    goals           TEXT,
    knowledge       TEXT[],
    physical_state  TEXT
);
```

Add a `possessions UUID[]` column and you can answer our question directly: look up
Aelric's row for chapter 31, check whether the dagger id is in the array. One query.
Done.

Now let's break it, three ways.

### Break 1: the storage cost is quadratic in the wrong thing

To answer "as of chapter N" for *every* N, you need a row per character per chapter.

```
70 chapters × 40 characters = 2,800 rows
```

That's nothing — 2,800 rows is trivial. So this isn't really a storage problem. But look
at *what's in* those rows. If Aelric doesn't move for twenty chapters, twenty rows each
say "Aelric is in Fogmere". The same fact, twenty times.

The real cost isn't disk. It's that **you have twenty copies of a fact and no single
place to correct it.** Discover that chapter 6's location extraction was wrong, and you
have to fix chapter 6's row *and* the nineteen carried-forward copies downstream. Miss
one and the database contradicts itself.

### Break 2: absence of information is indistinguishable from information

Chapter 7 is about someone else. Aelric appears in one line of dialogue. The extractor
has nothing to say about where he is.

So what goes in `location_id`?

- `NULL` means "unknown" — but we're not ignorant, we know exactly where he is, he's
  where chapter 6 left him.
- Carrying chapter 6's value forward means the row now asserts something the chapter
  never said. If chapter 6 was wrong, chapter 7 is now confidently wrong too, and
  there's nothing in the row marking it as inferred rather than observed.

You cannot tell, from the table, which values are *observations* and which are
*inferences*. Every fact looks equally solid. That's a bad property for a system whose
entire job is being trustworthy about facts.

### Break 3 — the fatal one: it's a conclusion, not a derivation

Here's the deepest problem, and it applies far beyond this project.

A row saying `Aelric.location = Fogmere` is an **answer**. Somebody, at some point, did
some reasoning and wrote down the result. That reasoning is gone.

Which means:

- **You can't re-derive it.** Improve your extraction prompts and you have no way to
  regenerate this row except by re-reading the chapter — which you can do here, because
  we kept `raw_text`, but only at full LLM cost.
- **You can't audit it.** Why does the system think he's in Fogmere? Silence.
- **You can't fix it partially.** Discover a bug in how "arrived at" is interpreted and
  every row in the table is suspect, with no way to identify which ones the bug touched.
- **It goes stale.** Any change anywhere upstream invalidates it, silently.

The general form:

> **Storing derived state without storing what it was derived from is a one-way
> function. You can never get back.**

So what's the alternative? Store the derivation. Store the *changes*, and compute the
answer on demand.

---

## Event sourcing, from first principles

You already understand this pattern; you've just probably met it under a different name.

Think about a bank account. There are two ways to store your balance.

**Way A — store the balance:**

```
accounts
┌─────────┬─────────┐
│ user_id │ balance │
├─────────┼─────────┤
│ 42      │  £315   │
└─────────┴─────────┘
```

Deposit £50: `UPDATE accounts SET balance = balance + 50`. Simple. Fast.

And *no bank on earth does this*, because the moment there's a dispute — "I never
authorised that" — you have nothing. No history, no audit trail, no way to answer "what
was my balance on March 3rd."

**Way B — store the transactions:**

```
transactions
┌──────┬─────────┬────────┬──────────────────┐
│ id   │ user_id │ amount │ description      │
├──────┼─────────┼────────┼──────────────────┤
│ 1    │ 42      │  +500  │ salary           │
│ 2    │ 42      │  -200  │ rent             │
│ 3    │ 42      │   -35  │ groceries        │
│ 4    │ 42      │   +50  │ deposit          │
└──────┴─────────┴────────┴──────────────────┘
```

The balance is now a *computed* thing: `SELECT sum(amount) WHERE user_id = 42` → £315.

Look at what you get for free:

- **Balance on March 3rd?** Sum the transactions dated on or before March 3rd. Any date,
  any time, no extra storage.
- **Why is it £315?** Here are the four rows that produced it.
- **Found a bug in how interest was computed?** Fix the calculation, recompute. The
  transactions never change.
- **Wrong transaction?** Correct it and recompute the balance. The dependency is
  one-directional and explicit.

This pattern is called **event sourcing**. Two pieces of vocabulary:

- The **event log** is the append-only list of things that happened. It is the *ground
  truth*. You only ever add to it.
- A **projection** (or materialized view) is a derived summary computed from the log —
  the current balance, say. Projections are *disposable*. You can delete them all and
  rebuild from the log.

```
   ┌──────────────────────────────────────┐
   │  EVENT LOG  (append-only, truth)     │
   │  +500 · −200 · −35 · +50             │
   └───────────────┬──────────────────────┘
                   │  replay(up to date D)
                   ▼
   ┌──────────────────────────────────────┐
   │  PROJECTION  (derived, disposable)   │
   │  balance = £315                      │
   └──────────────────────────────────────┘
```

Now map it onto our problem. Substitute "chapter number" for "date":

```
   ┌──────────────────────────────────────────────────────┐
   │  ch4:  Aelric  GAINS  silver dagger                  │
   │  ch12: Aelric  LOSES  silver dagger                  │
   │  ch12: Mira    GAINS  silver dagger                  │
   │  ch14: Mira    MOVES TO  the Saltmarsh               │
   └───────────────────────┬──────────────────────────────┘
                           │  replay(through chapter 31)
                           ▼
   ┌──────────────────────────────────────────────────────┐
   │  Aelric held the dagger from ch4 to ch12             │
   │  Mira has held the dagger since ch12                 │
   │  Mira is in the Saltmarsh                            │
   └──────────────────────────────────────────────────────┘
```

"Does Aelric have the dagger in chapter 31?" Replay the log through chapter 31. Answer:
no, Mira does, since chapter 12. And you can name the chapter that changed it.

Every one of the three breakages above dissolves:

- **Storage**: two rows for the dagger, not seventy.
- **Absence vs information**: the log only contains things the text actually said. If
  chapter 7 said nothing about Aelric's location, there is no chapter-7 entry, and
  replay simply carries the last known value forward — *with the reason attached*.
- **Conclusions vs derivation**: the projection is explicitly derived and explicitly
  disposable.

---

## What goes in the log? (The first version got this badly wrong)

Here's where the project made an instructive mistake, kept it for about six weeks, and
then deleted it.

The first version of replay didn't have a change log at all. It walked the **events**
table — free-text descriptions like *"Aelric took the silver dagger from the dead man's
belt"* — and inferred state changes from the prose using regular expressions:

```python
# Simple verb-based possession heuristics. Order matters: most specific first.
GAIN_VERBS = (
    "took", "takes", "take", "received", "receives", "receive",
    "stole", "steals", "steal", "picked up", "picks up", "pick up",
    "grabbed", "grabs", "grab", "seized", "seizes", "seize",
    "acquired", "acquires", "acquire", "obtained", "obtains", "obtain",
    "found", "finds", "find", "claimed", "claims", "claim",
)

LOSS_VERBS = (
    "gave", "gives", "give", "lost", "loses", "lose",
    "dropped", "drops", "drop", "handed over", "hands over", "hand over",
    "handed", "hands", "hand", "discarded", "discards", "discard",
    "surrendered", "surrenders", "surrender",
    "relinquished", "relinquishes", "relinquish",
)

def _classify_possession(description: str) -> str | None:
    lower = description.lower()
    # Check loss first since "gave up" overlaps with "up"
    for verb in LOSS_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return "loss"
    for verb in GAIN_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return "gain"
    return None
```

And who did the taking?

```python
if possession_kind and involved_objects and involved_characters:
    # Default: the first involved character is the actor.
    actor = involved_characters[0]
```

And emotions?

```python
EMOTIONAL_HINTS = {
    "death": "grieving",      "betrayal": "betrayed",
    "revelation": "shocked",  "battle": "tense",
    "discovery": "curious",   "reunion": "relieved",
    "loss": "grieving",       "victory": "triumphant",
    "defeat": "defeated",     "alliance": "hopeful",
}
```

Let's enumerate the ways this fails, because each one is a general lesson.

**It cannot handle negation.** *"Aelric did not take the dagger."* Contains "take".
Classified as a gain. The regex sees a verb; it does not see the word standing three
characters in front of it.

**It cannot handle failed actions.** *"Aelric tried to take the dagger, but Mira was
faster."* Gain.

**It cannot handle hypotheticals or dialogue.** *"'If I take the dagger,' Aelric said,
'they'll know.'"* Gain.

**It cannot identify the actor.** `involved_characters[0]` is the first name in a list
whose order came from an LLM's output ordering. *"Mira handed the dagger to Aelric"*
might list Mira first or Aelric first. Whoever is first is recorded as the one who
lost/gained it. It is a coin flip whether the transfer runs the right direction.

**Passive voice inverts it entirely.** *"The dagger was taken from Aelric."* "Taken" is a
GAIN verb; Aelric is the only involved character; therefore Aelric gained the dagger he
just lost.

**And the emotional table is simply making things up.** A "death" event does not mean
every character present is grieving. The villain might be delighted. The table encodes
one interpretation of every event type and applies it universally, with no evidence.

Look at the confidence values it assigned itself:

```python
fact = PossessionFact(..., certainty=0.7)   # possession
new_fact = LocationFact(..., certainty=0.8) # location
```

Hardcoded. Not derived from anything. The system was recording a made-up confidence
score for a guess made by a verb list.

### The root cause

Here's the thing worth extracting from that wreckage. The mistake was not "the regexes
were too simple." Better regexes wouldn't fix it. A dependency parser wouldn't fix it
either, not really.

The mistake was **doing natural language understanding twice.**

We already have a component that reads prose and produces structure: the LLM extractor.
It read the chapter, understood it, and emitted `"Aelric took the silver dagger from the
dead man's belt"` — which is *still prose*. Then a second component tried to understand
that prose with regular expressions.

We threw away the understanding, then tried to reconstruct it with a worse tool.

The fix is obvious once you see it: **make the LLM emit the structure directly.** Don't
ask for a sentence and then parse it. Ask for the fields.

> **If a downstream component has to parse natural language, the upstream component
> emitted the wrong thing.**

---

## Typed state deltas

So extraction pass 3 was replaced. Instead of `entity_deltas` (a bag of per-character
attribute values), it emits `state_deltas` — explicit, typed, atomic changes:

```python
"state_deltas": {
    "state_deltas": [
        {
            "kind": "possession|location|knowledge|status",
            "character_name": "string  # the character affected (or the entity moving, for location)",
            "object_name": "string|null  # possession only: the object gained/lost",
            "location_name": "string|null  # location only: where the character now is",
            "change": "gain|loss|null  # possession only",
            "fact": "string|null  # knowledge only: what the character now knows",
            "attribute": "emotional_state|goals|physical_state|appearance|notes|null  # status only",
            "value": "string|null  # status only: the new value of that attribute",
            "quote": "string  # short verbatim evidence from the chapter text",
        }
    ]
}
```

Four kinds of change, and that's deliberately all:

| kind | meaning | example |
|---|---|---|
| `possession` | a character gains or loses a physical object | Aelric gains the silver dagger |
| `location` | a character (or significant object) arrives somewhere | Mira arrives at the Saltmarsh |
| `knowledge` | a character learns something | Mira learns the lantern opens the vault |
| `status` | a lasting change to a character's condition | Aelric's goal becomes "find his sister" |

The instruction that goes with it is the most carefully written prompt in the codebase.
Read it properly — every line is a fix for a failure mode above:

```
Extract every EXPLICIT state change in this chunk as a typed delta.
These deltas are the machine-readable event log for character state —
downstream code applies them literally and never re-reads the prose,
so precision beats recall.

KINDS
- possession: a character gains or loses a physical object.
  Set character_name, object_name, change=gain|loss.
  The actor must be explicit in the text. "Aelric took the dagger"
  -> gain. "Aelric handed Mira the dagger" -> TWO deltas: loss for
  Aelric, gain for Mira.
- location: a character (or significant object) arrives at / is
  established to be at a location. Set character_name (the mover)
  and location_name. Emit one delta per arrival, not per mention.
- knowledge: a character learns something new. Set character_name
  and fact (one sentence). Only knowledge acquired IN THIS CHUNK.
- status: a lasting change to a character's condition. Set
  character_name, attribute (one of emotional_state|goals|
  physical_state|appearance|notes) and value. Emit only when the
  text establishes a new state, not for momentary reactions.

RULES
- Failed or negated actions are NOT deltas ("tried to grab", "did
  not take", "refused the sword" -> nothing).
- Hypotheticals, plans, and dialogue about actions are NOT deltas
  unless the chunk shows them happening.
- Every delta needs a short verbatim quote as evidence.
- Use canonical entity names from STORY CONTEXT when the chunk uses
  an alias.
Return JSON only.
```

Line by line against the regex failures:

- *"The actor must be explicit"* → fixes `involved_characters[0]`.
- *"'Aelric handed Mira the dagger' → TWO deltas"* → fixes the transfer direction, by
  making the model decompose a transfer into a loss and a gain. Two atomic facts beat one
  ambiguous one.
- *"Failed or negated actions are NOT deltas"* → fixes negation, explicitly, with the
  exact examples that broke the regex.
- *"Hypotheticals, plans, and dialogue about actions are NOT deltas"* → fixes the "'If I
  take the dagger,' he said" case.
- *"Emit one delta per arrival, not per mention"* → prevents thirty location deltas for a
  chapter set in one room.
- *"Every delta needs a short verbatim quote as evidence"* → the audit trail, and the
  same trick as Post 4's grammatical anchor: asking for a citation makes the model
  ground its claim in the text rather than in its priors.
- *"precision beats recall"* → the stated policy. A missing delta means state doesn't
  update; a wrong delta means state is actively false. Same asymmetry as Post 4.

And the first paragraph is the whole contract in one sentence: *downstream code applies
them literally and never re-reads the prose.* No second parser. Ever.

### The table

```sql
CREATE TABLE state_deltas (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id  UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    event_id    UUID REFERENCES events(id) ON DELETE SET NULL,
    -- narrative order within the chapter; created_at can't order rows written
    -- in one transaction (now() is transaction-stable) and UUIDs are random.
    ordinal     INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL CHECK (kind IN ('possession','location','knowledge','status')),
    subject_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    object_id   UUID REFERENCES entities(id) ON DELETE CASCADE,
    location_id UUID REFERENCES locations(id) ON DELETE CASCADE,
    change      TEXT CHECK (change IN ('gain','loss','move','learn','update')),
    attribute   TEXT,
    detail      TEXT,
    certainty   FLOAT DEFAULT 1.0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

Two things demand explanation.

**`CHECK (kind IN (...))`.** A check constraint is a rule the database enforces on every
insert. Try to write `kind = 'teleport'` and the insert is rejected. This matters more
than it looks: the set of kinds is small and closed *by construction*, so the replay
engine's `if/elif` chain is provably exhaustive. If someone adds a fifth kind, they have
to change the schema, which makes them think about the replay code. Constraints are how
you make a schema self-documenting *and* self-enforcing.

**`ordinal`**, and its comment, which is a small masterclass in why obvious solutions
fail:

> *narrative order within the chapter; `created_at` can't order rows written in one
> transaction (`now()` is transaction-stable) and UUIDs are random.*

Unpack that. Order matters enormously — a chapter where Aelric gains and then loses the
dagger is completely different from one where he loses and then gains it. So how do you
recover the order?

- **`created_at`?** In Postgres, `now()` returns the *transaction start time*, and it is
  constant for the entire transaction. Every row a chapter's persistence writes gets an
  identical timestamp. Sorting by it is sorting by a constant. (There's a function that
  *does* advance, `clock_timestamp()`, but relying on microsecond wall-clock ordering for
  semantic correctness is fragile in a different way.)
- **The primary key?** They're UUIDv4 — 122 random bits. Sorting by them is shuffling.
- **Insertion order in the table?** Postgres makes no guarantee that a `SELECT` without
  `ORDER BY` returns rows in insertion order. It usually does, until a vacuum moves a
  page and it doesn't.

So order gets an explicit column, assigned in a loop while writing:

```python
db.execute("""INSERT INTO state_deltas (chapter_id, ordinal, kind, ...)
              VALUES (%s, %s, %s, ...)""",
           (chapter_id, written, kind, ...))
written += 1
```

The general lesson: **if the order of your data is semantically meaningful, store it
explicitly.** Don't rely on timestamps, ids, or insertion order. All three will betray
you, and only in production.

### Persisting deltas: reference-only, again

```python
def persist_state_deltas(db, *, chapter_id, deltas, resolver):
    """Persist typed state deltas — the tier-2 state log the materializer replays.

    Reference-only resolution throughout: a delta naming an unknown entity is
    dropped (with a warning), never minted.
    """
```

Same discipline as Post 2. A delta says "Aelric gained the Tutorial Window"; if
"Tutorial Window" isn't a known object, the delta is dropped, not used as an excuse to
create an object.

One kind gets special handling, and the comment explains itself:

```python
if kind == "location":
    # A location delta's mover can be a character or a significant
    # object (prompts.py: "a character (or significant object)") — a
    # dagger moving between locations, say. resolve_character would
    # silently drop every object mover, so resolve_any_entity (which
    # checks the entities table and every typed table by name/alias)
    # is used instead. It returns a universal id STRING, not a
    # ResolvedEntity, unlike the other resolve_* calls below.
    subject_universal_id = resolver.resolve_any_entity(character_name, create=False)
```

The field is called `character_name` because that's what it usually is. For location
deltas it might not be. This is the kind of thing that produces a silent 20% data loss —
every object movement dropped — and is invisible until someone traces one specific
missing edge.

---

## Replay: folding the log

Now the good part. `StateReplay.replay` reads the deltas and folds them into projections.

```python
deltas = db.fetchall("""
    SELECT d.kind, d.subject_id, d.object_id, d.location_id, d.change,
           d.attribute, d.detail, d.certainty, d.event_id, d.chapter_id
      FROM state_deltas d
      JOIN chapters c ON c.id = d.chapter_id
     WHERE c.novel_id = %s AND c.number <= %s
     ORDER BY c.number ASC, d.ordinal ASC, d.id ASC
""", (novel_id, through_chapter), dict_rows=True)
```

Three things in that query carry the whole design:

1. **`c.number <= %s`** — the cutoff. Replay through any chapter you like. This is where
   time travel comes from, and it's one comparison.
2. **`ORDER BY c.number, d.ordinal, d.id`** — chapter order, then narrative order within
   the chapter, then id as a final deterministic tiebreak. Totally ordered, reproducible
   across runs.
3. It reads **only** `state_deltas`. Not events, not prose, not `character_states`. One
   input.

Then a fold — a loop that walks the log carrying running state:

```python
snapshots: dict[str, dict[int, StateSnapshot]] = {}      # [character][chapter]
active_location: dict[str, LocationFact] = {}            # entity -> open location fact
location_facts: list[LocationFact] = []
active_possession: dict[tuple[str, str], PossessionFact] = {}
possession_facts: list[PossessionFact] = []

for d in deltas:
    if d["kind"] == "location":
        ...
    elif d["kind"] == "possession":
        ...
```

### Possession: opening and closing intervals

```python
elif kind == "possession" and character_id is not None and d["object_id"]:
    object_id = object_by_entity.get(str(d["object_id"]))
    if object_id is None:
        continue
    key = (character_id, object_id)
    if d["change"] == "gain":
        if key not in active_possession:
            fact = PossessionFact(
                character_id=character_id,
                object_id=object_id,
                since_chapter=chapter_number,
                evidence_event_id=str(d["event_id"]) if d["event_id"] else None,
                certainty=float(d["certainty"] or 1.0),
            )
            active_possession[key] = fact
            possession_facts.append(fact)
    elif d["change"] == "loss":
        existing = active_possession.pop(key, None)
        if existing is not None:
            closed = replace(existing, until_chapter=chapter_number)
            for i in range(len(possession_facts) - 1, -1, -1):
                f = possession_facts[i]
                if (f.character_id, f.object_id, f.since_chapter, f.until_chapter) == (
                    existing.character_id, existing.object_id, existing.since_chapter, None,
                ):
                    possession_facts[i] = closed
                    break
```

A gain **opens an interval** (`since_chapter = N`, `until_chapter = None`, meaning "still
open"). A loss **closes** the matching open interval. The `if key not in
active_possession` guard makes a repeated gain idempotent — the extractor mentioning the
same pickup twice doesn't create two intervals.

Backwards iteration to find the fact to close (`range(len(...) - 1, -1, -1)`) is because
the same character can gain, lose, and re-gain the same object; we want to close the
*most recent* open interval, not the first one.

The output is exactly the picture we drew in Post 1:

```
       ch1   ch4      ch12                       ch70
Aelric  │     ├────────┤                          │
Mira    │              ├──────────────────────────▶  (until_chapter = NULL)
```

### Location: replacement rather than open/close

```python
if kind == "location" and d["location_id"] is not None:
    location_id = str(d["location_id"])
    current = active_location.get(subject_entity)
    if current is None or current.location_id != location_id:
        fact = LocationFact(entity_id=subject_entity, location_id=location_id,
                            since_chapter=chapter_number, ...)
        active_location[subject_entity] = fact
        location_facts.append(fact)
```

Locations differ from possessions in a way that matters: you can hold zero objects, but
you are always *somewhere*. So there's no "loss" — arriving somewhere new implicitly
ends the previous stay. The `current.location_id != location_id` check means a chapter
that re-states where someone already is produces no new fact.

### Carry-forward: which fields persist, and one that doesn't

```python
def snapshot_for(character_id: str, chapter_number: int) -> StateSnapshot:
    per_char = snapshots.setdefault(character_id, {})
    if chapter_number not in per_char:
        prior = None
        for n in sorted(per_char):
            if n < chapter_number:
                prior = per_char[n]           # most recent earlier snapshot
        per_char[chapter_number] = StateSnapshot(
            character_id=character_id,
            chapter_id=chapter_id_by_number[chapter_number],
            chapter_number=chapter_number,
            location_id=prior.location_id if prior else None,
            goals=prior.goals if prior else None,
            knowledge=list(prior.knowledge) if prior else [],
            physical_state=prior.physical_state if prior else None,
            appearance=prior.appearance if prior else None,
        )
    return per_char[chapter_number]
```

Count the fields being copied forward: `location_id`, `goals`, `knowledge`,
`physical_state`, `appearance`.

Count the fields **not** copied: `emotional_state`, `notes`.

That omission is deliberate and it's a genuinely nice piece of modelling. Think about
what these attributes *are*:

- Your **location** persists until you move. If nobody moved you, you're where you were.
- Your **goals** persist until you change them.
- Your **knowledge** is monotonic — you don't unlearn things. (Fiction can break this
  with amnesia; the model doesn't handle that, and that's a known simplification.)
- A **missing limb** persists. Physical state is durable.
- Your **emotional state does not persist.** Being furious in chapter 3 does not mean
  you're furious in chapter 4. Carrying it forward would show a character as "grieving"
  for forty chapters because one chapter said so and nothing since has said otherwise.

So `emotional_state` is populated only where a delta explicitly sets it, and is blank
otherwise. Blank correctly means "the text didn't say," which is the honest answer.

This is Break 2 from the top of the post, solved properly: **the projection distinguishes
carried-forward facts from observed ones by which fields carry forward at all**, and the
choice is made per field, based on what the field means in a story. You can't get that
from a generic framework. Someone had to sit down and think about grief.

---

## Bitemporal edges

The possession and location facts get persisted into tables shaped as *intervals*:

```sql
CREATE TABLE possesses_edges (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id      UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    object_id         UUID NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    since_chapter     INTEGER NOT NULL,
    until_chapter     INTEGER,                       -- NULL = still true
    evidence_event_id UUID REFERENCES events(id),
    certainty         FLOAT DEFAULT 1.0,
    superseded_by_id  UUID REFERENCES possesses_edges(id)
);
```

The word **bitemporal** comes from database theory and normally means tracking two time
axes: when a fact was true *in the world*, and when the database *knew* it. Here the
axes are chapter number (story time) and the `superseded_by_id` chain (what replaced
what). The idea, borrowed explicitly from a system called Graphiti/Zep that does this
for AI agent memory, is:

> **Invalidate, don't delete.** When a fact stops being true, close its interval and
> write a new row. Never overwrite. Never delete.

Now the payoff. "Who holds object X at chapter N?" is one query:

```sql
SELECT character_id
FROM possesses_edges
WHERE object_id = %s
  AND since_chapter <= %s
  AND (until_chapter IS NULL OR until_chapter >= %s)
```

Read the predicate as: *the interval started at or before N, and either never ended or
ended at or after N.* That's an interval-containment test, and those two lines recur
verbatim throughout the codebase — the list endpoints, the entity graph, and the
continuity critic all share them. (Read paths that ask for *currently active* edges add
one more predicate, `superseded_by_id IS NULL`; Part 9 explains the bug that made it
necessary.)

**`until_chapter` is inclusive.** If it's 12, the character held the object *through*
chapter 12. That's why the query says `>= N` rather than `> N`. Inclusive-vs-exclusive
interval bugs are a classic source of off-by-one errors, so it's pinned in a comment and
tested.

### The same-chapter supersession bug

Here's a subtle one that the materializer has to handle, and the code carries a
five-line explanation of why:

```python
# Now stitch consecutive edges. Normally until_chapter is the
# chapter before the next fact's since_chapter, but when one
# entity has two location facts in the SAME chapter (e.g. two
# location deltas from separate extraction chunks), that would
# invert the interval (since > until), which
# since<=N AND (until IS NULL OR until>=N) queries silently
# exclude. Clamp to the current fact's own since_chapter so a
# same-chapter supersession still yields a valid single-chapter
# interval.
for i in range(len(entity_facts) - 1):
    prior_id = inserted_ids[i]
    fact = entity_facts[i]
    next_fact = entity_facts[i + 1]
    until_chapter = max(next_fact.since_chapter - 1, fact.since_chapter)
    cur.execute("""UPDATE located_in_edges
                      SET until_chapter = %s, superseded_by_id = %s::uuid
                    WHERE id = %s::uuid""",
                (until_chapter, inserted_ids[i + 1], prior_id))
```

Walk through it. A character moves twice in chapter 7 — into the tavern, then out to the
docks. Two location facts, both `since_chapter = 7`.

Naive stitching sets the first fact's `until_chapter = next.since_chapter - 1 = 6`. So
the first interval is `[7, 6]` — it starts *after* it ends.

Now run the containment query for chapter 7: `since (7) <= 7` ✓ and `until (6) >= 7` ✗.
The row doesn't match. It doesn't match for chapter 6 either (`since 7 <= 6` ✗). **The
row matches no chapter at all.** It's invisible data — present in the table, returned by
nothing, never erroring.

The `max(..., fact.since_chapter)` clamp turns `[7, 6]` into `[7, 7]`: a valid
single-chapter interval that says "this was true during chapter 7, then superseded."

I like this bug as a specimen because it has all three properties of the worst kind:
it's arithmetically obvious once stated, it produces no error, and the symptom (a missing
row in a graph view) is many layers away from the cause.

---

## The materializer: making rebuilds safe

`StateMaterializer` takes what replay computed and writes it. Its docstring makes one
promise:

```python
class StateMaterializer:
    """Persists derived projections (character_states, located_in_edges,
    possesses_edges) computed from state_deltas, in a single transaction.

    The materializer is idempotent: re-running for the same
    (novel_id, through_chapter) will not produce duplicate rows.
    """
```

**Idempotent** means: running it twice has the same effect as running it once. It's a
property worth designing for deliberately, because it converts a whole class of scary
failures into boring ones. If a run crashes halfway, you just run it again. No cleanup,
no "did it get to step 4?", no partial-state reasoning.

How is it achieved? Not by cleverness — by demolition:

```python
def _write_location_edges(self, cur, novel_id, facts):
    # Rebuild the whole novel's projection: delete before the empty-facts
    # return, and scope by novel rather than by the entities present in
    # the new facts — otherwise edges whose source events disappeared
    # (e.g. a chapter re-processed with replace=True) survive as stale
    # rows. Safe because the events table is the ground truth and we are
    # re-deriving from scratch.
    cur.execute("""DELETE FROM located_in_edges
                    WHERE entity_id IN (SELECT id FROM entities WHERE novel_id = %s)""",
                (novel_id,))
    if not facts:
        return 0
    ...
```

Delete every edge for the novel, then write the whole thing fresh.

That looks wasteful, and for a big enough novel it is (it's a listed scaling concern —
materialization cost grows with novel length). But look at what it buys:

- **Idempotency, trivially.** Same input, same output, however many times you run it.
- **No stale rows, ever.** Re-process chapter 12 with different extraction, and any edge
  the old extraction produced simply isn't re-derived. It's gone. An incremental
  "upsert" approach would leave it sitting there forever, and you'd never know.
- **The code is simple enough to be obviously correct.** No merge logic, no conflict
  resolution, no "does this edge already exist" checks.

Two subtleties in the comment. **Delete before the empty-facts early return** — otherwise
a novel whose deltas were all removed keeps its old edges forever. And **scope the delete
by novel, not by the entities in the new facts** — otherwise an entity that used to have
edges and now has none never gets cleaned.

Note also that this rebuild is safe *only because* the log is the ground truth. Deleting
a projection is a non-event when you can regenerate it. That's the whole reason the
event-sourcing split earns its keep.

### The race that ate a novel's state

Two chapters of the same novel can be processed at once — the job runner allows two
concurrent workers. Both finish, both call `materialize()`. Now:

```
time ──────────────────────────────────────────────────────▶

worker A:  replay(through 12) ───────── DELETE ─── INSERT ──▶ commit
worker B:       replay(through 13) ──── DELETE ─── INSERT ─▶ commit
                       │                                  │
                  read the log                     writes state
                  through ch13                     for ch ≤ 13
```

...and if A commits *after* B, A's older snapshot overwrites B's newer one. Chapter 13's
state is gone. And nothing will re-derive it, because `analyze_chapter` already returned
`materialized: true` for chapter 13. Recovery requires a human noticing and running a
CLI by hand.

The fix is two changes:

```python
with self.db.transaction() as cur:
    # Serialize per novel. materialize is a full novel-wide
    # DELETE-then-rebuild, so two concurrent runs (jobs.py allows two
    # chapters of one novel in flight) let the slower one overwrite the
    # faster one's projections with a stale snapshot — and nothing
    # re-derives them, because analyze_chapter already reported
    # materialized=True. The lock is transaction-scoped and released on
    # commit or rollback.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(novel_id),))
    # Replay on this cursor so the reads and the rebuild see one
    # consistent snapshot of the delta log.
    snapshots, location_facts, possession_facts = self.replay.replay(
        novel_id, through_chapter, cur=cur
    )
```

**`pg_advisory_xact_lock`** is a Postgres feature for application-defined locking. You
pass it any 64-bit number and it blocks until no other transaction holds a lock on that
number. `hashtextextended(novel_id, 0)` hashes the novel's UUID into one. So two
materializations of the *same* novel serialize; two different novels run in parallel,
untouched.

The `_xact_` in the name means transaction-scoped: it releases automatically on commit
*or* rollback. You cannot leak it by crashing. Compare to a lock you have to release
manually, where any unhandled exception between acquire and release deadlocks the system
until someone restarts it.

**And the second half — the cursor.** Replay used to do its four reads through the
connection pool, which hands out a *different connection per call*:

```python
class _CursorReader:
    """Runs replay's reads on one caller-supplied cursor.

    DBClient.fetchall checks out a fresh pooled connection per call, so
    replay's four reads would otherwise land in four different transaction
    snapshots. A chapter committed between them yields deltas whose chapter_id
    is missing from the map built by the first read, and those deltas are
    dropped silently.
    """
```

Here's why that matters, and it needs a short detour into how Postgres handles
concurrency. Postgres uses **MVCC** (multi-version concurrency control): each
transaction sees a consistent *snapshot* of the database as of when it started. Other
transactions' commits after that point are invisible to it. This is what makes
concurrent reads and writes safe without locking everything.

But: **a new transaction gets a new snapshot.** Replay's four reads were four separate
connections, hence four transactions, hence four snapshots:

```
read 1: chapters       ← snapshot at T1  (chapters 1..12)
        ← chapter 13 commits here
read 2: characters     ← snapshot at T2
read 3: objects        ← snapshot at T3
read 4: state_deltas   ← snapshot at T4  (includes chapter 13's deltas!)
```

Then the fold does this:

```python
chapter_number = chapter_number_by_id.get(str(d["chapter_id"]))
if chapter_number is None:
    continue                     # ← silently drops chapter 13's deltas
```

Chapter 13's deltas are in the delta list but chapter 13 isn't in the chapter map, so
every one of them is skipped. No error. No log line. The state for a whole chapter
quietly does not exist.

The fix threads one cursor through all four reads, so they share one transaction and one
snapshot. `_CursorReader` is a tiny adapter that presents the same `fetchall` interface
over a raw cursor:

```python
def fetchall(self, query, params=None, *, dict_rows=False, **_):
    self._cur.execute(query, params)
    rows = self._cur.fetchall()
    if not dict_rows:
        return list(rows)
    cols = [d[0] for d in self._cur.description]
    return [dict(zip(cols, row)) for row in rows]
```

Twelve lines. It exists entirely so that replay can be handed either a pooled client (in
tests) or a live cursor (in production) without knowing the difference.

> **Reading related tables in separate transactions gives you a torn read.** If the
> pieces have to be consistent with each other, they have to be in one transaction. This
> is not exotic; it's the default failure mode of any code that uses a connection pool
> casually.

---

## Where the write path ends up

Putting it together, processing one chapter is five phases:

```
┌─ analyze_chapter ────────────────────────────────────────────────────┐
│                                                                      │
│  1 INGEST      insert the raw chapter row                            │
│                                                                      │
│  2 EXTRACT     13 LLM passes per chunk → merge → dedup → canonicalize│
│                                                                      │
│  ┌───── ONE TRANSACTION ────────────────────────────────────────┐    │
│  │ 3 PERSIST  entities · events · relationships · scenes ·      │    │
│  │            knows_edges · commitments · canon_facts ·         │    │
│  │            state_deltas          ← the immutable tier        │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                            ↓ commit                                  │
│  4 MATERIALIZE  replay state_deltas → character_states +             │
│                 located_in_edges + possesses_edges                   │
│                                                                      │
│  5 CRITIQUE     run the continuity checks, persist findings          │
└──────────────────────────────────────────────────────────────────────┘
```

Phases 1–3 are one transaction: all or nothing. Phases 4 and 5 are deliberately *outside*
it:

```python
# ---- phase 4: MATERIALIZE / phase 5: CRITIQUE ----
# Both derive from the committed data and are idempotent; a failure
# here must not roll back the saved chapter. materialized/critique in
# the result tell callers whether a manual re-run is needed.
materialized = False
try:
    max_chapter = client.fetchval(
        "SELECT COALESCE(MAX(number), %s) FROM chapters WHERE novel_id = %s",
        (chapter_number, novel_id))
    StateMaterializer(client).materialize(novel_id, int(max_chapter))
    materialized = True
except Exception:
    logger.exception("materialize failed for novel %s; re-run pipeline.state.cli", novel_id)
```

The reasoning: extraction cost real money and the chapter is safely stored. Materializing
is cheap and repeatable. Throwing away the expensive thing because the cheap repeatable
thing failed would be perverse. So a failure is caught, logged, and reported in the
result dict as `materialized: false`, and a CLI can repair it later.

That's a general shape worth naming: **transactional for the expensive irreversible part,
best-effort-and-flagged for the cheap derivable part.**

Notice one more thing in that snippet: it materializes through `MAX(number)`, not through
the chapter just processed. Why? Because re-processing chapter 5 of a 40-chapter novel
changes the deltas that chapters 6–40's state depends on. Replaying only through 5 would
leave 6–40 built on the old log. Replay through the latest chapter and everything
downstream is rebuilt too.

---

## The honest boundary

Time to be precise about what this system does and doesn't give you, because "time
travel" is the kind of phrase that invites over-claiming.

**The edge tables serve every cutoff at once.** `possesses_edges` and `located_in_edges`
store intervals with supersession chains. One materialization produces rows that answer
"at chapter 4?" and "at chapter 31?" and "at chapter 70?" equally well. That's the good
case, and it's most of the interesting state.

**`character_states` serves one cutoff at a time.** It's rebuilt DELETE-then-INSERT for a
specific `through_chapter`. Ask for a different cutoff and you need to re-materialize.
In practice it's always materialized through the latest chapter, and the cutoff is
applied at read time (`WHERE ch.number <= cutoff`), which works because the snapshots
are per-chapter rows. But it's not the same guarantee the edge tables give.

The honest one-sentence framing, from the project's own notes:

> *Any cutoff is reconstructable from the log; edge projections serve all cutoffs at
> once, the character-state snapshot serves one at a time.*

**And the log itself is truly append-only — with one asterisk.** `state_deltas` rows are
never updated. They are cascade-deleted when their chapter is deleted, which happens on
`replace=True` re-processing. So "append-only" means "never edited in place," not "never
removed." Deleting a chapter and re-deriving is a legitimate operation; silently
rewriting history is not.

Re-processing has known residue, and the code says so rather than pretending otherwise:

```python
def delete_chapter_data(db, *, novel_id, chapter_number):
    """Delete one chapter and every derived row, enabling re-processing.

    The chapters FK cascades cover events (and thread_events), scenes,
    character_states, continuity_flags, shared_dynamics, and relationships
    rows with a non-NULL chapter_id. Tables keyed by chapter *number* instead
    of a FK need explicit handling. Known non-undoable residue: plot_threads
    upserts and entity rows created by this chapter remain — re-processing
    resolves back onto them. canon_facts updates from the prior run also persist
    (keyed by source_chapter int, no FK).
    """
```

Entities created by a chapter survive its deletion. That's arguably correct — entity
identity is expensive to establish (Post 4!) and stable across re-processing is a
feature. But it's a decision, and it's documented as one rather than discovered later by
someone confused about why a character didn't disappear.

There's also a lovely piece of FK-ordering care in there:

```python
# located_in_edges / possesses_edges reference events with NO ACTION FKs;
# NULL their evidence pointers so the chapter's event cascade can't violate
# them. The rows themselves are materialized projections — re-running the
# state materializer rebuilds them from the surviving event log.
db.execute("""UPDATE located_in_edges SET evidence_event_id = NULL
               WHERE evidence_event_id IN (
                 SELECT e.id FROM events e JOIN chapters ch ON ch.id = e.chapter_id
                  WHERE ch.novel_id = %s AND ch.number = %s)""",
           (novel_id, chapter_number))
```

Edges point at events as evidence. Deleting a chapter cascades to its events. But the
edge→event foreign key is `NO ACTION`, not `CASCADE` — so Postgres would refuse the
delete. NULL the pointers first. (Why not make it CASCADE? Because that would delete the
*edge*, and edges are projections that should be rebuilt, not destroyed.)

---

## What we can answer now

Real capabilities, not aspirational ones:

**"Who has the silver dagger at chapter 31?"** One interval query. Mira, since chapter
12, evidenced by the event in chapter 12.

**"Where was Aelric in chapter 7?"** Look up his chapter-7 snapshot; the location was
carried forward from chapter 6 because no delta moved him.

**"Was Kessa in two places at once in chapter 22?"** Query active location edges for her
at chapter 22. More than one → a continuity error. (This is literally what the critic's
location check does — Post 8.)

**"What does Mira know as of chapter 12?"** `knows_edges` filtered by
`learned_chapter <= 12 AND superseded_by_id IS NULL`.

**"Show me the whole novel as it existed at chapter 12."** Every read takes a cutoff.
Post 9 is about making that structurally impossible to forget.

And one we still can't answer well: **"Find me the scene where Mira first doubted
Toren."** That's not a lookup. There's no table of doubts. It's a fuzzy, semantic
question over prose, and no amount of schema design will turn it into an interval query.

For that, we need search. Which turns out to have its own set of traps.

---

*Next: [Part 6 — Modelling a World](06-modelling-a-world.md) — relationships that point
both ways (or don't), genre-specific entity types the schema was never designed for, five
kinds of graph edge, and a full accounting of the two id spaces.*
