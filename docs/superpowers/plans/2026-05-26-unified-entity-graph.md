# Unified Entity Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Entity Graph" nav item and page that renders all entity types (characters, locations, objects, factions, custom) as a single vis-network graph, connected by their relationships.

**Architecture:** New backend endpoint `/api/novels/{id}/entity-graph?cap=N` queries the `entities` table with LEFT JOINs to get native table IDs, returns all entity types as typed nodes plus all cross-type relationship edges. New `EntityGraph.tsx` frontend page uses vis-network with per-type node colours and a legend-chip row that toggles entity type visibility.

**Tech Stack:** FastAPI (backend), Pydantic v2 (schemas), pytest + FastAPI TestClient (tests), React + TypeScript (frontend), vis-network (graph), @tanstack/react-query (data fetching).

---

### Task 1: Backend schemas

**Files:**
- Modify: `backend/api/schemas.py`

- [ ] **Step 1: Add `EntityGraphNode` and `EntityGraph` to `schemas.py`**

  Open `backend/api/schemas.py`. After the `RelationshipGraph` class (around line 195), add:

  ```python
  class EntityGraphNode(BaseModel):
      id: UUID
      label: str
      entity_type: str
      native_id: UUID
      description: str | None = None


  class EntityGraph(BaseModel):
      nodes: list[EntityGraphNode]
      edges: list[GraphEdge]
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add backend/api/schemas.py
  git commit -m "feat(backend): add EntityGraphNode and EntityGraph schemas"
  ```

---

### Task 2: Write failing backend tests

**Files:**
- Create: `backend/api/tests/test_entity_graph.py`

- [ ] **Step 1: Create the test file**

  ```python
  from __future__ import annotations

  from uuid import uuid4

  from api.tests.conftest import make_chapter, make_novel


  def test_entity_graph_includes_all_entity_types(fake_db_factory, client):
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)

      char_entity_id = uuid4()
      char_id = uuid4()
      loc_entity_id = uuid4()
      loc_id = uuid4()
      obj_entity_id = uuid4()
      obj_id = uuid4()
      fac_entity_id = uuid4()
      fac_id = uuid4()
      custom_entity_id = uuid4()

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[
              {"id": char_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Aria"},
              {"id": loc_entity_id,  "novel_id": novel["id"], "entity_type": "location",  "name": "The Castle"},
              {"id": obj_entity_id,  "novel_id": novel["id"], "entity_type": "object",    "name": "Magic Sword"},
              {"id": fac_entity_id,  "novel_id": novel["id"], "entity_type": "faction",   "name": "The Guild"},
              {"id": custom_entity_id, "novel_id": novel["id"], "entity_type": "spell",   "name": "Fireball"},
          ],
          characters=[
              {"id": char_id, "entity_id": char_entity_id, "novel_id": novel["id"],
               "name": "Aria", "aliases": [], "description": None, "first_appearance_chapter": 1},
          ],
          locations=[
              {"id": loc_id, "entity_id": loc_entity_id, "novel_id": novel["id"], "name": "The Castle"},
          ],
          objects=[
              {"id": obj_id, "entity_id": obj_entity_id, "novel_id": novel["id"], "name": "Magic Sword"},
          ],
          factions=[
              {"id": fac_id, "entity_id": fac_entity_id, "novel_id": novel["id"], "name": "The Guild"},
          ],
          relationships=[
              {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": loc_entity_id,
               "rel_type": "lives in", "from_chapter": 1, "to_chapter": None, "notes": None},
              {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": obj_entity_id,
               "rel_type": "wields", "from_chapter": 1, "to_chapter": None, "notes": None},
              {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": fac_entity_id,
               "rel_type": "member of", "from_chapter": 1, "to_chapter": None, "notes": None},
              {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": custom_entity_id,
               "rel_type": "knows", "from_chapter": 1, "to_chapter": None, "notes": None},
          ],
      )
      response = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert response.status_code == 200
      body = response.json()
      labels = {n["label"] for n in body["nodes"]}
      assert labels == {"Aria", "The Castle", "Magic Sword", "The Guild", "Fireball"}
      types = {n["entity_type"] for n in body["nodes"]}
      assert types == {"character", "location", "object", "faction", "spell"}
      assert len(body["edges"]) == 4


  def test_entity_graph_native_ids(fake_db_factory, client):
      """native_id is the table-specific row ID, not the entity ID."""
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)

      char_entity_id = uuid4()
      char_id = uuid4()
      loc_entity_id = uuid4()
      loc_id = uuid4()
      custom_entity_id = uuid4()

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[
              {"id": char_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Aria"},
              {"id": loc_entity_id,  "novel_id": novel["id"], "entity_type": "location",  "name": "The Castle"},
              {"id": custom_entity_id, "novel_id": novel["id"], "entity_type": "spell",   "name": "Fireball"},
          ],
          characters=[
              {"id": char_id, "entity_id": char_entity_id, "novel_id": novel["id"],
               "name": "Aria", "aliases": [], "description": None, "first_appearance_chapter": 1},
          ],
          locations=[
              {"id": loc_id, "entity_id": loc_entity_id, "novel_id": novel["id"], "name": "The Castle"},
          ],
          relationships=[
              {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": loc_entity_id,
               "rel_type": "at", "from_chapter": 1, "to_chapter": None, "notes": None},
              {"id": uuid4(), "entity_a_id": char_entity_id, "entity_b_id": custom_entity_id,
               "rel_type": "knows", "from_chapter": 1, "to_chapter": None, "notes": None},
          ],
      )
      response = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert response.status_code == 200
      nodes = {n["entity_type"]: n for n in response.json()["nodes"]}

      assert nodes["character"]["native_id"] == str(char_id)
      assert nodes["location"]["native_id"] == str(loc_id)
      assert nodes["spell"]["native_id"] == nodes["spell"]["id"]


  def test_entity_graph_cap_filters_characters(fake_db_factory, client):
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)
      chap3 = make_chapter(novel["id"], 3)

      early_entity_id = uuid4()
      early_char_id = uuid4()
      late_entity_id = uuid4()
      late_char_id = uuid4()

      fake_db_factory(
          novels=[novel],
          chapters=[chap1, chap3],
          entities=[
              {"id": early_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Early"},
              {"id": late_entity_id,  "novel_id": novel["id"], "entity_type": "character", "name": "Late"},
          ],
          characters=[
              {"id": early_char_id, "entity_id": early_entity_id, "novel_id": novel["id"],
               "name": "Early", "aliases": [], "description": None, "first_appearance_chapter": 1},
              {"id": late_char_id,  "entity_id": late_entity_id,  "novel_id": novel["id"],
               "name": "Late",  "aliases": [], "description": None, "first_appearance_chapter": 3},
          ],
          relationships=[
              {"id": uuid4(), "entity_a_id": early_entity_id, "entity_b_id": late_entity_id,
               "rel_type": "knows", "from_chapter": 3, "to_chapter": None, "notes": None},
          ],
      )
      response = client.get(f"/api/novels/{novel['id']}/entity-graph?cap=1")
      assert response.status_code == 200
      body = response.json()
      labels = {n["label"] for n in body["nodes"]}
      assert "Late" not in labels
      assert "Early" in labels


  def test_entity_graph_edges_cross_types(fake_db_factory, client):
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)

      char_entity_id = uuid4()
      char_id = uuid4()
      loc_entity_id = uuid4()
      loc_id = uuid4()

      rel_id = uuid4()
      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[
              {"id": char_entity_id, "novel_id": novel["id"], "entity_type": "character", "name": "Aria"},
              {"id": loc_entity_id,  "novel_id": novel["id"], "entity_type": "location",  "name": "Cave"},
          ],
          characters=[
              {"id": char_id, "entity_id": char_entity_id, "novel_id": novel["id"],
               "name": "Aria", "aliases": [], "description": None, "first_appearance_chapter": 1},
          ],
          locations=[
              {"id": loc_id, "entity_id": loc_entity_id, "novel_id": novel["id"], "name": "Cave"},
          ],
          relationships=[
              {"id": rel_id, "entity_a_id": char_entity_id, "entity_b_id": loc_entity_id,
               "rel_type": "hides in", "from_chapter": 1, "to_chapter": None, "notes": None},
          ],
      )
      response = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert response.status_code == 200
      body = response.json()
      assert len(body["edges"]) == 1
      edge = body["edges"][0]
      assert edge["label"] == "hides in"
      node_ids = {n["id"] for n in body["nodes"]}
      assert edge["from"] in node_ids
      assert edge["to"] in node_ids
  ```

- [ ] **Step 2: Run tests to verify they all fail**

  ```bash
  cd /path/to/repo
  python -m pytest backend/api/tests/test_entity_graph.py -v
  ```

  Expected: 4 errors — `ImportError` or `404` because the endpoint doesn't exist yet.

- [ ] **Step 3: Commit the failing tests**

  ```bash
  git add backend/api/tests/test_entity_graph.py
  git commit -m "test(backend): add failing tests for entity graph endpoint"
  ```

---

### Task 3: Backend query

**Files:**
- Modify: `backend/api/queries.py`

- [ ] **Step 1: Add `get_entity_graph` to `queries.py`**

  Append the following function at the end of `backend/api/queries.py`:

  ```python
  def get_entity_graph(novel_id: UUID, cap: int | None) -> dict[str, Any]:
      db = _get_db()
      effective_cap = _resolve_cap(db, novel_id, cap)

      if hasattr(db, "entities"):
          # In-memory (FakeDB) path
          char_by_entity_id = {c["entity_id"]: c for c in db.characters}
          loc_by_entity_id  = {l["entity_id"]: l for l in db.locations}
          obj_by_entity_id  = {o["entity_id"]: o for o in db.objects}
          fac_by_entity_id  = {f["entity_id"]: f for f in db.factions}

          def _native_id(entity_id: Any, entity_type: str) -> str:
              if entity_type == "character" and entity_id in char_by_entity_id:
                  return str(char_by_entity_id[entity_id]["id"])
              if entity_type == "location" and entity_id in loc_by_entity_id:
                  return str(loc_by_entity_id[entity_id]["id"])
              if entity_type == "object" and entity_id in obj_by_entity_id:
                  return str(obj_by_entity_id[entity_id]["id"])
              if entity_type == "faction" and entity_id in fac_by_entity_id:
                  return str(fac_by_entity_id[entity_id]["id"])
              return str(entity_id)

          nodes = []
          entity_id_set: set[Any] = set()
          for e in db.entities:
              if e.get("novel_id") != novel_id:
                  continue
              if e.get("entity_type") == "character":
                  char = char_by_entity_id.get(e["id"])
                  if (
                      char
                      and char.get("first_appearance_chapter") is not None
                      and char["first_appearance_chapter"] > effective_cap
                  ):
                      continue
              nodes.append({
                  "id": str(e["id"]),
                  "label": e["name"],
                  "entity_type": e["entity_type"],
                  "native_id": _native_id(e["id"], e["entity_type"]),
                  "description": e.get("description"),
              })
              entity_id_set.add(e["id"])

          edges = [
              {
                  "id": str(r["id"]),
                  "from": str(r["entity_a_id"]),
                  "to": str(r["entity_b_id"]),
                  "label": r.get("rel_type"),
                  "chapter_number": r.get("from_chapter"),
              }
              for r in db.relationships
              if r["entity_a_id"] in entity_id_set
              and r["entity_b_id"] in entity_id_set
              and (r.get("from_chapter") is None or r["from_chapter"] <= effective_cap)
          ]
          return {"nodes": nodes, "edges": edges}

      # Real DB path
      node_rows = db.fetchall(
          """
          SELECT e.id::text AS id,
                 e.name,
                 e.entity_type,
                 COALESCE(c.id, l.id, o.id, f.id, e.id)::text AS native_id,
                 NULL::text AS description
          FROM entities e
          LEFT JOIN characters c ON c.entity_id = e.id
          LEFT JOIN locations  l ON l.entity_id = e.id
          LEFT JOIN objects    o ON o.entity_id = e.id
          LEFT JOIN factions   f ON f.entity_id = e.id
          WHERE e.novel_id = %s
            AND (
              e.entity_type != 'character'
              OR c.first_appearance_chapter IS NULL
              OR c.first_appearance_chapter <= %s
            )
          """,
          (str(novel_id), effective_cap),
          dict_rows=True,
      )
      nodes_list = [dict(r) for r in node_rows]
      node_entity_ids = {n["id"] for n in nodes_list}

      edge_rows = db.fetchall(
          """
          SELECT r.id::text AS id,
                 r.entity_a_id::text AS "from",
                 r.entity_b_id::text AS "to",
                 r.rel_type AS label,
                 r.from_chapter AS chapter_number
          FROM relationships r
          JOIN entities ea ON ea.id = r.entity_a_id AND ea.novel_id = %s
          JOIN entities eb ON eb.id = r.entity_b_id AND eb.novel_id = %s
          WHERE r.from_chapter IS NULL OR r.from_chapter <= %s
          """,
          (str(novel_id), str(novel_id), effective_cap),
          dict_rows=True,
      )
      edges_list = [dict(r) for r in edge_rows]

      return {"nodes": nodes_list, "edges": edges_list}
  ```

- [ ] **Step 2: Run tests — should still fail (no route yet)**

  ```bash
  python -m pytest backend/api/tests/test_entity_graph.py -v
  ```

  Expected: 4 failures with `404 Not Found` (query exists but endpoint not yet registered).

- [ ] **Step 3: Commit**

  ```bash
  git add backend/api/queries.py
  git commit -m "feat(backend): add get_entity_graph query"
  ```

---

### Task 4: Backend route and app registration

**Files:**
- Create: `backend/api/routes/entity_graph.py`
- Modify: `backend/api/app.py`

- [ ] **Step 1: Create the route file**

  Create `backend/api/routes/entity_graph.py`:

  ```python
  from __future__ import annotations

  from uuid import UUID

  from fastapi import APIRouter, Query

  from api import queries
  from api.schemas import EntityGraph

  router = APIRouter(prefix="/api/novels/{novel_id}/entity-graph", tags=["entity_graph"])


  @router.get("", response_model=EntityGraph)
  def get_entity_graph(novel_id: UUID, cap: int | None = Query(default=None)) -> EntityGraph:
      data = queries.get_entity_graph(novel_id, cap)
      return EntityGraph.model_validate(data)
  ```

- [ ] **Step 2: Register the router in `app.py`**

  In `backend/api/app.py`, add the import and `include_router` call:

  ```python
  # In the import block, add:
  from api.routes import (
      ...
      entity_graph,        # add this line
      entity_types,
      ...
  )

  # After the last app.include_router call, add:
  app.include_router(entity_graph.router)
  ```

- [ ] **Step 3: Run all entity graph tests — all should pass**

  ```bash
  python -m pytest backend/api/tests/test_entity_graph.py -v
  ```

  Expected:
  ```
  test_entity_graph_includes_all_entity_types   PASSED
  test_entity_graph_native_ids                  PASSED
  test_entity_graph_cap_filters_characters      PASSED
  test_entity_graph_edges_cross_types           PASSED
  ```

- [ ] **Step 4: Run the full test suite to check for regressions**

  ```bash
  python -m pytest backend/api/tests/ -v
  ```

  Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

  ```bash
  git add backend/api/routes/entity_graph.py backend/api/app.py
  git commit -m "feat(backend): add entity-graph route and register router"
  ```

---

### Task 5: Frontend API types

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add `EntityGraphNode` and `EntityGraphData` types**

  In `frontend/src/api.ts`, after the `RelationshipGraph` type definition (around line 191), add:

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

- [ ] **Step 2: Add the `entityGraph` API method**

  In the `api` object (bottom of `api.ts`), add after the `relationships` entry:

  ```typescript
  entityGraph: (novelId: string, cap: number | null) =>
    fetchJson<EntityGraphData>(`/api/novels/${novelId}/entity-graph${capParam(cap)}`),
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add frontend/src/api.ts
  git commit -m "feat(frontend): add EntityGraphNode, EntityGraphData types and entityGraph API method"
  ```

---

### Task 6: EntityGraph component

**Files:**
- Create: `frontend/src/routes/EntityGraph.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add CSS for legend pills to `frontend/src/styles.css`**

  Append to `frontend/src/styles.css`:

  ```css
  .entity-graph-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
    align-items: center;
  }

  .entity-type-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border: 1px solid #444;
    border-radius: 20px;
    background: transparent;
    color: #ccc;
    font-size: 12px;
    cursor: pointer;
    transition: opacity 0.15s;
  }

  .entity-type-pill:hover {
    border-color: #888;
    color: #fff;
  }

  .entity-type-pill.dimmed {
    opacity: 0.35;
  }

  .pill-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  ```

- [ ] **Step 2: Create `frontend/src/routes/EntityGraph.tsx`**

  ```tsx
  import { useQuery } from "@tanstack/react-query";
  import { DataSet } from "vis-data";
  import { Network } from "vis-network/standalone";
  import { useEffect, useRef, useState } from "react";
  import { useNavigate, useParams } from "react-router-dom";
  import { api } from "../api";
  import { useChapterCap } from "../hooks/useChapterCap";

  const TYPE_COLORS: Record<string, string> = {
    character: "#4e9af1",
    location: "#52c41a",
    object: "#fa8c16",
    faction: "#9254de",
  };
  const CUSTOM_COLOR = "#13c2c2";

  function nodeColor(entityType: string): string {
    return TYPE_COLORS[entityType] ?? CUSTOM_COLOR;
  }

  function navUrl(novelId: string, entityType: string, id: string, nativeId: string): string {
    switch (entityType) {
      case "character": return `/novels/${novelId}/characters/${nativeId}`;
      case "location":  return `/novels/${novelId}/locations/${nativeId}`;
      case "object":    return `/novels/${novelId}/objects/${nativeId}`;
      case "faction":   return `/novels/${novelId}/factions/${nativeId}`;
      default:          return `/novels/${novelId}/custom-entities/${id}`;
    }
  }

  const TYPE_LABELS: Record<string, string> = {
    character: "Characters",
    location: "Locations",
    object: "Objects",
    faction: "Factions",
  };

  function typeLabel(t: string): string {
    return TYPE_LABELS[t] ?? (t.charAt(0).toUpperCase() + t.slice(1) + "s");
  }

  export default function EntityGraph() {
    const { novelId } = useParams();
    const [cap] = useChapterCap();
    const navigate = useNavigate();
    const containerRef = useRef<HTMLDivElement>(null);
    const networkRef = useRef<Network | null>(null);
    const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

    const { data, isLoading } = useQuery({
      queryKey: ["entity-graph", novelId, cap],
      queryFn: () => api.entityGraph(novelId!, cap),
      enabled: Boolean(novelId),
    });

    useEffect(() => {
      if (!data || !containerRef.current) return;

      const visibleNodes = data.nodes.filter((n) => !hiddenTypes.has(n.entity_type));
      const visibleIds = new Set(visibleNodes.map((n) => n.id));
      const visibleEdges = data.edges.filter(
        (e) => visibleIds.has(e.from) && visibleIds.has(e.to)
      );

      const nodes = new DataSet(
        visibleNodes.map((n) => ({
          id: n.id,
          label: n.label,
          title: n.description ?? undefined,
          color: nodeColor(n.entity_type),
        }))
      );
      const edges = new DataSet(
        visibleEdges.map((e) => ({
          id: e.id,
          from: e.from,
          to: e.to,
          label: e.label ?? undefined,
          arrows: "to",
        }))
      );

      const network = new Network(
        containerRef.current,
        { nodes, edges },
        {
          physics: { stabilization: { iterations: 200 } },
          nodes: { shape: "dot", size: 16, font: { size: 14 } },
          edges: {
            font: { size: 11, align: "middle" },
            smooth: { enabled: true, type: "continuous", roundness: 0.5 },
          },
        }
      );

      network.on("doubleClick", (params: any) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0] as string;
          const node = data.nodes.find((n) => n.id === nodeId);
          if (node) {
            navigate(
              navUrl(novelId!, node.entity_type, node.id, node.native_id) +
                window.location.search
            );
          }
        }
      });

      networkRef.current = network;
      return () => {
        network.destroy();
        networkRef.current = null;
      };
    }, [data, hiddenTypes, navigate, novelId]);

    function toggleType(entityType: string) {
      setHiddenTypes((prev) => {
        const next = new Set(prev);
        if (next.has(entityType)) next.delete(entityType);
        else next.add(entityType);
        return next;
      });
    }

    if (isLoading) return <p>Loading…</p>;
    if (!data) return null;

    const presentTypes = [...new Set(data.nodes.map((n) => n.entity_type))].sort();

    return (
      <div>
        <h1>Entity Graph</h1>
        {presentTypes.length > 0 && (
          <div className="entity-graph-legend">
            {presentTypes.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => toggleType(t)}
                className={`entity-type-pill${hiddenTypes.has(t) ? " dimmed" : ""}`}
                aria-pressed={!hiddenTypes.has(t)}
              >
                <span className="pill-dot" style={{ background: nodeColor(t) }} />
                {typeLabel(t)}
              </button>
            ))}
          </div>
        )}
        {data.edges.length > 0 ? (
          <>
            <p className="muted">Double-click a node to open · Hover for description</p>
            <div ref={containerRef} style={{ height: 600, border: "1px solid #ddd" }} />
            <p className="muted">{data.nodes.length} nodes · {data.edges.length} edges</p>
          </>
        ) : (
          <p>No relationships yet. Process some chapters to populate the graph.</p>
        )}
      </div>
    );
  }
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add frontend/src/routes/EntityGraph.tsx frontend/src/styles.css
  git commit -m "feat(frontend): add EntityGraph page with vis-network and legend pills"
  ```

---

### Task 7: Wire up route and sidebar nav

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Add route to `App.tsx`**

  In `frontend/src/App.tsx`, add the import at the top with the other route imports:

  ```tsx
  import EntityGraph from "./routes/EntityGraph";
  ```

  Then add the route inside `<Routes>`, after the `dynamics` route:

  ```tsx
  <Route path="/novels/:novelId/entity-graph" element={<Layout><EntityGraph /></Layout>} />
  ```

- [ ] **Step 2: Add nav item to `Sidebar.tsx`**

  In `frontend/src/components/Sidebar.tsx`, add `"Entity Graph"` to `staticLinks` after the `"Dynamics"` entry:

  ```tsx
  const staticLinks: [string, string][] = novelId
    ? [
        ["Characters", `/novels/${novelId}/characters`],
        ["Chapters", `/novels/${novelId}/chapters`],
        ["Scenes", `/novels/${novelId}/scenes`],
        ["Timeline", `/novels/${novelId}/timeline`],
        ["Threads", `/novels/${novelId}/threads`],
        ["Commitments", `/novels/${novelId}/commitments`],
        ["State & Knowledge", `/novels/${novelId}/knowledge`],
        ["Canon Facts", `/novels/${novelId}/canon`],
        ["Locations", `/novels/${novelId}/locations`],
        ["Objects", `/novels/${novelId}/objects`],
        ["Factions", `/novels/${novelId}/factions`],
        ["Dynamics", `/novels/${novelId}/dynamics`],
        ["Entity Graph", `/novels/${novelId}/entity-graph`],   // ← add this line
        ["Continuity", `/novels/${novelId}/continuity`],
        ["Process Chapter", `/novels/${novelId}/process`],
      ]
    : [];
  ```

- [ ] **Step 3: Verify TypeScript compiles**

  ```bash
  cd frontend && npx tsc --noEmit
  ```

  Expected: no errors.

- [ ] **Step 4: Commit**

  ```bash
  git add frontend/src/App.tsx frontend/src/components/Sidebar.tsx
  git commit -m "feat(frontend): add Entity Graph route and sidebar nav item"
  ```

---

### Task 8: Update docs

**Files:**
- Modify: `docs/reference.html`

- [ ] **Step 1: Add sidebar nav entry in `docs/reference.html`**

  In `docs/reference.html` around line 187, add a link after `#api-relationships`:

  ```html
  <a href="#api-relationships">Relationships</a>
  <a href="#api-entity-graph">Entity Graph</a>    <!-- add this line -->
  <a href="#api-sota">SOTA Endpoints</a>
  ```

- [ ] **Step 2: Add the endpoint section after `#api-relationships`**

  After the closing `</section>` of the `api-relationships` section (around line 1059), add:

  ```html
  <section id="api-entity-graph">
    <h2>API: Entity Graph</h2>

    <table>
      <thead><tr><th>Method</th><th>Path</th><th>Query Params</th></tr></thead>
      <tbody>
        <tr>
          <td><span class="badge green">GET</span></td>
          <td><code>/api/novels/{id}/entity-graph</code></td>
          <td><code>?cap=N</code></td>
        </tr>
      </tbody>
    </table>

    <p>Returns <code>EntityGraph</code>: <code>{"nodes": EntityGraphNode[], "edges": GraphEdge[]}</code>.</p>
    <p><code>EntityGraphNode</code> extends <code>GraphNode</code> with <code>entity_type</code> (e.g. <code>"character"</code>, <code>"location"</code>, <code>"object"</code>, <code>"faction"</code>, or a custom type name) and <code>native_id</code> (the row ID in the entity's own table, used for frontend navigation URLs).</p>
    <p>Unlike the <code>/relationships</code> endpoint, all entity types are included as nodes. The frontend renders this with <strong>vis-network</strong>, colouring nodes by entity type, with clickable legend pills to toggle type visibility.</p>
  </section>
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add docs/reference.html
  git commit -m "docs: add entity-graph endpoint to reference.html"
  ```
