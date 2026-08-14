# Frontend

The Continuum wiki: a React 19 + TypeScript + Vite SPA over the backend's
read layer. It renders what the pipeline extracted — characters, locations,
objects, factions, relationships, threads, commitments, knowledge, canon
facts, scenes, timeline, continuity critiques — and lets you process new
chapters from the Process page.

```bash
npm install
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

The single idea worth knowing before reading the code. Every read endpoint
takes an optional chapter cutoff, and the UI keeps that cutoff in the URL
(`?cap=N`) via `useChapterCap`. Setting it makes every page show the story
as it stood at the end of chapter N — no later spoilers — which is the same
mechanism the MCP server uses to keep a drafting agent from seeing its own
future. Because it lives in the query string, a capped view is shareable and
survives a reload.

Data fetching is TanStack Query throughout; the cap is part of each query
key, so changing it refetches rather than filtering client-side.

Graphs (`EntityGraph.tsx`, relationship views) use `vis-network`.

See `../docs/reference.html` for the API these pages call and
`../docs/architecture.html` for how the data gets there.
