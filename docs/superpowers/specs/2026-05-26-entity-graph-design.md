# Unified Entity Graph — Design Spec

**Date:** 2026-05-26
**Branch:** closing-the-gap-with-sota

## Overview

> **Extended:** See `2026-05-26-entity-graph-rich-edges-design.md` for the follow-up that adds shared_dynamics, events co-occurrence, possesses, and located_in edges.

A new "Entity Graph" page and nav item that renders a force-directed graph of all entity types in a novel — characters, locations, objects, factions, and custom entities — connected by their relationships. The existing Relationships page (character-only) is unchanged.

## Scope

- New backend endpoint: `GET /api/novels/{novel_id}/entity-graph?cap=N`
- New frontend route: `/novels/:novelId/entity-graph` (`EntityGraph.tsx`)
- New sidebar nav item: "Entity Graph"
- Respects the chapter cap slider
- Double-click on a node navigates to the entity's detail page

## Backend

### New query: `get_entity_graph(novel_id, cap)`

Located in `backend/api/queries.py`.

**Nodes:** Query all entities for the novel from the `entities` table, left-joining `characters`, `locations`, `objects`, and `factions` to retrieve the native table ID. Apply cap to characters only (filter by `first_appearance_chapter <= cap`). Return `entity_id` as the canonical graph node ID, plus `native_id` (the table-specific row ID used for navigation URLs), `entity_type`, `label` (name), and `description`.

```sql
SELECT e.id AS entity_id,
       e.name,
       e.entity_type,
       COALESCE(c.id, l.id, o.id, f.id, e.id) AS native_id
FROM entities e
LEFT JOIN characters c ON c.entity_id = e.id
LEFT JOIN locations  l ON l.entity_id = e.id
LEFT JOIN objects    o ON o.entity_id = e.id
LEFT JOIN factions   f ON f.entity_id = e.id
WHERE e.novel_id = %s
  AND (e.entity_type != 'character'
       OR c.first_appearance_chapter IS NULL
       OR c.first_appearance_chapter <= %s)
```

**Edges:** Query all relationships where both endpoints belong to the novel, filtered by cap.

```sql
SELECT r.id, r.entity_a_id AS "from", r.entity_b_id AS "to",
       r.rel_type AS label, r.from_chapter AS chapter_number
FROM relationships r
JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s
JOIN entities eb ON eb.id = r.entity_b_id AND eb.novel_id = %s
WHERE r.from_chapter IS NULL OR r.from_chapter <= %s
```

### New schemas (`backend/api/schemas.py`)

```python
class EntityGraphNode(BaseModel):
    id: str
    label: str
    entity_type: str
    native_id: str
    description: str | None = None

class EntityGraph(BaseModel):
    nodes: list[EntityGraphNode]
    edges: list[GraphEdge]   # reuse existing GraphEdge schema
```

### New route (`backend/api/routes/entity_graph.py`)

```
GET /api/novels/{novel_id}/entity-graph?cap=N
```

Returns `EntityGraph`. Registered in `app.py`.

## Frontend

### New API method (`frontend/src/api.ts`)

```typescript
export type EntityGraphNode = {
  id: string;
  label: string;
  entity_type: string;
  native_id: string;
  description: string | null;
};

export type EntityGraphData = {
  nodes: EntityGraphNode[];
  edges: GraphEdge[];
};
```

```typescript
entityGraph: (novelId: string, cap: number | null) =>
  fetchJson<EntityGraphData>(`/api/novels/${novelId}/entity-graph${capParam(cap)}`),
```

### New route (`frontend/src/routes/EntityGraph.tsx`)

- Fetches `api.entityGraph(novelId, cap)` via React Query
- Renders a vis-network graph (same library as `Relationships.tsx`)
- Node colors by entity type:
  - `character` → `#4e9af1` (blue)
  - `location` → `#52c41a` (green)
  - `object` → `#fa8c16` (orange)
  - `faction` → `#9254de` (purple)
  - *(any custom type)* → `#13c2c2` (teal)
- Legend row above the graph: one pill per entity type present in the data, showing color dot + label. Clicking a pill toggles visibility of that entity type's nodes and their incident edges.
- Double-click on a node navigates to the entity's detail page:

| `entity_type` | URL |
|---|---|
| `character` | `/novels/:novelId/characters/:native_id` |
| `location` | `/novels/:novelId/locations/:native_id` |
| `object` | `/novels/:novelId/objects/:native_id` |
| `faction` | `/novels/:novelId/factions/:native_id` |
| *(custom)* | `/novels/:novelId/custom-entities/:id` |

- Node count + edge count displayed below the graph (e.g. "14 nodes · 22 edges")
- Hint text: "Double-click a node to open · Hover for description"
- Empty state: if `edges.length === 0`, show "No relationships yet. Process some chapters to populate the graph." instead of the canvas
- Loading: `<p>Loading…</p>`
- vis-network is destroyed and recreated on data change (same lifecycle as `Relationships.tsx`)

### Sidebar (`frontend/src/components/Sidebar.tsx`)

Add "Entity Graph" to `staticLinks`:
```typescript
["Entity Graph", `/novels/${novelId}/entity-graph`],
```

Placed after the existing "Dynamics" entry and before "Continuity".

### App.tsx

Add route:
```tsx
<Route path="/novels/:novelId/entity-graph" element={<Layout><EntityGraph /></Layout>} />
```

## Error handling

- Fetch errors: thrown by `fetchJson`, surfaced by React Query — no extra handling needed
- Empty graph: detected client-side when `edges.length === 0`; show message instead of canvas

## Testing

New test file: `backend/api/tests/test_entity_graph.py`

Tests to cover:
1. All entity types appear as nodes in the response
2. Edges span across entity types (e.g. character → location)
3. Chapter cap filters character nodes correctly (characters with `first_appearance_chapter > cap` excluded)
4. `native_id` is the correct table-specific ID for characters, locations, objects, factions
5. `native_id` equals `id` for custom entity types
6. Only edges where both endpoints belong to the novel are returned

No new frontend tests — consistent with the existing pattern for graph pages.
