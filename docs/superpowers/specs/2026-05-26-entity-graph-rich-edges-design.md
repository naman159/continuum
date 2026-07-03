# Entity Graph — Rich Edges Design Spec

**Date:** 2026-05-26
**Branch:** closing-the-gap-with-sota
**Extends:** `2026-05-26-entity-graph-design.md` (basic graph already shipped)

## Overview

Extend the existing Entity Graph page to show a unified story graph. Currently only `relationships` table edges appear. This spec adds edges derived from `shared_dynamics`, `events` co-occurrence, `possesses_edges`, and `located_in_edges`, giving every entity pair that shares any story moment a visible connection.

No schema changes required. All data already exists in the database.

## Edge Sources

| Source | Description | Join needed? | Current rows (ph, ch1) |
|---|---|---|---|
| `relationships` | Explicit named relationships (already in graph) | No | 10 |
| `shared_dynamics` | Story moments between two entity pairs with description | No (uses entity IDs) | 5 |
| `events` | Co-occurrence: character↔character and character→location per event | Yes (`characters.entity_id`, `locations.entity_id`) | 5 events → many pairs |
| `possesses_edges` | Character→object ownership | Yes (`characters.entity_id`, `objects.entity_id`) | 0 (wired up for future) |
| `located_in_edges` | Entity→location edges | Yes (`locations.entity_id`) | 0 (wired up for future) |

## Edge Collapsing

Multiple records between the same entity pair within a source are collapsed into one edge:
- Label: `"N events"` / `"N dynamics"` (plain description if N=1)
- Tooltip: all relevant descriptions joined, one per line

At most **2 edges** between any entity pair in the final graph:
1. A **relationship edge** (if one exists in `relationships`)
2. A **story edge** (if any co-occurrence/dynamic/possession/location data exists) — all non-relationship sources for a pair are merged into one story edge with a combined tooltip

## New `edge_kind` Field

The API adds `edge_kind: string` to each edge:

| `edge_kind` | Source |
|---|---|
| `"relationship"` | `relationships` table |
| `"dynamic"` | `shared_dynamics` table |
| `"event"` | `events` co-occurrence |
| `"possession"` | `possesses_edges` table |
| `"location"` | `located_in_edges` table |

When multiple non-relationship sources contribute to a story edge, `edge_kind` is set to the primary source in precedence order: `dynamic` > `event` > `possession` > `location`.

## Backend Changes

### `queries.py` — `get_entity_graph`

**Real DB path additions** (three new SQL subqueries, results merged with relationship edges):

**1. shared_dynamics edges**
```sql
SELECT gen_random_uuid()::text AS id,
       sd.entity_a_id::text AS "from",
       sd.entity_b_id::text AS "to",
       'dynamic' AS edge_kind,
       COUNT(*)::int AS n,
       string_agg(sd.description, E'\n') AS tooltip
FROM shared_dynamics sd
JOIN chapters ch ON ch.id = sd.chapter_id
WHERE ch.novel_id = %s
  AND (ch.number IS NULL OR ch.number <= %s)
  AND sd.entity_a_id = ANY(%s::uuid[])
  AND sd.entity_b_id = ANY(%s::uuid[])
GROUP BY sd.entity_a_id, sd.entity_b_id
```

**2. events co-occurrence edges** (character↔character)
```sql
SELECT gen_random_uuid()::text AS id,
       ca.entity_id::text AS "from",
       cb.entity_id::text AS "to",
       'event' AS edge_kind,
       COUNT(*)::int AS n,
       string_agg(e.description, E'\n') AS tooltip
FROM events e
JOIN chapters ch ON ch.id = e.chapter_id
JOIN characters ca ON ca.id = ANY(e.involved_characters)
JOIN characters cb ON cb.id = ANY(e.involved_characters) AND cb.id > ca.id
WHERE ch.novel_id = %s
  AND (ch.number IS NULL OR ch.number <= %s)
  AND ca.entity_id = ANY(%s::uuid[])
  AND cb.entity_id = ANY(%s::uuid[])
GROUP BY ca.entity_id, cb.entity_id
```

**3. events co-occurrence edges** (character→location)
```sql
SELECT gen_random_uuid()::text AS id,
       c.entity_id::text AS "from",
       l.entity_id::text AS "to",
       'event' AS edge_kind,
       COUNT(*)::int AS n,
       string_agg(e.description, E'\n') AS tooltip
FROM events e
JOIN chapters ch ON ch.id = e.chapter_id
JOIN characters c ON c.id = ANY(e.involved_characters)
JOIN locations l ON l.id = ANY(e.involved_locations)
WHERE ch.novel_id = %s
  AND (ch.number IS NULL OR ch.number <= %s)
  AND c.entity_id = ANY(%s::uuid[])
  AND l.entity_id = ANY(%s::uuid[])
GROUP BY c.entity_id, l.entity_id
```

**4. possesses_edges** (character→object)
```sql
SELECT gen_random_uuid()::text AS id,
       c.entity_id::text AS "from",
       o.entity_id::text AS "to",
       'possession' AS edge_kind,
       1 AS n,
       NULL AS tooltip
FROM possesses_edges pe
JOIN characters c ON c.id = pe.character_id
JOIN objects o ON o.id = pe.object_id
WHERE c.entity_id = ANY(%s::uuid[])
  AND o.entity_id = ANY(%s::uuid[])
  AND (pe.since_chapter IS NULL OR pe.since_chapter <= %s)
  AND (pe.until_chapter IS NULL OR pe.until_chapter > %s)
```

**5. located_in_edges** (entity→location)
```sql
SELECT gen_random_uuid()::text AS id,
       lie.entity_id::text AS "from",
       l.entity_id::text AS "to",
       'location' AS edge_kind,
       1 AS n,
       NULL AS tooltip
FROM located_in_edges lie
JOIN locations l ON l.id = lie.location_id
WHERE lie.entity_id = ANY(%s::uuid[])
  AND l.entity_id = ANY(%s::uuid[])
  AND (lie.since_chapter IS NULL OR lie.since_chapter <= %s)
  AND (lie.until_chapter IS NULL OR lie.until_chapter > %s)
```

**Merging into final edge list:**

After fetching all five sources, Python merges non-relationship edges:

```python
# Group non-relationship edges by (from, to) canonical pair
# canonical pair: (min(from, to), max(from, to)) to treat undirected edges as same pair
# Precedence for edge_kind: dynamic > event > possession > location
# Label: "N dynamics" / "N events" etc. (plain description if N=1)
# Tooltip: all descriptions joined with newline
```

Relationship edges are passed through unchanged (solid, labelled with rel_type, no tooltip).

**FakeDB (in-memory) path:** extended to derive the same edge types from the in-memory test data structures (`db.shared_dynamics`, `db.events`, `db.possesses_edges`, `db.located_in_edges` if present, graceful fallback to empty list if not present).

### `schemas.py`

`GraphEdge` gets one new optional field:
```python
class GraphEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: UUID
    from_: UUID = Field(alias="from")
    to: UUID
    label: str | None
    chapter_number: int | None
    edge_kind: str | None = None      # new
    tooltip: str | None = None        # new
```

## Frontend Changes

### `api.ts`

`GraphEdge` type updated:
```typescript
export type GraphEdge = {
  id: string;
  from: string;
  to: string;
  label: string | null;
  chapter_number: number | null;
  edge_kind: string | null;   // new
  tooltip: string | null;     // new
};
```

### `EntityGraph.tsx`

Edge rendering in the vis-network DataSet:
```typescript
edges = new DataSet(
  visibleEdges.map((e) => ({
    id: e.id,
    from: e.from,
    to: e.to,
    label: e.edge_kind === "relationship" ? (e.label ?? undefined) : undefined,
    title: e.tooltip ?? undefined,   // hover tooltip
    arrows: e.edge_kind === "relationship" ? "to" : undefined,
    dashes: e.edge_kind !== "relationship",
    color: e.edge_kind === "relationship"
      ? { color: "#4e9af1", opacity: 1 }
      : { color: "#666", opacity: 0.7 },
    width: e.edge_kind === "relationship" ? 2 : 1,
  }))
);
```

No changes to legend pills, node rendering, double-click navigation, or chapter cap behaviour.

## Error Handling

- `events.involved_characters` / `involved_locations` are arrays; if empty, the JOIN produces no rows — handled naturally by SQL.
- `possesses_edges` / `located_in_edges` currently empty — zero rows returned, no edges added.
- FakeDB path falls back gracefully if `db.shared_dynamics` or `db.events` are missing from a test fixture.

## Testing

New tests in `backend/api/tests/test_entity_graph.py`:

1. `test_entity_graph_shared_dynamics_edge` — shared_dynamics between two entities appears as a dashed edge with `edge_kind="dynamic"`
2. `test_entity_graph_event_cooccurrence_edge` — two characters in the same event get a co-occurrence edge with `edge_kind="event"`
3. `test_entity_graph_character_location_event_edge` — character + location in same event get an edge
4. `test_entity_graph_collapsed_multiple_events` — 3 events between same pair → one edge with label "3 events"
5. `test_entity_graph_relationship_and_dynamic_coexist` — explicit relationship + dynamic between same pair → 2 separate edges

No new frontend tests (consistent with existing pattern).
