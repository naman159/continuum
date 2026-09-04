# Part 2 — From Prose to Rows

*Part 2 of the Continuum series. [Part 1](01-the-problem.md) established the goal:
read each chapter once, expensively, turn it into structured facts, then answer every
future question cheaply and exactly. This post builds the first version that does that.
Nothing here assumes you've used a database before.*

---

## Where we left off

We decided we want this:

```
chapter text  ──(expensive, once)──▶  structured facts  ──(cheap, forever)──▶  answers
```

Now we have to actually build the middle box. That means answering three questions, in
order:

1. **What shape** should the structured facts have? (What tables?)
2. **How** do we get prose into that shape? (What do we ask the LLM?)
3. **What breaks** when we try? (Spoiler: quite a lot.)

Let's take them in order. And let's start where the real project started — with a
deliberately small version, six extraction passes and a handful of tables, that took
about a day to write and immediately revealed which parts of the problem were actually
hard.

---

## Question 1: what shape?

### A crash course in relational databases

If you already know SQL, skim this section. If you don't, this is everything you need
for the entire series.

A **relational database** stores data in **tables**. A table is a grid: named columns,
and rows of values. Here's a table called `characters`:

```
characters
┌──────────────────────────────────────┬────────────────┬──────────────────────────┐
│ id                                   │ name           │ description              │
├──────────────────────────────────────┼────────────────┼──────────────────────────┤
│ 3f2a…c91                             │ Aelric Vane    │ A former city guard.     │
│ 8b04…d17                             │ Mira Solen     │ Aelric's younger sister. │
└──────────────────────────────────────┴────────────────┴──────────────────────────┘
```

Each row is one character. Each column holds one kind of value.

That `id` column is a **primary key** — a value guaranteed unique within the table, used
to refer to this row from anywhere else. Those particular ids are **UUIDs** (Universally
Unique Identifiers): 128-bit identifiers written as hex, of which 122 bits are random.
Continuum uses UUIDs for every primary key. Why not simple counting numbers (1, 2, 3…)?

- **They can be generated anywhere, without asking the database.** With counters you
  have to insert a row, wait, and read back the number it assigned. With UUIDs you can
  generate the id first and use it immediately.
- **They don't leak information.** If your character ids run 1…40, anybody who sees id
  39 knows roughly how many characters exist.
- **They don't collide across databases.** Merge two novels' data and integer ids fight;
  UUIDs don't.

The cost is that they're bigger (16 bytes vs 4) and unreadable to humans. For a system
with thousands of rows, not millions, that trade is free.

Now the crucial bit. Tables refer to each other by id. Here's `events`:

```
events
┌──────────┬────────────┬────────────────────────────────────────┬─────────────────────┐
│ id       │ chapter_id │ description                            │ involved_characters │
├──────────┼────────────┼────────────────────────────────────────┼─────────────────────┤
│ e1a…     │ c04…       │ Aelric takes a silver dagger from a…   │ {3f2a…c91}          │
│ e2b…     │ c12…       │ Aelric gives the dagger to Mira.       │ {3f2a…c91, 8b04…d17}│
└──────────┴────────────┴────────────────────────────────────────┴─────────────────────┘
```

`chapter_id` points at a row in the `chapters` table. That pointer is a **foreign key**.
Declaring it as one tells the database: *this value must correspond to a real row over
there*. If you try to insert an event pointing at a chapter that doesn't exist, the
insert is rejected. The database enforces the integrity for you — you can't get a
dangling reference by accident.

Foreign keys also let you say what happens on deletion:

```sql
chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE
```

`ON DELETE CASCADE` means: if that chapter row is deleted, delete this event too. This
turns out to be enormously useful — re-processing a chapter becomes "delete the chapter
row, and everything derived from it evaporates."

You query with **SQL**:

```sql
SELECT description
FROM events
WHERE chapter_id = 'c04…';
```

Read it as: *from the events table, give me the description column, for rows where
chapter_id equals this*. That's 90% of the SQL in this series. The rest is `JOIN`
(combine two tables by matching ids) and aggregate functions like `count(*)`.

One more thing you need: an **index**. Without one, `WHERE chapter_id = 'c04…'` makes
the database read *every row* in the table and check each one — a "sequential scan." An
index is a separate sorted structure (usually a B-tree) that lets it jump straight to
the matching rows. You create one like this:

```sql
CREATE INDEX idx_events_chapter ON events(chapter_id);
```

Indexes make reads fast and writes slightly slower, and they take disk space. The rule
is: index the columns you filter or join on. We'll come back to indexes in Post 11, where
forgetting some of them made a routine operation take minutes.

### Why Postgres and not something else

Three real alternatives, and why each loses.

**A graph database (Neo4j, Memgraph).** We're building a knowledge *graph* — characters
connected to objects connected to places — so a graph database sounds like the obvious
home. Graph databases earn their keep on **multi-hop traversal**: "find everyone within
four relationship-hops of Aelric who belongs to a faction that opposes the Council." In
SQL that's a recursive query and it's genuinely awkward.

But look at what we actually ask:

> *facts about entity X whose chapter-interval covers chapter N*

That's one filter on one indexed column. It is not a traversal. Almost every read in
Continuum has this shape. Adopting a second database — a second thing to run, back up,
monitor and keep in sync — to make the *rarest* query type marginally nicer is a bad
trade. The "graph" here is just tables with foreign keys, which is all the structure the
access patterns need.

**A document store (MongoDB).** Store each chapter's extraction as a JSON blob. This is
tempting because the LLM already gives us JSON. But then "who held the dagger in chapter
31" means loading 70 blobs and scanning them in application code. We'd be reimplementing
a query engine badly. The whole point is to get *out* of blob-land.

**A vector database (Pinecone, Weaviate, Chroma).** These store text embeddings and do
similarity search — very good at that, and nothing else. We need similarity search
*and* exact filtering *and* transactions *and* relational joins. A vector DB gives us
one of four.

**Postgres wins because it does all of it in one process:**

- ordinary relational tables and joins,
- **transactions** — a group of writes that either all happen or none do (Post 11 has a
  war story about this),
- `pgvector`, an extension that adds vector similarity search (Post 7),
- full-text search built in (also Post 7),
- array columns and JSON columns for the shapes that don't want their own table.

One system. One backup. One connection pool. For a workload measured in thousands of
rows per novel, there is no performance argument for anything else, and there's a very
strong operational argument for fewer moving parts.

### The MVP schema

Here's what the first real version created, in May 2026. I've stripped the SQL down to
essentials so you can see the shape.

```sql
CREATE TABLE novels (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title    TEXT NOT NULL,
    author   TEXT
);

CREATE TABLE chapters (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id     UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    number       INTEGER NOT NULL,
    title        TEXT,
    raw_text     TEXT NOT NULL,          -- ← the ground truth
    summary      TEXT,
    embedding    VECTOR(1536),           -- ← for Post 7
    processed_at TIMESTAMPTZ,
    UNIQUE(novel_id, number)
);

CREATE TABLE characters (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id                 UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name                     TEXT NOT NULL,
    aliases                  TEXT[] DEFAULT '{}',   -- ← Postgres array column
    first_appearance_chapter INTEGER,
    description              TEXT,
    UNIQUE(novel_id, name)
);

-- locations, factions, objects: same shape, different columns
```

Four things worth pausing on.

**`raw_text` is the ground truth.** The chapter's original prose is stored, forever,
untouched. Everything else in the database is *derived* from it. That single decision
buys an enormous amount of freedom: if you improve the extraction prompts, you can throw
away all the derived data and rebuild it. If you find a bug in how possessions are
tracked, you re-derive. You are never stuck with your first attempt, because you never
threw away the input. Write this down as principle #1:

> **Keep the input. Everything else is a cache.**

**`UNIQUE(novel_id, number)`** means one row per chapter number per novel. Try to insert
chapter 4 twice and the database refuses. That's a guard rail we get for free — the
application doesn't need to remember to check.

**`aliases TEXT[]`** is a Postgres array column: a list of strings inside one cell. In
strict relational theory this is heresy; you're supposed to make a separate
`character_aliases` table. In practice, aliases are always read together with the
character and never queried independently, so a separate table would be pure ceremony —
an extra join on every read for zero benefit. Postgres arrays can even be indexed and
searched (`WHERE 'Lizzy' = ANY(aliases)`), so we lose nothing.

**`UNIQUE(novel_id, name)`** on `characters` is doing something sneaky. It means the
character's *name* is effectively its identity. Two characters in the same novel can't
share a name. This looks like a sensible constraint and it is — but it also quietly sets
up the hardest problem in the entire system, which gets Post 4 all to itself. (Preview:
what happens when the same person is extracted as "Alice" from chapter 1 and "Ms. Vance"
from chapter 2?)

And here's `character_states`, the table that tries to answer "what was true when":

```sql
CREATE TABLE character_states (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id    UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    chapter_id      UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    location_id     UUID REFERENCES locations(id),
    emotional_state TEXT,
    goals           TEXT,
    knowledge       TEXT[] DEFAULT '{}',
    physical_state  TEXT,
    notes           TEXT
);
```

One row per character per chapter: where they were, how they felt, what they wanted,
what they knew. To ask "where was Aelric in chapter 12", you find the row with his
character_id and chapter 12's chapter_id.

This design has a serious flaw. I'll let you sit with it for a moment — we'll come back
at the end of the post, and Post 5 rebuilds it from scratch.

---

## Question 2: how do we get prose into that shape?

We have tables. We have a chapter of prose. We need the prose to become rows.

### Step 1: chunking

Chapters are long. Model context windows are finite. Even when a chapter *fits*, quality
degrades on very long inputs (the lost-in-the-middle effect from Post 1). So we cut the
chapter into overlapping windows.

Here's the real code:

```python
def sliding_window_chunks(text: str, chunk_size: int = 2000, overlap: int = 200):
    tokens = tokenize(text)            # tiktoken, encoding derived from DEFAULT_MODEL
    chunks, start = [], 0
    stride = chunk_size - overlap      # 1800

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunks.append(detokenize(tokens[start:end]))
        if end >= len(tokens):
            break
        start += stride
    return chunks
```

The defaults are `CHUNK_SIZE=2000` tokens and `CHUNK_OVERLAP=200` tokens. Picture it:

```
chapter (5000 tokens)
│
├───────── chunk 0 ─────────┤                            tokens    0 – 2000
                      ├───────────── chunk 1 ────────────┤         1800 – 3800
                                             ├──────── chunk 2 ────┤  3600 – 5000
                      └─ 200 overlap ─┘
```

**Why overlap at all?** Because a sentence — or worse, an *event* — can straddle a
boundary. Suppose the text reads:

> *"…she reached for the dagger on the table. | Aelric caught her wrist before she
> could take it."*

If the cut lands at `|`, chunk 0 sees an attempted grab, chunk 1 sees a wrist being
caught, and neither has the whole picture. The 200-token overlap (about 150 words, two
or three sentences) means the boundary text appears in *both* chunks. One of them will
have the complete event.

**Why 200 and not 500?** Overlap is pure duplicated cost — you pay for those tokens
twice, and you have to deduplicate the results afterward. 10% is enough to cover a
sentence or two of straddle without materially inflating the bill. There is nothing
sacred about the number; it's the smallest value that reliably covers a straddling
sentence.

**Why token-based chunks and not, say, paragraphs?** Because the constraint we're
respecting is a *token* limit. Paragraphs vary from one line to two pages; chunking by
paragraph gives you no control over prompt size. (Token windows have their own cost —
they cut mid-sentence and mid-scene, which is what the overlap above is paying for.)

The tokenizer itself used to be a fallback-tolerant helper, and that turned out to be a
mistake worth walking through:

```python
def tokenize(text: str):                 # the old version
    enc = _encoding()          # tiktoken.get_encoding("cl100k_base")
    if enc is not None:
        return enc.encode(text)
    return text.split()        # whitespace fallback
```

The reasoning at the time was **degrade, don't die, when the degradation is safe** —
if `tiktoken` couldn't load, chunks became word-counted instead of token-counted, and
the system still ran. Both halves of that sentence were wrong.

*Not safe.* Whitespace tokens are coarser than BPE, so a fixed `chunk_size=2000` packs
**more** text per chunk, not less — measured on this project's own prose, 4,980
`cl100k_base` tokens against 3,357 whitespace tokens, a factor of 1.48. And
`detokenize` re-joined on single spaces, flattening every paragraph break in the
chapter before the extraction prompt ever saw it. Scene boundaries are precisely what
the extractor reads structure from.

*Not rare.* `tiktoken` downloads its vocab on first use and caches it under `TMPDIR` —
which macOS and most CI runners sweep periodically. The fallback wasn't guarding
against a missing dependency so much as against a cleared temp directory, on a machine
that had worked the day before.

So it's gone. The encoding is now derived from `DEFAULT_MODEL`, so the chunker and the
model can't drift into different vocabularies, and a tokenizer that won't load raises
instead of quietly producing different chunks. Post 11 works through the general rule
this violates.

### Step 2: asking an LLM for JSON

Now the interesting part. We hand a chunk to a model and want structured data back.

The mechanism is simple: describe the exact JSON shape you want, then insist on it.

```python
completion(
    model="gpt-4o-mini",
    temperature=0.1,
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ],
)
```

Three parameters, three decisions:

**`response_format={"type": "json_object"}`** is **JSON mode**, a feature most providers
now support. It constrains the model's output at the sampling level so that the result is
guaranteed to parse as JSON. Without it, models love to reply with

> Sure! Here's the extraction:
> ```json
> {...}
> ```
> Let me know if you'd like me to adjust anything.

...which is not JSON, and which your parser chokes on. JSON mode removes an entire class
of failure. Note what it does *not* do: it guarantees *valid JSON*, not JSON matching
*your* schema. You still have to validate the fields yourself.

**`temperature=0.1`.** Temperature controls randomness in sampling. At 0, the model
always picks its highest-probability next token; at 1.0 it samples more freely; above
that it gets wild. For creative writing you want high temperature. For *extraction* you
want the opposite — there is one correct answer to "which characters appear in this
passage" and you want it every time. Why 0.1 and not exactly 0? Slight non-zero
temperature avoids some models' tendency to fall into degenerate repetition loops at
exactly 0, at essentially no cost in determinism.

**LiteLLM.** The `completion` function comes from a library called LiteLLM, which
presents one interface over many providers (OpenAI, Anthropic, Google, local models).
Continuum reads a model string from config — `DEFAULT_MODEL`, defaulting to
`gpt-4o-mini` — and LiteLLM routes accordingly. The benefit is concrete rather than
philosophical: when you want to test whether a cheaper model degrades extraction
quality, you change one environment variable, not your code.

And the belt-and-braces parser, for when JSON mode isn't available or the model finds a
way to misbehave anyway:

```python
def safe_json_loads(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Fall back to the outermost {...} slice — models sometimes wrap JSON in prose.
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}
```

Note the last line. On total failure it returns an empty dict, not an exception. A
single unparseable pass yields no facts for that pass; it does not kill the chapter.

### Step 3: the pass structure — and why six calls beat one

Here's the design decision that makes the biggest difference to extraction quality, and
it looks wrong at first.

The obvious approach: one giant prompt. *"Read this chunk and return characters,
locations, events, plot threads, and foreshadowing, all in one JSON object."* One call,
minimum cost.

Continuum does not do that. The MVP made **six separate calls per chunk**, each asking
for exactly one thing:

```python
PASS_ORDER = [
    "chapter_summary",     # summarize this chunk
    "new_entities",        # characters / locations / factions / objects
    "entity_deltas",       # what changed for each character
    "events",              # what happened
    "thread_updates",      # plot threads opened / advanced / closed
    "continuity_flags",    # foreshadowing and planted details
]
```

Six calls cost six times as much as one. Why on earth?

**Reason 1: attention is finite, and a schema is a set of instructions.** A combined
prompt has to hold five different definitions in play simultaneously — what counts as an
event, what counts as a plot thread, what counts as foreshadowing, the difference
between a location and a faction, and so on. Each additional instruction competes for
the model's attention with all the others. In practice, big multi-part schemas get
partially filled: the model does an excellent job on the first two fields and a
perfunctory job on the last three. A focused prompt whose entire job is "find the
events" produces noticeably better events.

**Reason 2: you can debug it.** This is the one that matters most in practice. Suppose
plot threads come out badly. With six passes you look at the plot-thread prompt, change
it, and re-run *that pass*. Nothing else moves. With a monolithic prompt, every edit
perturbs every output, and you can never tell whether your fix helped or whether the
model just rolled differently. **Independent passes are independently tunable, testable,
and measurable.** Come Post 10, when we build an eval harness, this is what makes it
possible to attribute a quality change to a specific prompt.

**Reason 3: failures are contained.** If the commitments pass returns garbage, you lose
commitments for that chunk. In a monolithic design, one malformed field can invalidate
the whole object.

**Reason 4: passes can specialise.** Different jobs deserve different rules. Look at the
finished object-extraction instruction:

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
```

That level of specificity — including a fix for a real bug about two identical cars —
is only writable because the prompt has one job. You could not sanely fold all thirteen
of the current passes' instructions into one prompt; it would be pages long, and the
model would follow about a third of it.

**What's the cost, honestly?** Six passes per chunk, three chunks per chapter, is 18
LLM calls per chapter instead of 3. The current system with thirteen passes is 39, plus
dedup and canonicalization calls — [Part 3](03-extraction-passes.md) does the full
arithmetic. That is genuinely a lot, and Post 11 lists "no
per-chapter cost accounting" as an unresolved gap in the project — nobody can currently
tell you what one chapter costs. But recall the bet from Post 1: this is write-time
cost, paid once per chapter, and it buys read-time queries that involve no LLM at all.

**Does pass order matter?** Somewhat. `new_entities` runs early because later passes
reference entities by name, and the context assembled for later passes benefits from the
earlier ones. But the passes are not *strictly* dependent — each gets the same chunk and
the same story context. The order is more about conceptual grouping than data flow.

### Step 4: what a pass actually looks like

Here's the whole prompt-building path for one pass, unabridged. First the system prompt:

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

The schema is a Python dict describing the shape, serialised into the prompt. For
`events` it looks like this:

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

Notice the enums written as pipe-separated strings — `"action|revelation|death|…"`. That's
not real JSON Schema; it's a convention the model reads perfectly well, and it keeps
`event_type` from becoming a free-for-all of 200 distinct values.

And the user prompt:

```python
def build_user_prompt(pass_name, chunk, context, custom_entity_types=None):
    context_block = build_context_block(context)
    task = PASS_TASK_INSTRUCTIONS.get(
        pass_name, f"Execute the {pass_name} pass and return JSON only."
    )
    return dedent(f"""
        STORY CONTEXT (summarized JSON)
        {json.dumps(context)}

        CHAPTER CHUNK
        {chunk}

        TASK
        {task}

        Return JSON only.
    """).strip()
```

That `STORY CONTEXT` block is doing critical work, and it's worth its own section.

### Step 5: story context — telling the model what already exists

Extract chapter 12 in isolation and the model has no idea that "Mira" was introduced
eleven chapters ago. It will confidently report her as a *new* character. Do that for
every chapter and you get a database full of duplicates.

So before extraction, we load what we already know:

```python
def load_story_context(db, novel_id, chapter_number, chapter_text=""):
    characters   = db.fetchall(...)   # every character + their latest known state
    locations    = db.fetchall(...)
    open_threads = db.fetchall(...)   # plot threads not yet closed
    recent_events= db.fetchall(...)   # events from the last ~3 chapters
    ...
```

and inject it into every pass's prompt. The `new_entities` instruction then says:

```
Extract ONLY entities that are genuinely new — not already present in the
STORY CONTEXT above.
```

Two details in that function are worth noticing because they're the kind of thing you
only learn by running the system on a real book.

**The character query uses a `LATERAL` join to get each character's most recent state
*before* this chapter:**

```sql
SELECT c.id, c.name, c.aliases,
       ls.emotional_state, ls.goals, ls.physical_state, ls.last_chapter
FROM characters c
LEFT JOIN LATERAL (
    SELECT cs.emotional_state, cs.goals, cs.physical_state, ch.number AS last_chapter
    FROM character_states cs
    JOIN chapters ch ON ch.id = cs.chapter_id
    WHERE cs.character_id = c.id
      AND ch.number < %s          -- ← strictly before the chapter being processed
    ORDER BY ch.number DESC
    LIMIT 1
) ls ON true
WHERE c.novel_id = %s
```

`LEFT JOIN LATERAL` means "for each row on the left, run this subquery, which is allowed
to reference that row." It's the standard SQL way to say "give me the latest child row
per parent." The `ch.number < %s` is the first appearance of spoiler-safety in this
system: even while *writing* the database, we don't show the extractor states from
chapters that (from this chapter's point of view) haven't happened.

**`recent_events` deliberately looks at only the last three chapters:**

```sql
WITH max_number AS (
    SELECT COALESCE(MAX(number), 0) AS value
    FROM chapters WHERE novel_id = %s AND number < %s
)
SELECT e.id, e.description, e.event_type, e.impact_level, ch.number
FROM events e JOIN chapters ch ON ch.id = e.chapter_id
CROSS JOIN max_number
WHERE ch.novel_id = %s
  AND ch.number BETWEEN GREATEST(max_number.value - 2, 1) AND max_number.value
ORDER BY ch.number DESC, e.created_at DESC
LIMIT 200
```

Why three chapters and not all of them? Because context has to be bounded. Every event
from a 70-chapter novel would be thousands of rows and would blow the prompt budget
we're trying to protect. Three chapters is a judgement call: enough to catch "the thing
that just happened," not so much that we're back to pasting the book.

**And a problem that shows up around chapter 30.** By then you have 60 characters and 40
locations, and dumping all of them into 13 prompts × 3 chunks is a lot of tokens spent
on cast members who aren't in this chapter. The fix — added later, but let's mention it
here since we're on the topic — is to *select* rather than dump:

```python
def select_context_entities(chapter_text, roster, *, cap):
    if cap <= 0 or len(roster) <= cap:
        return list(roster)

    mentioned = [e for e in roster if _mentioned(text_lower, e)]
    if len(mentioned) >= cap:
        return mentioned[:cap]

    rest = sorted(others, key=_recency, reverse=True)
    return [*mentioned, *rest[: cap - len(mentioned)]]
```

The rule is: **anyone whose name or alias literally appears in the chapter text is
always included**; the remaining slots are filled by whoever appeared most recently.
Caps default to 40 characters and 30 locations. The mention test is a word-boundary
regex, so "Al" doesn't match "Always":

```python
re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text_lower)
```

and there's an unglamorous unicode detail — chapter text from EPUB files uses curly
apostrophes (`’`) while your database has straight ones (`'`), so both sides get
normalised before comparing. Miss that and every character with an apostrophe in their
name silently fails the mention test.

### Step 6: merging the chunks back together

Each chunk produces its own extraction. A three-chunk chapter gives three JSON objects.
They have to become one, and the overlap guarantees duplicates.

`merge_extractions()` does this, and it merges *differently per field*, which is the
whole point:

**Entities: deduplicate by name, but merge their fields.**

```python
def _dedupe_by_name(items, *, include_owner=False):
    deduped = {}
    for item in items:
        name = str(item.get("name", "")).strip()
        owner = str(item.get("owner_name") or "").strip().lower() if include_owner else ""
        key = (name.lower(), owner)
        if key not in deduped:
            deduped[key] = item
            continue
        existing = deduped[key]
        for field, value in item.items():
            if field not in existing or not existing[field]:
                existing[field] = value           # fill blanks
            elif isinstance(existing[field], list) and isinstance(value, list):
                existing[field] = list(dict.fromkeys([*existing[field], *value]))  # union lists
    return list(deduped.values())
```

If chunk 0 saw "Aelric" with a description and chunk 1 saw "Aelric" with an alias, the
merged record has both. Blank fields get filled; list fields get unioned.

That `include_owner` flag is a scar from a real bug. Objects are keyed by
`(name, owner)`, not name alone, because **two characters can each own "a black sedan"
and those are two different cars.** Key on name only and Sarah's car merges into Jake's,
and every subsequent fact about either attaches to a single phantom vehicle.

**Events: deduplicate by exact description.** Crude — two chunks describing the same
event in slightly different words both survive — but the alternative (semantic
similarity matching) risks collapsing two genuinely distinct events, which is worse.
When in doubt, keep both.

**Summaries: last non-empty chunk wins.**

```python
for field in ("summary_short", "summary_medium", "summary_long"):
    for extraction in reversed(extractions):
        value = str(extraction.get(field, "") or "").strip()
        if value:
            merged[field] = value
            break
```

Later chunks in a chapter tend to contain the closing recap, which usually covers the
most ground. Reversed iteration with a `break` means "prefer the last, fall back
backwards."

**State changes: pure concatenation, no dedup at all.**

```python
# Chunk order across extractions IS the narrative order the replay folds in,
# so no dedup/merge-by-key here.
merged["state_deltas"] = [...]
```

This is the opposite policy from every other field, deliberately. State changes are a
*sequence*: gain the dagger, lose the dagger, gain it back. Deduplicating "Aelric gains
the dagger" against "Aelric gains the dagger" would erase a genuine second acquisition.
And chunk order is narrative order, so concatenating preserves the order in which things
happened. That ordering becomes load-bearing in Post 5.

The lesson generalises: **there is no such thing as "merge two JSON objects." There is
only merging *this* field, with a policy justified by what the field means.**

---

## Question 3: what breaks?

We have chunks, passes, merged extraction. Now we write to the database. And this is
where the MVP started teaching us things.

### Names are not identities

The extraction gives us `"involved_characters": ["Aelric", "Mira"]`. The database wants
UUIDs. Something has to map one to the other. That something is the **resolver**:

```python
class EntityResolver:
    def resolve_character(self, name, metadata=None, *, create=True):
        # 1. in-memory cache for this run?
        # 2. exact name match in the DB (case-insensitive)?
        # 3. alias match — is `name` in some character's aliases[]?
        # 4. (characters only) partial-name match: "Jane" ↔ "Jane Bennet"?
        # 5. create a new row — or return None if create=False
```

Steps 1–3 are unobjectionable. Step 5 is where the trouble is, and it is worth being
precise about the trouble, because it's the theme of Post 4.

Chapter 1 introduces "Alice Vance." Chapter 2 refers to her only as "Ms. Vance." The
resolver looks for an exact match — none. An alias match — none, because nobody ever
told it about that alias. So it creates a *second* character.

```
characters
┌──────────┬──────────────┐
│ 3f2a…    │ Alice Vance  │  ← chapter 1's Alice
│ 91cd…    │ Ms. Vance    │  ← chapter 2's Alice
└──────────┴──────────────┘
```

Now every fact splits. Half her events attach to one row, half to the other. The wiki
shows two characters. Ask "what does Alice know as of chapter 12" and you get half an
answer with no indication that it's half.

The first instinct is fuzzy string matching: if two names are 85% similar, merge them.
The project tried exactly that, using a library function `fuzz.ratio`. The commit that
removed it is blunt about why:

> *The previous approach (`fuzz.ratio` ≥ 85%) silently merged distinct characters whose
> names differed only by honorific ("Mr. Bennet" / "Mrs. Bennet"). It was removed.*

Run the numbers yourself. "Mr. Bennet" and "Mrs. Bennet" differ by one character out of
ten. Any string-similarity metric scores them extremely high. They are two different
people, married to each other, both central, and merging them destroys the book.

Note the *asymmetry* here, because it drives every design decision that follows:

- **A missed merge** leaves two visible rows. Somebody notices; one API call fixes it.
- **A wrong merge** silently welds two entities into one and drags every relationship,
  event, and possession on both along with it. There is no visible symptom. Unpicking it
  by hand is expensive and may be impossible.

So the MVP made the strict choice: **exact name or explicit alias, nothing else.** Better
to have duplicates you can see than merges you can't. That's a defensible position, and
also obviously not the end of the story. Post 4 is the whole story.

### Reference-only resolution: the phantom-character bug

Here's a bug that took a while to see clearly, and whose fix is a beautiful one-word
change.

The `events` pass emits `"involved_characters": ["Aelric", "the Tutorial"]`. The
resolver looks up "the Tutorial", finds nothing, and — because that's what it does —
**creates a character called "the Tutorial."**

Now your character list has a game UI element in it. On a LitRPG novel (a genre where
characters level up with visible stat screens) the character list fills with "Archer
Class", "Skills", "System Notification" — all things the extraction prompts explicitly
tell the model not to extract as characters, and all of which slip in anyway through the
*back door* of being mentioned by a downstream pass.

The realisation: **not every pass has the authority to create an entity.**

- `new_entities` is *authoritative*. Its entire job is deciding what exists. If it says
  there's a new character, there's a new character.
- Every other pass — events, scenes, state changes, knowledge, relationships — is
  *referential*. It mentions names that ought to already exist. If a name doesn't
  resolve, that's a signal that something is wrong with the name, not a licence to mint
  a row.

So `resolve_character` gained a `create` flag:

```python
def resolve_character(self, name, metadata=None, *, create=True):
    """
    With create=True (default, used by the authoritative new_entities pass)
    an unknown name is created. With create=False (reference-only passes —
    events, scenes, deltas, knowledge) an unknown name resolves to None and
    NO character row is minted.
    """
```

and every reference-only call site passes `create=False`:

```python
involved_characters = [
    resolved.entity_id
    for name in event.get("involved_characters", [])
    if str(name).strip()
    for resolved in (resolver.resolve_character(name, create=False),)
    if resolved is not None                # ← unresolvable name is dropped
]
```

An unresolvable reference is *dropped*, not created. We lose a link. We don't gain a
phantom. Given the asymmetry of costs, that's the right trade every time.

There's a subtle correctness detail hiding in the resolver too:

```python
if not create:
    # Don't cache the miss — a later authoritative pass (create=True)
    # may still create it.
    return None
```

Misses are not cached. If the events pass fails to resolve "Mira" at 10:00:01 and the
`new_entities` pass creates her at 10:00:02, a cached miss would keep every later
reference in that chapter broken.

### There is no such thing as a novel with no relationships

The MVP stored relationships as a JSONB blob inside `character_states`:

```sql
relationships JSONB DEFAULT '{}'::jsonb
```

roughly `{"Mira": "sister", "Kessa": "rival"}`. It works, sort of, and it's fast to
build. It falls apart the moment you ask a real question:

- *"Show me everyone who is a rival of anyone."* You'd have to load every state row from
  every chapter and parse every blob.
- *"When did Aelric and Kessa stop being allies?"* The blob has no time dimension beyond
  the chapter it sits in.
- *"Aelric owns the silver dagger."* Can't be expressed at all. This structure only
  connects characters to characters, because it lives on a character's state row.

That last one is the killer. Relationships in a story are not character-to-character;
they're entity-to-entity. A character belongs to a faction. A character owns an object.
A location is inside another location. A faction controls a location.

Which is what forced the single most important schema change in the project's history,
and the one that makes everything afterwards possible.

### The `entities` table

Every character, location, faction, and object gets a row in one shared table *first*:

```sql
CREATE TABLE entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id    UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,       -- 'character' | 'location' | 'faction' | 'object' | …
    name        TEXT NOT NULL,
    aliases     TEXT[] DEFAULT '{}',
    UNIQUE(novel_id, entity_type, name)
);
```

and the type-specific tables hang off it:

```sql
ALTER TABLE characters ADD COLUMN entity_id UUID REFERENCES entities(id);
ALTER TABLE locations  ADD COLUMN entity_id UUID REFERENCES entities(id);
ALTER TABLE factions   ADD COLUMN entity_id UUID REFERENCES entities(id);
ALTER TABLE objects    ADD COLUMN entity_id UUID REFERENCES entities(id);
```

Now a relationship is just two entity ids and it doesn't care what kinds they are:

```sql
CREATE TABLE relationships (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel_type     TEXT,
    from_chapter INTEGER,
    to_chapter   INTEGER,
    notes        TEXT,
    CHECK (entity_a_id <> entity_b_id)     -- nothing relates to itself
);
```

Picture the two-level structure:

```
                     ┌──────────────────────────────┐
                     │          entities            │   ← the universal id space
                     │  id · novel_id · type · name │
                     └───┬────────┬────────┬────────┘
             entity_id   │        │        │
          ┌──────────────┘        │        └──────────────┐
          ▼                       ▼                       ▼
    ┌───────────┐          ┌───────────┐           ┌───────────┐
    │characters │          │ locations │           │  objects  │  ← type-specific
    │ +desc     │          │ +parent   │           │ +signif.  │     detail
    │ +embedding│          │ +aliases  │           │ +aliases  │
    └───────────┘          └───────────┘           └───────────┘

    relationships(entity_a_id, entity_b_id)  ──▶ points at `entities`, any type
```

**Why not one table with nullable type-specific columns?** Because you'd have a
`parent_location_id` column that is meaningless for characters, a `significance` column
meaningless for locations, and so on — and every constraint would have to be conditional
on the type. Two levels keeps the shared identity in one place and the type-specific
detail properly typed.

**The cost** is that every entity now has *two* ids: a universal `entities.id` and a
typed `characters.id`. Getting them confused is a genuine footgun and the codebase
carries an explicit warning about it:

```
Mind the two id spaces: events.involved_* hold TYPED ids (characters.id etc.),
while relationships/shared_dynamics entity_a/b_id hold UNIVERSAL entities.id.
```

The resolver returns both, in a small dataclass, so the caller has to pick deliberately:

```python
@dataclass
class ResolvedEntity:
    entity_id: str       # type-specific ID (e.g. characters.id)
    universal_id: str    # entities.id — use for relationships / shared_dynamics
    created: bool
```

That's an honest design tax. The alternative — collapsing to one id space — would have
meant giving up either the type-specific tables or the polymorphic relationships. The
comment exists because someone got it wrong at least once.

### Mock mode: the escape hatch you need on day one

One more MVP decision, small but disproportionately useful. Every LLM-touching component
takes a `use_mock` flag:

```python
class ChapterExtractor:
    def __init__(self, *, use_mock: bool | None = None) -> None:
        completion = _load_completion()
        if use_mock is None:
            self.use_mock = settings.use_mock_llm or completion is None
        else:
            self.use_mock = use_mock
```

In mock mode, extraction runs on regex and sentence-splitting instead of an LLM:

```python
@staticmethod
def _extract_candidate_names(chunk: str) -> list[str]:
    blocked = {"The", "A", "An", "Chapter", "He", "She", "They", ...}
    candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", chunk)
    ...
```

Capitalised words that aren't sentence-starters become characters. Sentences become
events. It is not remotely accurate. **That is not the point.** The point is:

- CI runs the whole pipeline with **no API key and no network**,
- tests are deterministic — same input, same output, every time,
- it costs nothing, so you can run it in a loop while developing,
- and it exercises every line of plumbing between extraction and the database.

The rule that emerges: **separate the parts that are statistically correct from the
parts that are exactly correct, and test them differently.** Everything after the LLM
boundary — dedup, resolution, replay, cutoff filtering — is deterministic and gets
ordinary unit tests. The LLM boundary itself is statistical and needs an eval harness
(Post 10). Mock mode is the seam between the two.

It also has a sharp edge that bit hard later. Mock mode produces *hash-based fake
embeddings* — and those got written to the database indistinguishably from real ones,
silently poisoning search. Post 11 tells that story; the fix required a new column and a
hard failure.

---

## What we have, and the flaw we haven't fixed

At the end of the MVP, this worked end to end:

```
$ novel-pipeline create-novel --title "The Silver Dagger"
$ novel-pipeline process-chapter --novel-id <uuid> --number 1 --file ch1.txt
{
  "chapter_id": "…",
  "chunks": 3,
  "new_characters": 4,
  "new_locations": 2,
  "events": 11,
  "thread_updates": 2,
  "continuity_flags": 1
}
```

Chapters go in; a queryable knowledge base comes out. A CLI could print a character's
history, the timeline, open threads. That's a real system.

Now back to the flaw I asked you to hold. Look at `character_states` again:

```sql
CREATE TABLE character_states (
    character_id    UUID NOT NULL REFERENCES characters(id),
    chapter_id      UUID NOT NULL REFERENCES chapters(id),
    location_id     UUID REFERENCES locations(id),
    emotional_state TEXT,
    goals           TEXT,
    knowledge       TEXT[],
    physical_state  TEXT
);
```

One row per character per chapter, written directly by the extractor from what the LLM
said about that chapter.

**Question: where was Aelric in chapter 7?**

Suppose chapter 7 doesn't mention where he is. It's a chapter about someone else; he
appears in one line of dialogue. The extractor has nothing to say about his location, so
`location_id` is `NULL`.

The honest answer to "where was Aelric in chapter 7" is *"wherever he was in chapter 6,
since nothing moved him."* The table says `NULL`. The table has lost information that
the story clearly contains.

You can patch this by making the extractor "carry forward" the previous chapter's values
— and the MVP tried. But now consider:

**Question: does Aelric have the silver dagger in chapter 31?**

`character_states` doesn't have a possessions column at all. You could add one — an
array of object ids, carried forward, edited when the extractor notices a change. Now
ask:

- What happens when chapter 12 says "he gave it away" but the extractor phrases it as an
  event rather than a state change?
- What happens when you re-process chapter 12 after improving the prompt — does chapter
  31's carried-forward array get fixed?
- What happens if two chapters are processed out of order?
- What happens when you want to ask about chapter 8 *and* chapter 31 at the same time?

Every one of those questions has the same root. **We are storing the answer instead of
storing the reasoning that produces the answer.** A row that says "Aelric's location is
X" is a conclusion. Conclusions can't be re-derived, can't be audited, and go stale
the moment anything upstream changes.

The fix is to store the *changes* — "in chapter 4, Aelric gained the dagger"; "in chapter
12, Aelric lost the dagger, Mira gained it" — as an append-only log, and compute the
answer by replaying the log up to whatever chapter you care about. That idea has a name
(event sourcing), a set of consequences, and a post of its own.

But before we can do any of that, two things have to come first.

One is the rest of the extractor. Six passes was the MVP; the finished system runs
thirteen, and the seven additions are each there because a question couldn't be answered
without them. That's the next post, and it's where you'll see what the pipeline is
actually asking the model for.

The other is more basic still. Every one of those log entries starts with a *name*. If
"Aelric" in chapter 4 and "Aelric Vane" in chapter 12 are two different rows in your
database, your beautiful append-only log is recording the history of two people who don't
exist.

Identity is the post after that, and it's the hardest problem in the system.

---

*Next: [Part 3 — Thirteen Ways of Reading a Chapter](03-extraction-passes.md) — every
extraction pass the finished system runs, the problem each one was added to solve, the
prompt rules that are really bug fixes, and what one chapter actually costs.*
