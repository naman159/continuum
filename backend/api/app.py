from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import characters, chapters, continuity, novels, relationships, threads, timeline

app = FastAPI(title="Continuum Wiki API")
app.include_router(novels.router)
app.include_router(characters.router)
app.include_router(chapters.router)
app.include_router(timeline.router)
app.include_router(threads.router)
app.include_router(continuity.router)
app.include_router(relationships.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        target = _FRONTEND_DIST / full_path
        if full_path and target.is_file():
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
