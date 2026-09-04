from __future__ import annotations

from dataclasses import dataclass

from reads import search as search_reads


@dataclass
class _R:
    kind: str = "chapter"
    chapter_number: int = 1
    score: float = 0.9
    snippet: str = "Aelric took the dagger"


class FakeRetriever:
    def __init__(self):
        self.last_query = None

    def retrieve(self, query):
        self.last_query = query
        return [_R()]


def test_search_applies_cutoff_and_shapes_results(db, seed_novel):
    seeded = seed_novel(db)
    fake = FakeRetriever()
    out = search_reads.search(
        db, seeded["novel_id"], "dagger", up_to_chapter=2, k=5, retriever=fake
    )
    assert out["results"][0]["snippet"] == "Aelric took the dagger"
    assert fake.last_query.max_chapter == 2
    assert fake.last_query.k == 5

    # None resolves to the novel's latest chapter (3 in the fixture).
    search_reads.search(db, seeded["novel_id"], "dagger", up_to_chapter=None, retriever=fake)
    assert fake.last_query.max_chapter == 3
