"""The coaching HTTP surface, mounted by server.create_app as one router."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import summarize
from . import analysis, stats, store


class AnalyzeBody(BaseModel):
    line_index: int


def router(notes_dir: Path, model: str, resolve: Callable[[str], Path | None]) -> APIRouter:
    """`resolve` is the server's path-traversal guard for note names."""
    api = APIRouter()

    def note(name: str) -> Path:
        path = resolve(name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        return path

    def run(fn: Callable[[], dict]) -> dict:
        try:
            return fn()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except summarize.OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.post("/api/notes/{name}/analyze")
    def analyze_line(name: str, body: AnalyzeBody):
        path = note(name)
        return run(lambda: analysis.analyze(path, body.line_index, model))

    @api.post("/api/notes/{name}/analyze/meeting")
    def analyze_meeting(name: str):
        path = note(name)
        review = run(lambda: analysis.analyze_meeting(path, model))
        return store.save(notes_dir, path.name, review)

    @api.get("/api/notes/{name}/analyze/meeting")
    def saved_review(name: str):
        record = store.load(notes_dir, note(name).name)
        if record is None:
            raise HTTPException(status_code=404, detail="no saved review")
        return record

    @api.get("/api/coaching/dashboard")
    def dashboard():
        return stats.dashboard(notes_dir)

    return api
