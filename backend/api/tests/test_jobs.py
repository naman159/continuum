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


def test_pending_admission_is_atomic_bounded_and_released():
    from api.jobs import JobStore, JobQueueFull
    store = JobStore(max_active=3)
    barrier = threading.Barrier(10)
    accepted, rejected = [], []
    def reserve():
        barrier.wait()
        try:
            accepted.append(store.create(total_passes=0))
        except JobQueueFull:
            rejected.append(True)
    threads = [threading.Thread(target=reserve) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(accepted) == 3 and len(rejected) == 7
    store.mark_done(accepted[0], {})
    store.mark_error(accepted[1], 'failed')
    store.create(total_passes=0)
    store.create(total_passes=0)


def test_novel_reservation_blocks_duplicate_until_completion():
    import pytest
    from api.jobs import JobStore, NovelJobBusy
    store = JobStore()
    job = store.create(total_passes=0, novel_id='novel')
    with pytest.raises(NovelJobBusy):
        store.create(total_passes=0, novel_id='novel')
    store.mark_error(job, 'failed')
    assert store.create(total_passes=0, novel_id='novel')


def test_executor_rejection_releases_reservation(monkeypatch):
    import pytest
    from api import jobs
    store = jobs.JobStore(max_active=1)
    monkeypatch.setattr(jobs, '_job_store', store)
    class Reject:
        def submit(self, fn):
            raise RuntimeError('executor stopped')
    monkeypatch.setattr(jobs, '_executor', Reject())
    with pytest.raises(RuntimeError, match='executor stopped'):
        jobs.submit_job(novel_id='n', chapter_number=1, text='Story.')
    assert store.create(total_passes=0, novel_id='n')


def test_processing_failure_releases_capacity(monkeypatch):
    from api import jobs
    from pipeline import pipeline
    store = jobs.JobStore(max_active=1)
    monkeypatch.setattr(jobs, '_job_store', store)
    class Inline:
        def submit(self, fn):
            fn()
    monkeypatch.setattr(jobs, '_executor', Inline())
    def fail(**kw):
        raise RuntimeError('provider failed')
    monkeypatch.setattr(pipeline, 'analyze_chapter', fail)
    job = jobs.submit_job(novel_id='n', chapter_number=1, text='Story.')
    assert store.get(job).status == 'error'
    assert store.create(total_passes=0, novel_id='n')
