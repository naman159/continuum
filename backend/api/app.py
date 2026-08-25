from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import (
    canon,
    chapters,
    characters,
    commitments,
    continuity,
    drafts,
    dynamics,
    entity_graph,
    entity_types,
    factions,
    knowledge,
    locations,
    novels,
    objects,
    process,
    relationships,
    scenes,
    search,
    threads,
    timeline,
)

app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)
app.include_router(characters.router)
app.include_router(locations.router)
app.include_router(objects.router)
app.include_router(factions.router)
app.include_router(chapters.router)
app.include_router(timeline.router)
app.include_router(threads.router)
app.include_router(continuity.router)
app.include_router(relationships.router)
app.include_router(entity_graph.router)
app.include_router(dynamics.router)
app.include_router(process.router)
# SOTA-upgrade routes
app.include_router(scenes.router)
app.include_router(commitments.router)
app.include_router(canon.router)
app.include_router(knowledge.router)
app.include_router(entity_types.router)
app.include_router(search.router)
app.include_router(drafts.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        # Without this guard an unmatched /api/* GET falls through to
        # index.html with status 200, so the frontend's res.ok check passes
        # and res.json() dies on "<!doctype" instead of surfacing a 404.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target = (_FRONTEND_DIST / full_path).resolve()
        # Refuse anything that escapes the dist directory ("../" traversal).
        if full_path and target.is_relative_to(_FRONTEND_DIST) and target.is_file():
            return FileResponse(target)
        return FileResponse(_FRONTEND_DIST / "index.html")


def run() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Continuum wiki web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    run()
