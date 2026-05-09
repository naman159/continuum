from __future__ import annotations

import threading


def test_job_store_create_and_get():
    from api.jobs import JobStore

    store = JobStore()
    job_id = store.create(total_passes=7)
    record = store.get(job_id)

    assert record is not None
    assert record.job_id == job_id
    assert record.status == "pending"
    assert record.total_passes == 7
    assert record.passes_done == 0
    assert record.current_pass is None


def test_job_store_get_missing_returns_none():
    from api.jobs import JobStore

    store = JobStore()
    assert store.get("nonexistent") is None


def test_progress_tracker_on_pass_start():
    from api.jobs import JobStore, ProgressTracker

    store = JobStore()
    job_id = store.create(total_passes=7)
    tracker = ProgressTracker(job_id=job_id, store=store)

    tracker.on_pass_start("chapter_summary")

    record = store.get(job_id)
    assert record.status == "running"
    assert record.current_pass == "chapter_summary"


def test_progress_tracker_on_pass_done():
    from api.jobs import JobStore, ProgressTracker

    store = JobStore()
    job_id = store.create(total_passes=7)
    tracker = ProgressTracker(job_id=job_id, store=store)

    tracker.on_pass_start("chapter_summary")
    tracker.on_pass_done("chapter_summary")
    tracker.on_pass_start("new_entities")
    tracker.on_pass_done("new_entities")

    record = store.get(job_id)
    assert record.passes_done == 2
    assert record.current_pass is None


def test_job_store_thread_safety():
    from api.jobs import JobStore, ProgressTracker

    store = JobStore()
    job_id = store.create(total_passes=100)
    tracker = ProgressTracker(job_id=job_id, store=store)

    errors: list[Exception] = []

    def increment():
        try:
            for _ in range(10):
                tracker.on_pass_start("x")
                tracker.on_pass_done("x")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=increment) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    record = store.get(job_id)
    assert record.passes_done == 100  # 10 threads × 10 increments
