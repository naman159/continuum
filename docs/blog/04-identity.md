# Part 4 — Who Is "The Old Man At The Tavern"?

*Part 4 of the Continuum series. [Parts 2](02-the-mvp.md) and
[3](03-extraction-passes.md) built a pipeline that turns prose into database rows, and
ended on a cliffhanger: every one of those rows is keyed on a **name**, and names are a
disaster. This post is about the hardest problem in the system.*

---

## The problem, stated precisely

An extraction pass hands you a name. You have to decide which row in the database it
refers to — or whether it needs a new one.

That's it. That's the problem. It's called **entity resolution**, or **coreference
resolution**, or **canonicalization**, depending on which field you learned it in. It
sounds like a preprocessing detail. It is the load-bearing wall of the entire system,
and here's why:

Every fact you extract is attached to an entity id. Every event, every relationship,
every possession, every piece of knowledge. If the id is wrong, the fact is attached to
the wrong thing. Not *missing* — **wrong**. A missing fact is a hole; a wrong fact is a
lie that looks exactly like the truth.

Here's what a single chapter of an ordinary novel might call one woman:

```
"Alice Vance"          ← her full name, used at introduction
"Alice"                ← what her friends call her
"Ms. Vance"            ← what her colleagues call her
"Al"                   ← what her brother calls her
"the woman from the tavern"   ← how a stranger who doesn't know her name refers to her
"she"                  ← 200 times
```

Six surface forms. One person. (The pronouns are the extraction prompt's problem, not
ours — we ask the model to resolve pronouns before it emits a name.) And here's what the
same chapter might contain that *looks* similar and is not:

```
"Mr. Bennet"  vs  "Mrs. Bennet"       ← married couple, two people
"Elizabeth Bennet" vs "Elizabeth Elliot"  ← two women from two different books,
                                            or two characters in one long series
"Jake's black sedan" vs "Sarah's black sedan"   ← two cars
"Archer Class" (a game mechanic) vs "the archer" (a person)
```

Get the first group wrong and you have duplicates. Get the second group wrong and you
have corruption. Those two failure modes are not equally bad, and understanding *how*
unequal they are determines every decision in this post.

---

## The asymmetry

Let's make this concrete, because the whole design hangs on it.

**Case A — a missed merge.** The system files "Alice Vance" and "Ms. Vance" as two
entities.

```
characters
┌──────────┬──────────────┬────────────────────────────┐
│ 3f2a…    │ Alice Vance  │  12 events, 3 relationships│
│ 91cd…    │ Ms. Vance    │  8 events, 1 relationship  │
└──────────┴──────────────┴────────────────────────────┘
```

What's the damage? The wiki lists two characters. Anyone who reads it notices
immediately — the names are *right there* next to each other. Asking "what does Alice
know" returns 60% of the answer. Recovery is one merge operation: repoint 8 events and
1 relationship, absorb the name as an alias, delete the row. Twenty lines of SQL, fully
reversible if you keep a backup.

**Case B — a wrong merge.** The system decides "Mr. Bennet" and "Mrs. Bennet" are the
same person and merges them.

```
characters
┌──────────┬──────────────┬───────────────────────────────────────────┐
│ 3f2a…    │ Mr. Bennet   │  47 events, 9 relationships, 4 possessions│
│          │ aliases:     │  ...belonging to two different people,    │
│          │  [Mrs.Bennet]│     now indistinguishable                 │
└──────────┴──────────────┴───────────────────────────────────────────┘
```

What's the damage? There is now one entity holding the merged history of two people. He
is married to himself (the relationship between them became a self-loop and was
deleted). He was in two places at once in eleven chapters. He knows things he was never
told. The critic we build in Post 8 will fire on him constantly and every finding will
be spurious.

And crucially: **nothing looks wrong.** A single row named "Mr. Bennet" is exactly what
you'd expect to see. To detect the problem you'd have to already know the answer.

Recovery? You'd have to re-read the novel and manually reassign 47 events. Or throw away
the derived data and re-process — which is possible, because we kept `raw_text`, but
only fixes it if the resolver behaves differently the second time.

So:

> **Precision beats recall.** When the evidence is weak, refuse to merge. Duplicates are
> a visible, cheap, repairable problem. Wrong merges are an invisible, expensive,
> possibly permanent one.

This is written into the prompts, in capital letters:

```
- When grammatical evidence is absent, return "new". When in doubt,
  return "new". Visible duplicates are acceptable; wrong character
  merges destroy continuity.
```

It's written into the eval thresholds (Post 10): the entity-resolution eval fails on
**any single false merge**, but tolerates recall as low as 0.6. It's written into the
merge-repair tool, which exists precisely because missed merges need a cheap fix.

Everything below follows from that one inequality.

---

## Attempt 1: exact match

The simplest possible resolver:

```python
def resolve(name):
    row = db.fetchone("SELECT id FROM characters WHERE lower(name) = lower(%s)", (name,))
    if row:
        return row[0]
    return create_character(name)
```

Zero false merges — guaranteed, by construction. Two different strings never collide.

And catastrophic recall. Every alias becomes a new character. On a real chapter of
*Pride and Prejudice* you'd get Elizabeth Bennet, Lizzy, Eliza, Miss Bennet, and Miss
Eliza Bennet as five separate people before you finish chapter 3.

We need something better. But notice the property we want to *keep*: whatever we build,
its false-merge rate should stay near zero.

---

## Attempt 2: fuzzy string matching (and why it failed)

The natural next move: if two names are similar enough, they're the same. This is the
approach everyone tries, and the project tried it too.

### What "similar" means, precisely

Two strings can be compared numerically in several ways. The most common is **edit
distance** (Levenshtein distance): the minimum number of single-character insertions,
deletions, or substitutions to turn one string into the other.

```
"Alice"  →  "Alicia"
    A-l-i-c-e
    A-l-i-c-i-a
    insert 'i', substitute 'e'→'a'   =  edit distance 2
```

Python's standard library ships something related, `difflib.SequenceMatcher`, whose
`.ratio()` gives a similarity score in [0, 1]:

```
ratio = 2 × M / T
```

where `M` is the number of matching characters and `T` is the total length of both
strings. Identical strings score 1.0; strings with nothing in common score 0.0.

The project's first resolver used a fuzzy-matching library's `fuzz.ratio` — the same
idea — with a threshold of **85%**. Above 85% similar? Same entity.

### Why it blew up

Let's actually run the numbers. Here are real similarity scores, computed with the
comparison function the current codebase uses:

```
0.952  'Mr. Bennet'        vs  'Mrs. Bennet'        ← SAME PERSON?! No. Married couple.
0.750  'Elizabeth Bennet'  vs  'Elizabeth Elliot'   ← two different women
0.727  'Alice'             vs  'Alicia'             ← possibly two people
0.600  'Alice Vance'       vs  'Ms. Vance'          ← SAME PERSON, scores below threshold
0.500  'Odo Bram'          vs  'the old ferryman'   ← SAME PERSON, way below
0.296  'Toren Vale'        vs  'the archivist on duty' ← SAME PERSON, nowhere close
0.133  'Kessa Dray'        vs  'the thief'          ← SAME PERSON, essentially zero
```

Look at the top row and the bottom row together. That's the whole story:

- The pair that string similarity is **most confident** about (0.952) is a *wrong* merge
  that destroys a novel.
- The pairs it is **least confident** about (0.13–0.50) are the *right* merges — and
  they're the interesting ones, the descriptive references that make prose readable.

The commit that removed fuzzy matching says it plainly:

> *The previous approach (`fuzz.ratio` ≥ 85%) silently merged distinct characters whose
> names differed only by honorific ("Mr. Bennet" / "Mrs. Bennet"). It was removed.*

And here is the deep reason, which is worth internalising because it generalises far
beyond this project:

> **String similarity measures how names look. Identity is about what names refer to.
> These are different things, and in the hardest cases they are anti-correlated.**

"Mr." and "Mrs." differ by one character and denote different genders — one of the
strongest identity signals in English. "Kessa Dray" and "the thief" share no characters
and denote the same woman. No amount of tuning the threshold fixes this, because there
is no threshold that separates the two lists above. They interleave.

So fuzzy matching came out, and the resolver went back to exact-match-only. Which is
correct but useless. We need a different *kind* of signal.

---

## Attempt 3: ask the model — but make it show its work

The realisation is that the evidence for identity is not in the *names*. It's in the
*text*.

Consider:

> *"Mr. Darcy, the master of Pemberley, said little at dinner."*

A human reading that instantly knows "the master of Pemberley" and "Mr. Darcy" are one
person. Not because the strings are similar — they share nothing — but because of an
**apposition**: two noun phrases placed side by side, separated by commas, which in
English grammar means "these are the same thing."

Language models are genuinely good at this. Reading comprehension is what they're for.
So the obvious move is: give the model the chapter text, give it the list of names we
already know about, give it the new names, and ask which are which.

And immediately you hit the problem with asking a language model anything: **it will
also do it when there's no evidence at all.** Ask "is 'the old man' the same as
'Aelric'?" and a model that has read a lot of fantasy will happily construct a
plausible-sounding rationale. Models are trained to be helpful. A confident wrong merge
is exactly the failure mode we said is unrecoverable.

### The grammatical anchor

Here is the trick, and it's the best idea in this part of the system.

**Don't ask for a verdict. Ask for a verdict plus a verbatim quote from the chapter that
proves it — then check the quote yourself.**

The output schema the model must fill:

```python
CANONICALIZATION_SCHEMA = {
    "resolutions": [
        {
            "candidate": "string (verbatim from input list)",
            "verdict": "existing|new",
            "id": "uuid string when verdict=existing, else null",
            "grammatical_anchor": (
                "verbatim substring from chapter text proving the link "
                "(apposition, possessive, restated full name); null when verdict=new"
            ),
            "reasoning": "short string",
        }
    ]
}
```

And the rules, for characters:

```
Rules:
- Verdict "existing" requires BOTH an id from the roster AND a
  grammatical_anchor: a verbatim substring of the chapter text in which
  the candidate is grammatically tied to that existing character via
  apposition, unambiguous possessive, or a restated full name in
  immediate context.
- Do NOT merge based on stylistic similarity, topical inference, plot
  guesswork, or general knowledge of the source novel. Use only the
  chapter text provided.
- When grammatical evidence is absent, return "new". When in doubt,
  return "new". Visible duplicates are acceptable; wrong character
  merges destroy continuity.
- "grammatical_anchor" must be an EXACT substring of the chapter text.
  If you cannot quote one verbatim, return "new".
```

That "Do NOT merge based on … general knowledge of the source novel" clause is not
paranoia. If you feed a model a chapter of *Pride and Prejudice*, it already knows the
whole book. It will merge "Lizzy" and "Elizabeth Bennet" correctly — but for the wrong
reason, and by the same mechanism it will merge things your chapter never established.
We want it reading *this text*, not its memory.

Now the part that makes it actually work — a single line of Python:

```python
anchor_valid = bool(anchor) and anchor in chapter_text
```

**We check that the quote is real.** Not "does it look plausible" — is this exact string
present in the chapter we passed in? A hallucinated quote is caught by `str.__contains__`
and the merge is refused:

```python
if entity_type == "character" and not anchor_valid:
    logger.info(
        "entity_canonicalizer: rejected character merge "
        "(anchor missing or not in text): %s -> %s", candidate, target_id,
    )
    continue
```

This is worth pausing on as a general technique, because it applies to almost any
LLM-in-a-pipeline problem:

> **Don't ask a model for a decision. Ask it for a decision *plus a citation you can
> verify without a model*. Then verify.**

The model's job becomes *finding* the evidence — a genuine reading-comprehension task
that it's good at. The verification is deterministic string matching, which cannot
hallucinate. You've moved the trust boundary to a place where you can defend it.

### The self-improving loop

When a merge is accepted, the candidate name is written into the target's `aliases`
array:

```python
def _append_alias(self, entity_type, target, candidate):
    existing_aliases = [str(a) for a in (target.get("aliases") or [])]
    if candidate.lower() in {a.lower() for a in existing_aliases} \
       or candidate.lower() == str(target.get("name", "")).lower():
        return
    new_aliases = [*existing_aliases, candidate]
    table = TYPED_TABLES.get(entity_type, "entities")
    self.db.execute(
        f"UPDATE {table} SET aliases = %s WHERE id = %s AND novel_id = %s",
        (new_aliases, target["id"], self.novel_id),
    )
    target["aliases"] = new_aliases     # keep the in-memory roster in sync too
```

This has a lovely property. The *next* time "Ms. Vance" appears — chapter 3, chapter 40,
whenever — the plain alias lookup in the resolver finds her. No LLM call. No cost. No
risk of a different answer.

```
chapter 2:  "Ms. Vance"  →  no match  →  LLM call  →  anchor verified  →  alias written
chapter 3:  "Ms. Vance"  →  alias hit  →  done. free.
chapter 40: "Ms. Vance"  →  alias hit  →  done. free.
```

**The system gets cheaper and more consistent as the novel grows.** That's the opposite
of the usual scaling story, and it comes from a simple discipline: cache the *decision*,
not just the computation.

---

## Attempt 4: but chapter 1 has no roster

Here's a hole in everything above. The canonicalizer compares new names against
**existing database rows**. In chapter 1, there are no existing rows.

And chapter 1 is exactly where an author introduces a character three different ways in
the space of two pages:

> *"Jane Bennet was the eldest of five sisters. Jane had been called the handsomest girl
> in the county… Miss Bennet accepted the compliment without visible pleasure."*

Three surfaces, empty roster, three new characters created. From chapter 2 onward the
canonicalizer will faithfully resolve against whichever of the three it happens to
match, cementing the split.

So we need a **second** dedup step that works *within a single extraction*, before
anything touches the database. That's the `IntraExtractionDeduplicator`.

Same shape, different question. Instead of "does this name match a database row?", it
asks "do any of these names refer to each other?":

```python
INTRA_DEDUP_SCHEMA = {
    "groups": [
        {
            "names": ["string (verbatim from input list — only names that refer to the same entity)"],
            "reasoning": "string",
        }
    ]
}
```

One LLM call per entity type, and only when there are at least two names to compare:

```python
for entity_type, names in by_type.items():
    if len(names) < 2:
        continue                # nothing to deduplicate — skip the call entirely
```

### Longest name wins

When a group comes back — `["Jane", "Jane Bennet", "Miss Bennet"]` — which one becomes
canonical?

```python
canonical = max(valid, key=len)
for variant in valid:
    if variant.lower() != canonical.lower():
        type_map[variant.lower()] = canonical
```

The longest. Why?

Because longer names carry more information and are less ambiguous. "Jane Bennet"
uniquely identifies a person; "Jane" might be any of three. If you later add a second
Jane, a database keyed on "Jane Bennet" survives; one keyed on "Jane" has a collision.
It's a heuristic, and occasionally it picks something clunky ("the woman who called
herself Kessa Dray" over "Kessa Dray"), but it always picks something *specific*, and
that's the safe direction.

### Guarding against a hallucinated canonical form

Here's a failure mode you might not anticipate: the model returns a group containing a
name that was never in the input.

```
input:  ["Jane", "Jane Bennet"]
output: {"names": ["Jane", "Jane Bennet", "Miss Jane Bennet of Longbourn"]}
```

That third string is invented. If it wins the length contest, every occurrence of "Jane"
and "Jane Bennet" gets renamed to a string that appears nowhere in the novel — and the
canonicalizer, which compares against the roster in later chapters, will never match it
again. You've quietly renamed a main character to a hallucination.

The guard:

```python
# Guard against hallucinated group members: only names actually present
# in this extraction may participate, and the canonical form must be a
# real input name (otherwise variants get renamed to invented strings).
originals = {n.lower(): n for n in names}
valid = []
for raw in group:
    key = raw.strip().lower()
    if key in originals and key not in seen_keys:
        seen_keys.add(key)
        valid.append(originals[key])
if len(valid) < 2:
    continue
```

Anything not in `originals` is discarded. If fewer than two survive, the group is
dropped entirely. Same principle as the grammatical anchor: **validate the model's
output against something you control.**

### The rename has to reach everywhere

Deciding that "Jane" → "Jane Bennet" is easy. Applying it is not, because the name
appears in a dozen different shapes across the extraction result:

```python
def _apply_rename_map(data, rename_map):
    ...
    for char in new_entities.get("characters", []): ...        # entity names
    for delta in data.get("state_deltas", []): ...             # subject/object/location
    for event in data.get("events", []): ...                   # 4 involved_* arrays
    for rel in data.get("relationship_updates", []): ...       # entity_a / entity_b
    for dyn in data.get("dynamics_updates", []): ...
    for scene in data.get("scenes", []): ...                   # pov + present_characters
    for learning in data.get("learnings", []): ...             # knower + source + shared_with
    for fact in data.get("canon_facts", []): ...               # subject
    for item in data.get("custom_entities", []): ...
```

Miss one and you get a half-renamed extraction, which is worse than no rename at all —
some facts land on "Jane Bennet", others on "Jane", and now you've *created* the split
you were trying to prevent.

Two subtleties in there worth calling out.

**Renaming creates duplicates in lists.** If an event's `involved_characters` is
`["Jane", "Jane Bennet"]` and both rename to "Jane Bennet", you get
`["Jane Bennet", "Jane Bennet"]`. So the list renamer deduplicates as it goes:

```python
def _r_list(names, m):
    """Rename every entry, then drop case-insensitive duplicates (renaming
    variants to one canonical form otherwise leaves the same name twice)."""
    seen, out = set(), []
    for n in names or []:
        renamed = _r(str(n), m)
        key = renamed.lower()
        if renamed and key not in seen:
            seen.add(key)
            out.append(renamed)
    return out
```

**A location delta's subject isn't always a character.** The state-change pass can
report that an *object* moved between locations — a dagger carried from one place to
another. So the rename tries the character map first (the common case) and falls back to
the object map:

```python
name = str(delta.get("character_name", ""))
if delta.get("kind") == "location" and name and name.lower() not in char_map:
    # A location delta's subject can be any entity type (e.g. an object carried
    # between locations), not just a character.
    delta["character_name"] = _r(name, obj_map)
else:
    delta["character_name"] = _r(name, char_map)
```

That's five lines of code encoding a fact about how stories work. Most of this system is
five-line blocks like that one.

---

## Different types need different rules

The canonicalizer started as characters-only and was generalised to locations, objects,
and factions. The naive generalisation — run the same prompt with the word "character"
swapped out — is wrong, because the identity criteria are genuinely different per type.

### Characters: strict, verbatim anchor required

Already covered. Highest stakes, strictest rule.

### Locations: semantic evidence is enough

```
Rules:
- Verdict "existing" requires an id from the roster AND clear reasoning
  that the candidate refers to the same physical place.
- A verbatim grammatical_anchor is NOT required. Semantic evidence is
  sufficient: the chapter describes the same building, the candidate name is
  a common variant or sub-location of an existing entry
  (e.g. "14th floor" → "Corporate Office", "Jake's home" → "Jake's Apartment"),
  or context makes the identity clear.
- When a candidate is clearly a sub-location (floor, room, corridor) of an
  existing roster entry, return verdict "existing" for that parent.
```

Why relax? Two reasons.

First, English rarely provides appositions for places. Nobody writes *"the 14th floor,
which is part of the Corporate Office."* They just say "the 14th floor" and expect you
to know.

Second, **the cost of a wrong location merge is much lower.** Merging two rooms in the
same building loses a little granularity. Merging two people fabricates a person. The
severity of the failure justifies a different evidence bar. This is a general principle
worth stating:

> **Match the strictness of your verification to the cost of being wrong, not to a
> uniform "quality" instinct.**

Locations also get a dedicated structural mechanism: sub-locations fold into their
parent at resolution time.

```python
def resolve_location(self, name, metadata=None, *, create=True):
    parent_name = str(meta.get("parent_location") or "").strip()
    if parent_name and parent_name.lower() != normalized.lower():
        # This is a sub-location — always resolve (or create) the parent instead.
        parent = self._resolve("location", parent_name, {}, create=create)
        if parent is None:
            return None
        self._append_location_alias(parent.entity_id, normalized)
        self._cache[("location", normalized.lower())] = (parent.entity_id,
                                                         parent.universal_id)
        return parent
```

If the extraction says "the 14th floor" with `parent_location: "Corporate Office"`, we
resolve to the *office*, and register "the 14th floor" as an alias of it. Without that
last step, a later chapter mentioning "the 14th floor" with no parent metadata would
create a standalone duplicate. The alias closes the loop.

### Objects: never merge across owners

```
- CRITICAL: NEVER merge two objects that belong to different owners. If the
  roster entry belongs to Jake and the candidate belongs to Sarah, return
  "new" even if the names are identical.
```

Note "even if the names are identical" — the exact opposite of what any string-based
approach does. Two rows both called "black sedan" are, by default, *different cars*, and
only owner information can tell you otherwise.

To make that rule usable, the object dedup prompt gets extra context the other types
don't:

```python
if entity_type == "object":
    enriched = []
    for e in entities:
        entry = {"name": e.get("name", "")}
        if e.get("owner_name"):
            entry["owner_name"] = e["owner_name"]
        if e.get("distinguishing_properties"):
            entry["distinguishing_properties"] = e["distinguishing_properties"]
        enriched.append(entry)
```

And the extraction prompt is written to *produce* that information in the first place:

```
- Set owner_name to the character who owns, carries, or is specifically
  associated with this object. Two characters can each have "a black sedan" —
  they are DIFFERENT objects; give each one a distinct name that includes the
  owner (e.g. "Jake's black sedan", "Sarah's black sedan").
```

This is a nice example of a fix that lands in three places at once: the extraction
prompt (produce owners), the merge key (`(name, owner)` in `_dedupe_by_name`), and the
dedup prompt (never cross owners). One bug, three coordinated defenses.

### Factions: abbreviations are fine

```
- Group a faction with its common abbreviation or alias when the chapter
  clearly equates them ("the Empire" used as shorthand for "the Galactic
  Empire", "HYDRA" and "the HYDRA organisation").
```

Organisations get abbreviated constantly and rarely get confused with each other.
Lowest stakes, loosest rule.

---

## Making it cheap: the deterministic tier

Every LLM call costs money and takes a second. Many of the comparisons we're making are
so trivial they don't deserve one.

```
"Aelric"   vs  "aelric"      ← case
"Aelric "  vs  "Aelric"      ← whitespace
"The Empire" vs "Empire"     ← leading article
"Aelric."  vs  "Aelric"      ← trailing punctuation
"D’Arcy"   vs  "D'Arcy"      ← curly vs straight apostrophe (EPUB vs typed)
```

So before any LLM is involved, names are normalised:

```python
def normalize_name(name: str) -> str:
    """Aggressive-but-safe normalization for same-type, same-novel name equality."""
    n = unicodedata.normalize("NFKC", str(name or ""))     # unicode canonical form
    n = n.replace("’", "'").replace("‘", "'")              # curly → straight quotes
    n = n.strip().strip("\"'“”").strip()                   # surrounding quotes
    n = re.sub(r"\s+", " ", n).lower()                     # collapse whitespace, casefold
    for article in ("the ", "a ", "an "):                  # ONE leading article
        if n.startswith(article) and len(n) > len(article):
            n = n[len(article):]
            break
    n = n.rstrip(".,;:!?")                                 # trailing punctuation
    return n or str(name or "").strip().lower()
```

A quick tour of the non-obvious bits:

**`unicodedata.normalize("NFKC", …)`.** Unicode lets the same visible character be
encoded multiple ways — "é" can be one code point or "e" plus a combining accent. NFKC
("Normalization Form Compatibility Composition") folds these to a single canonical form,
and also normalises compatibility characters like the full-width "Ａ" to "A". Without
it, two visually identical names can compare unequal forever.

**Only *one* leading article is stripped**, via that `break`. Deliberate: an entity
genuinely called "The The" (a real band name, and the kind of thing fiction does) keeps
one article.

**The fallback at the end.** If normalisation reduces a name to the empty string —
imagine an entity called `"The"` — we fall back to the simple lowercased original rather
than returning `""`, which would match every other degenerate name.

Now the free tier. If a candidate's normalised form equals **exactly one** roster entry's
normalised name or alias, merge it with no LLM call:

```python
def _apply_deterministic_matches(self, entity_type, unresolved, roster, merges):
    """Merge candidates whose normalized form equals exactly one roster
    name/alias, without an LLM call. Returns the still-unresolved names."""
    lookup = {}
    for entry in roster:
        for raw in [entry.get("name", ""), *(entry.get("aliases") or [])]:
            key = normalize_name(str(raw))
            if not key:
                continue
            if key in lookup and lookup[key] is not entry:
                lookup[key] = _AMBIGUOUS          # ← two entries share this form
            elif key not in lookup:
                lookup[key] = entry

    still_unresolved = []
    for candidate in unresolved:
        entry = lookup.get(normalize_name(candidate))
        if entry is None or entry is _AMBIGUOUS:
            still_unresolved.append(candidate)
            continue
        self._append_alias(entity_type, entry, candidate)
        merges[candidate] = str(entry["id"])
    return still_unresolved
```

Note `_AMBIGUOUS`. It's a sentinel object — a unique marker value:

```python
# Sentinel for normalized names shared by multiple roster entries — never auto-merge those.
_AMBIGUOUS = object()
```

If two *different* roster entries normalise to the same string — which can happen, e.g.
an entity named "The Empire" and another named "Empire" that were never merged — we
refuse to auto-merge anything onto that form. We don't know which one is meant, and
guessing is the unrecoverable error. The candidate falls through to the LLM tier, which
has the chapter text and might actually be able to tell.

**Refusing to guess when the evidence is ambiguous is a pattern you'll see four separate
times in this post.** It's the single most repeated idea in the codebase.

---

## Making it scale: bounding the roster

The LLM canonicalization prompt contains the entire roster for a type. On chapter 3 that
might be 8 characters. On chapter 60, 90 characters with descriptions and last-known
states — thousands of tokens, sent on every canonicalization call, for a novel that
keeps growing.

Naive fix: truncate. Terrible fix: you might truncate away the one entry that matters.

Actual fix: rank by relevance and keep the top N.

```python
def _select_roster_subset(candidates, roster, cap):
    """Bound the roster sent to the LLM. When the roster exceeds `cap`, keep
    the entries most string-similar to the candidates (the rest are near-certain
    non-matches and only inflate the prompt)."""
    if cap <= 0 or len(roster) <= cap:
        return roster

    normalized_candidates = [normalize_name(c) for c in candidates]

    def best_score(entry):
        return max((_entry_similarity(nc, entry) for nc in normalized_candidates),
                   default=0.0)

    ranked = sorted(range(len(roster)), key=lambda i: best_score(roster[i]), reverse=True)
    kept = sorted(ranked[:cap])   # keep original roster order for prompt stability
    return [roster[i] for i in kept]
```

Cap defaults to 80 (`CANONICALIZER_MAX_ROSTER`).

Two things to notice.

**We're using string similarity again** — the very thing that failed as a *decision*
rule. But here it's used as a *filter*, not a decision. That's a completely different
job, and it's fine at it. A filter's failure mode is dropping a candidate; a decision
rule's failure mode is corrupting the database. Using a weak signal to narrow a search
space, then a strong signal to decide, is a standard and good pattern. (It's exactly
what the hybrid retriever in Post 7 does, and what a "blocking" step does in classical
record-linkage systems.)

**`kept = sorted(ranked[:cap])`** re-sorts by original index, not by score. Why? Prompt
stability. If the roster order changed based on scores, two nearly-identical requests
would produce differently-ordered prompts, and LLM outputs are sensitive to ordering.
Deterministic inputs make debugging possible.

And the similarity function itself has a wrinkle worth understanding:

```python
def _name_pair_similarity(a: str, b: str) -> float:
    """Similarity of two normalize_name'd strings: the max of sequence ratio
    and token-overlap/min (a token subset like "empire" ⊂ "galactic empire"
    scores 1.0)."""
    if a == b:
        return 1.0
    score = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    if ta and tb:
        score = max(score, len(ta & tb) / min(len(ta), len(tb)))
    return score
```

The second term is the interesting one. `|A ∩ B| / min(|A|, |B|)` measures *containment*
rather than overlap. If every word of the shorter name appears in the longer one, it
scores 1.0:

```
"empire"          ∩  "galactic empire"   = {empire},  min size 1  →  1.0
"harbor"          ∩  "harbor of veyra"   = {harbor},  min size 1  →  1.0
"jane"            ∩  "jane bennet"       = {jane},    min size 1  →  1.0
```

Character-level `SequenceMatcher` would score "empire" vs "galactic empire" at about
0.57 — below any sensible threshold — because half the characters are unmatched. But
"the Empire" as shorthand for "the Galactic Empire" is one of the most common aliasing
patterns in fiction. The containment term catches exactly that family.

It also catches things it shouldn't ("harbor" would match "harbor of doom" just as
strongly), which is precisely why this is a *candidate filter* and not a decision.

---

## Three tiers of evidence

Now we can assemble the full decision procedure, which turned out to need three levels
of confidence rather than a yes/no.

The question that forced it: **what do you do when the LLM says "existing" and gives
reasoning, but no verbatim anchor?**

Rejecting outright loses real merges — locations and factions genuinely don't produce
appositions. Accepting writes a permanent alias on the strength of a field that the
schema *requires* the model to fill, so its mere presence proves nothing.

The answer is a third option: **let the merge take effect for this chapter only.**

```python
target = roster_by_id[str(target_id)]
anchor_valid = bool(anchor) and anchor in chapter_text

# Characters always require a verbatim anchor in the chapter text.
if entity_type == "character" and not anchor_valid:
    continue

# Other types tier the evidence: an anchor, or unambiguous lexical
# closeness, makes the merge permanent (alias written); reasoning alone —
# a required schema field, so its mere presence proves nothing — merges
# for this chapter only, because a hallucinated alias would reroute every
# future mention.
persist_alias = anchor_valid or (
    entity_type != "character" and _names_lexically_close(candidate, target, roster)
)
if not persist_alias and not reasoning:
    continue

if persist_alias:
    self._append_alias(entity_type, target, candidate)
else:
    logger.info("entity_canonicalizer: semantic-only merge %r -> %s (%s); "
                "alias not persisted (reasoning: %s)",
                candidate, target_id, entity_type, reasoning[:120])
merges[candidate] = str(target_id)
```

In a table:

| Evidence | Character | Location / Object / Faction |
|---|---|---|
| Verbatim anchor found in chapter text | **merge + persist alias** | **merge + persist alias** |
| No anchor, but lexically close & unambiguous | reject | **merge + persist alias** |
| No anchor, reasoning only | reject | **merge, this chapter only** |
| Nothing | reject | reject |

The distinction between rows 3 and 4 is subtle and important. A "this chapter only"
merge means the extraction is rewritten so this chapter's facts land on the right
entity — but no alias is stored, so the next chapter starts fresh and has to convince
the model again. The blast radius of a mistake is one chapter, not the rest of the book.

The mechanism that makes it work:

```python
def apply_merges_to_extraction(db, extracted, merges):
    """Public seam for the pipeline: rewrite merged candidate names to their
    targets' canonical names so every merge — including reasoning-only ones
    that persisted no alias — takes effect for the current chapter."""
    if not merges:
        return extracted
    return _apply_rename_map(extracted, rename_map_for_merges(db, merges))
```

It reuses the same `_apply_rename_map` machinery the intra-extraction deduplicator uses.
Two different problems, one rewriting engine.

And `_names_lexically_close` — the middle tier — carries the ambiguity guard again:

```python
def _names_lexically_close(candidate, target, roster) -> bool:
    """True when the candidate is lexically close to the target AND to no
    other roster entry. The ambiguity guard mirrors the deterministic pass's
    _AMBIGUOUS refusal: a generic candidate ("the Empire") that is close to
    two roster entries must not be permanently welded to whichever one the
    LLM happened to pick."""
    nc = normalize_name(candidate)
    if _entry_similarity(nc, target) < LEXICAL_MATCH_RATIO:      # 0.85
        return False
    for entry in roster:
        if entry is target:
            continue
        if _entry_similarity(nc, entry) >= LEXICAL_MATCH_RATIO:
            return False              # close to something else too → refuse
    return True
```

Closeness to the target isn't sufficient. It must *also* not be close to anything else.
Third appearance of the same idea.

---

## Two bugs that could permanently corrupt identity

Both found in an August 2026 audit; both are the kind of thing that only shows up on a
real book.

### Bug 1: `LIMIT 1` with no `ORDER BY`

The resolver has a character-only partial-name rule: if one name is a word-boundary
prefix of another, they're probably the same person. "Jane" matches "Jane Bennet".

The original query ended `LIMIT 1`. Now consider a novel with two characters:

```
Elizabeth Bennet
Elizabeth Elliot
```

A chapter mentions "Elizabeth". The query matches both. `LIMIT 1` returns whichever one
Postgres felt like returning — and with no `ORDER BY`, that ordering is genuinely
arbitrary, depending on physical row layout and the query planner's mood. Whichever came
back got "Elizabeth" **written into its aliases array, permanently.** A coin flip,
cemented.

The fix:

```python
# Deliberately NOT `LIMIT 1`: with two candidates the match is
# ambiguous, and picking one meant binding "Elizabeth" to whichever
# of Elizabeth Bennet / Elizabeth Elliot the planner happened to
# return — then persisting that guess as an alias, permanently. An
# unresolvable name is recoverable; a wrong merge is not.
partial_rows = self.db.fetchall(r"""
    SELECT id, entity_id, name, aliases
    FROM characters
    WHERE novel_id = %s
      AND (
          lower(name) LIKE lower(%s || ' %%')
          OR lower(%s) LIKE lower(
              replace(replace(replace(name, '\', '\\'), '%%', '\%%'), '_', '\_') || ' %%'
          )
      )
    ORDER BY id
""", (self.novel_id, like_safe, normalized_name))

if len(partial_rows) > 1:
    logger.warning(
        "character name %r partially matches %d existing characters (%s); "
        "refusing to guess — resolve it explicitly or add an alias",
        normalized_name, len(partial_rows),
        ", ".join(str(r[2]) for r in partial_rows[:5]),
    )
elif partial_rows:
    ...   # exactly one match: safe to bind
```

Fetch everything; if more than one, refuse and warn. Fourth appearance of the refuse-on-
ambiguity pattern.

(The `replace(replace(replace(...)))` is escaping LIKE metacharacters. In SQL `LIKE`,
`%` matches anything and `_` matches one character. A character named "Mr_Smith" would
otherwise match "MrXSmith". Not a security hole here since the input comes from your own
extraction, but a correctness one.)

### Bug 2: aliases only written on create

`_create_entity` wrote the extractor's supplied aliases into the new row. But if the
entity **already existed**, the aliases were silently dropped.

Trace what that means. Chapter 1 introduces "Elizabeth Bennet" with no nickname —
nobody's used one yet. Chapter 8, in dialogue, establishes that her family calls her
"Lizzy", and the extractor dutifully reports `aliases: ["Lizzy"]` on the existing
character. The resolver finds the existing row, ignores the aliases, moves on. Chapter
12 uses "Lizzy" bare. No alias exists. New character created.

The fix — fold incoming aliases into existing entities:

```python
def _merge_aliases(self, entity_type, typed_id, metadata):
    """Add extractor-supplied aliases to an entity that already exists.

    Only additive — an alias is never removed, and the canonical name is
    never changed, so this cannot merge two distinct entities. Names that
    already resolve elsewhere are skipped rather than stolen.
    """
    ...
    for alias in incoming:
        if alias.lower() in known:
            continue
        # Don't claim a surface form that already identifies a different
        # entity of this type — that would silently repoint it.
        clash = self._lookup_typed(entity_type, alias)
        if clash is not None and str(clash[0]) != str(typed_id):
            logger.warning(
                "alias %r for %s %r already resolves to a different entity; skipping",
                alias, entity_type, name,
            )
            continue
        added.append(alias)
```

Two guarantees in the docstring, both load-bearing. **Only additive**: aliases are never
removed and the canonical name never changes, so this operation *cannot* merge two
distinct entities — the worst it can do is fail to help. **Names that already resolve
elsewhere are skipped**: if the extractor claims "Lizzy" as an alias of Jane while Lizzy
is already Elizabeth's name, we refuse rather than steal it.

And a related discipline: **alias writes are gated on `create=True`.**

```python
# Only an authoritative pass may persist an alias. Reference-only
# passes (scene POV, knows-edges, event actors, state deltas)
# resolve for lookup and must not mutate identity: a stray name
# in a scene list is far weaker evidence than the canonicalizer demands.
```

Same principle as the phantom-character fix in Post 2, applied to a different mutation.
Some passes have authority over identity; most don't. Identity changes only ever come
from code that has thought about identity.

---

## When it goes wrong anyway: repair

Every layer above reduces the error rate. None of them make it zero. So there has to be
a way to fix a duplicate by hand.

`merge_entities` merges a source entity into a target, in one transaction. Its job is to
find *every* reference to the source anywhere in the database and repoint it. That list
is long, and the order matters:

```python
def merge_entities(db, *, novel_id, source_entity_id, target_entity_id):
    with db.transaction() as cur:
        # A pre-existing src<->tgt relationship would become a self-pair during
        # the repoint and trip CHECK (entity_a_id <> entity_b_id) — delete first.
        cur.execute("""DELETE FROM relationships
                        WHERE (entity_a_id = %s AND entity_b_id = %s)
                           OR (entity_a_id = %s AND entity_b_id = %s)""",
                    (src, tgt, tgt, src))
        cur.execute("UPDATE relationships SET entity_a_id = %s WHERE entity_a_id = %s", ...)
        cur.execute("UPDATE relationships SET entity_b_id = %s WHERE entity_b_id = %s", ...)
        # ... then dedupe (pair, rel_type) edges that repointing just duplicated
        # ... shared_dynamics, row by row (UNIQUE constraint on (a,b,chapter))
        # ... canon_facts (drop source facts whose predicate the target has)
        # ... commitments.related_entity_ids array
        # ... located_in_edges, state_deltas.subject_id/object_id
        # ... events.involved_* arrays
        # ... character_states, scenes, knows_edges, possesses_edges
        # ... union the aliases, delete the source row
```

The self-pair delete at the top is the kind of detail you only find by hitting it. If
"Mr. Bennet" and "Mrs. Bennet" have a `spouse_of` relationship and you merge them, the
repoint would produce a row where both endpoints are the same entity — and the schema
has `CHECK (entity_a_id <> entity_b_id)`, so the whole transaction aborts. Delete those
rows first.

Another one, ordered for a reason:

```python
# state_deltas.subject_id/object_id reference entities(id) directly (not a
# typed table) and CASCADE on delete — repoint before the source entity is
# dropped, or tier-2 extraction data (possession/knowledge/status deltas)
# is silently destroyed.
cur.execute("UPDATE state_deltas SET subject_id = %s WHERE subject_id = %s", (tgt, src))
cur.execute("UPDATE state_deltas SET object_id = %s WHERE object_id = %s", (tgt, src))
```

`ON DELETE CASCADE` is a wonderful feature that will happily delete your most expensive
data if you delete rows in the wrong order. Repoint first, delete last.

There's also a genuinely elegant bit of SQL for arrays. Events store involvement as UUID
arrays, so repointing means replacing one id with another *inside* an array, and then
deduplicating in case the target was already there:

```sql
UPDATE events
   SET involved_characters = ARRAY(
         SELECT DISTINCT x
           FROM unnest(array_replace(involved_characters, %s::uuid, %s::uuid)) AS x
       )
 WHERE %s::uuid = ANY(involved_characters)
```

Read it inside-out: `array_replace` swaps source for target; `unnest` turns the array
into rows; `SELECT DISTINCT` deduplicates; `ARRAY(...)` collapses back to an array.

---

## The measurement that found the real bug

Here's where the story turns, and it's the most instructive part.

By mid-2026, the canonicalizer had been tuned for months: type-specific rules, three
evidence tiers, ambiguity guards, deterministic fast path, roster capping. Real
engineering, all of it defensible.

Nobody had ever *measured* whether it worked.

So an eval was built (the full harness gets Post 10; here's the entity-resolution piece).
The idea: score entity resolution the way clustering is scored — as a judgement on every
**pair** of surface forms.

```python
def pairwise_resolution(truth: dict[str, str], predicted: dict[str, str]):
    """Pairwise precision/recall for entity resolution.

    Both maps are surface-form -> cluster id: `truth` from the answer key,
    `predicted` from whatever entity the pipeline filed that surface under.
    Every unordered pair of surfaces is one judgement — same cluster or not —
    which is the standard way to score clustering without needing the two id
    spaces to correspond.
    """
    gradeable = sorted(set(truth) & set(predicted))
    tp = 0
    false_merges, missed_merges = [], []
    for i, a in enumerate(gradeable):
        for b in gradeable[i + 1:]:
            same_truth = truth[a] == truth[b]
            same_pred  = predicted[a] == predicted[b]
            if same_truth and same_pred:
                tp += 1
            elif same_pred:
                false_merges.append((a, b))      # welded together wrongly
            elif same_truth:
                missed_merges.append((a, b))     # left split
```

Why pairwise? Because the ground-truth ids ("Mira Solen") and the database ids
(`3f2a…c91`) live in different id spaces and can't be compared directly. But "are these
two surfaces in the same cluster?" is a yes/no question that both sides can answer. It's
the standard trick for evaluating clustering.

Two details that make this a good metric rather than a plausible-looking one:

**The two error kinds are reported separately, and never traded off.**

```python
# `false_merges` are distinct entities welded together, which corrupt every edge
# attached to either and are expensive to unpick. `missed_merges` leave duplicate
# rows, which are visible and cheap to repair. Precision and recall respectively
# track them, and they should not be traded off one-for-one.
```

The test asserts them at wildly different bars, which is the asymmetry from the top of
this post encoded as an executable rule:

```python
assert not report["false_merges"], report["false_merges"]     # ZERO tolerated
assert report["recall"] >= 0.6, report["missed_merges"]       # 40% misses tolerated
```

**Surfaces the extractor never emitted are excluded from scoring** and reported
separately as `coverage`. If extraction never produced "the old ferryman" at all, that's
an *extraction* failure, not a *resolution* failure. Folding them together would make the
resolution metric move for reasons that have nothing to do with resolution. A metric
that moves for the wrong reasons is worse than no metric, because you'll act on it.

### Then they pointed a second tool at a real book

The pairwise eval needs hand-labelled ground truth, so it only runs on a small fixture
novel. A second diagnostic was built that needs **no ground truth at all**:

```python
def find_duplicate_candidates(db, novel_id):
    """Duplicate-looking entity pairs, split same-type vs cross-type.

    Scoring reuses the canonicalizer's own `_name_pair_similarity` at its own
    `LEXICAL_MATCH_RATIO`, so a pair listed here is one the canonicalizer would
    have called a match had it been comparing them at all.
    """
    entities = load_entity_surfaces(db, novel_id)
    same_type, cross_type = [], []
    for a, b in combinations(entities, 2):
        if a["surfaces"] & b["surfaces"]:
            score, kind = 1.0, "exact"
        else:
            score = max((_name_pair_similarity(x, y)
                         for x in a["surfaces"] for y in b["surfaces"]), default=0.0)
            if score < LEXICAL_MATCH_RATIO:
                continue
            kind = "near"
        (same_type if a["type"] == b["type"] else cross_type).append(pair)
```

It compares every entity to every other entity, using **the canonicalizer's own
similarity function at its own threshold**. So a pair that shows up here is, by
definition, a pair the canonicalizer *would have merged* — if it had ever compared them.

Run against a real three-chapter ingest of a LitRPG novel, 63 entities:

```
duplicates: 63 entities, 1 same-type, 14 cross-type

  1.00 exact  character:'Healer class' <-> object:'Healer class'
  1.00 exact  character:'Healer class' <-> faction:'Healer class'
  1.00 exact  object:'Healer class'    <-> faction:'Healer class'
  1.00 exact  character:'System'       <-> object:'System'
  1.00 near   character:'Archer Class' <-> object:'Archer class'
  ...
```

**Fourteen cross-type duplicate pairs against one same-type.** Fourteen to one.

The root cause is four lines of ordinary-looking code that had been there since the
generalisation from characters to all types:

```python
def _load_roster(self, entity_type: str) -> list[dict[str, Any]]:
    table = TYPED_TABLES.get(entity_type)
    ...
    rows = self.db.fetchall(
        f"SELECT id, name, aliases, description FROM {table} WHERE novel_id = %s",
        (self.novel_id,), dict_rows=True,
    )
```

**The roster is loaded one entity type at a time.** When the canonicalizer resolves
object names, it compares them against objects. When it resolves character names, it
compares them against characters. A thing extracted as a character in chapter 1 and as
an object in chapter 2 is **never compared to itself**, because the comparison never
happens.

```
     chapter 1                         chapter 2
     extraction says:                  extraction says:
     "Healer class" is a CHARACTER      "Healer class" is an OBJECT
              │                                   │
              ▼                                   ▼
     ┌────────────────┐                 ┌────────────────┐
     │  characters    │                 │    objects     │
     │  Healer class  │                 │  Healer class  │
     └────────────────┘                 └────────────────┘
              ▲                                   ▲
              └───────────  never compared ───────┘
                    (roster is per-entity-type)
```

The months of tuning had been spent on the same-type path. That path was handling **one
duplicate out of fifteen.**

Worse: the repair tool refused cross-type merges too. `merge_entities` raised on a type
mismatch, on the reasonable-sounding logic that a character and an object obviously
aren't the same thing. So these duplicates couldn't be fixed by the pipeline *or* by
hand. They were simply unreachable.

### What changed

**Repair, first.** `merge_entities` learned that a cross-type merge means something
different from a same-type one:

> *A same-type merge says "these two rows are the same thing"; a cross-type merge says
> "the source was **misclassified**, and the target's type is correct."*

That distinction determines what survives:

```python
# Entity-level references (relationships, shared_dynamics, canon_facts,
# commitments, state_deltas) key off `entities.id` and repoint identically
# either way. Type-specific rows do not: a `character_states` row hanging off
# something that turned out to be an object is an artifact of the
# misclassification, not data worth migrating, so it dies with the source's
# typed row. Event involvement *is* migrated, moving from the source's
# `involved_*` column into the target's.
```

That's a real semantic judgement, not a mechanical one. "The emotional state of the
Healer class in chapter 2" is not data; it's residue from a mistake. Delete it. But "an
event that involved the Healer class" *is* data — the event genuinely involved this
thing; only the *kind* of involvement was wrong. So it moves columns:

```python
def _move_between_array_columns(cur, table, from_column, to_column, src, tgt):
    """Move a typed id out of one array column and into another on the same row.

    Used for `events.involved_*` when the merge crosses types: the event still
    involves the entity, it was just filed under the wrong kind of involvement.
    """
```

Plus a detail that only real Postgres teaches you. Several foreign keys pointing at
typed rows are *not* `ON DELETE CASCADE` — `scenes.pov_character_id`,
`scenes.location_id`, `character_states.location_id`, `locations.parent_location_id`.
Deleting the source's typed row would raise a foreign-key violation. They're all
nullable, and the reference is wrong data anyway, so they get detached first:

```python
def _detach_typed_references(cur, entity_type, typed_id):
    """Clear references to a typed row that will not survive a cross-type merge.
    Only the FKs that are *not* ON DELETE CASCADE need this..."""
```

This is also why the merge has an integration test against a **real** Postgres, not a
fake:

> *Covered against real Postgres by `pipeline/db/tests/test_entity_merge_integration.py`,
> because the fake-DB tests assert which statements run and cannot catch a foreign-key
> violation.*

A test double that records SQL strings will happily let you delete a row that a real
database would refuse to delete.

**Detection, second.** `GET /api/novels/{id}/entities/duplicates` serves exactly the
function above, so the eval and the product can't drift apart:

```python
def count_duplicate_pairs(db, novel_id):
    """Re-exported here so the eval and the `/entities/duplicates` route grade
    the same thing. If these two ever diverge, the eval stops measuring what
    the product actually surfaces."""
    return find_duplicate_candidates(db, novel_id)
```

**Prevention, still open.** The canonicalizer still loads its roster one type at a time.
Cross-type duplicates are now *found* and *repairable*, but not *prevented*. This is
listed as an open gap, with an explicit exit criterion:

> *Done when: the canonicalizer considers candidates across entity types (or a
> post-ingest pass does), and the real-LLM ER eval's `cross_type_count == 0` assertion
> holds on a novel that isn't the hand-written fixture.*

---

## The lesson

The technical content of this post is a resolver with three evidence tiers, four
type-specific rule sets, two dedup stages, a deterministic fast path, four separate
ambiguity guards, and a transactional repair operation. All of it is real, all of it is
defensible, and I'd build it again.

But the thing actually worth taking away is smaller and more uncomfortable:

> **The measurement was worth more than the tuning.** Months went into optimising a path
> that handled one case in fifteen. The bug wasn't subtle once seen — it's four lines of
> obvious code — but it was invisible from inside the code, because from inside the code
> everything is working exactly as designed. It only became visible from outside, when
> something counted the results.

There's a corollary that's easy to state and hard to practise. When you build a system
where an LLM makes judgement calls, your instinct will be to improve the judgement:
better prompts, better rules, better thresholds. That instinct is not wrong, but it is
*unprioritised*. Until you have a number, you have no idea whether the case you're
improving is common or vanishingly rare — and the case you can most easily imagine is
usually not the one that's actually hurting you.

Build the eval first. Post 10 is about doing exactly that, and about how easy it is to
build an eval that measures the wrong thing.

---

## Where we are

Names now reliably become the right entity ids — reliably enough to measure, and with a
known list of cases where they don't. That means the facts we extract land on stable
identities, which finally makes it worth building the thing Post 2 ended by demanding:
a proper representation of *time*.

Because we still can't answer the question this series opened with. Aelric picks up a
dagger in chapter 4 and gives it away in chapter 12. Which chapter's fact wins? Both are
rows in the database. Neither knows about the other.

---

*Next: [Part 5 — Time Travel Without Snapshots](05-time.md) — event sourcing, why
storing state is a trap, bitemporal edges, and how to answer "as of chapter N" for any N
without storing N copies of the world.*
