# Part 3 — Thirteen Ways of Reading a Chapter

*Part 3 of the Continuum series. [Part 2](02-the-mvp.md) built an extraction pipeline
with six passes and argued for why six focused prompts beat one big one. This post walks
all thirteen passes the finished system runs, what problem each one was added to solve,
and what a chapter actually costs.*

---

## From six to thirteen

The MVP asked six questions of each chunk:

```python
PASS_ORDER = [
    "chapter_summary",
    "new_entities",
    "entity_deltas",
    "events",
    "thread_updates",
    "continuity_flags",
]
```

The system that ships today asks thirteen:

```python
PASS_ORDER = [
    "chapter_summary",              # 1  ── from the MVP
    "new_entities",                 # 2  ── from the MVP
    "state_deltas",                 # 3  ── replaced entity_deltas
    "events",                       # 4  ── from the MVP
    "thread_updates",               # 5  ── from the MVP
    "continuity_flags",             # 6  ── from the MVP
    "relationship_updates",         # 7  ── added: relationships need their own table
    "dynamics_updates",             # 8  ── added: how a pair behaves this chapter
    "scene_segmentation",           # 9  ── added: chapters are too coarse for retrieval
    "multi_granularity_summaries",  # 10 ── added: one summary length doesn't fit
    "knowledge_state_deltas",       # 11 ── added: who knows what
    "commitments",                  # 12 ── added: Chekhov's guns
    "canon_facts",                  # 13 ── added: facts that must not change
]
```

Seven additions and one replacement. None of them were speculative — each one exists
because a question couldn't be answered without it. This post goes through them in order,
and for each: **what it produces, why it exists, and where it goes wrong.**

A note on how to read the schemas below. Each pass declares its output shape as a Python
dict that gets serialised straight into the system prompt:

```python
def build_system_prompt(pass_name: str, custom_entity_types: list[dict] | None = None) -> str:
    schema = PASS_SCHEMAS[pass_name]
    # (the custom_entity_types branch is Part 6's story — omitted here)
    return dedent(f"""
        You are an extraction engine for a novel continuity pipeline.
        Return only strict JSON. No prose, no markdown.
        Keep facts grounded in provided text.

        Extraction pass: {pass_name}
        Required output schema:
        {json.dumps(schema, ensure_ascii=True, indent=2)}
    """).strip()
```

So the schemas you're about to read are, quite literally, the prompts. There's no
separate specification that can drift from them — the dict *is* the contract, and it's
also the thing the extractor's composition step reads from. That's a small design choice
with a big payoff: it is impossible for the prompt and the parser to disagree about the
field names.

---

## Pass 1 — `chapter_summary`

```python
"chapter_summary": {
    "summary": "string (<= 300 tokens)",
}
```

The simplest possible pass. One paragraph, per chunk, merged across chunks by
concatenation.

Why does this exist when pass 10 produces *three* summaries at different lengths? History,
mostly — this one came first and is still the value written to `chapters.summary` during
the core persistence step. Pass 10's medium summary then overwrites it if present:

```sql
UPDATE chapters
SET summary_short = %s,
    summary = COALESCE(NULLIF(%s, ''), summary),   -- ← keeps pass 1's value if pass 10 was empty
    summary_long = %s
WHERE id = %s
```

`COALESCE(NULLIF(x, ''), summary)` reads as: *use `x` unless it's the empty string, in
which case keep what's already there.* So pass 1 is a fallback for when pass 10 fails or
returns nothing. Redundancy costing one cheap call per chunk, buying a guarantee that
every chapter has *some* summary.

There's a related optimisation in the embedding step that's worth seeing, because it's
the kind of coordination that's easy to get wrong:

```python
embed_chapter_and_events(
    s, chapter_id=chapter_id, chapter_summary=extracted.get("summary", ""),
    event_rows=event_rows, service=embedding_service,
    # persist_multi_summaries re-embeds the chapter from summary_medium;
    # skip the throwaway embedding when that will happen.
    embed_chapter=not (extracted.get("summary_medium") or "").strip(),
)
```

If pass 10 produced a medium summary, the chapter will be re-embedded from *that* a few
lines later. Embedding it twice would mean paying for an embedding call whose result is
immediately overwritten. One boolean, one saved API call per chapter.

---

## Pass 2 — `new_entities`

The most consequential pass in the system, because it's the only one allowed to create
entities (Part 2's `create=True` rule).

```python
"new_entities": {
    "characters": [
        {"name": "string", "aliases": ["string"], "description": "string"}
    ],
    "locations": [
        {"name": "string", "description": "string",
         "parent_location": "string|null  # canonical parent if this is a sub-location "
                            "(room/floor/corridor); null for top-level locations"}
    ],
    "factions": [{"name": "string", "description": "string"}],
    "objects": [
        {"name": "string", "description": "string",
         "owner_name": "string|null  # character who owns/carries this object; null if unowned or unknown",
         "distinguishing_properties": "string|null  # brief note distinguishing this from similar objects",
         "significance": "string|null"}
    ],
}
```

Every optional field on that schema is a scar. Let's take the instruction block section
by section, because each paragraph is a bug fix.

### Locations: the sub-location explosion

```
LOCATIONS
- Use the canonical, top-level name for a place (e.g. "Corporate Office",
  "Jake's Apartment"). Never create a sub-location row for a room, floor,
  corridor, or fixture that lives inside an already-established location.
- If the scene happens inside a sub-location (14th floor, elevator, back room),
  set name to the PARENT location and leave parent_location null.
- If you must distinguish the sub-location (e.g. a named room important to the
  plot), set name to that specific name AND set parent_location to the canonical
  parent (e.g. parent_location="Corporate Office").
- Name variants of the same place ("Jake's home", "Jake's flat", "Jake's
  apartment") are the SAME location. Pick one canonical name; do not emit both.
- If a location already exists in STORY CONTEXT under any alias or close variant,
  do NOT create a new entry.
```

The failure this prevents: a single office building generating *thirty* location rows —
"the lobby", "the 14th floor", "the elevator", "the corridor outside the boardroom", "the
break room". Each is a real place mentioned in the text. Each is also noise, and worse,
each fragments the location history: a character who spent three chapters in one building
appears to have visited eleven distinct places.

The rule has an escape hatch (`parent_location`) for rooms that genuinely matter to the
plot, and the resolver honours it structurally — Part 4 covers how a sub-location folds
into its parent and leaves its own name behind as an alias.

### Objects: physical things only, and owners matter

```
OBJECTS
- Extract physical items only: weapons, armor, tools, artifacts, equipment,
  and named possessions with narrative significance.
- DO NOT extract skills, abilities, spells, character classes, stat windows,
  or system notifications as objects — these are game mechanics with no
  physical form.
- Set owner_name to the character who owns, carries, or is specifically
  associated with this object. Two characters can each have "a black sedan" —
  they are DIFFERENT objects; give each one a distinct name that includes the
  owner (e.g. "Jake's black sedan", "Sarah's black sedan").
- Set distinguishing_properties to any brief descriptor that makes this object
  unique (colour, damage, inscriptions, enchantments, etc.).
- Generic props with no identity (a glass of water, a chair) should NOT be
  extracted as objects unless they recur or carry narrative significance.
```

The `owner_name` rule is the interesting one because it's a *modelling* rule dressed up
as an extraction rule. It exists because merge logic downstream keys objects on
`(name, owner)`:

```python
# Objects with different owners are distinct even when names collide.
owner = str(item.get("owner_name") or "").strip().lower() if include_owner else ""
key = (name.lower(), owner)
```

If extraction doesn't supply owners, the merge can't distinguish two black sedans, and
Sarah's car merges into Jake's. So the prompt is written to produce the field the merge
needs. **Extraction schema and downstream logic have to be designed together**; a schema
designed in isolation produces data that's shaped wrong for its consumers.

The "no skills or classes" rule is the LitRPG-genre defence. In a novel where characters
have visible stat screens, "Archer Class" reads exactly like a proper noun for an object.
It isn't one. (And Part 4 will show that even with this rule, the same string sometimes
gets filed as a character in one chapter and an object in the next — the dominant
duplicate-generating failure in the whole system.)

### Characters: sentient beings only

```
CHARACTERS
- Extract only sentient beings who act in the story: people, named NPCs,
  monsters, and creatures that interact with the protagonist.
- DO NOT extract game-system elements as characters. This includes:
  character classes (e.g. "Archer Class", "Mage Class"), skills, abilities,
  spells, stat windows, tutorial screens, system notifications, or any
  mechanic that is not a being. If it cannot think, speak, or act of its
  own will, it is NOT a character.
- Only extract if not already in STORY CONTEXT.
```

*"If it cannot think, speak, or act of its own will, it is NOT a character."* That's a
definition, offered because the category boundary is genuinely unclear in some genres and
the model needs a decision procedure, not just a list of exclusions.

This prompt rule is the *first* line of defence against phantom characters. The
`create=False` resolver flag from Part 2 is the second. Two independent defences for the
same failure, because prompt rules are probabilistic and code rules are not.

---

## Pass 3 — `state_deltas`

The replacement for the MVP's `entity_deltas`, and the subject of [Part 5](05-time.md) in
full. Briefly, here, for completeness:

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

The MVP's version asked for the *new value of every attribute* for every character — a
snapshot. This one asks for **changes**, typed, each with evidence. The difference turns
out to be the difference between a system that can answer "as of chapter N" and one that
can't. Part 5 is that story.

One detail belongs here, though, because it's about the *extraction* rather than the
storage: the output is validated field-by-field before anything downstream sees it.

```python
_VALID_DELTA_KINDS = {"possession", "location", "knowledge", "status"}
_VALID_STATUS_ATTRIBUTES = {"emotional_state", "goals", "physical_state", "appearance", "notes"}

def _clean_state_delta(item: Any) -> dict[str, Any] | None:
    """Validate a single state_delta and fill in its kind-derived `change`."""
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind", "")).strip().lower()
    if kind not in _VALID_DELTA_KINDS:
        return None
    item["kind"] = kind
    if kind == "possession":
        change = str(item.get("change", "")).strip().lower()
        if change not in {"gain", "loss"}:
            return None
        item["change"] = change
    elif kind == "location":
        item["change"] = "move"
    elif kind == "knowledge":
        item["change"] = "learn"
    else:  # status
        item["change"] = "update"
        if str(item.get("attribute", "")).strip() not in _VALID_STATUS_ATTRIBUTES:
            return None
    return item
```

Two things it does. It **rejects** anything malformed — an unknown `kind`, a possession
with no gain/loss, a status update naming an attribute that doesn't exist. And it
**derives** the `change` field for the three kinds where it's implied, so the model
doesn't have to fill in a field whose value is determined by another field.

The second half matters more than it looks. Every field a model has to fill is a field it
can get wrong. If `kind="location"` always implies `change="move"`, asking for both is
asking for an inconsistency. **Derive what you can; only ask for what's genuinely free.**

---

## Pass 4 — `events`

```python
"events": {
    "events": [
        {
            "description": "string",
            "event_type": "action|revelation|death|arrival|conflict|other",
            "impact_level": "low|medium|high|critical",
            "involved_characters": ["string"],
            "involved_locations": ["string"],
            "involved_objects": ["string"],
            "involved_factions": ["string"],
        }
    ]
}
```

Events are the story's verbs. They're free text plus four arrays of entity names.

The **free text** is deliberate, and it's a decision that was made, unmade, and re-made.
The research literature this project drew on recommends **SVO triples** — decomposing each
event into subject, verb, object, so events become machine-comparable. The schema was
extended to support exactly that:

```sql
events.subject_entity_id
events.verb
events.object_entity_id
events.story_time_ordinal
events.narrative_order
events.scene_id
```

None of those columns ever got a writer. No extraction pass produced them. They sat empty
for months, and the sixth continuity check queried a table (`temporal_constraints`) that
was likewise never populated — a check that passed vacuously, forever.

They were all deleted:

```sql
DROP TABLE IF EXISTS temporal_constraints;
ALTER TABLE events DROP COLUMN IF EXISTS subject_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS verb;
ALTER TABLE events DROP COLUMN IF EXISTS object_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS story_time_ordinal;
ALTER TABLE events DROP COLUMN IF EXISTS narrative_order;
ALTER TABLE events DROP COLUMN IF EXISTS scene_id;
```

The reasoning, from the project's own retrospective:

> *Rather than build an SVO extraction pass to fill them, the write-spine change removed
> those columns and `temporal_constraints` from the schema entirely, and gave
> narrative-order and typed state changes a home in the new `state_deltas` table instead —
> which does have a producer (pass 3) and a consumer (the state materializer).*

The lesson worth extracting: **structured event decomposition was the right idea, and
`state_deltas` is that idea done properly.** SVO triples would have given you
"Aelric / took / dagger" as a general-purpose structure that downstream code would then
have to interpret. `state_deltas` gives you `{kind: possession, subject: Aelric, object:
dagger, change: gain}` — the *interpretation itself*. Same insight, one level less
generic, and therefore actually usable.

The four `involved_*` arrays hold **typed** ids (`characters.id`, `locations.id`, …), not
universal `entities.id`. This is the two-id-space footgun from Part 2, and it's called
out at every read site:

```python
# events.involved_* store TYPED ids (characters.id / locations.id /
# objects.id / factions.id) — see reads/characters.py's module docstring
# for the two-id-space explanation.
```

Why arrays instead of a junction table? A junction table (`event_participants(event_id,
entity_id, role)`) is the textbook answer and would be more normalised. Arrays win here
for three reasons: writes are one row instead of five; reads don't need a join; and
Postgres can index array containment with GIN so `%s = ANY(involved_characters)` is still
fast. The cost is that "all events involving entity X" is a slightly odd query and the
arrays can't carry per-participant metadata. Neither has bitten.

---

## Pass 5 — `thread_updates`

```python
"thread_updates": {
    "thread_updates": [
        {
            "title": "string",
            "description": "string",
            "status": "open|progressing|closed",
            "impact": "opens|advances|closes",
            "thread_type": "mystery|conflict|goal|prophecy|secret|other",
            "event_description": "string",
        }
    ]
}
```

A **plot thread** is a narrative arc that spans chapters — a mystery, a quest, a
rivalry. This pass reports which threads this chunk touched and how.

The persistence is an **upsert keyed on title**:

```sql
INSERT INTO plot_threads (novel_id, title, description, status, opened_chapter,
                          closed_chapter, thread_type)
VALUES (%s, %s, %s, %s, %s,
        CASE WHEN %s = 'closed' THEN %s ELSE NULL END, %s)
ON CONFLICT (novel_id, title)
DO UPDATE SET
    description = COALESCE(EXCLUDED.description, plot_threads.description),
    status = EXCLUDED.status,
    thread_type = COALESCE(EXCLUDED.thread_type, plot_threads.thread_type),
    closed_chapter = CASE
        WHEN EXCLUDED.status = 'closed' THEN EXCLUDED.closed_chapter
        ELSE NULL
    END
RETURNING id
```

`ON CONFLICT … DO UPDATE` is Postgres's upsert: insert, or if a row with this
`(novel_id, title)` already exists, update it instead. So a thread first reported in
chapter 3 and advanced in chapter 9 is one row, updated.

Keying on **title** is a weak point and worth naming. The LLM has to produce the same
title string for the same thread across chapters, or you get "The missing archivist" and
"The archivist's disappearance" as two threads. There is no canonicalizer for threads —
the whole apparatus of Part 4 applies to entities only. This is a known, unaddressed
limitation: thread identity is exactly as good as the model's consistency at naming
things.

Two `CASE` expressions guard `closed_chapter`: it's set only when the status is `closed`,
and *cleared* when a thread that was closed re-opens. Without the second half, a thread
that closed in chapter 12 and re-opened in chapter 20 would keep a stale
`closed_chapter = 12` — and every point-in-time query would think it was still closed.

### The bug: welding a thread to the wrong event

`event_description` is how a thread update points at the event that advanced it. The
linking code matched that description against the events actually persisted this chapter:

```python
def _find_related_event_id(*, event_lookup, event_rows, event_description):
    if not event_rows:
        return None
    if event_description:
        direct = event_lookup.get(event_description.lower())
        if direct:
            return direct
        lowered = event_description.lower()
        for row in event_rows:
            candidate = row["description"].lower()
            if lowered in candidate or candidate in lowered:
                return row["id"]
    ...
```

Exact match, then substring containment either way. Reasonable.

But the original fell back to `event_rows[0]` when nothing matched — the chapter's first
event. The fix, and its explanation:

```python
    # No match: return None rather than falling back to the chapter's first
    # event. That fallback welded every unidentifiable thread update to
    # whatever event happened to be extracted first, and thread_events rows are
    # read as evidence that a thread was advanced — so it invented links the
    # text never supported, and surfaced a blade-sharpening as the evidence for
    # a succession crisis. An unlinked thread update is merely incomplete;
    # a wrongly-linked one is false.
    return None
```

Part 11 revisits this one, because it's the purest example in the codebase of a bug that
isn't a crash — every row was well-formed, every foreign key resolved, and the system was
confidently asserting causal links the text never made.

---

## Pass 6 — `continuity_flags`

```python
"continuity_flags": {
    "continuity_flags": [
        {"description": "string",
         "flag_type": "foreshadowing|planted_detail|setup|callback|other"}
    ]
}
```

Simple schema, and by far the most interesting *instruction* in the codebase, because
it's a prompt written to solve a precision problem:

```
Flag ONLY narrative elements that must pay off in a future chapter or would
create a plot hole / broken promise if forgotten. Ask: "If the author never
references this again, would a careful reader feel cheated?"

Flag:
- Character abilities, skills, or traits explicitly established for later use
- Backstory (trauma, rivalries, history) that will plausibly drive future choices
- Introduced objects whose special properties haven't been exercised yet
- Explicit foreshadowing — stated predictions, ominous hints, prophecies
- Open promises, threats, oaths, or stated goals not yet pursued
- Unresolved mysteries or questions the narrative implicitly promises to answer

Do NOT flag:
- Mechanical scene transitions (timers, transport messages, system notifications)
- World-building facts that are informational but carry no narrative debt
- Events or setups that are fully resolved within the same chapter
- Generic character traits with no specific future hook
```

That second line — *"If the author never references this again, would a careful reader
feel cheated?"* — is the whole prompt. Everything else is elaboration.

It's a **test the model can apply**, not a category to match. Compare it to what the
prompt could have said: *"extract foreshadowing."* The model would then have to guess what
you mean by foreshadowing, and it would flag every ominous adjective in the chapter. The
counterfactual test ("if this never pays off, is the reader cheated?") gives it a
decision procedure with a clear answer for most cases.

The explicit **do-not** list is doing equal work. Without it, a LitRPG chapter produces
forty flags, thirty-eight of which are system notifications and timers. Negative examples
are as important as positive ones, and they're the part most prompts omit.

> **When a prompt's problem is over-triggering, add a counterfactual test and a
> do-not list. "Extract X" invites maximal recall; "extract X, where X means [test], but
> never [these]" invites judgement.**

---

## Pass 7 — `relationship_updates`

```python
"relationship_updates": {
    "relationship_updates": [
        {
            "entity_a": "string",
            "entity_b": "string",
            "rel_type": "string",
            "symmetric": "boolean|null  # true if genuinely mutual, false if one-sided, null if unclear",
            "from_chapter": "integer|null",
            "to_chapter": "integer|null",
            "notes": "string|null",
        }
    ]
}
```

Added when relationships got their own table (Part 2's `entities` refactor). The full
data-modelling story — symmetric versus directional, why `symmetric` is a per-instance
judgement rather than a property of the label — is [Part 6](06-modelling-a-world.md).

The extraction rule that matters here is about *decomposition*:

```
Extract relationships between entities. Each row must have exactly one
relationship type — never combine multiple types with "/" or ",".
If two entities have more than one distinct relationship, emit a separate
row for each (e.g. one row for "mentor", another for "father").

Use lowercase, specific labels (e.g. "rival", "employer", "romantic_interest")
rather than vague or compound ones ("guide/subject", "friends/colleagues").
```

Without this, the extractor produces `rel_type = "mentor/father/rival"`. Which is
readable, and completely unqueryable — you cannot ask "show me all mentor relationships"
without substring matching, and substring matching on "father" also hits "grandfather"
and "stepfather".

**One row per relationship type** makes the column a categorical value instead of a
sentence. The persist-time duplicate check depends on it:

```sql
SELECT id FROM relationships
 WHERE rel_type IS NOT DISTINCT FROM %s
   AND superseded_by_id IS NULL
   AND ((entity_a_id = %s AND entity_b_id = %s)
     OR (entity_a_id = %s AND entity_b_id = %s))
 LIMIT 1
```

Note `IS NOT DISTINCT FROM` rather than `=`. In SQL, `NULL = NULL` evaluates to `NULL`
(not true), so a plain equality check never matches two rows that both have a NULL
`rel_type`. `IS NOT DISTINCT FROM` is the null-safe comparison: it treats two NULLs as
equal. Without it, every untyped relationship gets re-inserted on every chapter that
mentions the pair.

Note also both orderings of the pair are checked. Relationships are stored with an
arbitrary `entity_a`/`entity_b` order, so "Aelric ↔ Kessa: rival" and "Kessa ↔ Aelric:
rival" are the same edge and must not both be stored.

---

## Pass 8 — `dynamics_updates`

```python
"dynamics_updates": {
    "dynamics_updates": [
        {"entity_a": "string", "entity_b": "string", "description": "string"}
    ]
}
```

The smallest schema in the system, and the one whose *purpose* takes the most explaining.

A **relationship** is a durable, typed fact: Aelric is Mira's brother. That doesn't change
between chapters. A **shared dynamic** is how a pair *behaved in this specific chapter*:
"they barely spoke; Mira answered in monosyllables and left early." That's not a
relationship type. It's a snapshot of a mood.

```sql
CREATE TABLE shared_dynamics (
    entity_a_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chapter_id  UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    description TEXT,
    CHECK (entity_a_id <> entity_b_id),
    UNIQUE(entity_a_id, entity_b_id, chapter_id)
);
```

`UNIQUE(entity_a_id, entity_b_id, chapter_id)` — **one row per pair per chapter.** Which
immediately creates a problem, because a three-chunk chapter can report the same pair
three times, sometimes with the arguments swapped:

```python
# The extractor may emit several dynamics for the same pair in one chapter
# (including with entity_a/entity_b swapped); UNIQUE(entity_a_id,
# entity_b_id, chapter_id) allows only one row, so collapse them first.
dynamics_by_pair: dict[frozenset[str], dict] = {}
for dyn in extracted.get("dynamics_updates", []):
    ...
    pair = frozenset((str(a_universal), str(b_universal)))
    entry = dynamics_by_pair.get(pair)
    if entry is None:
        dynamics_by_pair[pair] = {"a": a_universal, "b": b_universal,
                                  "descriptions": [description]}
    elif description not in entry["descriptions"]:
        entry["descriptions"].append(description)

for entry in dynamics_by_pair.values():
    db.execute("""INSERT INTO shared_dynamics (entity_a_id, entity_b_id, chapter_id, description)
                  VALUES (%s, %s, %s, %s)""",
               (entry["a"], entry["b"], chapter_id, " ".join(entry["descriptions"])))
```

`frozenset((a, b))` as the key is a small, elegant trick: a frozenset is unordered and
hashable, so `frozenset(("A","B")) == frozenset(("B","A"))`. Swapping the arguments
produces the same key. The descriptions are collected and joined rather than
last-one-wins, so nothing is lost.

And a guard that appears in both this pass and pass 7:

```python
if a_universal == b_universal:
    logger.warning(
        "dynamics_updates: entity_a %r and entity_b %r both resolved to the "
        "same entity; skipping self-referential dynamic", a_name, b_name,
    )
    continue
```

Two *different* names resolving to the same entity is normal and correct — that's what
Part 4's alias machinery is for. But a relationship between an entity and itself violates
the `CHECK (entity_a_id <> entity_b_id)` constraint, so it has to be caught before the
insert. The warning names both original strings, so you can see *which* alias pair
collapsed.

---

## Pass 9 — `scene_segmentation`

```python
"scene_segmentation": {
    "scenes": [
        {
            "scene_index": "integer (0-based)",
            "pov_character_name": "string|null",
            "location_name": "string|null",
            "time_anchor": "string|null",
            "present_character_names": ["string"],
            "summary": "string (~50 words)",
            "starts_at_excerpt": "string (~30 chars verbatim from chapter text where the scene begins)",
        }
    ]
}
```

Added because **a chapter is too big a unit for retrieval.** A 4,000-word chapter might
contain a tavern conversation, a road journey, and a fight. Embed the whole thing and you
get a vector that means "generic fantasy chapter" — the three distinct topics average out
into mush. Search for "the tavern conversation" and this chapter competes with every
other chapter that has any tavern in it.

Scenes give you a finer retrieval granularity: each gets its own summary, its own
embedding, and its own row in the BM25 index. The hybrid retriever ([Part 7](07-search.md))
searches chapters, scenes, and events as three separate kinds and fuses the results.

The instruction defines what a scene *is*, because it's genuinely ambiguous:

```
Segment the chapter chunk into discrete scenes. A scene is a continuous
unit of action sharing the same time, place, and (usually) POV. A new
scene begins on a hard break: location change, time jump, POV switch,
explicit scene divider, or a clear shift of focus.
...
Prefer fewer, well-defined scenes over over-segmentation. If the entire
chunk is one scene, return a single entry with scene_index=0.
```

"Prefer fewer" is a deliberate bias. Over-segmentation is the more likely failure — a
model given a segmentation task will find boundaries — and it degrades retrieval by
producing lots of tiny, near-identical summaries that then compete with each other.

### The `starts_at_excerpt` trick

That last field looks decorative. It's load-bearing.

Chunks overlap by 200 tokens (Part 2). So a scene that begins inside the overlap gets
reported by *both* chunks. You need to deduplicate. But `scene_index` is per-chunk — both
copies might be `scene_index: 0` — so it can't identify anything.

`starts_at_excerpt` is a verbatim ~30-character quote of where the scene starts. The same
scene reported by two chunks quotes the same text:

```python
# Scenes: dedupe across chunks by starts_at_excerpt (a scene re-reported
# by an overlapping chunk shares its verbatim anchor; scene_index is
# per-chunk so it cannot disambiguate) and renumber so scene_index is
# unique and ordered across the merged result. An empty excerpt carries
# no identity, so those scenes are always kept.
seen_scenes: set[str] = set()
collected_scenes: list[dict[str, Any]] = []
for extraction in extractions:
    for scene in extraction.get("scenes", []):
        excerpt = str(scene.get("starts_at_excerpt", "")).strip().lower()
        if excerpt:
            if excerpt in seen_scenes:
                continue
            seen_scenes.add(excerpt)
        collected_scenes.append(scene)
# Re-index sequentially based on appearance order.
for new_idx, scene in enumerate(collected_scenes):
    scene["scene_index"] = new_idx
```

Asking the model for a verbatim anchor gives you an identity key you can compare without
any semantics. Same technique as Part 4's grammatical anchor, used for a completely
different purpose: **when you need to identify something across independent LLM calls, ask
for a quote from the shared source.**

And the empty-excerpt carve-out is the right default: a scene with no anchor has no
identity, so it's always kept. Dropping it would risk losing a real scene to a missing
field; keeping it risks a duplicate. Duplicates are the cheaper error.

---

## Pass 10 — `multi_granularity_summaries`

```python
"multi_granularity_summaries": {
    "summary_short": "string (~50 chars, one sentence)",
    "summary_medium": "string (~150 words, one paragraph)",
    "summary_long": "string (~500 words)",
}
```

Three summaries, because one summary length can't serve three jobs:

- **short** (~50 chars) — a list item. "Kessa steals the lantern." Fits in a table cell.
- **medium** (~150 words) — the default display summary, and what the chapter's embedding
  is computed from.
- **long** (~500 words) — a detailed recap for someone who needs the chapter's content
  without re-reading it.

They also feed the BM25 index at different weights (Part 7 covers weighting):

```sql
setweight(to_tsvector('english', coalesce(NEW.title, '')),          'A') ||
setweight(to_tsvector('english', coalesce(NEW.summary_short, '')),  'A') ||
setweight(to_tsvector('english', coalesce(NEW.summary, '')),        'B') ||
setweight(to_tsvector('english', coalesce(NEW.summary_long, '')),   'C') ||
setweight(to_tsvector('english', coalesce(NEW.raw_text, '')),       'D');
```

A word that made it into the 50-character summary counts ten times as much as the same
word buried in the prose. That's an editorial judgement encoded as a weight: **brevity is
a signal of importance.** If a word survived compression to one sentence, the chapter is
about it.

The chapter embedding comes from the *medium* summary rather than the raw text, which is
worth a moment. Embedding 4,000 words of prose gives you an average of everything in it.
Embedding a 150-word summary gives you a vector for what the chapter is *about*, because
the summarisation step has already done the semantic compression — with a model that
understands the text, rather than by averaging token vectors.

---

## Pass 11 — `knowledge_state_deltas`

```python
"knowledge_state_deltas": {
    "learnings": [
        {
            "character_name": "string",
            "fact_description": "string (what they learned)",
            "source_type": "dialogue|observation|inference|witnessed|told|assumed",
            "source_character_name": "string|null",
            "certainty": "high|medium|low",
            "shared_with_character_names": ["string"],
        }
    ]
}
```

The theory-of-mind pass. Part 1 gave the justification: benchmarks put the best models at
**69%** on tracking who-knows-what against a **92%** human baseline, with one widely-used
model at chance. You cannot ask a model to reason about this at query time. You have to
record it.

The `source_type` enum is a genuine ontology of how knowledge is acquired:

| value | meaning |
|---|---|
| `dialogue` | told in conversation |
| `observation` | saw it directly |
| `inference` | deduced from other facts |
| `witnessed` | saw an event happen |
| `told` | received an explicit telling |
| `assumed` | taken on belief without evidence |

The distinctions are subtle — `dialogue` vs `told`, `observation` vs `witnessed` — and
they carry different reliability. Something `assumed` might be wrong; something
`witnessed` probably isn't. The `certainty` field carries that separately, mapped to a
float at persist time:

```python
_CERTAINTY_MAP = {"high": 1.0, "medium": 0.6, "low": 0.3}
```

Why let the model say "high" rather than "0.9"? Because models are poorly calibrated at
producing numeric confidences and reasonably good at three-way ordinal judgements. Asking
for the coarse thing you can trust, then mapping it to the fine-grained thing your schema
wants, is generally better than asking for the fine-grained thing directly.

`shared_with_character_names` handles the common case where several people are in the
room. One learning event, several knowers.

Two instructions do the pruning:

```
Skip background recollections. Skip the omniscient narrator's knowledge.
```

*Background recollections* — a character remembering something they've always known — are
not learnings. *The narrator's knowledge* is the biggest trap: in third-person omniscient
prose, the narration constantly states things no character knows. Without this line the
pass records the narrator's knowledge as everyone's, and the knowledge check ([Part
8](08-the-critic.md)) becomes useless, because everyone knows everything.

---

## Pass 12 — `commitments`

```python
"commitments": {
    "foreshadows_introduced": [
        {
            "foreshadow_text": "string (~1-2 sentences describing what was planted)",
            "trigger_predicate": "string (the condition under which this should pay off)",
            "weight": "low | medium | high — how central this is to the story",
            "related_entity_names": ["string"],
        }
    ],
    "payoffs_delivered": [
        {
            "payoff_text": "string (what was paid off this chunk)",
            "matches_foreshadow": "string (text of the original foreshadow if identifiable, else empty)",
        }
    ],
}
```

Chekhov's gun, as a data structure. The formalism is from a 2026 paper (CFPG) that models
narrative debt as **(Foreshadow, Trigger, Payoff)** triples maintained in a pool that's
eligibility-checked at every step.

How is this different from pass 6, `continuity_flags`? Overlapping, honestly. The flags
pass is a general "someone should remember this" observation. Commitments are
specifically the *pending-debt* structure, with a trigger condition and a payoff slot,
tracked to resolution. Flags are read-only observations; commitments have a lifecycle:

```
pending  ──▶  satisfied     the payoff happened
         ──▶  broken        the story contradicted the setup
         ──▶  abandoned     the setup was dropped
```

The `trigger_predicate` is the interesting field: *"when the protagonist confronts the
antagonist"*, *"when the locked door is opened"*. It's stored as JSONB with the intent of
eventual automated matching against events. Today it's free text and nothing evaluates it
— an honest piece of unfinished work rather than a claim.

### Matching payoffs to foreshadows

The hard part. Chapter 40 delivers a payoff; which of the 30 pending commitments does it
satisfy?

```python
def _best_pending_match(pending_rows, matches_text, payoff_text) -> str | None:
    best_id, best_score = None, 0.0
    needle_primary = matches_text.lower() if matches_text else ""
    needle_fallback = payoff_text.lower()

    for row in pending_rows:
        fs_text = str(row.get("foreshadow_text", "")).lower()
        score_primary = (SequenceMatcher(None, needle_primary, fs_text).ratio()
                         if needle_primary else 0.0)
        score_fallback = SequenceMatcher(None, needle_fallback, fs_text).ratio() * 0.6
        score = max(score_primary, score_fallback)
        if score > best_score:
            best_score, best_id = score, str(row["id"])

    # Require a reasonable similarity to avoid wild matches.
    if best_score < 0.45:
        return None
    return best_id
```

Two signals, deliberately weighted differently. If the model told us *which* foreshadow
this pays off (`matches_foreshadow`), that's the primary signal at full weight. If it
didn't, we fall back to comparing the payoff text against foreshadow texts — a weaker
signal, discounted by ×0.6 so it can never outrank a direct claim.

And a floor of 0.45, below which nothing matches. Same principle as everywhere else in
this series: when the evidence is weak, do nothing. A commitment left pending is visible
in the UI and easy to close by hand; a commitment wrongly marked satisfied disappears from
the pending list and is never seen again.

There's also a small guard against double-matching:

```python
satisfied.append(target_id)
# Drop it from the in-memory pending list so a second payoff
# doesn't latch onto the same row.
pending = [row for row in pending if str(row["id"]) != target_id]
```

Two payoffs in one chapter, both vaguely similar to the same foreshadow, would otherwise
both claim it — and the second would overwrite the first's `payoff_text`.

---

## Pass 13 — `canon_facts`

```python
"canon_facts": {
    "canon_facts": [
        {
            "subject_name": "string  # entity the fact is about, exactly as named in the chapter",
            "subject_type": "character|location|object|faction",
            "predicate": "string  # stable snake_case key, e.g. eye_color, home_town, weapon, title, sibling_of",
            "value": "string  # the fact's value, concise",
            "kind": "physical|relational|world_rule|backstory|other",
            "confidence": "number 0.0-1.0",
            "quote": "string  # verbatim supporting sentence from the chapter",
        }
    ]
}
```

Durable facts that later chapters must not contradict. This is what the critic's
entity-mention check reads ([Part 8](08-the-critic.md)).

The instruction is mostly about drawing a line between *canon* and *state*:

```
Extract DURABLE, objective facts that future chapters must not
contradict — physical traits (eye_color, hair_color, height), fixed
relations (sibling_of, parent_of), origins (home_town, birthplace),
possessions with identity (signature weapon), and hard world rules
(magic costs, physical laws of the setting).

Rules:
- predicate must be a stable snake_case key; reuse common predicates
  (eye_color, hair_color, title, home_town, weapon, sibling_of,
  parent_of, species, age) rather than inventing synonyms.
- Only facts explicitly stated or unambiguously shown in this chunk.
- SKIP transient state (mood, current location, temporary injuries),
  opinions, and speculation. Those belong to other passes.
- quote must be a verbatim sentence from the chunk supporting the fact.
- confidence: 1.0 for directly stated, lower for strongly implied.
```

*"Those belong to other passes."* Explicit routing. Without it, `canon_facts` and
`state_deltas` both try to record a character's mood, and the same information lands in
two tables with different semantics — one of which says it can never change.

The **predicate stability** rule is the load-bearing one, and it's a hard problem. Canon
facts are keyed on `(novel_id, subject_entity_id, predicate)`. If chapter 2 says
`eye_color` and chapter 40 says `eye_colour`, those are two separate facts and a
contradiction between them is invisible. Hence the explicit list of predicates to reuse.
It's a soft constraint — the model can still invent `iris_colour` — and there is no
predicate canonicalizer. Another honest gap.

### Persistence semantics

The rules are worth reading in full, because they encode a small policy:

```python
"""Semantics:
- new (novel, subject, predicate)        -> INSERT
- existing, unlocked, conf >= existing   -> UPDATE value/confidence/source
- existing, unlocked, conf <  existing   -> skip (keep stronger fact)
- existing, LOCKED, same value           -> no-op
- existing, LOCKED, different value      -> continuity_flag (canon_contradiction)
"""
```

Line 3 is the good one. A later chapter that re-states a fact with *lower* confidence
does not overwrite a higher-confidence earlier statement. Chapter 2 says directly "her
eyes were grey" (confidence 1.0); chapter 30 implies grey-ish (confidence 0.6). Without
this rule, the weaker statement wins purely by being later. **Recency is not evidence.**

Line 5 is the lock mechanism. A human can mark a fact locked via the UI. After that, a
contradicting extraction doesn't overwrite it — it raises a flag:

```python
if existing["locked"]:
    if not same_value:
        db.execute("""INSERT INTO continuity_flags (chapter_id, description, flag_type)
                      VALUES (%s, %s, %s)""",
                   (chapter_id,
                    (f"Chapter contradicts locked canon: {subject_name} "
                     f"{predicate} is locked to {existing['value']!r} but this "
                     f"chapter says {value!r}. Quote: {fact.get('quote') or 'n/a'}"),
                    "canon_contradiction"))
```

The flag text names the subject, the predicate, both values, and the quote. A human can
adjudicate it without opening the manuscript.

And a concurrency guard worth stealing:

```sql
INSERT INTO canon_facts (...) VALUES (...)
ON CONFLICT (novel_id, subject_entity_id, predicate) DO NOTHING
```

```python
# ON CONFLICT: a concurrent chapter job may have inserted the same
# (subject, predicate); losing this race must not abort the whole chapter transaction.
```

Two chapters processing concurrently can both find no existing row and both try to
insert. Without `ON CONFLICT DO NOTHING`, the loser raises a unique-violation — which, as
[Part 11](11-production.md) explains at length, aborts the entire transaction and can
silently lose the chapter. A four-word clause preventing a catastrophic failure.

---

## What a chapter costs

Let's do the arithmetic, because "thirteen passes" is the number people react to.

For a 6,000-word chapter — about 8,000 tokens, so **4 chunks** at 2,000 tokens with 200
overlap:

```
extraction passes         13 × 4 chunks              = 52 calls
intra-extraction dedup    ≤ 1 per entity type        ≈  4 calls
canonicalization          ≤ 1 per entity type        ≈  4 calls
critic claim extraction   1 per chapter              =  1 call
                                                      ───────────
                                                       ~61 LLM calls
```

Plus embeddings: one chapter, one per event (10–20), one per scene (2–5), one per new
commitment. Call it 25 embedding calls, which are far cheaper than completions.

Sixty-one completions per chapter. For a 70-chapter novel: **~4,300 LLM calls** for a
full ingest.

That is a lot, and the project is explicit that nobody knows the dollar figure:

> *No per-chapter cost/token accounting, no model tiering. A chapter is 13 extraction
> passes plus dedup, canonicalization, multi-summaries, scenes, knowledge, and
> commitments, all on one model (`DEFAULT_MODEL`).*
>
> *Done when: a per-chapter run record exists (tokens, cost, timings, finding counts) and
> low-stakes passes can route to a cheaper model via LiteLLM.*

Two things bound it in the meantime.

**It happens once.** Sixty-one calls per chapter, ever. Every subsequent question — every
wiki page, every agent lookup, every search — is SQL plus at most one query embedding. The
architectural bet from Part 1, restated numerically: pay 61 calls once, then answer
unlimited queries for approximately zero.

**Prompt size is bounded, even as the novel grows.** Two caps do this:

```python
canonicalizer_max_roster: int = 80    # entries per canonicalization call
context_max_characters:   int = 40    # roster entries injected into extraction prompts
context_max_locations:    int = 30
```

Without them, chapter 60's prompts would carry 90 characters and 50 locations into 52
calls. The mentioned-first selection from Part 2 keeps the *relevant* ones and back-fills
by recency. Cost per chapter stays roughly flat instead of growing with the cast.

And an obvious optimisation that hasn't been taken: **model tiering.** `chapter_summary`
and `multi_granularity_summaries` are undemanding and could run on a cheap model;
`canon_facts` and `state_deltas` are precision-critical and want a strong one. LiteLLM
makes this a one-line change. It hasn't been done because there's no cost measurement to
justify it against, and no eval attribution per pass to detect the quality regression it
might cause. Which is a nice illustration of how missing measurement blocks optimisation:
you can't safely take a trade whose downside you can't see.

---

## The mock extractor, in more detail

Part 2 introduced mock mode; here's what it actually produces, because the details show
what a mock is *for*.

```python
def _mock_extract(self, chunk: str, context: dict[str, Any]) -> dict[str, Any]:
    extraction = empty_extraction()
    sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
    non_empty = [s.strip() for s in sentences if s.strip()]

    extraction["summary"] = " ".join(non_empty[:3])[:1200]

    candidate_names = self._extract_candidate_names(chunk)
    for name in candidate_names:
        if name.lower() in known_characters:
            continue
        extraction["new_entities"]["characters"].append(
            {"name": name, "aliases": [],
             "description": "Auto-detected from chapter text (mock extractor)."})
        if len(extraction["new_entities"]["characters"]) >= 10:
            break

    for sentence in non_empty[:20]:
        extraction["events"].append({
            "description": sentence, "event_type": "action", "impact_level": "medium",
            "involved_characters": [n for n in candidate_names if n in sentence][:4],
            ...})
```

Capitalised words that aren't sentence-openers become characters. Sentences become
events. First three sentences become the summary.

The name detector:

```python
@staticmethod
def _extract_candidate_names(chunk: str) -> list[str]:
    blocked = {"The", "A", "An", "Chapter", "He", "She", "They", "It", "His", "Her",
               "Their", "When", "After", "Before", "Meanwhile", "But", "And", "Then"}
    candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", chunk)
    names = []
    for candidate in candidates:
        if candidate.split()[0] in blocked:
            continue
        if candidate not in names:
            names.append(candidate)
    return names
```

Wildly inaccurate. Deliberately so. But notice two decisions.

**One kind of mock output is deliberately empty:**

```python
# Mock knowledge-state deltas: emit nothing — knowledge extraction is
# too speculative to fake. Tests should not assume any rows.
```

Faking knowledge edges would let tests assert on data with no semantic basis, and those
tests would then constrain the *real* implementation to keep producing fake-shaped
output. Emitting nothing is more honest: tests that need knowledge rows must seed them
explicitly.

**One kind is deliberately derived from another:**

```python
# Mock foreshadow/payoff: reuse the continuity_flag heuristic so that
# tests can assert plumbing without inventing semantics.
for flag in extraction.get("continuity_flags", []):
    extraction["foreshadows_introduced"].append({
        "foreshadow_text": flag.get("description", ""),
        "trigger_predicate": "unknown", "weight": "medium", "related_entity_names": []})
```

Commitments are produced by reusing whatever the (equally crude) flag heuristic found. It
tests that the commitment persistence path runs, without pretending the commitments mean
anything.

> **A mock's job is to exercise the plumbing, not to approximate the model.** Where it
> can't do the first without faking the second, it should produce nothing.

---

## Where we are

Thirteen passes, each with a job, a schema that *is* its prompt, and a set of instructions
that mostly encode past failures. Every one of them emits **names**.

Names are the problem.

---

*Next: [Part 4 — Who Is "The Old Man At The Tavern"?](04-identity.md) — entity resolution,
the two kinds of error and why they're not equally bad, and the measurement that found the
real failure somewhere nobody was looking.*
