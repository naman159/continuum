# Part 6 — Modelling a World

*Part 6 of the Continuum series. [Part 5](05-time.md) solved time. This post covers the
data-modelling decisions that aren't about time or identity: how relationships are
represented, how the schema stretches to genres it was never designed for, and how five
different kinds of connection get drawn as one graph.*

---

## Three questions this post answers

1. **Is "friend_of" the same in both directions?** (Harder than it sounds, and the obvious
   answer is wrong.)
2. **What happens when someone writes a novel with things your schema has no table for?**
   Cultivation realms. Character classes. Starship models.
3. **What does the whole story look like, drawn as one picture?**

Along the way, the footgun that has appeared in every post so far and finally gets a full
explanation: the two id spaces.

---

## Part one: relationships that point both ways, or don't

### Where we left the relationship table

Part 2 established it. Relationships connect two **entities** — any types — with a text
label and a chapter interval:

```sql
CREATE TABLE relationships (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    entity_b_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel_type     TEXT,
    from_chapter INTEGER,
    to_chapter   INTEGER,
    notes        TEXT,
    CHECK (entity_a_id <> entity_b_id)
);
```

Plus, added later:

```sql
ALTER TABLE relationships ADD COLUMN superseded_by_id  UUID REFERENCES relationships(id);
ALTER TABLE relationships ADD COLUMN evidence_event_ids UUID[] DEFAULT '{}';
ALTER TABLE relationships ADD COLUMN sentiment          FLOAT;
ALTER TABLE relationships ADD COLUMN "symmetric"        BOOLEAN;
ALTER TABLE relationships ADD COLUMN chapter_id         UUID REFERENCES chapters(id) ON DELETE CASCADE;
```

Two of those are worth pausing on before the main event.

**`chapter_id`** makes a relationship traceable to the chapter that asserted it, and
because it's `ON DELETE CASCADE`, re-processing a chapter cleanly removes the
relationships that chapter created. The schema is honest about the boundary:

```sql
-- relationships become traceable to the chapter that asserted them; rows with a
-- non-NULL chapter_id cascade-delete when that chapter is replaced (rows created
-- before this column existed have NULL and are not covered).
```

Rows written before the column existed have `NULL` and don't cascade. Stated, rather than
pretended away.

**`superseded_by_id`** is Part 5's invalidate-don't-delete pattern applied here. A
relationship that changes isn't overwritten; the old row stays and points at its
replacement.

Now the interesting column.

### `entity_a` and `entity_b` are arbitrary

When the extractor reports a relationship, it picks an order. Nothing makes that order
meaningful. "Aelric is Mira's brother" and "Mira is Aelric's sister" are the same fact,
and the extractor might emit either.

For some labels that's fine:

```
Aelric ──── spouse_of ──── Kessa
```

Swap the ends and the sentence still reads correctly. **Symmetric.**

For others it is not:

```
Toren ──── mentor_of ──▶ Mira
```

Swap the ends and you've asserted that Mira mentors Toren, which is a different claim
about the world. **Directional.**

A graph UI needs to know which is which, because it decides whether to draw an arrowhead.
Draw an arrow on a symmetric edge and you're asserting a direction the data doesn't have.
Omit one from a directional edge and you've lost the meaning.

### Attempt 1: a list of symmetric labels

The obvious approach — classify by the label:

```python
SYMMETRIC_REL_TYPES = frozenset({
    "spouse_of", "spouse", "married_to", "marriage", "husband_of", "wife_of",
    "sibling_of", "sibling", "brother_of", "sister_of", "twin_of",
    "cousin_of", "cousin", "friend_of", "friend", "best_friend_of",
    "colleague_of", "coworker_of", "co-worker_of", "partner_of",
    "business_partner_of", "ally_of", "allied_with", "rival_of", "rival",
    "enemy_of", "nemesis_of", "neighbor_of", "roommate_of", "classmate_of",
    "engaged_to", "in-law_of", "related_to",
})

def is_symmetric(rel_type: str | None) -> bool:
    if rel_type is None:
        return False
    return rel_type.strip().lower() in SYMMETRIC_REL_TYPES
```

The module docstring is honest about the limits from the first line:

```python
"""Classification of relationship-type labels as symmetric or directional.

`relationships.rel_type` is freeform text produced by the LLM extractor, not
a fixed enum, so this is a best-effort lookup rather than an exhaustive
mapping.
"""
```

`rel_type` is **free text**. The extractor can emit `"rival"`, `"rival_of"`,
`"bitter rival"`, `"arch-nemesis"`. A finite set can never cover a freeform column. Note
how the set hedges against the most common variation — both `spouse_of` and `spouse`, both
`rival` and `rival_of` — which is a maintenance treadmill by construction.

But that's the *small* problem. Here's the real one.

### Attempt 2: symmetry is a fact about the instance, not the label

Consider:

> *Aelric had considered Kessa a friend for eleven years. Kessa had considered him useful
> for eleven years.*

What's the relationship? `friend_of` — it's in the symmetric set. Draw it as an
undirected edge and the graph asserts a mutual friendship.

The novel's entire point is that it isn't mutual.

`friend_of` is *usually* symmetric. `rival_of` is *usually* symmetric. But whether a
particular relationship is mutual is a fact about **this pair, in this story**, and it is
in the prose, which the label has thrown away.

Some labels *are* symmetric by definition. `spouse_of` is mutual regardless of anyone's
feelings — it's a legal or social fact, not an attitude. `sibling_of` likewise. But
`friend_of`, `rival_of`, `ally_of` are all attitudes, and attitudes can be one-sided.

So the extractor was asked to judge each instance:

```
Set "symmetric" based on what the text actually shows, not what the label usually
implies:
  - true: both sides hold the relationship equally, or it is true by definition
    regardless of feelings (spouses, siblings, an explicitly mutual friendship).
  - false: the label reflects one entity's view or treatment of the other, and the
    other side's view isn't confirmed as the same (A believes they're B's friend
    but B doesn't reciprocate; A resents B with no indication B feels it back).
  - null: the text doesn't give enough signal either way.
Labels like "friend_of" or "rival_of" are NOT automatically mutual — judge each
instance from the text. If the two entities' feelings genuinely differ, emit two
separate rows (one per entity's actual stance, each with symmetric=false) rather
than forcing one shared label. For relationships that are definitionally mutual
(e.g. "spouse_of", "sibling_of"), emit exactly one row for the pair, not one per
direction.
```

Three-valued, and the third value is the important one. `null` means *"the text doesn't
say."* Not false, not true — unknown. Which is different from both, and forcing a boolean
would fabricate a judgement.

The last two sentences give the model a *procedure* for the hard case: when the two sides
genuinely differ, don't compromise on one shared label — emit **two rows**, one per
stance, each marked one-sided. That produces a graph with two arrows pointing opposite
ways with different labels, which is exactly right for the Aelric/Kessa case.

### Resolving the two signals

Now there are two sources: the per-instance judgement (which may be NULL) and the static
label lookup. Combining them:

```python
def resolve_symmetric(rel_type: str | None, stored_symmetric: bool | None) -> bool:
    """Prefer the extractor's per-instance judgment; fall back to the static label lookup.

    The extractor sees the actual chapter text and can tell a one-sided "friend_of"
    (A considers B a friend; B doesn't) from a genuinely mutual one, which a label-only
    lookup never can. `stored_symmetric` is the `relationships.symmetric` column, which
    is NULL for rows written before this field existed or when the extractor left it unset.
    """
    if stored_symmetric is not None:
        return bool(stored_symmetric)
    return is_symmetric(rel_type)
```

Four lines, and a general pattern worth naming:

> **When you have a specific signal that's sometimes missing and a general signal that's
> always available, prefer the specific one and fall back to the general.** The fallback
> covers old data and cases the specific signal declined to judge; it never overrides a
> real judgement.

And the schema comment records what each state means:

```sql
-- NULL = extractor didn't judge it; the API falls back to a static label heuristic
-- (reads/relationship_types.py). TRUE/FALSE = the extractor read the chapter text and
-- judged whether the relationship is genuinely mutual or reflects one side's view
-- (e.g. A considers B a friend, but B doesn't feel the same).
```

`resolve_symmetric` is also one of the six functions exempted from Part 9's cutoff
contract, and the exemption note explains exactly why:

```python
# Pure label-classification helpers: no `db`/`novel_id` param at all, no
# SQL, no point-in-time state to cut off.
```

It takes a string and a boolean and returns a boolean. There's nothing to filter.

### The reserved word

`symmetric` is a **fully reserved keyword** in PostgreSQL — it comes from
`BETWEEN SYMMETRIC`. So it parses as a bare identifier only after a dot
(`SELECT r.symmetric`) or as an alias (`AS symmetric`). Anywhere else it needs quoting.

The DDL quoted it. The `INSERT` didn't:

```python
db.execute("""
    INSERT INTO relationships (
        entity_a_id, entity_b_id, rel_type, "symmetric", from_chapter, to_chapter, notes, chapter_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
""", (...))
```

Before those quotes were added, **every relationship insert raised a syntax error.** It
went unnoticed for a while because it was swallowed by a best-effort exception handler —
the subject of [Part 11](11-production.md), where two bugs turn out to have been hiding
each other.

The fix note is the interesting part:

> *Covered by a round-trip integration test over all three column values (TRUE/FALSE/NULL)
> against a real database — a mocked cursor would have accepted the broken SQL.*

A mock cursor accepts any string you hand it. Only a real parser rejects a reserved word.
This is the same lesson as Part 4's foreign-key violation and Part 11's transaction-state
bug: **anywhere your code depends on database semantics, you need a test against a real
database.**

### Deduplication and repair

Persisting a relationship first checks for an existing active edge with the same
unordered pair and the same type:

```sql
SELECT id FROM relationships
 WHERE rel_type IS NOT DISTINCT FROM %s
   AND superseded_by_id IS NULL
   AND ((entity_a_id = %s AND entity_b_id = %s)
     OR (entity_a_id = %s AND entity_b_id = %s))
 LIMIT 1
```

Both orderings, and `IS NOT DISTINCT FROM` rather than `=` because `NULL = NULL` is not
true in SQL — without it, every untyped relationship re-inserts on every chapter. So
"Aelric–Kessa: rival" re-extracted in ten chapters stays one edge. **Different types
between the same pair coexist by design**: mentor *and* father is two rows, deliberately.

And when a merge repoints relationships onto a surviving entity ([Part 4](04-identity.md)),
that can create duplicates the persist-time check never saw. So the merge dedupes
afterwards, keeping the oldest:

```sql
DELETE FROM relationships a
 USING relationships b
 WHERE a.id <> b.id
   AND a.rel_type IS NOT DISTINCT FROM b.rel_type
   AND LEAST(a.entity_a_id::text, a.entity_b_id::text) = LEAST(b.entity_a_id::text, b.entity_b_id::text)
   AND GREATEST(a.entity_a_id::text, a.entity_b_id::text) = GREATEST(b.entity_a_id::text, b.entity_b_id::text)
   AND (a.entity_a_id = %s OR a.entity_b_id = %s)
   AND (b.entity_a_id = %s OR b.entity_b_id = %s)
   AND (a.created_at > b.created_at
        OR (a.created_at = b.created_at AND a.id::text > b.id::text))
```

`LEAST`/`GREATEST` on the two ids is the SQL way to canonicalise an unordered pair — it
sorts the two endpoints so (A,B) and (B,A) produce the same comparison key. Same trick as
the `frozenset` in the dynamics dedup, in a different language.

The last two lines are the tiebreak. Keep the older row, and if two rows have *identical*
timestamps — which happens constantly, because `now()` is transaction-stable and both were
written in the same transaction — fall back to comparing ids. Without that second clause,
neither row is "greater", the `DELETE` matches neither, and both survive.

> **Any "keep one of these" rule needs a total order.** A comparison that can return
> "equal" needs a tiebreaker, or the rule silently doesn't fire.

### Relationships vs shared dynamics

Two tables that look similar and mean different things:

| | `relationships` | `shared_dynamics` |
|---|---|---|
| what it is | a durable typed fact | how a pair behaved in one chapter |
| example | `sibling_of` | "they barely spoke; Mira left early" |
| lifetime | chapter interval, superseded | one chapter, one row |
| shape | categorical label | free text |
| constraint | dedup by (pair, type) | `UNIQUE(pair, chapter)` |

[Part 3](03-extraction-passes.md) covers the extraction and the `frozenset` collapse that
makes the unique constraint survivable.

The reason both exist: "Aelric and Mira are siblings" doesn't change and belongs in a
typed, queryable column. "In chapter 12 they were barely speaking" changes every chapter
and can't be typed at all without inventing an enum of moods. Forcing either into the
other's shape loses information.

---

## Part two: entity types the schema was never designed for

### The problem

The schema has four entity types: character, location, faction, object. Reasonable for
most fiction.

Now someone writes **LitRPG** — a genre where characters have visible game mechanics.
Their novel is full of things that matter enormously and are none of the four:

```
"Archer Class"       — a character class. Not a person. Not an object.
"Fireball"           — a skill. Not an object.
"The Iron Realm"     — a world/dimension. Bigger than a location.
"Soul Cultivation"   — a power system with rules.
```

Three bad options:

1. **Force-fit them.** "Archer Class" becomes an object. This is exactly what produced the
   cross-type duplicate disaster in [Part 4](04-identity.md) — the same string filed as a
   character in one chapter and an object in the next, fourteen times over.
2. **Ignore them.** The knowledge base misses the things the book is most about.
3. **Add tables.** `classes`, `skills`, `realms`, `power_systems`… and then `deities` for
   fantasy, `starship_classes` for sci-fi. Unbounded schema growth driven by genre.

The fourth option: **let the novel declare its own types.**

### The mechanism

A registry table:

```sql
CREATE TABLE novel_entity_types (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    novel_id    UUID NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    UNIQUE(novel_id, name)
);
```

And a relaxed constraint on `entities.entity_type` — it's plain `TEXT`, not an enum, so
any registered type name is valid. Custom entities live in the `entities` table only, with
no type-specific table of their own:

```python
"""Single source of truth for the built-in entity type -> typed table map.

Custom entity types live only in the ``entities`` table and are not listed
here; callers that support them fall back to ``entities`` explicitly.
"""

TYPED_TABLES: dict[str, str] = {
    "character": "characters",
    "location":  "locations",
    "faction":   "factions",
    "object":    "objects",
}
```

`TYPED_TABLES.get(entity_type)` returning `None` *is* the signal that this is a custom
type. Every component that handles both checks it the same way:

```python
table = TYPED_TABLES.get(entity_type)
if table is None:
    # Custom entity type: roster lives in the generic entities table.
    ...
```

or more tersely:

```python
table = TYPED_TABLES.get(entity_type, "entities")
```

One module owns the map; nobody else hardcodes table names. A small thing that keeps a
polymorphic system from sprawling.

### Genre presets

Asking a novelist to invent an entity taxonomy from scratch is a bad first-run experience.
So the system ships starting points:

```python
GENRE_PRESETS: dict[str, list[dict[str, str]]] = {
    "litrpg": [
        {"name": "realm",
         "description": "A distinct universe, dimension, or world that characters inhabit or travel to."},
        {"name": "power_system",
         "description": "A named system of abilities, cultivation, or magic with defined rules and tiers."},
        {"name": "class",
         "description": "A named character class, job, or profession granted by the system, including its evolutions or unique variants."},
        {"name": "skill",
         "description": "A named ability, spell, or technique granted by the system that a character gains, levels, or evolves."},
        {"name": "species",
         "description": "A distinct race, creature type, or non-human species with collective traits."},
    ],
    "high_fantasy": [
        {"name": "deity", "description": "A god, divine being, or supernatural entity..."},
        {"name": "species", "description": "A distinct race or non-human species (elves, dwarves, orcs, etc.)."},
        {"name": "magic_system", "description": "A named, rule-governed system of magic with distinct schools or limitations."},
    ],
    "xianxia": [
        {"name": "realm", "description": "A cultivation realm, spiritual plane, or distinct world..."},
        {"name": "cultivation_technique", "description": "A named cultivation method, martial art, or technique..."},
        {"name": "species", "description": "A distinct cultivator race, demon species, or supernatural being type."},
    ],
    "scifi": [
        {"name": "species", "description": "An alien race or distinct non-human sapient species."},
        {"name": "technology", "description": "A named technology, invention, or system with narrative significance beyond setting dressing."},
    ],
    "contemporary": [
        {"name": "institution", "description": "A named institution, corporation, or organisation larger than a faction..."},
    ],
}
```

The **descriptions matter more than the names**, because they go straight into the
extraction prompt. Look at the qualifiers:

- *"...with narrative significance beyond setting dressing"* — don't extract every gadget.
- *"...including its evolutions or unique variants"* — an upgraded class is the same class.
- *"...larger than a faction with structural importance"* — draws the boundary against the
  built-in type it's most likely to collide with.

Each one is a rule about where the category ends. A bare name (`"technology"`) would have
the extractor tagging every mention of a door.

Five genres, twelve type definitions, ~60 lines. Adding a genre is appending a dict entry.

### How a custom type reaches the extractor

Types are declared at novel creation:

```python
class NovelCreate(BaseModel):
    title: str
    author: str | None = None
    language: str = "en"
    custom_entity_types: list[NovelEntityTypeInput] = []
```

then loaded per chapter and injected into the `new_entities` prompt:

```python
def build_system_prompt(pass_name, custom_entity_types=None):
    schema = PASS_SCHEMAS[pass_name]
    if pass_name == "new_entities" and custom_entity_types:
        schema = dict(schema)                            # ← copy, don't mutate the module global
        schema["custom_entities"] = [{
            "name": "string",
            "type": " | ".join(t["name"] for t in custom_entity_types),
            "description": "string",
        }]
```

`schema = dict(schema)` is doing real work. `PASS_SCHEMAS` is a module-level dict; mutating
it in place would leak one novel's custom types into every subsequent novel's prompt in
the same process. A one-line shallow copy prevents a genuinely confusing cross-tenant bug.

The user prompt gets an extra block listing the types, their descriptions, and what's
already known:

```python
custom_block = dedent(f"""
CUSTOM ENTITY TYPES FOR THIS NOVEL
Extract entities of these types into the custom_entities array:
{type_lines}

Only extract if genuinely new and not already in STORY CONTEXT.
Each item: {{name, type (one of the types above), description}}.
{existing_lines}
""").strip()
```

And when a novel has no custom types, none of this appears at all. The prompt is identical
to a system without the feature — no dead instructions burning tokens.

### Validating what comes back

Models invent categories. Ask for one of five types and you'll sometimes get a sixth:

```python
def _normalize_custom_entities(custom_entities, custom_entity_types):
    """Drop extracted custom entities whose type isn't registered for the novel
    and normalize the type to its registered casing. Unregistered types would
    otherwise create entities rows that no dedup pass or UI page ever sees."""
    registered = {
        str(t.get("name", "")).strip().lower(): str(t.get("name", "")).strip()
        for t in custom_entity_types or []
        if str(t.get("name", "")).strip()
    }
    normalized = []
    for item in custom_entities or []:
        raw_type = str(item.get("type", "")).strip()
        match = registered.get(raw_type.lower())
        if match is None:
            logger.warning("custom entity %r has unregistered type %r — skipped",
                           item.get("name"), raw_type)
            continue
        item["type"] = match          # normalise casing to the registered form
        normalized.append(item)
    return normalized
```

Two jobs. **Drop unregistered types** — the docstring names the consequence precisely: an
entity with an unregistered type creates a row that no dedup pass and no UI page will ever
look at. Invisible data, permanently. **Normalise casing** — `"Skill"` becomes `"skill"`
if that's how it was registered, because `entities.entity_type` is compared as an exact
string everywhere downstream. `"Skill"` and `"skill"` would be two separate types with two
separate sidebar links.

The rest of the pipeline treats custom types as first-class. `collect_names_by_type` uses
`setdefault` so any type key works:

```python
for item in extracted.get("custom_entities", []) or []:
    ce_type = str(item.get("type", "")).strip()
    name = str(item.get("name", "")).strip()
    if ce_type and name:
        result.setdefault(ce_type, set()).add(name)
```

The canonicalizer runs on them with the generic rule set (the strict verbatim-anchor rule
is characters-only). Aliases go to `entities.aliases` since there's no typed table. The
API generates list and detail routes per type. The sidebar fetches the type list and adds
a nav link per type.

### The visibility problem custom types create

Every built-in type has a chapter anchor — `first_appearance_chapter` — so the cutoff
filter is easy. Custom entities live in `entities`, which has no such column.

So how do you know whether a cultivation realm should be visible at chapter 12?

```python
def list_custom_entities(db, novel_id, entity_type, up_to_chapter):
    """Custom entity rows carry no first_appearance anchor, so visibility is
    derived from the earliest relationship that references the entity (custom
    ids ARE universal entities.id), anchored like every relationship read:
    from_chapter, else the asserting chapter. An entity with no references
    has no derivable anchor and stays visible."""
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall("""
        SELECT e.id, e.name, e.entity_type, NULL AS description
        FROM entities e
        WHERE e.novel_id = %(novel_id)s AND e.entity_type = %(entity_type)s
          AND COALESCE(
                (SELECT MIN(COALESCE(r.from_chapter, rch.number))
                   FROM relationships r
                   LEFT JOIN chapters rch ON rch.id = r.chapter_id
                  WHERE r.entity_a_id = e.id OR r.entity_b_id = e.id),
                0) <= %(cutoff)s
        ORDER BY e.name
    """, {"novel_id": novel_id, "entity_type": entity_type, "cutoff": cutoff},
       dict_rows=True)
```

The anchor is **derived**: the earliest chapter in which any relationship referenced this
entity. Nested `COALESCE`s handle the gaps — a relationship's own `from_chapter`, else the
chapter that asserted it, else (outer `COALESCE`) `0`, meaning "no references at all,
always visible."

That last default is a deliberate choice in the *permissive* direction, and it's arguably
the wrong one for a spoiler-safety system. An entity with no relationships is visible at
every cutoff. The defence is that with no references anywhere, there's nothing to leak
except its name. It's a compromise, and the docstring states it rather than hiding it.

**The general lesson**: extensibility has a cost, and the cost shows up in the places you
didn't think about. Adding custom types was easy — one table, one prompt block, one
`TYPED_TABLES.get` fallback. Making them behave correctly under a point-in-time query
required inventing a whole derived anchor, because the property the built-ins get from a
column has to be computed for the extension.

---

## Part three: drawing the whole thing

There are two graph views, and the difference between them is the point.

### The relationship graph: characters only

```python
def relationship_graph(db, novel_id, up_to_chapter):
    char_rows = db.fetchall("""
        SELECT c.id, e.name, c.first_appearance_chapter
        FROM entities e
        JOIN characters c ON c.entity_id = e.id
        WHERE e.novel_id = %s AND e.entity_type = 'character'
          AND (c.first_appearance_chapter IS NULL OR c.first_appearance_chapter <= %s)
        ORDER BY e.name
    """, (novel_id, cutoff), dict_rows=True)
```

Characters, and explicit `relationships` rows between them. The classic character web.

Two filters worth noticing.

**"Active at the cutoff", not "started before the cutoff":**

```sql
WHERE COALESCE(r.from_chapter, rch.number, 0) <= %s
  -- Active at the cutoff: a relationship that ended before it is not
  -- a current relationship.
  AND (r.to_chapter IS NULL OR r.to_chapter >= %s)
  AND r.superseded_by_id IS NULL
```

That middle predicate is the fix for the bug [Part 9](09-serving-it.md) describes — a grep
of the whole repository found `to_chapter` in **zero** WHERE clauses, so an alliance that
broke in chapter 6 was still reported as current at chapter 20.

The `COALESCE(r.from_chapter, rch.number, 0)` chain is the anchor fallback again:
the relationship's own start chapter, else the chapter that asserted it, else 0.

**Isolated nodes are dropped:**

```python
# Only characters that actually appear in an edge are worth graphing —
edge_node_ids = {str(e["from"]) for e in edges} | {str(e["to"]) for e in edges}
nodes = [n for n in nodes if str(n["id"]) in edge_node_ids]
```

A character with no relationships contributes a dot floating in space. Thirty of them
make the graph unreadable. They're still in the character *list*; they're just not in the
*relationship* graph, which is about relationships.

### The entity graph: everything, five edge kinds

The relationship graph shows what the extractor *said* about relationships. But the
database contains far more connection than that. Two characters who appear in fourteen
events together are connected, even if no `relationships` row says so. A character who
carries an object is connected to it.

So the entity graph assembles **five kinds of edge**:

| kind | source | meaning | rendered |
|---|---|---|---|
| `relationship` | `relationships` | an explicit typed relationship | solid, labelled, arrowed unless symmetric |
| `dynamic` | `shared_dynamics` | how a pair behaved in a chapter | solid |
| `event` | co-occurrence in `events.involved_*` | appeared together | dashed |
| `possession` | `possesses_edges` | holds this object | dashed |
| `location` | `located_in_edges` | is at this place | dashed |

The last three are called **story edges** — connections the extractor never asserted
directly, derived from what happened.

Character-to-character co-occurrence is a self-join with a neat trick:

```sql
SELECT ca.entity_id::text AS "from",
       cb.entity_id::text AS "to",
       'event'            AS edge_kind,
       e.description      AS description
FROM events e
JOIN chapters ch ON ch.id = e.chapter_id
JOIN characters ca ON ca.id = ANY(e.involved_characters)
JOIN characters cb ON cb.id = ANY(e.involved_characters) AND cb.id > ca.id
...
WHERE ch.number <= %s
```

`cb.id > ca.id` generates each unordered pair exactly once. Without it you'd get both
(A,B) and (B,A), plus every self-pair (A,A). One comparison replaces a deduplication step.

### Collapsing story edges

Two characters in fourteen events would produce fourteen parallel edges. Unreadable. So
edges between the same pair collapse into one, with a precedence rule:

```python
_STORY_KIND_PRECEDENCE = ["dynamic", "event", "possession", "location"]

def merge_story_edges(raw: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for item in raw:
        a, b = item["from"], item["to"]
        pair = (min(a, b), max(a, b))          # canonical unordered key
        if pair not in grouped:
            grouped[pair] = {"from": a, "to": b, "edge_kind": item["edge_kind"],
                             "descriptions": [desc] if desc else []}
        else:
            existing = grouped[pair]
            cur_prec = _STORY_KIND_PRECEDENCE.index(existing["edge_kind"])
            new_prec = _STORY_KIND_PRECEDENCE.index(item["edge_kind"])
            if new_prec < cur_prec:
                existing["edge_kind"] = item["edge_kind"]
            if desc:
                existing["descriptions"].append(desc)
```

`(min(a,b), max(a,b))` canonicalises the pair — same idea as the SQL `LEAST`/`GREATEST`
and the Python `frozenset`, three times in three contexts.

The label degrades gracefully with volume:

```python
n = len(data["descriptions"])
label = data["descriptions"][0] if n == 1 else (f"{n} {kind_plural}" if n > 0 else None)
tooltip = "\n".join(data["descriptions"]) or None
```

One description → show it. Several → show a count ("14 events"). All of them go into the
tooltip. **The visible label is bounded; the detail is one hover away.** That's the right
shape for a graph edge, which has room for about four words.

The precedence order — dynamic > event > possession > location — is a claim about which
connection is most *informative* when a pair has several. "They had a tense exchange" says
more about a relationship than "they were both in the tavern." It's a judgement call, and
it lives in one named list rather than scattered through the merge logic.

### Which graph should you look at?

The relationship graph answers *"what does the book say about who these people are to each
other?"* — sparse, curated, high-confidence.

The entity graph answers *"what is actually connected in this story?"* — dense, derived,
includes objects and places.

Both are correct. They answer different questions, which is why both exist rather than
one being a superset.

---

## Part four: the two id spaces

This has come up in every post since Part 2. Time to lay it out completely, because it is
the single most confusing thing in the schema and the codebase warns about it in four
separate places.

### What they are

Every entity has **two** identifiers:

```
                    entities
        ┌────────────────────────────────┐
        │ id (UNIVERSAL)                 │  ← entities.id
        │ novel_id, entity_type, name    │
        └───────────────┬────────────────┘
                        │  entity_id (FK)
        ┌───────────────▼────────────────┐
        │ characters                     │
        │ id (TYPED)                     │  ← characters.id
        │ entity_id ──────────────────┐  │
        │ name, aliases, description  │  │
        └─────────────────────────────┼──┘
                                      └──▶ points back at entities.id
```

- **Universal id** = `entities.id`. Exists for every entity of every type.
- **Typed id** = `characters.id` / `locations.id` / `objects.id` / `factions.id`. Exists
  only for the four built-in types.

Custom entities have only a universal id — which is why their code path can look
deceptively simple, and why `resolve_custom_entity` returns the same value twice:

```python
self._cache[cache_key] = (universal_id, universal_id)
return ResolvedEntity(universal_id, universal_id, created=True)
```

### Which tables use which

```
UNIVERSAL (entities.id)          TYPED (characters.id, locations.id, …)
─────────────────────────        ──────────────────────────────────────
relationships.entity_a/b_id      events.involved_characters[]
shared_dynamics.entity_a/b_id    events.involved_locations[]
canon_facts.subject_entity_id    events.involved_objects[]
commitments.related_entity_ids[] events.involved_factions[]
state_deltas.subject_id          knows_edges.character_id
state_deltas.object_id           possesses_edges.character_id
located_in_edges.entity_id       possesses_edges.object_id
                                 character_states.character_id
                                 scenes.pov_character_id
                                 scenes.present_characters[]
                                 state_deltas.location_id  ← note!
```

Look at the last two lines of the right column. `located_in_edges.entity_id` is
**universal** (because anything can be somewhere — a character, an object) but
`state_deltas.location_id` is **typed** (`locations.id`), because it points at a location
row directly.

`state_deltas` uses *both id spaces in one table*: `subject_id` and `object_id` are
universal, `location_id` is typed. The docstring flags it:

```python
"""Reference-only resolution throughout: a delta naming an unknown entity is
dropped (with a warning), never minted. subject_id/object_id are universal
entity ids; location_id is the typed locations.id (matching the FK)."""
```

### Why not just have one

Two ways to collapse this, both worse.

**Drop the typed tables.** Put every attribute on `entities` as nullable columns:
`description` (characters), `parent_location_id` (locations), `significance` (objects).
Every constraint becomes conditional on the type, and every query has to know which
columns are meaningful for which type. Postgres can't help you enforce "objects must not
have a `parent_location_id`."

**Drop the universal table.** Then `relationships` needs `entity_a_type` alongside
`entity_a_id`, and there's no foreign key that can validate the pair — you've hand-rolled
polymorphism and lost referential integrity. This is exactly what the MVP had before the
`entities` refactor, and it's why relationships couldn't connect a character to an object
at all.

The two-space design is the price of having both **polymorphic references** (a
relationship between any two things) and **typed integrity** (an object can't have a
parent location). The cost is that every developer must know which space they're in.

### How the code copes

**The resolver returns both, in a named type:**

```python
@dataclass
class ResolvedEntity:
    entity_id: str       # type-specific ID (e.g. characters.id)
    universal_id: str    # entities.id — use for relationships / shared_dynamics
    created: bool
```

You can't get it by accident — you have to write `.entity_id` or `.universal_id`, and the
comment is right there.

**Every read site that crosses spaces documents it:**

```python
# Two id spaces are in play. events.involved_* store TYPED ids
# (characters.id / locations.id / objects.id / factions.id): pipeline.py's
# event insert uses resolver ResolvedEntity.entity_id, the type-specific
# id. So the events query filters on `character_id` (the typed PK) and
# involved_* names resolve via the typed tables. By contrast,
# relationships/shared_dynamics entity_a/b_id are UNIVERSAL entities.id
# (the pipeline uses ResolvedEntity.universal_id there), so those resolve
# via characters.entity_id.
char_entity_id = str(identity_row["entity_id"]) if identity_row.get("entity_id") else None
```

**The graph query joins across both, and says why:**

```sql
SELECT e.id::text AS id,
       e.name AS label,
       e.entity_type,
       COALESCE(c.id, l.id, o.id, f.id, e.id)::text AS native_id
FROM entities e
LEFT JOIN characters c ON c.entity_id = e.id
LEFT JOIN locations  l ON l.entity_id = e.id
LEFT JOIN objects    o ON o.entity_id = e.id
LEFT JOIN factions   f ON f.entity_id = e.id
WHERE e.novel_id = %s
  -- The typed LEFT JOINs are mutually exclusive, so COALESCE picks
  -- the one first_appearance anchor this entity has; factions and
  -- custom entities carry none and stay visible at every cutoff.
```

Four `LEFT JOIN`s, one of which will match. `COALESCE` picks whichever typed id exists,
falling back to the universal id for custom entities. The node's `id` is universal (for
edges) and its `native_id` is typed (for linking to a detail page). Both, explicitly, in
one row.

**And the merge repoints both, in the right order:**

```python
# state_deltas.subject_id/object_id reference entities(id) directly (not a
# typed table) and CASCADE on delete — repoint before the source entity is
# dropped, or tier-2 extraction data is silently destroyed.
cur.execute("UPDATE state_deltas SET subject_id = %s WHERE subject_id = %s", (tgt, src))
...
# state_deltas.location_id references locations(id) (the typed row),
# not entities(id) — repoint with the typed ids, same CASCADE-before-delete
# reasoning as the subject/object repoint above.
cur.execute("UPDATE state_deltas SET location_id = %s WHERE location_id = %s", (tt, st))
```

Note `(tgt, src)` — universal ids — in the first, and `(tt, st)` — typed ids — in the
second. Same table, same operation, different id space, four lines apart. Get it backwards
and you either lose data or corrupt it.

> **A polymorphic schema that keeps typed integrity will have two id spaces. There is no
> way to avoid it. What you can do is name them, return both from one place, and comment
> every crossing.** The alternative — a convention everyone is supposed to remember — fails
> the first time someone new touches the code.

---

## Where we are

The world model is complete: entities of any type, relationships that know whether they
point both ways, genre-specific types the schema never anticipated, and two graph views
that answer different questions.

Everything so far has been about *knowing* things. The next problem is *finding* them —
the questions with no exact answer, like "find the scene where Mira first doubted Toren."
There's no `doubts` table, and there never will be.

---

*Next: [Part 7 — Finding Things](07-search.md) — keyword search and vector search, why each
one fails alone, how to fuse them by rank, and three bugs that made a carefully-built
"hybrid" retriever silently not hybrid at all.*
