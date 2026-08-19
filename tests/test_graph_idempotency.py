from __future__ import annotations

from datetime import datetime, timezone

from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import AssertionType, CanonicalEntity, EntityType, ResolvedRelation


def test_sqlite_graph_is_idempotent(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    entities = [
        CanonicalEntity(
            canonical_id="ent_a",
            canonical_name="A",
            entity_type=EntityType.person,
            aliases=["A"],
            resolution_reason="test",
            confidence=1.0,
        ),
        CanonicalEntity(
            canonical_id="ent_b",
            canonical_name="B",
            entity_type=EntityType.organization,
            aliases=["B"],
            resolution_reason="test",
            confidence=1.0,
        ),
    ]
    relation = ResolvedRelation(
        edge_id="edge_ab",
        subject_id="ent_a",
        subject_name="A",
        subject_type=EntityType.person,
        predicate="CO_MENTIONED_WITH",
        object_id="ent_b",
        object_name="B",
        object_type=EntityType.organization,
        assertion_type=AssertionType.evidence,
        confidence=0.5,
        evidence_id="ev_ab",
        document_id="doc_1",
        quote="A and B appear in the same excerpt.",
        source_name="source",
        source_side="side",
        url="https://example.org/doc",
        title="Doc",
        published_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
        extraction_method="test",
    )

    graph.upsert_entities(entities)
    graph.upsert_relations([relation])
    graph.upsert_entities(entities)
    graph.upsert_relations([relation])

    assert graph.counts() == {"entities": 2, "edges": 1, "evidence": 1}
