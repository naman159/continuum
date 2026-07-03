# Entity Graph Rich Edges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `GET /api/novels/{id}/entity-graph` to return edges from `shared_dynamics`, `events` co-occurrence, `possesses_edges`, and `located_in_edges`, in addition to existing `relationships` edges. Frontend renders story edges as dashed gray lines with hover tooltips.

**Architecture:** `get_entity_graph` gains 4 new SQL subqueries (shared_dynamics, event char↔char, event char→location, possesses/located_in). A helper `_merge_story_edges` collapses multiple records between the same entity pair into one edge. `GraphEdge` schema gains `edge_kind` and `tooltip` optional fields. Frontend renders `relationship` edges as solid labelled arrows and all others as dashed gray lines with tooltips.

**Tech Stack:** FastAPI, Pydantic v2, PostgreSQL, pytest + FastAPI TestClient, React + TypeScript, vis-network, @tanstack/react-query.

---

### Task 1: Extend GraphEdge schema

**Files:**
- Modify: `backend/api/schemas.py`

- [ ] **Step 1: Add `edge_kind` and `tooltip` to `GraphEdge`**

  Open `backend/api/schemas.py`. Find the `GraphEdge` class. Replace it with:

  ```python
  class GraphEdge(BaseModel):
      model_config = ConfigDict(populate_by_name=True)

      id: UUID
      from_: UUID = Field(alias="from")
      to: UUID
      label: str | None
      chapter_number: int | None
      edge_kind: str | None = None
      tooltip: str | None = None
  ```

- [ ] **Step 2: Verify existing tests still pass**

  ```bash
  cd backend
  python -m pytest api/tests/ -v -x
  ```

  Expected: all existing tests pass (new fields are optional with `None` defaults).

- [ ] **Step 3: Commit**

  ```bash
  git add backend/api/schemas.py
  git commit -m "feat(backend): add edge_kind and tooltip to GraphEdge schema"
  ```

---

### Task 2: Write failing tests

**Files:**
- Modify: `backend/api/tests/test_entity_graph.py`

- [ ] **Step 1: Append 5 new tests to `test_entity_graph.py`**

  ```python
  # ── helpers ───────────────────────────────────────────────────────────────


  def _make_char_entity(novel_id, name, chapter=1):
      entity_id = uuid4()
      char_id = uuid4()
      entity = {"id": entity_id, "novel_id": novel_id, "entity_type": "character", "name": name}
      char = {
          "id": char_id,
          "entity_id": entity_id,
          "novel_id": novel_id,
          "name": name,
          "aliases": [],
          "description": None,
          "first_appearance_chapter": chapter,
      }
      return entity, char


  def _make_loc_entity(novel_id, name):
      entity_id = uuid4()
      loc_id = uuid4()
      entity = {"id": entity_id, "novel_id": novel_id, "entity_type": "location", "name": name}
      loc = {"id": loc_id, "entity_id": entity_id, "novel_id": novel_id, "name": name}
      return entity, loc


  # ── new tests ─────────────────────────────────────────────────────────────


  def test_entity_graph_shared_dynamics_edge(fake_db_factory, client):
      """shared_dynamics between two entities appears as a 'dynamic' edge."""
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)
      e1, c1 = _make_char_entity(novel["id"], "Alice")
      e2, c2 = _make_char_entity(novel["id"], "Bob")

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[e1, e2],
          characters=[c1, c2],
          relationships=[
              {"id": uuid4(), "entity_a_id": e1["id"], "entity_b_id": e2["id"],
               "rel_type": "friends", "from_chapter": 1, "to_chapter": None, "notes": None},
          ],
          shared_dynamics=[
              {"id": uuid4(), "entity_a_id": e1["id"], "entity_b_id": e2["id"],
               "description": "They argued over lunch.", "chapter_id": chap1["id"]},
          ],
      )
      resp = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert resp.status_code == 200
      edges = resp.json()["edges"]
      kinds = {e["edge_kind"] for e in edges}
      assert "relationship" in kinds
      assert "dynamic" in kinds
      dyn_edge = next(e for e in edges if e["edge_kind"] == "dynamic")
      assert dyn_edge["tooltip"] == "They argued over lunch."


  def test_entity_graph_event_cooccurrence_char_char(fake_db_factory, client):
      """Two characters in the same event get a co-occurrence edge."""
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)
      e1, c1 = _make_char_entity(novel["id"], "Alice")
      e2, c2 = _make_char_entity(novel["id"], "Bob")

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[e1, e2],
          characters=[c1, c2],
          events=[
              {"id": uuid4(), "chapter_id": chap1["id"],
               "description": "Alice and Bob entered the room.",
               "involved_characters": [c1["id"], c2["id"]],
               "involved_locations": [], "involved_objects": [], "involved_factions": []},
          ],
      )
      resp = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert resp.status_code == 200
      edges = resp.json()["edges"]
      assert len(edges) == 1
      assert edges[0]["edge_kind"] == "event"
      assert "Alice and Bob entered the room." in edges[0]["tooltip"]


  def test_entity_graph_event_char_location_edge(fake_db_factory, client):
      """Character and location in the same event get an edge."""
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)
      e_char, char = _make_char_entity(novel["id"], "Alice")
      e_loc, loc = _make_loc_entity(novel["id"], "Cave")

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[e_char, e_loc],
          characters=[char],
          locations=[loc],
          events=[
              {"id": uuid4(), "chapter_id": chap1["id"],
               "description": "Alice explored the cave.",
               "involved_characters": [char["id"]],
               "involved_locations": [loc["id"]],
               "involved_objects": [], "involved_factions": []},
          ],
      )
      resp = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert resp.status_code == 200
      body = resp.json()
      edges = body["edges"]
      assert len(edges) == 1
      assert edges[0]["edge_kind"] == "event"
      node_ids = {n["id"] for n in body["nodes"]}
      assert edges[0]["from"] in node_ids
      assert edges[0]["to"] in node_ids


  def test_entity_graph_collapses_multiple_events(fake_db_factory, client):
      """Three events between the same pair collapse to one edge labelled '3 events'."""
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)
      e1, c1 = _make_char_entity(novel["id"], "Alice")
      e2, c2 = _make_char_entity(novel["id"], "Bob")

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[e1, e2],
          characters=[c1, c2],
          events=[
              {"id": uuid4(), "chapter_id": chap1["id"], "description": "Event one.",
               "involved_characters": [c1["id"], c2["id"]],
               "involved_locations": [], "involved_objects": [], "involved_factions": []},
              {"id": uuid4(), "chapter_id": chap1["id"], "description": "Event two.",
               "involved_characters": [c1["id"], c2["id"]],
               "involved_locations": [], "involved_objects": [], "involved_factions": []},
              {"id": uuid4(), "chapter_id": chap1["id"], "description": "Event three.",
               "involved_characters": [c1["id"], c2["id"]],
               "involved_locations": [], "involved_objects": [], "involved_factions": []},
          ],
      )
      resp = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert resp.status_code == 200
      edges = resp.json()["edges"]
      assert len(edges) == 1
      assert edges[0]["label"] == "3 events"
      assert "Event one." in edges[0]["tooltip"]
      assert "Event two." in edges[0]["tooltip"]
      assert "Event three." in edges[0]["tooltip"]


  def test_entity_graph_relationship_and_dynamic_coexist(fake_db_factory, client):
      """Explicit relationship + dynamic between same pair → 2 separate edges."""
      novel = make_novel()
      chap1 = make_chapter(novel["id"], 1)
      e1, c1 = _make_char_entity(novel["id"], "Alice")
      e2, c2 = _make_char_entity(novel["id"], "Bob")

      fake_db_factory(
          novels=[novel],
          chapters=[chap1],
          entities=[e1, e2],
          characters=[c1, c2],
          relationships=[
              {"id": uuid4(), "entity_a_id": e1["id"], "entity_b_id": e2["id"],
               "rel_type": "rivals", "from_chapter": 1, "to_chapter": None, "notes": None},
          ],
          shared_dynamics=[
              {"id": uuid4(), "entity_a_id": e1["id"], "entity_b_id": e2["id"],
               "description": "They clashed in the arena.", "chapter_id": chap1["id"]},
          ],
      )
      resp = client.get(f"/api/novels/{novel['id']}/entity-graph")
      assert resp.status_code == 200
      edges = resp.json()["edges"]
      assert len(edges) == 2
      kinds = {e["edge_kind"] for e in edges}
      assert kinds == {"relationship", "dynamic"}
      rel_edge = next(e for e in edges if e["edge_kind"] == "relationship")
      assert rel_edge["label"] == "rivals"
  ```

- [ ] **Step 2: Run tests to confirm they all fail**

  ```bash
  cd backend
  python -m pytest api/tests/test_entity_graph.py -v -k "dynamic or cooccurrence or char_location or collapses or coexist"
  ```

  Expected: 5 failures.

- [ ] **Step 3: Commit the failing tests**

  ```bash
  git add backend/api/tests/test_entity_graph.py
  git commit -m "test(backend): add failing tests for entity graph rich edges"
  ```

---

### Task 3: Add `_merge_story_edges` helper and tag relationship edges

**Files:**
- Modify: `backend/api/queries.py`

This task adds the merge helper and wires `edge_kind="relationship"` onto existing relationship edges. It does NOT add the `return` yet — that happens in Task 6 after all edge sources are collected.

- [ ] **Step 1: Add `_merge_story_edges` near the top of `queries.py` (after imports, before any functions)**

  ```python
  _STORY_KIND_PRECEDENCE = ["dynamic", "event", "possession", "location"]


  def _merge_story_edges(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
      """Collapse multiple raw story records between the same entity pair into one edge.

      Each item in raw must have: from (str), to (str), edge_kind (str), description (str|None).
      Returns one dict per canonical pair with keys: id, from, to, label, chapter_number,
      edge_kind, tooltip.
      """
      from uuid import uuid4 as _uuid4

      grouped: dict[tuple[str, str], dict[str, Any]] = {}
      for item in raw:
          a, b = item["from"], item["to"]
          pair = (min(a, b), max(a, b))
          desc = item.get("description") or ""
          if pair not in grouped:
              grouped[pair] = {
                  "from": a,
                  "to": b,
                  "edge_kind": item["edge_kind"],
                  "descriptions": [desc] if desc else [],
              }
          else:
              existing = grouped[pair]
              cur_prec = _STORY_KIND_PRECEDENCE.index(existing["edge_kind"])
              new_prec = _STORY_KIND_PRECEDENCE.index(item["edge_kind"])
              if new_prec < cur_prec:
                  existing["edge_kind"] = item["edge_kind"]
              if desc:
                  existing["descriptions"].append(desc)

      result = []
      for data in grouped.values():
          n = len(data["descriptions"])
          kind = data["edge_kind"]
          kind_plural = {
              "dynamic": "dynamics", "event": "events",
              "possession": "possessions", "location": "locations",
          }.get(kind, kind)
          label = data["descriptions"][0] if n == 1 else (f"{n} {kind_plural}" if n > 0 else None)
          tooltip = "\n".join(data["descriptions"]) or None
          result.append({
              "id": str(_uuid4()),
              "from": data["from"],
              "to": data["to"],
              "label": label,
              "chapter_number": None,
              "edge_kind": kind,
              "tooltip": tooltip,
          })
      return result
  ```

- [ ] **Step 2: Tag relationship edges in both paths of `get_entity_graph`**

  **FakeDB path** — find the `edges = [...]` list comprehension that builds relationship edges. After it, add:

  ```python
  for e in edges:
      e["edge_kind"] = "relationship"
      e["tooltip"] = None
  ```

  Do NOT change the `return` yet — it will be replaced in Task 6.

  **Real DB path** — after `edges_list = [dict(r) for r in edge_rows]`, add:

  ```python
  for e in edges_list:
      e["edge_kind"] = "relationship"
      e["tooltip"] = None
  raw_story: list[dict[str, Any]] = []
  ```

  Do NOT change the `return` yet.

- [ ] **Step 3: Run existing entity graph tests**

  ```bash
  cd backend
  python -m pytest api/tests/test_entity_graph.py -v -k "not (dynamic or cooccurrence or char_location or collapses or coexist)"
  ```

  Expected: original 4 tests still pass (edge_kind field now present on relationship edges).

- [ ] **Step 4: Commit**

  ```bash
  git add backend/api/queries.py
  git commit -m "feat(backend): add _merge_story_edges helper, tag relationship edges with edge_kind"
  ```

---

### Task 4: Add shared_dynamics edges

**Files:**
- Modify: `backend/api/queries.py`

- [ ] **Step 1: Add shared_dynamics to the FakeDB path**

  In `get_entity_graph`, inside the `if hasattr(db, "entities"):` block, directly after the `for e in edges: e["edge_kind"] = ...` loop added in Task 3, add:

  ```python
  chapter_by_id = {c["id"]: c for c in db.chapters}
  raw_story: list[dict[str, Any]] = []

  # shared_dynamics
  for sd in db.shared_dynamics:
      chap = chapter_by_id.get(sd.get("chapter_id"))
      if chap is None or chap.get("novel_id") != novel_id:
          continue
      if chap["number"] > effective_cap:
          continue
      if sd["entity_a_id"] not in entity_id_set or sd["entity_b_id"] not in entity_id_set:
          continue
      raw_story.append({
          "from": str(sd["entity_a_id"]),
          "to": str(sd["entity_b_id"]),
          "edge_kind": "dynamic",
          "description": sd.get("description"),
      })
  ```

  Leave the existing `return {"nodes": nodes, "edges": edges}` in place for now — it will be updated in Task 6.

- [ ] **Step 2: Add shared_dynamics to the real DB path**

  In `get_entity_graph`, in the real DB path, after `raw_story: list[dict[str, Any]] = []` (added in Task 3), add:

  ```python
  dyn_rows = db.fetchall(
      """
      SELECT sd.entity_a_id::text AS "from",
             sd.entity_b_id::text AS "to",
             'dynamic'            AS edge_kind,
             sd.description       AS description
      FROM shared_dynamics sd
      JOIN chapters ch ON ch.id = sd.chapter_id
      JOIN entities ea ON ea.id = sd.entity_a_id AND ea.novel_id = %s
      JOIN entities eb ON eb.id = sd.entity_b_id AND eb.novel_id = %s
      WHERE ch.number <= %s
      """,
      (str(novel_id), str(novel_id), effective_cap),
      dict_rows=True,
  )
  raw_story.extend(dict(r) for r in dyn_rows)
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/api/queries.py
  git commit -m "feat(backend): collect shared_dynamics edges in entity graph"
  ```

---

### Task 5: Add events co-occurrence edges

**Files:**
- Modify: `backend/api/queries.py`

- [ ] **Step 1: Add events to the FakeDB path**

  In `get_entity_graph`, FakeDB block, after the shared_dynamics loop (still building `raw_story`), add:

  ```python
  char_by_id = {c["id"]: c for c in db.characters}
  loc_by_id = {l["id"]: l for l in db.locations}

  for ev in db.events:
      chap = chapter_by_id.get(ev.get("chapter_id"))
      if chap is None or chap.get("novel_id") != novel_id:
          continue
      if chap["number"] > effective_cap:
          continue
      desc = ev.get("description") or ""
      char_eids = [
          char_by_id[cid]["entity_id"]
          for cid in (ev.get("involved_characters") or [])
          if cid in char_by_id and char_by_id[cid]["entity_id"] in entity_id_set
      ]
      loc_eids = [
          loc_by_id[lid]["entity_id"]
          for lid in (ev.get("involved_locations") or [])
          if lid in loc_by_id and loc_by_id[lid]["entity_id"] in entity_id_set
      ]
      for i, eid_a in enumerate(char_eids):
          for eid_b in char_eids[i + 1:]:
              raw_story.append({"from": str(eid_a), "to": str(eid_b), "edge_kind": "event", "description": desc})
      for eid_c in char_eids:
          for eid_l in loc_eids:
              raw_story.append({"from": str(eid_c), "to": str(eid_l), "edge_kind": "event", "description": desc})
  ```

- [ ] **Step 2: Add events to the real DB path**

  In the real DB path, after the `dyn_rows` block, add:

  ```python
  ev_char_rows = db.fetchall(
      """
      SELECT ca.entity_id::text AS "from",
             cb.entity_id::text AS "to",
             'event'            AS edge_kind,
             e.description      AS description
      FROM events e
      JOIN chapters ch ON ch.id = e.chapter_id
      JOIN characters ca ON ca.id = ANY(e.involved_characters)
      JOIN characters cb ON cb.id = ANY(e.involved_characters) AND cb.id > ca.id
      JOIN entities ea ON ea.id = ca.entity_id AND ea.novel_id = %s
      JOIN entities eb ON eb.id = cb.entity_id AND eb.novel_id = %s
      WHERE ch.number <= %s
      """,
      (str(novel_id), str(novel_id), effective_cap),
      dict_rows=True,
  )
  raw_story.extend(dict(r) for r in ev_char_rows)

  ev_loc_rows = db.fetchall(
      """
      SELECT c.entity_id::text AS "from",
             l.entity_id::text AS "to",
             'event'           AS edge_kind,
             e.description     AS description
      FROM events e
      JOIN chapters ch ON ch.id = e.chapter_id
      JOIN characters c ON c.id = ANY(e.involved_characters)
      JOIN locations  l ON l.id = ANY(e.involved_locations)
      JOIN entities ea ON ea.id = c.entity_id AND ea.novel_id = %s
      JOIN entities eb ON eb.id = l.entity_id AND eb.novel_id = %s
      WHERE ch.number <= %s
      """,
      (str(novel_id), str(novel_id), effective_cap),
      dict_rows=True,
  )
  raw_story.extend(dict(r) for r in ev_loc_rows)
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/api/queries.py
  git commit -m "feat(backend): collect events co-occurrence edges in entity graph"
  ```

---

### Task 6: Add possesses/located_in edges and finalize both paths

**Files:**
- Modify: `backend/api/queries.py`

- [ ] **Step 1: Add possesses_edges and located_in_edges to the FakeDB path**

  In the FakeDB block, after the events loop, add:

  ```python
  obj_by_id = {o["id"]: o for o in db.objects}

  for pe in getattr(db, "possesses_edges", []):
      char = char_by_id.get(pe.get("character_id"))
      obj = obj_by_id.get(pe.get("object_id"))
      if not char or not obj:
          continue
      if char["entity_id"] not in entity_id_set or obj["entity_id"] not in entity_id_set:
          continue
      since = pe.get("since_chapter")
      until = pe.get("until_chapter")
      if since is not None and since > effective_cap:
          continue
      if until is not None and until <= effective_cap:
          continue
      raw_story.append({
          "from": str(char["entity_id"]),
          "to": str(obj["entity_id"]),
          "edge_kind": "possession",
          "description": None,
      })

  for lie in getattr(db, "located_in_edges", []):
      eid = lie.get("entity_id")
      loc = loc_by_id.get(lie.get("location_id"))
      if not loc or eid not in entity_id_set or loc["entity_id"] not in entity_id_set:
          continue
      since = lie.get("since_chapter")
      until = lie.get("until_chapter")
      if since is not None and since > effective_cap:
          continue
      if until is not None and until <= effective_cap:
          continue
      raw_story.append({
          "from": str(eid),
          "to": str(loc["entity_id"]),
          "edge_kind": "location",
          "description": None,
      })
  ```

- [ ] **Step 2: Replace the FakeDB `return` to include story edges**

  Find the existing `return {"nodes": nodes, "edges": edges}` in the FakeDB block. Replace it with:

  ```python
  story_edges = _merge_story_edges(raw_story)
  return {"nodes": nodes, "edges": edges + story_edges}
  ```

- [ ] **Step 3: Add possesses/located_in to the real DB path**

  In the real DB path, after the `ev_loc_rows` block, add:

  ```python
  poss_rows = db.fetchall(
      """
      SELECT c.entity_id::text AS "from",
             o.entity_id::text AS "to",
             'possession'      AS edge_kind,
             NULL::text        AS description
      FROM possesses_edges pe
      JOIN characters c ON c.id = pe.character_id
      JOIN objects    o ON o.id = pe.object_id
      JOIN entities ea ON ea.id = c.entity_id AND ea.novel_id = %s
      JOIN entities eb ON eb.id = o.entity_id AND eb.novel_id = %s
      WHERE (pe.since_chapter IS NULL OR pe.since_chapter <= %s)
        AND (pe.until_chapter IS NULL OR pe.until_chapter > %s)
      """,
      (str(novel_id), str(novel_id), effective_cap, effective_cap),
      dict_rows=True,
  )
  raw_story.extend(dict(r) for r in poss_rows)

  loc_in_rows = db.fetchall(
      """
      SELECT lie.entity_id::text AS "from",
             l.entity_id::text   AS "to",
             'location'          AS edge_kind,
             NULL::text          AS description
      FROM located_in_edges lie
      JOIN locations l ON l.id = lie.location_id
      JOIN entities ea ON ea.id = lie.entity_id AND ea.novel_id = %s
      JOIN entities eb ON eb.id = l.entity_id   AND eb.novel_id = %s
      WHERE (lie.since_chapter IS NULL OR lie.since_chapter <= %s)
        AND (lie.until_chapter IS NULL OR lie.until_chapter > %s)
      """,
      (str(novel_id), str(novel_id), effective_cap, effective_cap),
      dict_rows=True,
  )
  raw_story.extend(dict(r) for r in loc_in_rows)
  ```

- [ ] **Step 4: Replace the real DB `return` to include story edges**

  Find the existing `return {"nodes": nodes_list, "edges": edges_list}` in the real DB path. Replace it with:

  ```python
  story_edges = _merge_story_edges(raw_story)
  return {"nodes": nodes_list, "edges": edges_list + story_edges}
  ```

- [ ] **Step 5: Run the full entity graph test suite**

  ```bash
  cd backend
  python -m pytest api/tests/test_entity_graph.py -v
  ```

  Expected: all 9 tests PASS.

- [ ] **Step 6: Run the full test suite for regressions**

  ```bash
  python -m pytest api/tests/ -v
  ```

  Expected: all tests pass.

- [ ] **Step 7: Commit**

  ```bash
  git add backend/api/queries.py
  git commit -m "feat(backend): add possesses/located_in edges and finalize entity graph rich edges"
  ```

---

### Task 7: Frontend — update types and edge rendering

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/routes/EntityGraph.tsx`

- [ ] **Step 1: Update `GraphEdge` type in `api.ts`**

  Find `GraphEdge` (around line 190). Replace it with:

  ```typescript
  export type GraphEdge = {
    id: string;
    from: string;
    to: string;
    label: string | null;
    chapter_number: number | null;
    edge_kind: string | null;
    tooltip: string | null;
  };
  ```

- [ ] **Step 2: Update edge rendering in `EntityGraph.tsx`**

  Find the `const edges = new DataSet(...)` call inside the `useEffect`. Replace it with:

  ```tsx
  const edges = new DataSet(
    visibleEdges.map((e) => ({
      id: e.id,
      from: e.from,
      to: e.to,
      label: e.edge_kind === "relationship" ? (e.label ?? undefined) : undefined,
      title: e.tooltip ?? undefined,
      arrows: e.edge_kind === "relationship" ? "to" : undefined,
      dashes: e.edge_kind !== "relationship",
      color: e.edge_kind === "relationship"
        ? { color: "#4e9af1", opacity: 1 }
        : { color: "#666", opacity: 0.7 },
      width: e.edge_kind === "relationship" ? 2 : 1,
    }))
  );
  ```

- [ ] **Step 3: Remove the debug error line added during debugging**

  Find and remove this line in `EntityGraph.tsx`:

  ```tsx
  if (isError) return <p>Error: {String(error)}</p>;
  ```

  Also remove `isError` and `error` from the `useQuery` destructuring:

  ```tsx
  const { data, isLoading } = useQuery({
  ```

- [ ] **Step 4: Verify TypeScript compiles**

  ```bash
  cd frontend && npx tsc --noEmit
  ```

  Expected: no errors.

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/src/api.ts frontend/src/routes/EntityGraph.tsx
  git commit -m "feat(frontend): render story edges as dashed lines with tooltips in entity graph"
  ```

---

### Task 8: Update docs

**Files:**
- Modify: `docs/superpowers/specs/2026-05-26-entity-graph-design.md`

- [ ] **Step 1: Add cross-reference note to the original spec**

  Open `docs/superpowers/specs/2026-05-26-entity-graph-design.md`. After the `## Overview` heading, add:

  ```markdown
  > **Extended:** See `2026-05-26-entity-graph-rich-edges-design.md` for the follow-up that adds shared_dynamics, events co-occurrence, possesses, and located_in edges.
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add docs/superpowers/specs/2026-05-26-entity-graph-design.md
  git commit -m "docs: cross-reference rich edges spec from original entity graph spec"
  ```
