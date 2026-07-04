from __future__ import annotations

"""generate_chapter: plan -> retrieve -> draft -> claims -> critique -> revise
-> (optionally) ingest with source='generated'.

Requires Project 1 (process_chapter(db=, source=, generation_meta=, replace=)).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pipeline.config import settings
from pipeline.critic.runner import ContinuityCritic
from pipeline.critic.types import CritiqueReport
from pipeline.db.client import DBClient
from pipeline.embeddings import EmbeddingService
from pipeline.generation.draft_claims import build_draft_chapter, extract_draft_claims
from pipeline.generation.drafter import SceneDrafter
from pipeline.generation.style import average_fingerprints
from pipeline.pipeline import process_chapter
from pipeline.planner.context import gather_plan_context
from pipeline.planner.planner import ScenePlanner, _mock_plan
from pipeline.planner.types import ChapterPlan
from pipeline.retrieval.hybrid import HybridRetriever
from pipeline.retrieval.types import RetrievalQuery

logger = logging.getLogger(__name__)


@dataclass
class GeneratedChapter:
    novel_id: str
    chapter_number: int
    text: str
    plan: ChapterPlan
    report: CritiqueReport
    iterations: int = 1
    ingested: bool = False
    chapter_id: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)


def _load_recent_style(db: Any, novel_id: str) -> dict[str, Any] | None:
    rows = db.fetchall(
        """
        SELECT style_fingerprint FROM chapters
         WHERE novel_id = %s AND style_fingerprint IS NOT NULL
         ORDER BY number DESC LIMIT 3
        """,
        (novel_id,),
        dict_rows=True,
    )
    fingerprints = []
    for r in rows or []:
        fp = r.get("style_fingerprint")
        if isinstance(fp, str):
            try:
                fp = json.loads(fp)
            except Exception:
                continue
        if isinstance(fp, dict):
            fingerprints.append(fp)
    return average_fingerprints(fingerprints)


def _resolve_planned_ids(
    db: Any, novel_id: str, plan: ChapterPlan
) -> tuple[list[str], list[str]]:
    """The planner speaks in plot-thread titles and commitment foreshadow
    texts; the critic's SQL expects row uuids. Resolve read-only, dropping
    (with a warning) anything that doesn't match an existing row."""
    titles = [t for s in plan.scenes for t in s.threads_to_advance]
    texts = [
        c
        for s in plan.scenes
        for c in (s.commitments_to_plant + s.commitments_to_satisfy)
    ]

    thread_ids: list[str] = []
    for title in dict.fromkeys(t.strip() for t in titles if t and t.strip()):
        row = db.fetchone(
            "SELECT id FROM plot_threads WHERE novel_id = %s AND lower(title) = lower(%s) LIMIT 1",
            (novel_id, title),
        )
        if row:
            thread_ids.append(str(row[0]))
        else:
            logger.warning("generation: planned thread %r not found, skipping", title)

    commitment_ids: list[str] = []
    for text in dict.fromkeys(t.strip() for t in texts if t and t.strip()):
        row = db.fetchone(
            "SELECT id FROM commitments WHERE novel_id = %s AND lower(foreshadow_text) = lower(%s) LIMIT 1",
            (novel_id, text),
        )
        if row:
            commitment_ids.append(str(row[0]))
        else:
            logger.warning(
                "generation: planned commitment %r not found, skipping", text[:80]
            )

    return thread_ids, commitment_ids


def _context_block_for_scene(
    retriever: HybridRetriever | None, novel_id: str, chapter_number: int, scene
) -> str | None:
    """Returns the context block, "" when retrieval succeeded with nothing to
    cite (not retried), or None on failure (retried next revision)."""
    if retriever is None:
        return ""
    query_text = f"{scene.scene_goal} {' '.join(scene.present_characters)}"
    try:
        bundle = retriever.retrieve(
            RetrievalQuery(
                text=query_text,
                novel_id=novel_id,
                max_chapter=chapter_number - 1,
                k=6,
            ),
            use_rerank=False,
        )
    except Exception as exc:
        logger.warning("generation: retrieval failed, drafting without it: %s", exc)
        return None
    lines = [
        f"[{r.kind} ch{r.chapter_number}] {r.snippet}" for r in bundle.results if r.snippet
    ]
    return "\n".join(lines[:6])


def generate_chapter(
    novel_id: str,
    chapter_number: int,
    *,
    db: DBClient | None = None,
    ingest: bool = False,
    use_mock: bool | None = None,
    max_revisions: int | None = None,
    progress: Any | None = None,
) -> GeneratedChapter:
    owned = db is None
    client = db if db is not None else DBClient()
    mock = settings.use_mock_llm if use_mock is None else use_mock
    revisions_allowed = (
        settings.generation_max_revisions if max_revisions is None else max_revisions
    )

    def _tick(name: str) -> None:
        if progress is not None:
            progress.on_pass_start(name)
            progress.on_pass_done(name)

    try:
        _tick("planning")
        if mock:
            # Force the mock plan: the planner consults the global
            # settings.use_mock_llm itself, so with an API key configured a
            # use_mock=True loop run would otherwise still hit the LLM.
            plan = _mock_plan(gather_plan_context(client, novel_id, chapter_number))
        else:
            plan = ScenePlanner(client).plan(novel_id, chapter_number)

        if not plan.scenes:
            raise ValueError(
                f"generation: planner returned no scenes for chapter {chapter_number}; "
                "refusing to draft an empty chapter"
            )
        planned_thread_ids, planned_commitment_ids = _resolve_planned_ids(
            client, novel_id, plan
        )

        retriever = None
        if not mock:
            retriever = HybridRetriever(client, EmbeddingService(use_mock=mock))
        style = _load_recent_style(client, novel_id)
        drafter = SceneDrafter(use_mock=mock)
        critic = ContinuityCritic(client)

        # Retrieval context depends only on the immutable plan: computed on
        # the first iteration and reused across revisions. None means "not
        # yet computed or failed" and is (re)tried; "" means retrieval
        # succeeded with nothing to cite and is reused as-is.
        context_blocks: list[str | None] = [None] * len(plan.scenes)

        revision_notes: list[str] = []
        text = ""
        report: CritiqueReport | None = None
        iterations = 0

        while iterations <= revisions_allowed:
            iterations += 1
            context_blocks = [
                cb
                if cb is not None
                else _context_block_for_scene(retriever, novel_id, chapter_number, scene)
                for scene, cb in zip(plan.scenes, context_blocks)
            ]
            scene_texts: list[str] = []
            for scene, context_block in zip(plan.scenes, context_blocks):
                _tick(f"drafting scene {scene.scene_index}/{len(plan.scenes)}")
                scene_texts.append(
                    drafter.draft_scene(
                        scene=scene,
                        plan=plan,
                        context_block=context_block or "",
                        prior_text_tail="\n\n".join(scene_texts)[-1500:],
                        style=style,
                        revision_notes=revision_notes or None,
                    )
                )
            text = "\n\n".join(scene_texts)

            _tick("critique")
            raw_claims = extract_draft_claims(text, use_mock=mock)
            draft = build_draft_chapter(
                client,
                novel_id=novel_id,
                chapter_number=chapter_number,
                text=text,
                raw_claims=raw_claims,
                planned_thread_ids=planned_thread_ids,
                planned_commitment_ids=planned_commitment_ids,
            )
            report = critic.critique(draft)
            if report.passed:
                break
            revision_notes = [
                f"{f.message}" + (f" (offending text: {f.quote})" if f.quote else "")
                for f in report.fails
            ]
            logger.info(
                "generation: draft failed critique (%d fails), revising (iteration %d)",
                len(report.fails),
                iterations,
            )

        assert report is not None
        result = GeneratedChapter(
            novel_id=novel_id,
            chapter_number=chapter_number,
            text=text,
            plan=plan,
            report=report,
            iterations=iterations,
        )

        if ingest and report.passed:
            _tick("ingest")
            outcome = process_chapter(
                novel_id=novel_id,
                chapter_number=chapter_number,
                raw_text=text,
                chapter_title=plan.title,
                use_mock_llm=mock or None,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
                progress=progress,
                db=client,
                replace=False,
                source="generated",
                generation_meta={
                    "model": settings.draft_model,
                    "chapter_goal": plan.chapter_goal,
                    "critic": report.summary(),
                    "iterations": iterations,
                },
            )
            result.ingested = True
            result.chapter_id = str(outcome.get("chapter_id"))
        elif ingest:
            logger.warning(
                "generation: NOT ingesting chapter %s — critique failed after %d iterations",
                chapter_number,
                iterations,
            )

        return result
    finally:
        if owned:
            client.close()


__all__ = ["generate_chapter", "GeneratedChapter"]
