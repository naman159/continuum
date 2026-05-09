from __future__ import annotations

from uuid import uuid4

from tests.api.conftest import make_chapter, make_novel


def test_continuity_resolved_filter(fake_db_factory, client):
    novel = make_novel()
    chap1 = make_chapter(novel["id"], 1)
    chap2 = make_chapter(novel["id"], 2)
    fake_db_factory(
        novels=[novel],
        chapters=[chap1, chap2],
        continuity_flags=[
            {
                "id": uuid4(),
                "chapter_id": chap1["id"],
                "description": "open flag",
                "flag_type": "foreshadowing",
                "resolved": False,
                "resolved_chapter_id": None,
            },
            {
                "id": uuid4(),
                "chapter_id": chap1["id"],
                "description": "resolved flag",
                "flag_type": "setup",
                "resolved": True,
                "resolved_chapter_id": chap2["id"],
            },
        ],
    )
    response = client.get(f"/api/novels/{novel['id']}/continuity?resolved=open")
    descriptions = [r["description"] for r in response.json()]
    assert descriptions == ["open flag"]
