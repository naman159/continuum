# Part 9 — Two Audiences, One Read Layer

*Part 9 of the Continuum series. Eight posts of machinery — extraction, identity, time,
search, checking — all of it behind Python functions that nobody outside the process can
call. This post is about exposing it, to two very different consumers, without letting
either one see the ending.*

---

## Two consumers, one constraint

**A human**, browsing a wiki of their own novel. They want character pages, a timeline, a
relationship graph — and a slider at the top that says *"show me this book as it existed
at chapter 12."* Not for safety; for usefulness. "What did the reader know at this point?"
is a question authors ask constantly.

**An AI agent**, drafting chapter 31. It wants the same data through a tool interface —
"what does Aelric know?", "which threads are open?", "has anyone mentioned the vault?" —
and it must **never** see chapter 31 or later. Post 1 established why: a model that can
see chapter 44 while writing chapter 3 writes chapter 3 as though the reveal has already
landed.

These are the same query with the same parameter. The human's slider position and the
agent's `writing_chapter - 1` are both "cutoff."

Which is exactly why building them separately is a trap you don't notice until you've
fallen in.

---

## What happened when they were built separately

For a while, they were. Two files:

- `backend/api/queries.py` — about 2,200 lines, serving the React wiki.
- `backend/mcp_server/queries.py` — serving the agent tools.

Both wrote SQL against the same tables. Both implemented "as of chapter N." Neither knew
about the other.

Here's what accumulated.

**The MCP timeline tool queried a table that never existed.** `timeline_events` selected
from a `timeline` table. There is no `timeline` table — there had been one for about a
week (commit `6bdaca5` added it, `7b96da4` removed it in favour of using `events` as the
timeline), and the wiki's copy was updated. The MCP copy wasn't. The tool had been
broken, in production, returning an error to every agent that called it, for months. The
wiki's timeline page worked fine, so nothing looked wrong.

**Two relationship graphs drifted apart.** `relationship_graph` existed in both files.
They'd been edited independently — different node shapes, different edge handling,
different bugs. An agent and a human asking for the same character's relationships got
different answers.

**Cutoff enforcement was inconsistent.** The wiki applied its cap; the MCP tools applied
theirs — sometimes. `timeline_events` was uncapped, so an agent asking for the timeline
while writing chapter 3 received the whole book. Which is precisely the failure mode the
entire architecture exists to prevent, sitting in production, unnoticed.

The lesson isn't "don't duplicate code" — that's a slogan, and sometimes duplication is
right. It's sharper than that:

> **Two implementations of the same invariant will drift, and the drift is invisible
> because each one is individually self-consistent.** The wiki tests passed. The MCP tests
> passed. Nothing tested that they *agreed*.

---

## One read layer

The consolidation produced `backend/reads/` — the only code in the system that runs
`SELECT` for presentation:

```
backend/reads/
├── db.py                    get_db() — the read layer's DB provider
├── common.py                resolve_cutoff(), max_chapter_for(), merge_story_edges()
├── novels.py                list_novels, get_novel
├── chapters.py              list_chapters (+ critique summary), list_scenes
├── characters.py            list_characters, get_character_detail, get_character_page
├── continuity.py            list_flags, get_chapter_critique, list_critiques
├── world.py                 locations, objects, factions, entity types, custom entities
├── timeline.py              list_timeline
├── graphs.py                relationship_graph, entity_graph, list_shared_dynamics
├── relationship_types.py    resolve_symmetric() — sole source of relationship symmetry
├── threads.py               list_threads (+ status_at_cutoff)
├── commitments.py           list_commitments (+ status_at_cutoff)
├── knowledge.py             knows_edges, located_in_edges, possesses_edges, canon_facts
└── search.py                hybrid search — backs /api/search and MCP search_story
```

And the architecture becomes a T:

```
        ┌─────────────────────┐        ┌──────────────────────────┐
        │  React wiki         │        │  AI writing agent        │
        │  ?cap=12            │        │  writing_chapter=13      │
        └──────────┬──────────┘        └────────────┬─────────────┘
                   │                                │
        ┌──────────▼──────────┐        ┌────────────▼─────────────┐
        │  FastAPI routes     │        │  MCP tools               │
        │  cap → up_to_chapter│        │  writing_chapter − 1     │
        │  NO SQL             │        │  NO SQL                  │
        └──────────┬──────────┘        └────────────┬─────────────┘
                   └───────────────┬────────────────┘
                                   ▼
                   ┌───────────────────────────────┐
                   │  backend/reads/               │
                   │  every fn: (db, novel_id,     │
                   │             up_to_chapter)    │
                   │  cutoff filtering in SQL,     │
                   │  exactly once                 │
                   └───────────────┬───────────────┘
                                   ▼
                            ┌─────────────┐
                            │  Postgres   │
                            └─────────────┘
```

The surfaces do exactly two things: translate their own parameter name into
`up_to_chapter`, and shape the response. All the SQL, and all the cutoff logic, lives in
one place.

Here's what a route looks like now:

```python
@router.get("", response_model=list[CharacterSummary])
def list_characters(novel_id: UUID, cap: int | None = Query(default=None)):
    return [CharacterSummary(**row)
            for row in characters_reads.list_characters(get_db(), novel_id, cap)]
```

Four lines. One translation (`cap` → third positional argument), one shape conversion.
There is nothing here to get wrong, which is the point.

---

## The cutoff contract

Every public function in `reads/` follows one rule:

```python
def f(db, novel_id, up_to_chapter: int | None, ...) -> ...
```

Third positional parameter, always `up_to_chapter`, and it means:

- **an integer**: return only data from chapters ≤ that number;
- **`None`**: the whole novel (resolved to the current maximum chapter).

```python
def resolve_cutoff(db, novel_id, up_to_chapter: int | None) -> int:
    return up_to_chapter if up_to_chapter is not None else max_chapter_for(db, novel_id)
```

Note that `None` doesn't mean "no filter." It resolves to the highest existing chapter
number, so the `WHERE ch.number <= cutoff` clause is *always present*. There is no code
path that runs an unfiltered query. If someone later adds chapter 71 while a request is
in flight, the request still sees the world it resolved at the start.

And the filter is always in **SQL**, never in Python:

```python
def list_characters(db, novel_id, up_to_chapter):
    cutoff = resolve_cutoff(db, novel_id, up_to_chapter)
    rows = db.fetchall("""
        SELECT id, name, aliases, description, first_appearance_chapter
        FROM characters
        WHERE novel_id = %s
          AND (first_appearance_chapter IS NULL OR first_appearance_chapter <= %s)
        ORDER BY name
    """, (novel_id, cutoff), dict_rows=True)
```

Filtering in Python would be a correctness bug, not just an inefficiency. Post 7 covered
the reason: if you retrieve 50 rows and *then* drop the future ones, you've returned 8
results when the caller asked for 50. The `LIMIT` must apply after the filter, which
means the filter must be in the database.

### Enforcing it with a test that reads the code

Conventions decay. Someone adds a function in a hurry, forgets the parameter, and now
there's a hole in the spoiler-safety guarantee that no functional test will find —
because the function *works*, it just answers a slightly different question than it
should.

So the contract is enforced structurally, by a test that inspects the codebase:

```python
"""The read-layer contract, enforced structurally.

1. No SQL in surfaces: api/routes/* and mcp_server/server.py contain no SQL.
2. Every public reads function (except documented exceptions) accepts
   up_to_chapter.
"""

def test_reads_functions_take_up_to_chapter():
    missing = []
    for mod_info in pkgutil.iter_modules(reads.__path__):
        if mod_info.name in {"db", "common", "tests"}:
            continue
        module = import_module(f"reads.{mod_info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            if (module.__name__, name) in CUTOFF_EXEMPT:
                continue
            if "up_to_chapter" not in inspect.signature(fn).parameters:
                missing.append(f"{module.__name__}.{name}")
    assert missing == [], f"reads functions missing up_to_chapter: {missing}"
```

Read what it does. `pkgutil.iter_modules` walks every module in the package. `inspect`
finds every function. `inspect.signature` reads its parameters. Any public function
without `up_to_chapter` fails the build, **by name**.

Two filters worth noting. `name.startswith("_")` skips private helpers — they're not
surface area. `fn.__module__ != module.__name__` skips *imported* functions: without it,
`from reads.common import resolve_cutoff` at the top of a module would make
`resolve_cutoff` look like a public function of that module and get flagged in every file
that imports it.

The companion test:

```python
SQL_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\bFROM\b|\bINSERT INTO\b",
                         re.I | re.S)

def test_no_sql_in_surfaces():
    surface_files = list((backend / "api" / "routes").glob("*.py"))
    surface_files.append(backend / "mcp_server" / "server.py")
    offenders = [str(f) for f in surface_files if SQL_PATTERN.search(f.read_text())]
    assert offenders == [], f"SQL found in surface files: {offenders}"
```

A regex over source files. Crude — it would flag the word SELECT in a docstring. That's
fine; the false-positive cost is "rename a variable", and the false-negative cost is a
route that reintroduces the drift this whole layer exists to prevent.

**These tests don't check behaviour. They check *shape*.** That's an unusual and
underused kind of test, and it's the right tool when your invariant is architectural
rather than functional. You cannot write a behavioural test for "nobody will ever add an
uncapped read function." You *can* write a test that fails when they do.

### The exemption list is documentation

```python
# Functions that legitimately take no cutoff (registry/config reads).
CUTOFF_EXEMPT = {
    ("reads.novels", "list_novels"),
    ("reads.novels", "get_novel"),
    ("reads.world", "list_entity_types"),
    ("reads.continuity", "get_chapter_critique"),   # keyed by explicit chapter
    # Pure label-classification helpers: no `db`/`novel_id` param at all, no
    # SQL, no point-in-time state to cut off. They're exported (not
    # underscore-prefixed) because reads.world/graphs/characters share them
    # to compute a relationship's `symmetric` flag from data those modules
    # already fetched under their own cutoff.
    ("reads.relationship_types", "is_symmetric"),
    ("reads.relationship_types", "resolve_symmetric"),
}
```

Six exemptions, each justified inline. `list_novels` returns a registry of books — books
don't have chapters relative to themselves. `is_symmetric` takes a relationship *label*
and returns a boolean; it has no database access and nothing to filter.

The important property: **adding an exemption requires editing this set**, which means it
shows up in code review as a deliberate act with a written reason, rather than as an
omission nobody noticed. The escape hatch exists and is expensive to use. That's the
right cost curve for an escape hatch.

---

## Point-in-time is harder than `WHERE number <= N`

Filtering rows by chapter is the easy 80%. The remaining 20% is where the real bugs live,
because some facts are *about* the future in ways a row filter doesn't catch.

### Status columns are novel-wide truth

A plot thread has `status` and `closed_chapter`:

```
plot_threads
┌────────────────────────┬──────────┬────────────────┬────────────────┐
│ title                  │ status   │ opened_chapter │ closed_chapter │
├────────────────────────┼──────────┼────────────────┼────────────────┤
│ The missing archivist  │ closed   │ 9              │ 40             │
└────────────────────────┴──────────┴────────────────┴────────────────┘
```

Now ask for open threads as of chapter 12. `WHERE opened_chapter <= 12` includes this row.
Correct — it *was* open at chapter 12. But the row says `status = 'closed'`, which is the
novel-wide truth as of chapter 70, and reporting it verbatim tells a reader at chapter 12
that the mystery gets solved.

So the read derives a second, cutoff-relative status alongside the raw one:

```sql
SELECT pt.id, pt.title, pt.status, pt.opened_chapter, pt.closed_chapter,
       CASE
         WHEN pt.status = 'closed'
              AND pt.closed_chapter IS NOT NULL
              AND pt.closed_chapter <= %(cutoff)s      THEN 'closed'
         WHEN pt.status = 'closed'
              AND pt.closed_chapter IS NULL
              AND %(uncapped)s                          THEN 'closed'
         WHEN pt.status = 'closed'                      THEN 'progressing'
         ELSE pt.status
       END AS status_at_cutoff
  FROM plot_threads pt
 WHERE pt.novel_id = %(novel_id)s
   AND (pt.opened_chapter IS NULL OR pt.opened_chapter <= %(cutoff)s)
```

Three branches:

1. Closed, and the closing chapter is at or before the cutoff → genuinely closed. Report
   it.
2. Closed, but with **no** `closed_chapter` recorded → we can't date the closure. Only
   report it if the view is uncapped (nothing is in the future).
3. Closed later than the cutoff → at this point in the book, it's still `progressing`.

Branch 2 needs its own helper, because "is this view uncapped?" isn't the same as
"`up_to_chapter is None`":

```python
def resolve_cutoff_and_uncapped(db, novel_id, up_to_chapter) -> tuple[int, bool]:
    """Cutoff plus whether the view is effectively uncapped (cutoff >= last chapter).

    Terminal statuses without a chapter anchor (a thread closed with no
    closed_chapter, a commitment marked broken, a flag resolved with no
    resolved_chapter_id) cannot be dated, so point-in-time views must not
    show them; only an uncapped view — where nothing lies in the future —
    may report them as terminal.
    """
    max_ch = max_chapter_for(db, novel_id)
    if up_to_chapter is None:
        return max_ch, True
    return up_to_chapter, up_to_chapter >= max_ch
```

Asking for `cap=70` on a 70-chapter novel is *effectively* uncapped, and should behave
the same as `None`. Getting that wrong means the last chapter behaves differently from
"the whole book," which is a confusing bug to chase.

The same three-way reasoning appears for commitments…

```sql
CASE
  -- broken/abandoned carry no chapter anchor: only an uncapped
  -- view may report them; a capped view saw them still pending.
  WHEN status IN ('broken','abandoned') AND %(uncapped)s THEN status
  WHEN payoff_chapter IS NOT NULL AND payoff_chapter <= %(cutoff)s THEN 'satisfied'
  ELSE 'pending'
END AS status_at_cutoff
```

…and for continuity flags, in Python because the resolution chapter needs a lookup:

```python
# An undatable resolution (no resolved_chapter_id, or one that points
# outside this novel) counts only in an uncapped view — a capped view
# cannot know the flag was ever resolved.
effectively_resolved = bool(r.get("resolved")) and (
    resolved_chapter_number <= cutoff if resolved_chapter_number is not None else uncapped
)
```

Three tables, one rule, stated three times because each has a different shape. That's the
sort of repetition that's worth it — the alternative is an abstraction that fits none of
them well.

### Even the derived status can leak

Filtering by `status_at_cutoff` is not enough for the agent-facing tools. Consider: a
thread closed in chapter 40, agent writing chapter 13. `status_at_cutoff` correctly says
`progressing`, so the thread appears in the open-threads list. But the row *also* carries
`status = 'closed'` and `closed_chapter = 40`, because those are the raw novel-wide
columns.

Hand that dictionary to an agent and it can read the ending off the metadata.

So the MCP tools **mask** the future-anchored fields:

```python
@mcp.tool()
def open_threads(novel_id: str, writing_chapter: int) -> Any:
    """Plot threads opened before writing_chapter and not yet closed as of that
    point... closed_chapter and status are future-anchored (the novel-wide
    truth): for any thread not yet closed at the cutoff, both are masked
    (closed_chapter=None, status=status_at_cutoff) so a thread closed in a
    later existing chapter doesn't leak its closure."""

    def run():
        rows = threads_reads.list_threads(reads_db.get_db(), UUID(novel_id),
                                          writing_chapter - 1, status="all")
        open_rows = [r for r in rows if r["status_at_cutoff"] != "closed"]
        for r in open_rows:
            if r["status_at_cutoff"] != "closed":
                r["closed_chapter"] = None
                r["status"] = r["status_at_cutoff"]
        return open_rows
    return _call(run)
```

And identically for commitments, masking `payoff_text`, `payoff_chapter`, and `status`.

Why mask in the MCP layer rather than in `reads/`? Because the human wiki genuinely wants
both. An author looking at chapter 12 with the slider on finds it useful to know that
this thread eventually closes in chapter 40 — that's their own book. An agent must not.
Same data, different trust levels, so the redaction belongs at the boundary that knows
which consumer it's serving.

### The bug where a filter simply didn't exist

The nastiest one in this area, found in the August 2026 audit.

Relationships have a validity interval — `from_chapter` and `to_chapter` — exactly like
the bitemporal edges of Post 5. An alliance formed in chapter 2 and broken in chapter 6
has `from_chapter = 2, to_chapter = 6`.

A grep of the entire repository found that **`to_chapter` appeared in zero WHERE
clauses.** Every relationship read meant "started before N." None meant "still in force at
N."

So an alliance that broke in chapter 6 was reported as current at chapter 20. Not
missing — *wrong*, confidently, with no indication.

The fix threads `to_chapter` and `superseded_by_id` through all five relationship reads.
And the same audit found its mirror image in the location edges: `active_only` filtered
on `until_chapter` but not `superseded_by_id`, so a character who moved twice within one
chapter matched two "active" edges and read as being in two places at once. Which is,
with some irony, exactly the contradiction the critic exists to catch.

```python
if active_only:
    # superseded_by_id is how the materializer marks an edge replaced by a
    # later one. Without this the materializer's own output contradicts
    # itself: a character who moves twice within one chapter has the old
    # edge closed at [N, N] and the new one open at [N, NULL], so both
    # match "active at N" and the character reads as being in two places
    # at once — the exact contradiction the critic exists to catch.
    where.append("le.superseded_by_id IS NULL")
    where.append("(le.until_chapter IS NULL OR le.until_chapter >= %s)")
```

Two lessons, both general:

> **Adding a column doesn't add a filter.** Post 5 designed intervals correctly and the
> materializer wrote them correctly. The read layer never learned to use them. Schema
> support and query support are separate pieces of work, and only the second one is
> observable.

> **`grep` for your invariants.** "Does `to_chapter` appear in any WHERE clause?" is a
> ten-second question with a devastating answer. Periodically asking whether the columns
> you designed are actually *used* is cheap and finds things no test will.

### Hiding a character who hasn't appeared yet

One more, subtler than it looks:

```python
first_appearance = identity_row.get("first_appearance_chapter")
if first_appearance is not None and first_appearance > cutoff:
    # The character hasn't appeared yet as of this cutoff: reporting the
    # identity (name/description summarize later chapters) would leak
    # spoilers, and list_characters already hides them at the same cutoff.
    return None
```

If someone requests a character detail page directly by id, with a cutoff before that
character's first appearance, the read returns `None` → the route returns 404.

The reasoning in the comment is the interesting part. The character's *description* is
generated from the whole book — "Aelric's estranged sister, revealed in chapter 44 to be
the Ash Council's agent." Returning that at chapter 12 leaks the reveal even though
you've technically only returned one row. And the list endpoint already hides them, so a
detail endpoint that didn't would be inconsistent: the character is invisible in the list
but reachable by URL.

**Spoiler-safety has to hold on every path, including the ones nobody navigates to.**

---

## MCP: giving an agent tools

Time to explain the agent side properly.

### What MCP is

**MCP** — the Model Context Protocol — is an open standard for exposing tools to AI
models. The problem it solves is integration sprawl: every AI application had its own
plugin format, so a tool built for one didn't work in another.

MCP defines a small protocol. A **server** advertises a list of tools: each has a name, a
description, and a typed parameter schema. A **client** (Claude Desktop, an IDE
extension, an agent framework) connects, reads the list, and can invoke them. Transport
is typically stdin/stdout of a subprocess — so a server is just a program, with no
network setup.

From the model's point of view, a tool is a function it may call:

```
tool: get_character
  description: A character's identity, latest state, state history, relationships,
               and events as of the chapter before writing_chapter.
  parameters:
    novel_id: string
    name: string
    writing_chapter: integer
```

The model decides when to call it and with what arguments, based on the description. Two
consequences follow:

1. **The description is a prompt.** It's the only thing telling the model what the tool
   does and when to reach for it. Vague descriptions produce unused or misused tools.
2. **You cannot rely on the model calling tools correctly.** Bad arguments, missing
   arguments, calls in a nonsensical order. The server must be robust to all of it.

### The server

```python
mcp = FastMCP("continuum")

@mcp.tool()
def get_character(novel_id: str, name: str, writing_chapter: int) -> Any:
    """A character's identity, latest state, state history, relationships, and
    events as of the chapter before writing_chapter."""
    return _call(
        lambda: characters_reads.get_character_page(
            reads_db.get_db(), UUID(novel_id), name, up_to_chapter=writing_chapter - 1
        )
    )
```

That's the whole pattern, thirteen times over. Note three things.

**`writing_chapter - 1`.** The tool takes the chapter being *written* and subtracts one.
An agent drafting chapter 31 passes `writing_chapter=31` and sees the world through
chapter 30. That's the spoiler-safety contract in a single arithmetic expression, applied
at the boundary — which is the right place, because it's the only place that knows the
parameter *means* "the chapter I am currently writing."

**No SQL.** Enforced by the contract test above.

**`_call`:**

```python
def _call(fn: Callable[[], Any]) -> Any:
    """Never raise into the transport; agents self-correct from error dicts."""
    try:
        return fn()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
```

Every tool is wrapped in it. An exception becomes `{"error": "ValueError: badly formed
hexadecimal UUID string"}`, returned as a normal result.

This is a genuinely different error-handling philosophy from a REST API, and the reason
is in the docstring. A model that gets a transport-level crash has nothing to work with —
the tool call just failed. A model that gets `{"error": "ValueError: badly formed
hexadecimal UUID string"}` reads it, notices it passed a malformed id, and tries again.
**The error message is part of the model's context, so it should be written for a
reader.**

### The thirteen tools

```
list_novels              every novel: id, title, author, highest chapter
list_chapters            chapters before writing_chapter, with critique summary
search_story             hybrid search over prose and extracted facts
get_character            identity, state, history, relationships, events
character_knowledge      who knows what + location and possession history
relationships            the character relationship graph
open_threads             plot threads still open at the cutoff
unresolved_commitments   foreshadowing still awaiting payoff
timeline_events          story events grouped by chapter
canon_facts              established world facts (optionally locked-only)
scene_list               scene segmentation: POV, location, present characters
check_continuity         critique a draft WITHOUT saving it
save_chapter             ingest a finished draft through the full pipeline
```

Eleven reads, two writes. The suggested workflow, from the README:

```
open_threads + unresolved_commitments + get_character
        ↓
     draft chapter 31
        ↓
   check_continuity          ← critic runs, nothing is saved
        ↓
     revise
        ↓
    save_chapter             ← full pipeline: extract, persist, materialize, critique
```

And the honest caveat, repeated from Post 8: nothing *enforces* that sequence.
`check_continuity`'s docstring says "Always run this before save_chapter", and a
docstring is advice.

### Descriptions that carry the semantics

Look at how much explanation lives in a tool description:

```python
@mcp.tool()
def list_chapters(novel_id: str, writing_chapter: int) -> Any:
    """List the chapters written *before* writing_chapter (number, title,
    summary, critique summary) for orientation. Chapters from writing_chapter
    onward are withheld: returning them would hand the drafting agent the plot
    of the book it has not written yet."""
```

That last sentence isn't for the model — it's for the next engineer, sitting where they
will definitely read it. An implementation comment about *why* a filter exists, placed in
the one piece of text that can't be skipped.

And:

```python
@mcp.tool()
def canon_facts(novel_id: str, writing_chapter: int, locked_only: bool = False) -> Any:
    """Established world facts as of the chapter before writing_chapter.
    locked_only=True limits to facts whose contradiction is a hard continuity
    failure. Facts sourced from writing_chapter or later are excluded (cutoff =
    writing_chapter - 1), matching the other cutoff-aware tools."""
```

`locked_only` is explained in terms of *consequence* — "facts whose contradiction is a
hard continuity failure" — rather than mechanism. A model choosing whether to pass it
needs to know what it's for, not what column it filters.

Incidentally, that final sentence documents a bug fix. `canon_facts` and `list_chapters`
were both capped at `writing_chapter` rather than `writing_chapter - 1` until August
2026 — an off-by-one that leaked exactly one chapter: the one being written. Which is the
worst possible one to leak, because if that chapter already exists, the agent is being
handed a previous draft of the thing it's supposed to be writing.

### One implementation, two callers — proven

The whole point of the consolidation is that the wiki and the agent see the same data.
That's not left to convention:

```python
"""reads.characters: cutoff-aware character reads against real Postgres.

`get_character_page` is the MCP writer tool's name-addressed entry point; it
resolves name -> character id in SQL and then delegates to
`get_character_detail` so the wiki detail page and the MCP character page
share one set of sub-queries instead of two drifting implementations.
"""
```

The MCP tool takes a *name* (an agent knows "Aelric", not a UUID); the wiki takes an
*id*. `get_character_page` resolves name → id and calls the exact function the wiki uses.
The difference between the two surfaces is one lookup. Everything after that is
physically shared code, so drift is impossible rather than merely discouraged.

---

## The HTTP side

The wiki runs on FastAPI. Nineteen routers, one per domain:

```python
app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)
app.include_router(characters.router)
...
```

Response shapes are Pydantic models, which validate at the boundary:

```python
@router.get("/{character_id}", response_model=CharacterDetail)
def get_character(novel_id: UUID, character_id: UUID, cap: int | None = Query(default=None)):
    detail = characters_reads.get_character_detail(get_db(), novel_id, character_id, cap)
    if detail is None:
        raise HTTPException(status_code=404, detail="Character not found")
    return CharacterDetail(**detail)
```

`novel_id: UUID` means FastAPI parses and validates the path parameter before your code
runs — a malformed UUID returns a structured 422, not a 500 from deep inside psycopg.

### Processing is a background job

Ingesting a chapter takes thirty-plus LLM calls and can run for minutes. You cannot hold
an HTTP request open for that. So it's a job:

```python
def submit_job(*, novel_id, chapter_number, text, replace=False, run_critic=None) -> str:
    chunks = sliding_window_chunks(text, chunk_size=..., overlap=...)
    total_passes = len(chunks) * len(PASS_ORDER) + 2   # passes per chunk + dedup + canonicalization

    job_id = _job_store.create(total_passes=total_passes)
    tracker = ProgressTracker(job_id=job_id, store=_job_store)

    def _run():
        try:
            result = analyze_chapter(..., progress=tracker, ...)
            _job_store.mark_done(job_id, result)
        except Exception as exc:
            _job_store.mark_error(job_id, str(exc))

    _executor.submit(_run)
    return job_id
```

`POST /process` returns a job id immediately; the browser polls `GET /jobs/{id}`. The
`total_passes` arithmetic — chunks × passes + 2 — lets the UI show a real progress bar
rather than a spinner, because the work is known in advance.

The progress tracker threads a callback down into the extractor:

```python
class ProgressTracker:
    def on_pass_start(self, pass_name: str) -> None:
        with self._store._lock:
            record = self._store._jobs.get(self._job_id)
            if record:
                record.status = "running"
                record.current_pass = pass_name
```

so the UI can say "running pass 7 of 41: relationship_updates". Small thing; the
difference between a system that feels broken during a four-minute wait and one that
doesn't.

And a limitation stated rather than hidden:

```python
# Module-level singletons
_job_store = JobStore()
_executor = ThreadPoolExecutor(max_workers=2)
```

Job state is a dict in memory. Restart the server and every in-flight job's status is
gone (the chapter itself is safe — that's transactional). Run two server processes and
each has its own store, so a poll can hit the wrong one.

That's documented as a known gap with an exit condition:

> *Done when: job state moves to the database or a queue, if multi-worker deployment is
> ever needed.*

Note the conditional. It's not on the roadmap; it's contingent on a requirement that
doesn't exist yet. Writing down "this is a limitation, here's what would trigger fixing
it" is more useful than either fixing it prematurely or pretending it isn't there.

There's also a small eviction policy, because unbounded dictionaries are a memory leak
with extra steps:

```python
class JobStore:
    _MAX_FINISHED = 50    # finished jobs kept for status polling; oldest evicted past this
```

### The catch-all that swallowed 404s

The API also serves the built React app, with a catch-all so client-side routes work:

```python
@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    ...
    return FileResponse(_FRONTEND_DIST / "index.html")
```

A single-page app needs this: the browser requests `/novels/abc/characters`, the server
has no such file, and it must return `index.html` so the JavaScript router can handle it.

But a catch-all catches *everything*, including a typo'd API route:

```
GET /api/novels/abc/charcters
     ↓
no route matches
     ↓
catch-all returns index.html with status 200
     ↓
frontend: `if (!res.ok)` → passes, it's a 200
     ↓
`res.json()` → SyntaxError: Unexpected token '<'
```

The developer sees a JSON parse error mentioning `<!doctype`, twelve layers from the
typo.

```python
# Without this guard an unmatched /api/* GET falls through to
# index.html with status 200, so the frontend's res.ok check passes
# and res.json() dies on "<!doctype" instead of surfacing a 404.
if full_path == "api" or full_path.startswith("api/"):
    raise HTTPException(status_code=404, detail="Not Found")
```

And while they were in there, a path-traversal guard:

```python
target = (_FRONTEND_DIST / full_path).resolve()
# Refuse anything that escapes the dist directory ("../" traversal).
if full_path and target.is_relative_to(_FRONTEND_DIST) and target.is_file():
    return FileResponse(target)
```

`resolve()` normalises `..` segments; `is_relative_to` then checks the result is still
inside the served directory. Without it, `GET /../../etc/passwd` is a file-read
primitive.

---

## The frontend, briefly

React, with the chapter cap in the URL:

```typescript
export function useChapterCap(): [number | null, (next: number | null) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("cap");
  const cap = raw == null || raw === "" ? null : Number(raw);
  const setCap = (next: number | null) => {
    const newParams = new URLSearchParams(params);
    if (next == null) newParams.delete("cap");
    else newParams.set("cap", String(next));
    setParams(newParams);
  };
  return [cap, setCap];
}
```

Twenty lines, and the choice of *where* to store the cap does a lot of work.

It could have been React state, or a context provider, or a store. Putting it in the URL
query string means:

- **It's shareable.** `?cap=12` in a link shows someone else exactly what you're seeing.
- **It survives reload.** No re-selecting after a refresh.
- **Back/forward work.** The browser's history is the cap's history, for free.
- **Every page reads it the same way.** `useChapterCap()`, passed to the API call.

> **Application state that describes "what am I looking at" belongs in the URL.** You get
> persistence, sharing, and history for nothing, and you avoid a whole category of
> synchronisation bugs.

Server data is managed by React Query with a 30-second stale time, which gives caching
and background refetching without a global store. There is no Redux, no global state
container — all the state that matters is either in the URL or on the server.

---

## What the shape buys you

Step back and count what falls out of "one read layer with an enforced contract."

**The wiki and the agent cannot disagree.** Not "shouldn't" — the same function serves
both.

**Spoiler-safety is checkable in one place.** To audit it, read `reads/`. Everything else
is provably SQL-free.

**Adding a surface is cheap.** A GraphQL endpoint, a CLI, a second agent protocol: all of
them call `reads.*` with a cutoff. None of them can introduce a leak, because none of
them can write SQL.

**The escape hatch has a name.** Six exemptions, each with a written justification. The
seventh requires an argument.

And the failure that motivated all of it is worth remembering, because it's cheap to
repeat: two implementations of one invariant, each individually tested, each
individually correct, silently answering different questions for months.

---

## Where we are

The system works. It reads chapters, resolves identities, tracks state through time,
searches, critiques, and serves two audiences without leaking the future.

Every claim in that sentence is currently unverified.

We have tests — 419 of them — and they prove the deterministic parts do what their
authors intended. They prove nothing about whether extraction is *accurate*, whether
retrieval finds the *right* passages, whether the critic catches *real* errors. Those are
statistical properties of a system with an LLM in the middle, and unit tests cannot
express them.

You already know how that turns out, because Post 4 spoiled it: the measurement found a
bug in the place nobody was looking.

---

*Next: [Part 10 — How Do You Know It Works?](10-measuring-it.md) — golden datasets, four
evals, why a metric that moves for the wrong reason is worse than no metric, and the
threshold that certified a broken system as working.*
