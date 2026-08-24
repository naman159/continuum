# Part 11 — The Unglamorous Twenty Percent

*Part 11, the last, of the Continuum series. Ten posts of architecture. This post is
about the work that doesn't appear in any diagram: making it not fall over. It contains
the worst bug in the project's history, and the story of deleting six weeks of working
code on purpose.*

---

## A bug that reported success

Start with this, because everything else in the post is a variation on it.

`analyze_chapter` returned:

```json
{
  "chapter_id": "8f3a2c91-...",
  "chunks": 3,
  "new_characters": 4,
  "events": 11,
  "materialized": true
}
```

Success. Four characters, eleven events, a chapter id.

The chapter did not exist. No row. No events. Nothing. The id in that response pointed at
nothing at all.

No exception was raised. Nothing was logged except one stray `WARNING scene insert
failed`. The test suite was green.

Here's how.

### Transactions, from first principles

A **transaction** is a group of database operations that either all take effect or none
do. You open it, do work, and either `COMMIT` (make it permanent) or `ROLLBACK` (discard
it).

```sql
BEGIN;
  INSERT INTO chapters (...) VALUES (...);
  INSERT INTO events   (...) VALUES (...);
  INSERT INTO events   (...) VALUES (...);
COMMIT;
```

If the process dies after the second insert, none of it happened. That's the guarantee —
the **A** in ACID, for *atomicity*.

Continuum uses this for the persistence phase, exactly as Post 5 described:

```python
with client.session() as s:
    chapter_id = ingest_chapter(s, ...)
    _persist_extraction(s, ...)          # entities, events, relationships, threads
    persist_multi_summaries(s, ...)
    persist_scenes(s, ...)
    persist_knows_edges(s, ...)
    persist_commitments(s, ...)
    persist_canon_facts(s, ...)
    persist_state_deltas(s, ...)
# ← commits here if no exception; rolls back if one escapes
```

One transaction. A failure anywhere means no partial chapter. Good design.

### The wrinkle: Postgres aborts the whole transaction on any error

Here's the behaviour that breaks the design, and it's specific to Postgres (MySQL, for
one, does not do this):

**When any statement in a transaction fails, Postgres marks the entire transaction as
aborted.** Every subsequent statement — including ones that would be perfectly valid —
fails immediately with:

```
InFailedSqlTransaction: current transaction is aborted,
commands ignored until end of transaction block
```

You cannot recover by catching the exception and carrying on. The connection is in a
failed state until you end the transaction.

Now look at the "best effort" persistence helpers, whose intent was completely
reasonable:

```python
try:
    scene_id = db.fetchval("INSERT INTO scenes (...) VALUES (...) RETURNING id", ...)
    inserted.append(str(scene_id))
except Exception as exc:  # pragma: no cover - log and continue
    logger.warning("scene insert failed: %s", exc)
```

*"One bad scene shouldn't cost the whole chapter."* Sensible. Scenes are a nice-to-have;
losing one is much better than losing the chapter.

Except the transaction is now aborted. Every statement after this point fails. And each
one hits a *different* handler that also catches and logs. So the failures cascade, each
one swallowed:

```
persist_scenes        → INSERT fails → caught, logged
                        ↓ transaction now ABORTED
persist_knows_edges   → INSERT fails (InFailedSqlTransaction) → caught, logged
persist_commitments   → INSERT fails (InFailedSqlTransaction) → caught, logged
persist_canon_facts   → INSERT fails (InFailedSqlTransaction) → RAISES (no handler)
```

That last one at least produces an error — but a useless one. The reported error is
"current transaction is aborted," which names the *symptom*. The actual cause appears
only as a lone `WARNING scene insert failed`, several hundred log lines earlier.

### And then the part that's genuinely alarming

What if `persist_canon_facts` and `persist_state_deltas` had nothing to write? Neither
issues a statement. Nothing raises. Execution reaches the end of the `with` block
cleanly, and Python calls `COMMIT`.

**Postgres silently converts a `COMMIT` on an aborted transaction into a `ROLLBACK`.**

No error. No warning. `COMMIT` returns success. The entire chapter — the raw text, the
events, the entities, everything the LLM calls paid for — is discarded, and the function
returns a success payload with a chapter id for a row that was never committed.

```
   ┌───────────────────────────────────────────────────────────┐
   │  BEGIN                                                     │
   │    INSERT chapter        ✓                                 │
   │    INSERT events × 11    ✓                                 │
   │    INSERT scene          ✗  ← caught & logged as WARNING   │
   │         ⚡ transaction state: ABORTED                       │
   │    (no further statements happen to run)                   │
   │  COMMIT  ──────────▶ silently becomes ROLLBACK             │
   └───────────────────────────────────────────────────────────┘
                    ↓
          returns {"chapter_id": "8f3a…", ...}
                    ↓
          the row does not exist
```

### Why the tests never caught it

Look again at that except clause:

```python
except Exception as exc:  # pragma: no cover - log and continue
```

`# pragma: no cover` tells the coverage tool to ignore this line — "we know it's
untested, don't complain." Every one of these handlers carried it.

So the code path that caused the bug was:

- **never executed in tests** (by explicit exclusion),
- **and could not be**, because the test suite uses a fake in-memory database, and
  transaction abortion is a property of a *real* Postgres connection's state machine. A
  fake DB that records SQL strings will happily accept a statement after a failure, and
  its "commit" is a no-op.

> **`# pragma: no cover` on an error handler is a note saying "the recovery path is
> untested."** That's exactly the path that runs when things are already going wrong. If
> a handler is worth writing, the case it handles is worth constructing.

### The fix: savepoints

A **savepoint** is a marker inside a transaction that you can roll back to without
discarding the whole thing.

```sql
BEGIN;
  INSERT INTO chapters (...);        -- keep
  SAVEPOINT sp1;
  INSERT INTO scenes (...);          -- fails
  ROLLBACK TO SAVEPOINT sp1;         -- undo just this; transaction is healthy again
  INSERT INTO knows_edges (...);     -- works
COMMIT;                              -- commits the chapter and the knows_edge
```

Rolling back to a savepoint clears the aborted state. The transaction lives.

`DBSession` gained one:

```python
@contextmanager
def savepoint(self):
    """Isolate a block of statements behind a SAVEPOINT.

    Postgres aborts the whole transaction on any statement error, so a
    caller that catches an exception and keeps issuing statements on the
    same connection gets ``InFailedSqlTransaction`` for everything that
    follows — and the eventual COMMIT is silently converted to ROLLBACK.
    Wrapping a best-effort block here rolls back just that block, leaving
    the surrounding chapter transaction usable.
    """
    with self._conn.transaction():
        yield
```

(psycopg3's `conn.transaction()` emits a real `SAVEPOINT` when it's nested inside an
existing transaction — which is exactly the case here.)

And a helper applied at every catch site:

```python
@contextmanager
def _row_savepoint(db: Any):
    """Isolate one best-effort statement so a failure can't poison the chapter.
    ...
    Falls back to a pass-through for DB objects with no savepoint support
    (test fakes, or a bare DBClient used outside a session).
    """
    sp = getattr(db, "savepoint", None)
    if sp is None:
        yield
        return
    with sp():
        yield
```

The `getattr` fallback is what lets the same helper work with test fakes and with a bare
`DBClient` used outside a session — no savepoint, no error, just a pass-through.

Every best-effort block is now wrapped:

```python
try:
    with _row_savepoint(db):
        scene_id = db.fetchval("INSERT INTO scenes (...) RETURNING id", ...)
    inserted.append(str(scene_id))
except Exception as exc:
    logger.warning("scene insert failed: %s", exc)
```

And the test that pins it runs against **real Postgres**, because it has to:

```python
"""Real-Postgres coverage for DBSession.savepoint.

Fake-DB tests cannot catch this; it is a property of the real connection's
transaction state machine.
"""

def test_swallowed_error_in_savepoint_leaves_transaction_usable(db, novel):
    """A caught failure inside a savepoint must not poison later statements."""
    with db.session() as s:
        s.execute("INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s)",
                  (novel, 1, "before the failure"))

        with pytest.raises(Exception):
            with s.savepoint():
                s.fetchval("SELECT 1/0")          # division by zero — a real SQL error

        # The whole point: the connection is still usable afterwards.
        assert s.fetchval("SELECT 42") == 42
        s.execute("INSERT INTO chapters (novel_id, number, raw_text) VALUES (%s,%s,%s)",
                  (novel, 2, "after the failure"))
    # And the outer transaction really committed both rows.
```

`SELECT 1/0` as a deliberate error is a nice touch — it's guaranteed to fail in a real
database and impossible to fake.

There's a second test in the same file that *characterises the old behaviour* — asserting
that without a savepoint, the data really is silently discarded. That's a valuable and
underused kind of test: it documents the failure mode so that if someone "simplifies" the
savepoint away, the test tells them exactly what they've reintroduced.

### The three lessons

**Lesson 1: "catch and continue" is a lie unless you know your transaction semantics.**
The pattern is fine in a language runtime. Inside a database transaction it can invert
its own intent completely.

**Lesson 2: a fake database tests your code, not your database.** In-memory test doubles
are wonderful for speed. They cannot catch anything that is a property of the real
engine: transaction state machines, foreign-key enforcement, reserved words, index
behaviour, constraint violations. Anywhere your code *depends on database semantics*, you
need at least one test against the real thing.

**Lesson 3: the worst bugs report success.** A crash is a gift — it points at itself. This
bug returned HTTP 200 with a plausible payload. The only way to find it is to check that
the thing you claim to have written is actually there.

---

## The fake-DB problem, again, in miniature

Two more bugs from the same family, both found in the same audit, both trivially small.

### A reserved word

```
`symmetric` is a fully reserved PostgreSQL keyword (it comes from BETWEEN
SYMMETRIC), so it parses as a bare identifier only after a dot or as an AS
label. The DDL already quoted it; the INSERT in _persist_extraction did not,
so every relationship persist raised a syntax error.
```

**Every relationship insert had been failing.** The schema quoted the column
(`"symmetric"`); the `INSERT` didn't. Postgres refuses to parse a bare `symmetric` in a
column list.

Why hadn't anyone noticed? Because the relationship persist ran inside… the same
best-effort transaction from the section above. The failure was swallowed. Two bugs, each
hiding the other.

And the fix note:

> *Covered by a round-trip integration test over all three column values (TRUE/FALSE/NULL)
> against a real database — a mocked cursor would have accepted the broken SQL.*

A mock cursor accepts any string. Only a real parser rejects a reserved word.

### A foreign key that wasn't a cascade

Post 4's cross-type entity merge deletes the source's typed row. Several columns point at
it with `NO ACTION` foreign keys rather than `ON DELETE CASCADE` —
`scenes.pov_character_id`, `scenes.location_id`, `character_states.location_id`,
`locations.parent_location_id`. Deleting the row raises a foreign-key violation.

> *Covered against real Postgres by `pipeline/db/tests/test_entity_merge_integration.py`,
> because the fake-DB tests assert which statements run and cannot catch a foreign-key
> violation.*

Same category. A fake DB verifies you issued the right SQL. Only a real one verifies the
SQL is *allowed*.

---

## Poisoning the vector store, silently, forever

The second-worst bug in the project, and the most instructive about fallback design.

`EmbeddingService.embed_text` had a mock mode producing deterministic fake vectors from a
SHA-256 hash — essential for offline testing, as Post 2 described. And it had a fallback:
if the real embedding call failed for *any* reason — rate limit, timeout, missing key,
wrong dimensions — it returned a hash vector instead.

That looks like graceful degradation. It is catastrophic, and here's the number that
proves it:

> *`_hash_embedding()`, whose SHA-256 output carries no semantic signal whatsoever: two
> strings differing only by a trailing period measure ≈ −0.016 cosine against each other,
> while two entirely unrelated texts measure ≈ −0.001.*

Read that carefully. Two nearly-identical strings are measured as *slightly further
apart* than two completely unrelated ones. The vectors are noise. Worse than noise —
mildly anti-correlated with similarity.

And those vectors were written to `chapters`, `events`, `scenes` and `commitments`, and
indexed by HNSW. Once stored, they are **indistinguishable from real embeddings.** There
is no way to look at a 1536-float array and tell whether a model produced it or a hash
function did.

So a transient rate limit during ingestion silently and permanently degraded dense
retrieval for whatever it touched, with no error, no log, and no way to find the affected
rows afterwards.

### Two fixes

**Fail loudly.** Outside explicit mock mode, embedding failures now raise:

```python
class EmbeddingError(RuntimeError):
    """Raised when a real embedding cannot be produced.

    Never fall back to ``_hash_embedding`` outside mock mode: hash vectors
    carry no semantic signal (near-identical strings score ~0 cosine against
    each other, the same as unrelated ones), so a fallback silently and
    permanently poisons dense retrieval for whatever it was written to.
    """
```

With a dimension assertion too, because a model returning the wrong width is its own
silent corruption:

```python
values = [float(v) for v in vector]
if len(values) != settings.embedding_dimensions:
    raise EmbeddingError(
        f"embedding model {settings.embedding_model!r} returned "
        f"{len(values)} dimensions, but EMBEDDING_DIMENSIONS is "
        f"{settings.embedding_dimensions}. The database vector columns "
        "were created at the configured size, so this would fail at "
        "insert time or silently corrupt retrieval.")
```

**Record provenance.** Every vector column gains a sibling recording what produced it:

```sql
-- ---- Embedding provenance ----
-- Which model produced each stored vector. Without this, a hash vector (from
-- a USE_MOCK_LLM ingest) is indistinguishable from a real embedding forever,
-- and a store that mixes the two silently returns near-random dense results.
-- Mixing is the normal case, not an exotic one: ingesting with mock and later
-- switching to a real model is the default development path.
-- NULL means "written before this column existed" — provenance unknown.
ALTER TABLE chapters    ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE events      ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE scenes      ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS embedding_model TEXT;
```

with an explicit sentinel for mock output:

```python
@property
def model_name(self) -> str:
    """Provenance tag stored alongside every vector this service writes.

    Mock vectors get an explicit sentinel rather than the configured model
    name — they are hash output with no semantic content, and the whole
    point of recording provenance is being able to find them later.
    """
    return "mock:hash" if self.use_mock else settings.embedding_model
```

Now you can audit the store:

```sql
SELECT embedding_model, count(*) FROM chapters GROUP BY 1;
```

> *Rows with `NULL` predate the column; rows tagged `mock:hash` carry no semantic content
> and should be re-ingested before dense retrieval is trusted.*

Note the second half of the schema comment, which is the real insight. Mock ingestion is
**not** an exotic failure — it's the normal development path. You ingest with mock while
building, then switch to a real model. The store *will* be mixed. Provenance isn't a
forensic tool for a past incident; it's a standing requirement.

### The general rule

Compare two fallbacks from this codebase:

| Fallback | Degraded state | Verdict |
|---|---|---|
| `tiktoken` missing → split on whitespace | Chunks are ~25% smaller. Everything works. Recoverable by reinstalling. | **Fine** |
| Embedding call fails → hash vector | Poisoned data, indistinguishable from good data, permanent, undetectable. | **Fatal** |

The difference isn't severity. It's **detectability and reversibility**.

> **Before writing a fallback, ask: if this fires in production and nobody notices for
> six months, what is the damage — and can I tell afterwards that it fired?** If the
> answer to the second half is no, don't write the fallback. Fail.

---

## Sizing things for the server that actually runs them

A short section of purely operational bugs, each one a two-line fix that changes whether
the thing survives contact with a second user.

### The connection pool was five

```python
db_max_connections: int = field(
    default_factory=lambda: int(os.getenv("DB_MAX_CONNECTIONS", "20"))
)
```

It used to be a hardcoded 5. Here's why that number is wrong, and the comment explains it
better than I can:

```python
# Connection-pool ceiling. Every API route handler is a plain `def`, so
# Starlette runs them on its 40-slot threadpool; a pool smaller than that
# makes concurrent requests queue and then fail with PoolTimeout after
# psycopg's 30s default, surfacing as an unhandled 500.
```

Unpack it. FastAPI is built on Starlette. A route handler declared `async def` runs on the
event loop; a plain `def` handler runs on a **threadpool**, so that blocking code (like
synchronous database calls) doesn't stall the loop. Starlette's default threadpool is 40
threads.

So up to 40 requests can be in flight, each wanting a database connection, against a pool
of 5. The other 35 wait. psycopg's pool has a 30-second timeout, after which it raises
`PoolTimeout` — which nothing catches, so it surfaces as an unhandled 500.

The symptom, from the outside: *the API works fine, until about six people use it at once,
and then it returns 500s for thirty seconds.*

> **A connection pool must be sized against the concurrency of the thing calling it.** Not
> against a number that felt reasonable. Find out how many concurrent callers your server
> permits.

### A race that leaked connections

The read layer's DB provider is a process-wide singleton:

```python
def get_db() -> DBClient:
    """Process-wide read client.

    The lock matters: route handlers run on Starlette's threadpool, so an
    unsynchronized check-then-set lets two threads race on the first request
    and each construct a DBClient. DBClient opens its pool eagerly, so the
    loser's connections are leaked for the process lifetime with no reference
    left to close them.
    """
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = DBClient()
    return _db
```

Classic double-checked locking. Without the lock, two threads arriving simultaneously on
the first request both see `None`, both construct a `DBClient`, and one assignment wins.
The loser's client is unreferenced — but `DBClient` **opens its pool eagerly** in
`__init__`, so its connections are already established. Nobody holds a reference, so
nobody can close them. They leak for the process lifetime.

The outer `if _db is None` avoids taking the lock on every subsequent call (which would
be a contention point on a hot path); the inner one is the actual correctness check.

### Indexes for the queries you actually run

Two families were missing.

**Array containment.** Events store involvement as UUID arrays, and every entity detail
page filters on them:

```sql
-- These UUID[] columns are the read layer's primary access path: every
-- character/location/object/faction detail page runs `%s = ANY(e.involved_*)`,
-- and reads/graphs.py self-joins events against characters on one of them.
-- Without GIN, each of those is a sequential scan over events, whose heap
-- tuples carry a VECTOR and a tsvector apiece.
CREATE INDEX idx_events_involved_characters ON events USING GIN (involved_characters);
CREATE INDEX idx_events_involved_locations  ON events USING GIN (involved_locations);
CREATE INDEX idx_events_involved_objects    ON events USING GIN (involved_objects);
CREATE INDEX idx_events_involved_factions   ON events USING GIN (involved_factions);
```

The last clause is the killer detail. A sequential scan over `events` reads every row's
heap tuple — and each of those carries a 1536-dimensional vector (6 KB) plus a tsvector.
So "scan the events table" means reading megabytes to answer a question about one
character.

**Foreign keys driving the delete cascade.** This one is genuinely non-obvious:

```sql
-- Deleting a chapter cascades to its events; Postgres then enforces every
-- referencing column with a per-deleted-row lookup. Unindexed, that is a
-- sequential scan per event per table, which is what makes --replace on a
-- mature novel take minutes while holding write locks.
CREATE INDEX idx_continuity_flags_chapter          ON continuity_flags(chapter_id);
CREATE INDEX idx_thread_events_event               ON thread_events(event_id);
CREATE INDEX idx_character_states_chapter          ON character_states(chapter_id);
CREATE INDEX idx_state_deltas_event                ON state_deltas(event_id);
CREATE INDEX idx_knows_source_event                ON knows_edges(source_event_id);
-- ...and a dozen more
```

Here's the mechanism most people miss. Postgres **automatically indexes the referenced
side** of a foreign key (the primary key), but **not the referencing side**. So when you
delete a row, it must check every table that references it — and if the referencing
column isn't indexed, that check is a sequential scan.

Now compound it: deleting a chapter cascades to its 40 events. For *each* of those, every
referencing table is scanned. Forty events × six referencing tables × a full scan each.

The observable symptom: `--replace` on a mature novel takes minutes, while holding write
locks that block everything else.

> **Index the referencing side of every foreign key you ever delete through.** It's not
> automatic, and the cost of forgetting shows up as a mysteriously slow delete rather than
> a slow select.

### Two more, briefly

**An advisory lock around materialization** — covered in Post 5. Two concurrent
materializations of the same novel were a data-loss race, not just a stale read.

**A dimension assertion at `init-db`:**

```python
def _assert_embedding_dimension_matches(db: DBClient) -> None:
    """Fail loudly when EMBEDDING_DIMENSIONS drifts from the live schema.

    Vector columns are created with the configured size baked in, but every
    CREATE TABLE is IF NOT EXISTS, so re-running init-db against a populated
    database silently leaves the old width in place. Every subsequent chapter
    then dies on `%s::vector` with an error that never mentions the config
    change that caused it.
    """
```

The schema is idempotent — every statement is `IF NOT EXISTS` — which is exactly what you
want for a schema you re-apply. But `CREATE TABLE IF NOT EXISTS` on an existing table is
a *no-op*, so it cannot widen a vector column. Change `EMBEDDING_DIMENSIONS` from 1536 to
3072, re-run `init-db`, get a success message, and then watch every chapter die on an
opaque cast error that never mentions the config change.

The fix reads the live column width out of the system catalogue and compares:

```python
live = db.fetchval("""
    SELECT atttypmod FROM pg_attribute
    WHERE attrelid = 'chapters'::regclass AND attname = 'embedding'
""")
if live is not None and int(live) != settings.embedding_dimensions:
    raise SystemExit(
        f"EMBEDDING_DIMENSIONS is {settings.embedding_dimensions} but "
        f"chapters.embedding is vector({int(live)}). CREATE TABLE IF NOT "
        "EXISTS cannot widen an existing column: either restore the old "
        "value, or migrate the vector columns and re-embed.")
```

The error message names the config variable, the actual width, the reason, and both
recovery options. That's the standard an error message should meet, and almost never does.

---

## A bug about meaning, not mechanics

One more, because it's a different species from everything above.

Thread updates link a plot thread to the event that advanced it. The linking code matched
the extractor's `event_description` against the events actually persisted, and if nothing
matched, it fell back to the chapter's *first* event.

Innocuous-looking. Here's what it produced:

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

*"surfaced a blade-sharpening as the evidence for a succession crisis."*

The system wasn't broken in any mechanical sense. Every row was well-formed; every
foreign key resolved. It was **making things up** — asserting a causal link between a
thread and an event that the text never supported, in a table whose entire semantic
content is "this event advanced this thread."

And the last sentence is the same principle that has run through the whole series, in its
seventh disguise:

> *An unlinked thread update is merely incomplete; a wrongly-linked one is false.*

Post 4: a missed merge is visible, a wrong merge is corruption. Post 5: a missing delta
means state doesn't update, a wrong delta makes it false. Post 8: a missing check is a
gap, a false FAIL destroys trust. Here: a missing link is a hole, a wrong link is a lie.

**When you can't determine something, say nothing.** A hole is honest. An invented answer
is worse than useless, because downstream consumers cannot tell it from a real one.

---

## Deleting six weeks of working code

The last production decision, and the biggest.

Between the SOTA schema work and the final architecture, Continuum grew a second product.
A full chapter-*generation* loop:

- a **scene planner** — given locked canon facts, pending commitments, active threads and
  recent context, produce a typed per-scene plan: POV, location, threads to advance,
  target beat, target word count;
- a **prose drafter** with a deterministic mock mode and revision notes;
- an **orchestrator** running plan → retrieve → draft → critique → revise → ingest, with
  the continuity critic as a gate;
- a **style fingerprint** so drafts matched the book's existing voice;
- a CLI command, an API endpoint, and a "generate" mode in the web UI.

It worked. It was well-typed, testable, and it was the architecture the research
literature recommends — the same plan/draft/critique/revise loop that Re3, DOC, and
StoryWriter all converge on.

It was deleted. Entirely. Not feature-flagged, not left dormant — `git rm`.

### Why

The project's own retrospective:

> *The system briefly grew a parallel chapter-authoring sub-system (a scene-level planner,
> a prose drafter, a critique-gated revision loop) while the SOTA schema and its four
> advanced subsystems — hybrid retrieval, a continuity critic, and an event-sourced state
> materializer among them — were still finding their callers. Once those subsystems were
> wired into the analyzer path and it became clear the analyzer was the product, the
> authoring sub-system was torn down rather than finished.*

The tell is in the middle: the four advanced subsystems were **still finding their
callers**. Retrieval, the critic, and the state materializer had all been built, and all
three were imported nowhere outside their own packages and tests. They existed and did
nothing.

Meanwhile the generation loop consumed them — but only for chapters that went through
generation. So the critic ran on generated chapters and not on ingested ones. The
retriever served the drafter and nothing else.

The choice was: finish the generator (making the subsystems useful for generated
chapters), or wire the subsystems into the analyzer (making them useful for *every*
chapter).

The second one won, for three reasons.

**Composability.** As an analyzer, Continuum works with *any* writer — a human typing in
the web UI, or any AI agent via MCP. As a generator, it competes with every AI writing
tool while being worse at prose than a frontier model called directly.

**Focus.** Two products in one repo means two sets of prompts, two failure surfaces, two
things to evaluate. The critic served the generator; making it serve every ingested
chapter meant it needed a different input adapter — work that only made sense once you'd
picked a product.

**The moat was in the memory, not the writing.** Anyone can call an LLM in a loop.
Spoiler-safe, point-in-time, event-sourced story state with entity resolution is the hard
part, and it's the part that composes.

### What survived

One piece: the style fingerprint. It was a deterministic, LLM-free measurement of prose —
sentence length, dialogue ratio, POV person, exclamation rate:

```python
def compute_style_fingerprint(text: str) -> dict[str, Any]:
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    word_counts = [len(s.split()) for s in sentences]
    avg_sentence_words = round(sum(word_counts) / len(word_counts), 2) if word_counts else 0.0
    dialogue_chars = sum(len(m.group(0)) for m in _DIALOGUE.finditer(text))
    dialogue_ratio = round(dialogue_chars / len(text), 3) if text else 0.0
    first = len(_FIRST_PERSON.findall(text))
    third = len(_THIRD_PERSON.findall(text))
    pov_person = "first" if first > third else "third"
    ...
```

It was built to condition a drafter. It survives as **chapter metadata for the wiki**,
computed at ingest for every chapter regardless of who wrote it. Useful in its own right,
independent of the thing it was built for.

### The schema got the same treatment

The research doc recommended SVO event triples (subject-verb-object) and a
`temporal_constraints` table for constraint propagation over event ordering. Both were
added to the schema.

Neither ever got a writer. The columns sat empty. The critic's sixth check queried
`temporal_constraints`, found nothing, and passed vacuously — a check that could never
fire, which is worse than no check because it appears in the count.

The consolidation removed them:

```sql
-- Idempotent DROPs converge databases created before the consolidation.
DROP TABLE IF EXISTS temporal_constraints;
ALTER TABLE events DROP COLUMN IF EXISTS subject_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS verb;
ALTER TABLE events DROP COLUMN IF EXISTS object_entity_id;
ALTER TABLE events DROP COLUMN IF EXISTS story_time_ordinal;
ALTER TABLE events DROP COLUMN IF EXISTS narrative_order;
ALTER TABLE events DROP COLUMN IF EXISTS scene_id;
ALTER TABLE knows_edges DROP COLUMN IF EXISTS fact_id;
ALTER TABLE chapters DROP COLUMN IF EXISTS generation_meta;
```

And the temporal check was deleted rather than left hollow.

The standard that emerged, and it's a good one to hold a schema to:

> **Every table and column in the schema has a producer and a consumer.** Anything that
> doesn't is deleted, not documented as future work.

A schema full of aspirational columns is worse than a small one. It suggests capabilities
that don't exist, it makes the critic's coverage look better than it is, and every reader
after you has to work out which half is real.

---

## What's still wrong

The honest list, from the project's own gap document. Every entry has an exit condition,
which is the part that makes it a list rather than an apology.

**1. Cross-type duplicate prevention.** The canonicalizer loads its candidate roster one
`entity_type` at a time, so a thing filed as a character in one chapter and an object in
the next is never compared. Measured at 14 cross-type pairs vs 1 same-type on a
three-chapter real ingest. Detection and repair exist; prevention doesn't.
*Done when: the canonicalizer considers candidates across entity types and the real-LLM
ER eval's `cross_type_count == 0` holds on a novel that isn't the fixture.*

**2. Critic precision isn't a precision measurement.** Floor of 0.8 by construction; one
negative case. A "flag everything" critic scores 0.8/1.0.
*Done when: scoring is per finding, matched on `(check, character, quote)`, against a
materially larger negative set.*

**3. Two checks have no live caller.** `thread_coverage` and the planned half of
`commitments` gate on ids that every caller passes as `[]`. Dormant by design — an
analyzer produces no plan — but `thread_coverage` also has a latent bug that would make
it warn on every thread if wired up today.
*Done when: either a planner supplies the ids and the plumbing is fixed, or both branches
are deleted rather than left as latent false positives.*

**4. No cost or token accounting.** A chapter is 13 extraction passes per chunk plus
dedup, canonicalization, and a claims-extraction call, all on one model. Nobody can tell
you what a chapter costs.
*Done when: a per-chapter run record exists (tokens, cost, timings, finding counts) and
low-stakes passes can route to a cheaper model.*

**5. Embedding dimensions are baked in at `init-db`.** Changing embedding models requires
a manual column and index rebuild that nothing documents. (The assertion above at least
makes it fail loudly.)
*Done when: there's a documented or scripted re-index path.*

**6. Job state is in memory.** A module-level dict. Doesn't survive a restart, doesn't
scale to multiple workers.
*Done when: job state moves to the database or a queue, if multi-worker deployment is ever
needed.*

Note the conditional on the last one. It's not a promise; it's a trigger.

There's also a mundane one that belongs here because it happens to everybody: a real API
key was once committed to `.env.example` and later replaced with a placeholder. The lesson
isn't interesting, but the frequency is — example files get filled in during setup and
then committed on autopilot.

---

## What I'd do differently

Five things, in order of how much time they'd have saved.

**Build the eval harness first.** By a distance. The eval was built in month five and
immediately found a bug that had been misdirecting effort for months (Post 4), plus a
retrieval regression that a mis-set threshold had been certifying as fine (Post 7).
Everything before that was tuning by intuition. The right order is: smallest possible
working pipeline → eval harness → improve, measured.

**Test against a real database from day one.** Every bug in this post's first three
sections was invisible to a fake DB. The fake is faster and fine for logic; it is
structurally blind to transaction state, foreign keys, reserved words, and index
behaviour. One real-Postgres integration test per module would have caught all of them.

**Decide what the product is before building the second one.** Six weeks of the
generation loop was work on a product that got cut. Not wasted — the critic and retriever
survived, and the decision to cut was informed by having built it. But it was expensive
tuition.

**Never write a fallback that produces undetectable bad data.** The hash-embedding
fallback was one line and could have permanently degraded the vector store with no trace.
The test to apply is: *if this fires and nobody notices for six months, can I tell
afterwards that it fired?*

**Grep for your invariants periodically.** "Does `to_chapter` appear in any WHERE clause?"
took ten seconds and found that every relationship read had been answering the wrong
question. Schema support and query support are separate pieces of work, and only the
second is observable.

---

## What it all adds up to

Eleven posts. The system, in one paragraph:

Chapters go in as raw text, which is never modified and is the only ground truth. Thirteen
focused LLM passes per chunk turn prose into typed facts. Two dedup stages plus a
three-tier evidence procedure resolve names to stable entity ids, refusing to merge when
the evidence is ambiguous. Typed state changes go into an append-only log; a deterministic
replay folds that log into snapshots and time-interval edges that can be rebuilt from
scratch at any time. A hybrid retriever fuses keyword and vector search by rank. Five typed
checks compare each chapter's claims against the world as it stood before that chapter.
One read layer serves a human wiki and an AI agent, with a chapter cutoff enforced by a
test that walks the codebase. Four evals and a ground-truth-free diagnostic measure the
statistical parts against a hand-written golden novel.

And the things that are actually worth carrying to your next system, none of which are
about novels:

**Pay once at write time to make reads cheap, deterministic, and safe.** The whole
architecture is that trade.

**Use LLMs to produce structure; use code to make decisions about structure.** The
grammatical anchor, the typed deltas, the critic's claims — all the same move. It puts
the trust boundary somewhere you can defend.

**Make the model cite evidence you can verify without a model.** `anchor in chapter_text`
is one line and it converts a hallucination-prone judgement into a checkable one.

**Store the derivation, not the conclusion.** Event log as truth, projections as
disposable caches. It buys time travel, auditability, and the freedom to fix bugs
retroactively.

**Know which of your two error types is unrecoverable, and bias hard against it.** A
missed merge is visible and cheap; a wrong merge is invisible and permanent. That single
inequality drove the design of the hardest component in the system.

**Refuse when the evidence is ambiguous.** It appears four times in the canonicalizer
alone. A hole is honest; a guess that looks like an answer is not.

**A fallback that produces undetectable bad data is worse than a crash.**

**Enforce architectural invariants with tests that read the code**, not tests that
exercise it. Behaviour tests can't express "nobody will ever add an uncapped read
function."

**Set thresholds from what correct looks like, not from what you measured.** A floor
calibrated against broken behaviour enforces the broken behaviour forever.

**Measure before you tune.** The single most expensive lesson here: months spent
optimising the path that handled one case in fifteen. The bug was four obvious lines. It
was invisible from inside the code, because from inside the code everything was working
exactly as designed.

Thanks for reading.

---

*Series: [1 — The Problem](01-the-problem.md) · [2 — From Prose to Rows](02-the-mvp.md) ·
[3 — Thirteen Passes](03-extraction-passes.md) · [4 — Identity](04-identity.md) ·
[5 — Time](05-time.md) · [6 — Modelling a World](06-modelling-a-world.md) ·
[7 — Search](07-search.md) · [8 — The Critic](08-the-critic.md) ·
[9 — Serving It](09-serving-it.md) · [10 — Measuring It](10-measuring-it.md) ·
11 — Production*
