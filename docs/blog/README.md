# Building Continuum — a series

An eleven-part narrative walkthrough of this codebase, written for someone who knows a
little Python and nothing at all about search, databases, or LLM systems. It starts from
"why does a long novel break an AI writer" and ends with a complete, measured system —
including the parts that are still wrong.

Every post follows the same shape: a concrete problem, the naive solution, the specific
example that breaks it, the real fix, and what the fix cost. Where the project got it
wrong first, the wrong version is shown too — those are usually the more instructive half.

The codebase is the source of truth throughout. Code, prompts, SQL and comments are quoted
verbatim.

---

| # | Post | What it covers |
|---|---|---|
| 1 | [The Novel That Won't Fit In Anyone's Head](01-the-problem.md) | Why context windows don't solve this. Why naive RAG leaks the ending. The six kinds of continuity error, and which ones models are worst at. |
| 2 | [From Prose to Rows](02-the-mvp.md) | Relational databases from scratch. Why Postgres. Chunking with overlap. JSON mode. Why six focused LLM calls beat one big one. The `entities` refactor. |
| 3 | [Thirteen Ways of Reading a Chapter](03-extraction-passes.md) | All thirteen extraction passes, the problem each was added to solve, the prompt rules that are really bug fixes, and what one chapter costs in LLM calls. |
| 4 | [Who Is "The Old Man At The Tavern"?](04-identity.md) | Entity resolution. Why fuzzy string matching fails catastrophically. Grammatical anchors. Three evidence tiers. The measurement that found 14 duplicates where everyone was looking at 1. |
| 5 | [Time Travel Without Snapshots](05-time.md) | Event sourcing. Why storing state is a trap. Typed state deltas replacing verb regexes. Bitemporal intervals, replay, idempotent materialization, and a concurrency race. |
| 6 | [Modelling a World](06-modelling-a-world.md) | Relationships that point both ways (or don't). Genre-specific entity types. Five kinds of graph edge. A full accounting of the two id spaces. |
| 7 | [Finding Things](07-search.md) | Inverted indexes, BM25, embeddings, cosine similarity, HNSW. Why hybrid search needs rank fusion. Three bugs that made "hybrid" silently dense-only. |
| 8 | [The Critic](08-the-critic.md) | Typed continuity checks instead of "ask a model if it looks wrong". Five checks. The audit that found three of them structurally incapable of firing. |
| 9 | [Two Audiences, One Read Layer](09-serving-it.md) | The cutoff contract, enforced by a test that walks the codebase. MCP for agents, REST for humans, one implementation. Point-in-time status derivation. |
| 10 | [How Do You Know It Works?](10-measuring-it.md) | Golden datasets, four evals, ground-truth-free diagnostics, and the threshold that certified a broken system as working. |
| 11 | [The Unglamorous Twenty Percent](11-production.md) | Transactions, savepoints, pool sizing, indexes. A bug that returned success for a chapter that didn't exist. Deleting six weeks of working code on purpose. |

---

## Also in `docs/`

- [`architecture.html`](../architecture.html) — the reference architecture guide: mental
  model, pipeline phase by phase, DB design, directory map.
- [`reference.html`](../reference.html) — schema, API endpoints, CLI, MCP tools, env vars,
  eval harness.
- [`state-of-the-system.html`](../state-of-the-system.html) — how the system got here and
  the honest list of what's still open.
- [`research/`](../research/) — the SOTA survey the design drew on.
