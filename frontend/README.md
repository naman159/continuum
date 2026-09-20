# Frontend

The Continuum wiki: a React 19 + TypeScript + Vite SPA over the backend's
read layer. It renders what the pipeline extracted — characters, locations,
objects, factions, relationships, threads, commitments, knowledge, canon
facts, scenes, timeline, continuity critiques — and lets you process new
chapters from the Process page.

```bash
npm ci          # Node.js 22.13+ on the 22 LTS line, or 24+
npm run dev      # http://localhost:5173, proxies /api to 127.0.0.1:8000
npm run build    # tsc -b && vite build → dist/ (served by novel-webapp)
npm run lint
```

The dev server proxies `/api` to the backend on port 8000
(`vite.config.ts`), so run `uv run novel-webapp` alongside it. In
production `novel-webapp` serves `dist/` directly with an SPA fallback.

## Layout

```
src/
├── App.tsx                 # Router + QueryClient; 24 pages
├── api.ts                  # Every fetch call + the TS types for API responses
├── components/
│   ├── Sidebar.tsx         # Nav (incl. per-novel custom entity types) + chapter-cap slider
│   ├── Layout.tsx
│   └── FieldList.tsx
├── hooks/useChapterCap.ts  # The chapter cutoff, stored in the ?cap= query param
└── routes/                 # One component per page
```

## The chapter cap

Story read endpoints take an optional chapter cutoff, and the UI keeps that
cutoff in the URL (`?cap=N`) via `useChapterCap`. It filters chapter assertions
and versioned metadata to the end of chapter N, using the same mechanism as
the MCP story lookups. Older imports have a metadata baseline at their latest
chapter when upgraded; earlier metadata requires re-import. See the
[public-readiness audit](../docs/public-readiness.md) for that migration boundary.
The cutoff stays in the query string, so a capped view is shareable and survives
a reload.

Data fetching is TanStack Query throughout; the cap is part of each query
key, so changing it refetches rather than filtering client-side.

Graphs (`EntityGraph.tsx`, relationship views) use `vis-network`. These routes
load on demand so the initial novel list does not download the graph renderer.

See `../docs/reference.html` for the API these pages call and
`../docs/architecture.html` for how the data gets there.
