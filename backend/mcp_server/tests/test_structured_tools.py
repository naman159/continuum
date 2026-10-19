"""Intent routing contracts: focused lookups must not invoke semantic retrieval."""
from uuid import UUID

import pytest

from mcp_server import server

NID = "00000000-0000-0000-0000-000000000001"
CID = "00000000-0000-0000-0000-000000000002"
OID = "00000000-0000-0000-0000-000000000003"


@pytest.fixture(autouse=True)
def no_search_or_database(monkeypatch):
    monkeypatch.setattr(server.reads_db, "get_db", lambda: object())
    def forbidden(*args, **kwargs):
        pytest.fail("Structured lookup attempted semantic search")
    monkeypatch.setattr(server.search_reads, "search", forbidden)


def test_ownership_intent_passes_both_typed_filters_and_cutoff(monkeypatch):
    calls = []
    monkeypatch.setattr(server.knowledge_reads, "list_possession_edges",
                        lambda *args: calls.append(args) or [{"object_name": "Dagger"}])
    assert server.possessions(NID, 12, CID, OID)[0]["object_name"] == "Dagger"
    assert calls[0][1:] == (UUID(NID), 11, True, UUID(CID), UUID(OID))


def test_knowledge_intent_can_find_knowers_without_character_filter(monkeypatch):
    calls = []
    monkeypatch.setattr(server.knowledge_reads, "list_knows_edges",
                        lambda *args: calls.append(args) or [])
    assert server.known_facts(NID, 12, fact_contains="king is dead") == []
    assert calls[0][1:] == (UUID(NID), 11, None, "king is dead")


def test_locations_filter_subject_and_destination(monkeypatch):
    def locations(db, nid, cap, active, **filters):
        assert (nid, cap, active, filters) == (UUID(NID), 11, False, {"location_id": UUID(OID), "entity_name": "mira"})
        return [{"entity_name": "Mira"}]
    monkeypatch.setattr(server.knowledge_reads, "list_location_edges", locations)
    assert server.entity_locations(NID, 12, "mira", OID, False) == [{"entity_name": "Mira"}]


def test_world_detail_rejects_entity_missing_at_cutoff(monkeypatch):
    monkeypatch.setattr(server.world_reads, "list_objects", lambda *args: [])
    monkeypatch.setattr(server.world_reads, "get_object_detail",
                        lambda *args: pytest.fail("Future entity detail was read"))
    assert "error" in server.get_world_entity(NID, 2, "object", OID)


def test_entity_discovery_routes_custom_types(monkeypatch):
    calls = []
    monkeypatch.setattr(server.world_reads, "list_custom_entities",
                        lambda *args: calls.append(args) or [])
    assert server.list_story_entities(NID, 12, "spell") == []
    assert calls[0][1:] == (UUID(NID), "spell", 11)
