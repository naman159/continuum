# Wiki Web App — Design

## Goal

Build a local web UI for browsing the data the pipeline produces. The pipeline already populates rich relational + vector data in Postgres (characters, character states per chapter, events, threads, relationships, continuity flags, embeddings). The CLI wiki commands surface this as JSON; the web app makes it browseable in HTML.

Scope is a **barebones inspector with an interactive relationship graph** — read-only, single-user, runs locally. Not a public wiki, not a pipeline operator UI (yet).

## Decisions baked in

- **Stack:** Vite + React + TypeScript on the frontend; FastAPI JSON API on the backend.
- **Layout:** `backend/` (containing `pipeline/` + `api/`) and `frontend/` as siblings at project root.
- **Chapter cap:** persistent global "as of chapter N" filter applied to every view.
- **Detail views:** show every column from the underlying DB tables — no curated subset.
- **Graph:** `vis-network` for relationships (interactive, force-directed).
- **Run:** `uv run novel-webapp` starts FastAPI on `:8000`; `npm run dev` (in `frontend/`) starts Vite on `:5173` with proxy to FastAPI. Production: FastAPI serves the `frontend/dist` build.

## Directory restructure (step 1 of implementation)

Current root has loose `.py` files (`config.py`, `pipeline.py`, etc.) and dirs (`db/`, `extraction/`, `ingestion/`, `wiki/`, `tests/`, `docs/`). This becomes:

```
continuum/
├── backend/
│   ├── pipeline/              # all current pipeline code, as a package
│   │   ├── __init__.py        # re-exports public API: process_chapter, init_db, etc.
│   │   ├── config.py
│   │   ├── pipeline.py
│   │   ├── embeddings.py
│   │   ├── db/
│   │   ├── extraction/
│   │   ├── ingestion/
│   │   └── wiki/
│   └── api/                   # new — FastAPI service
│       ├── __init__.py
│       ├── app.py             # FastAPI() instance + middleware
│       ├── routes/
│       │   ├── novels.py
│       │   ├── characters.py
│       │   ├── timeline.py
│       │   ├── threads.py
│       │   ├── relationships.py
│       │   └── continuity.py
│       ├── queries.py         # shared DB read helpers (or thin wrappers around pipeline.wiki.*)
│       └── schemas.py         # Pydantic response models = single source of types
├── frontend/                  # new — Vite + React + TS
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── routes/            # React Router pages
│       ├── components/
│       ├── hooks/
│       ├── api.ts             # typed fetch wrapper, derived from API schemas
│       └── styles.css
├── tests/                     # unchanged
├── docs/
├── pyproject.toml             # uses package-dir trick to keep imports short
├── README.md
└── .env.example
```

### Packaging trick

`pyproject.toml` configures `backend/` as the package source root, so the `backend.` prefix isn't required in imports:

```toml
[tool.setuptools]
package-dir = {"" = "backend"}

[tool.setuptools.packages.find]
where = ["backend"]
```

Imports become `from pipeline.db.client import DBClient`, not `from backend.pipeline.db.client import DBClient`.

### Refactor mechanics

- Move every existing root `.py` file (`config.py`, `pipeline.py`, `embeddings.py`, `main.py`) into `backend/pipeline/`.
- Move every existing dir (`db/`, `extraction/`, `ingestion/`, `wiki/`) into `backend/pipeline/`.
- Add `backend/pipeline/__init__.py` re-exporting key public symbols.
- Update internal imports: prefer explicit relative imports inside the package (`from .db.client import DBClient`).
- Update `pyproject.toml`:
  - `[tool.setuptools] package-dir = {"" = "backend"}`
  - `packages.find` scoped to `backend/`
  - `py-modules` removed (everything is now in packages)
  - CLI script entries updated: `novel-pipeline = "pipeline.pipeline:main"`, etc.
- Update `tests/` imports to use new paths.
- Verify: `uv run novel-pipeline init-db` and the existing CLI commands still work.

## Backend API design

FastAPI app with JSON-only endpoints. All read-only. Pydantic response models in `backend/api/schemas.py` mirror the DB shape and serve as the type source for frontend types.

### Routes

| Route | Returns |
|--|--|
| `GET /api/novels` | List of novels: `[{id, title, author, language, max_chapter, character_count, ...}]` |
| `GET /api/novels/{novel_id}` | Single novel summary + max chapter number |
| `GET /api/novels/{novel_id}/characters?cap=N` | Character list with name, aliases, description, first_appearance_chapter, count of states/events |
| `GET /api/novels/{novel_id}/characters/{cid}?cap=N` | Full character detail: identity, every state row ≤ cap, every relationship row, every event involving them ≤ cap |
| `GET /api/novels/{novel_id}/chapters?cap=N` | Chapter list with number, title, summary, processed_at |
| `GET /api/novels/{novel_id}/timeline?cap=N` | Events grouped by chapter: id, description, event_type, impact_level, involved_(characters/locations/objects) names |
| `GET /api/novels/{novel_id}/threads?cap=N&status=open\|closed\|all` | Threads with metadata + linked events |
| `GET /api/novels/{novel_id}/relationships?cap=N` | `{nodes: [...], edges: [...]}` for graph rendering |
| `GET /api/novels/{novel_id}/continuity?cap=N&resolved=open\|all` | Continuity flags with chapter, type, resolved status |

### Chapter-cap semantics

Every endpoint that takes `cap=N` filters its data to "as of chapter N" — chapters > N are excluded:
- Character `current_state` becomes the latest state ≤ N.
- `history` includes only state rows ≤ N.
- `events` includes only events from chapters ≤ N.
- `threads.opened_chapter` ≤ N; if `closed_chapter` > N, treat as still open.
- `continuity_flags` from chapters > N excluded.
- `relationships` from chapters > N excluded.

If `cap` is missing or empty, default = max chapter for the novel (i.e., show everything).

### Query layer

`backend/api/queries.py` wraps the existing logic from `pipeline/wiki/*.py` (which already accepts `up_to_chapter`). New helpers added only where the API returns shapes the CLI doesn't already produce (e.g., the graph nodes/edges format).

## Frontend design

### Stack

- React 18+ with TypeScript
- Vite for dev/build
- React Router for routing
- TanStack Query (React Query) for API calls + caching
- vis-network for the relationship graph
- No CSS framework — one hand-written `styles.css` (this can be swapped for Tailwind later if desired)

### Routing

```
/                                    → redirect to /novels
/novels                              → novel list
/novels/:novelId                     → novel landing (links to other views)
/novels/:novelId/characters          → character list (sidebar table)
/novels/:novelId/characters/:cid     → character detail
/novels/:novelId/chapters            → chapter list
/novels/:novelId/timeline            → events grouped by chapter
/novels/:novelId/threads             → threads
/novels/:novelId/relationships       → graph
/novels/:novelId/continuity          → continuity flags
```

The chapter cap is a query string `?cap=N` preserved across navigation. A `useChapterCap()` hook reads/writes it via `useSearchParams`.

### Layout

- Persistent left sidebar with: novel name, current chapter cap (numeric input + slider), nav links to each view.
- Main content area renders the route.
- Header shows breadcrumb and any view-specific filters (e.g., thread status toggle).

### Show-all-data principle

Detail views render every field from the underlying DB row as a labeled key/value pair. Null/empty values display as `—`. Arrays render as inline lists. JSON columns (`relationships` JSONB) render as a pretty-printed expandable block.

The character detail page is the most data-rich:
- **Identity card:** id, name, aliases (chips), first_appearance_chapter, description, embedding-present indicator
- **State history table:** every `character_states` row in chapter order, every column shown
- **Relationships table:** every `relationships` row touching this character (both directions), with both names resolved
- **Events table:** every `events` row where this character is in `involved_characters`, with linked location/object names

### Relationship graph

`/relationships` page mounts a vis-network `Network` instance. Nodes are characters; edges are relationship rows. Edge labels show `rel_type`. Hover highlights neighborhood; click navigates to the character detail.

The page also includes a plain HTML table fallback below the graph showing the raw `relationships` rows.

### Type safety

`backend/api/schemas.py` Pydantic models define the wire format. Frontend types in `frontend/src/api.ts` are kept in sync. Initially: hand-written TypeScript interfaces matching the Pydantic schemas. Future enhancement: auto-generate from OpenAPI (FastAPI exposes `/openapi.json`).

## Dev / run / deploy

### Development

Two processes:
- `uv run novel-webapp` — starts FastAPI on `:8000`
- `npm run dev` (from `frontend/`) — starts Vite on `:5173` with proxy to `:8000`

Vite config proxies `/api/*` requests to `http://localhost:8000`, so frontend code calls `/api/novels` directly (no CORS).

### Production / preview

- `npm run build` (in `frontend/`) produces `frontend/dist/`.
- FastAPI mounts `frontend/dist/` as static files at `/` (with SPA fallback to `index.html`).
- Single command `uv run novel-webapp` then serves both API and UI on `:8000`.

### Entry point

```toml
# pyproject.toml
[project.scripts]
novel-webapp = "api.app:run"
```

`api.app.run()` parses `--port`, `--reload`, `--no-frontend` flags and launches uvicorn.

## Testing

- **API unit tests** (`tests/api/`): use FastAPI's `TestClient` against an in-memory FakeDB (same pattern as `tests/test_canonicalizer.py`). One test per route asserting status 200 + key fields present.
- **Query unit tests** (`tests/api/test_queries.py`): chapter-cap filtering correctness — feed synthetic rows, assert post-cap exclusions are right.
- **Frontend smoke tests** (`frontend/src/__tests__/`): React Testing Library — render each route with mocked API responses, assert key fields visible. Vitest as runner.
- **No e2e tests** in this iteration — manual browser check is enough for an inspector.

## Dependencies added

### Python
- `fastapi`
- `uvicorn[standard]`
- `pydantic` (already a transitive dep, just declare)

### Node
- `react`, `react-dom`, `react-router-dom`
- `@tanstack/react-query`
- `vis-network`, `vis-data`
- TypeScript dev deps: `typescript`, `@types/react`, `@types/react-dom`, `vite`, `@vitejs/plugin-react`
- Test dev deps: `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`

## Out of scope

- Auth, multi-user, sharing
- Editing/mutating data via UI
- Triggering pipeline runs from UI (the "operator" feature)
- Search / fuzzy filtering across novels
- Mobile-optimized layout
- Auto-generated TypeScript types from OpenAPI (deferred refinement)
- E2E browser tests
- Production hosting (this is a local tool)

## Implementation ordering

1. **Refactor** — move pipeline into `backend/pipeline/`, update imports, verify CLI still works, run existing tests.
2. **API skeleton** — `backend/api/` with FastAPI app, `/api/novels` and `/api/novels/{id}` routes, Pydantic schemas, smoke tests.
3. **Frontend skeleton** — `frontend/` with Vite + React + Router, sidebar layout, novel list page consuming `/api/novels`.
4. **Character views** — `/api/.../characters` and `/api/.../characters/{cid}` + matching frontend pages with all-fields rendering.
5. **Chapter cap** — wire the slider, plumb `?cap=N` through all routes.
6. **Timeline + chapters + threads + continuity** — flesh out remaining views with chapter cap.
7. **Relationship graph** — vis-network integration with the JSON `nodes/edges` endpoint.
8. **Dev/prod build glue** — FastAPI static-file mount, single-command launch.
9. **Tests** — fill in missing unit tests across both Python and frontend.

Each step ends in a runnable, testable state.
