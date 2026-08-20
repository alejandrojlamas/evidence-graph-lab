from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from red_privada.graph.neo4j_graph import Neo4jGraph
from red_privada.models import AssertionType, EntityType, Neo4jConfig, ResolvedRelation


class RecordingDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def execute_query(self, query: str, **kwargs: Any) -> None:
        self.calls.append((query, kwargs))

    def close(self) -> None:
        self.closed = True


def test_init_creates_unique_assertion_constraint(monkeypatch) -> None:
    graph, driver = _graph(monkeypatch)

    queries = [query for query, _ in driver.calls]

    assert any(
        "CREATE CONSTRAINT assertion_id IF NOT EXISTS" in query
        and "FOR (a:Assertion) REQUIRE a.id IS UNIQUE" in query
        for query in queries
    )
    graph.close()
    assert driver.closed


def test_relation_upsert_reifies_assertion_and_preserves_provenance(monkeypatch) -> None:
    graph, driver = _graph(monkeypatch)
    relation = _relation()

    first_counts = graph.upsert_relations([relation])
    second_counts = graph.upsert_relations([relation])

    assert first_counts == (1, 1)
    assert second_counts == (1, 1)

    query, params = driver.calls[-1]
    normalized_query = " ".join(query.split())

    assert "MERGE (s)-[r:ASSERTS {id: rel.edge_id}]->(o)" in normalized_query
    assert "MERGE (a:Assertion {id: rel.edge_id})" in normalized_query
    assert "MERGE (a)-[:SUBJECT]->(s)" in normalized_query
    assert "MERGE (a)-[:OBJECT]->(o)" in normalized_query
    assert "MERGE (ev:Evidence {id: rel.evidence_id})" in normalized_query
    assert "MERGE (ev)-[:SUPPORTS]->(a)" in normalized_query
    assert "MERGE (ev)-[:FROM_DOCUMENT]->(d)" in normalized_query
    assert "SUPPORTS]->(r)" not in normalized_query

    payload = params["relations"][0]
    assert params["database_"] == "neo4j"
    assert payload["edge_id"] == relation.edge_id
    assert payload["evidence_id"] == relation.evidence_id
    assert payload["document_id"] == relation.document_id
    assert payload["quote"] == relation.quote
    assert payload["source_name"] == relation.source_name
    assert payload["source_side"] == relation.source_side
    assert payload["url"] == relation.url
    assert payload["assertion_type"] == "evidence"
    assert payload["subject_type"] == "person"
    assert payload["object_type"] == "organization"
    assert payload["published_at"] == "2026-06-08T12:00:00+00:00"


def _graph(monkeypatch) -> tuple[Neo4jGraph, RecordingDriver]:
    driver = RecordingDriver()
    monkeypatch.setattr(
        "red_privada.graph.neo4j_graph.GraphDatabase.driver",
        lambda *_args, **_kwargs: driver,
    )
    graph = Neo4jGraph(
        Neo4jConfig(
            uri="neo4j://localhost:7687",
            user="neo4j",
            password="test-password",
            database="neo4j",
        )
    )
    return graph, driver


def _relation() -> ResolvedRelation:
    return ResolvedRelation(
        edge_id="edge_ab",
        subject_id="ent_a",
        subject_name="A",
        subject_type=EntityType.person,
        predicate="CO_MENTIONED_WITH",
        object_id="ent_b",
        object_name="B",
        object_type=EntityType.organization,
        assertion_type=AssertionType.evidence,
        confidence=0.75,
        evidence_id="ev_ab",
        document_id="doc_1",
        quote="A y B aparecen en el mismo fragmento.",
        source_name="fuente",
        source_side="lado",
        url="https://example.org/documento",
        title="Documento",
        published_at=datetime(2026, 6, 8, 12, tzinfo=timezone.utc),
        extraction_method="test:fixture",
    )
