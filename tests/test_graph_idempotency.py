from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from red_privada.agents.cartographer import CartographerAgent
from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import (
    AppConfig,
    AssertionType,
    CanonicalEntity,
    EntityType,
    GraphConfig,
    LLMConfig,
    ProjectConfig,
    RawDocument,
    ResolvedRelation,
    ResolverConfig,
)


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
    relation = _relation()

    graph.upsert_entities(entities)
    graph.upsert_relations([relation])
    graph.upsert_entities(entities)
    graph.upsert_relations([relation])

    assert graph.counts() == {"entities": 2, "edges": 1, "evidence": 1}


def test_reconciliation_replaces_corrected_relation_for_document(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    original = _relation()
    corrected = _relation(
        edge_id="edge_ac",
        evidence_id="ev_ac",
        object_id="ent_c",
        object_name="C",
        predicate="WORKED_WITH",
        quote="A worked with C.",
    )

    graph.upsert_relations([original], replace_document_ids={"doc_1"})
    graph.upsert_relations([corrected], replace_document_ids={"doc_1"})

    assert graph.counts() == {"entities": 0, "edges": 1, "evidence": 1}
    evidence = graph.all_evidence()
    assert len(evidence) == 1
    assert evidence[0]["evidence_id"] == "ev_ac"
    assert evidence[0]["edge_id"] == "edge_ac"
    assert evidence[0]["predicate"] == "WORKED_WITH"
    assert evidence[0]["quote"] == "A worked with C."
    assert evidence[0]["object_id"] == "ent_c"


def test_reconciliation_removes_relation_no_longer_present(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    graph.upsert_entities(_entities())
    graph.upsert_relations([_relation()], replace_document_ids={"doc_1"})

    graph.upsert_relations([], replace_document_ids={"doc_1"})

    assert graph.counts() == {"entities": 2, "edges": 0, "evidence": 0}


def test_reconciliation_rolls_back_document_replacement_on_failure(tmp_path, monkeypatch) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    original = _relation()
    corrected = _relation(
        edge_id="edge_ac",
        evidence_id="ev_ac",
        object_id="ent_c",
        object_name="C",
        predicate="WORKED_WITH",
        quote="A worked with C.",
    )
    graph.upsert_relations([original])

    def fail_reconciliation(_conn, _edge_ids) -> None:
        raise RuntimeError("simulated reconciliation failure")

    monkeypatch.setattr(graph, "_reconcile_affected_edges", fail_reconciliation)

    with pytest.raises(RuntimeError, match="simulated reconciliation failure"):
        graph.upsert_relations([corrected], replace_document_ids={"doc_1"})

    evidence = graph.all_evidence()
    assert [(item["edge_id"], item["evidence_id"]) for item in evidence] == [("edge_ab", "ev_ab")]


def test_reconciliation_keeps_edge_supported_by_another_document(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    first = _relation()
    second = _relation(
        evidence_id="ev_ab_doc_2",
        document_id="doc_2",
        quote="A and B also appear in a second excerpt.",
        source_name="other_source",
        source_side="other_side",
    )
    graph.upsert_relations(
        [first, second],
        replace_document_ids={"doc_1", "doc_2"},
    )

    graph.upsert_relations([], replace_document_ids={"doc_1"})

    assert graph.counts() == {"entities": 0, "edges": 1, "evidence": 1}
    remaining = graph.all_evidence()
    assert [item["document_id"] for item in remaining] == ["doc_2"]
    assert remaining[0]["edge_id"] == "edge_ab"
    assert remaining[0]["assertion_type"] == "evidence"


def test_reconciliation_downgrades_shared_edge_to_remaining_inference(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    direct_evidence = _relation(confidence=0.95)
    inference = _relation(
        evidence_id="ev_ab_doc_2",
        document_id="doc_2",
        quote="A may be related to B.",
        source_name="other_source",
        source_side="other_side",
        assertion_type=AssertionType.inference,
        confidence=0.2,
    )
    graph.upsert_relations(
        [direct_evidence, inference],
        replace_document_ids={"doc_1", "doc_2"},
    )

    graph.upsert_relations([], replace_document_ids={"doc_1"})

    payload = graph.evidence_for_entity("ent_a")[0]
    assert payload["assertion_type"] == "inference"
    assert payload["confidence"] == 0.2
    assert payload["edge_assertion_type"] == "inference"
    assert payload["edge_confidence"] == 0.2
    edge = graph.to_networkx().edges["ent_a", "ent_b"]
    assert edge["assertion_type"] == "inference"
    assert edge["confidence"] == 0.2


def test_legacy_evidence_schema_migrates_to_conservative_values(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite"
    _create_legacy_graph(database_path)

    graph = SQLiteGraph(database_path)

    payload = graph.all_evidence()[0]
    assert payload["assertion_type"] == "inference"
    assert payload["confidence"] == 0.0
    edge = graph.to_networkx().edges["ent_a", "ent_b"]
    assert edge["assertion_type"] == "inference"
    assert edge["confidence"] == 0.0


def test_cartographer_clears_old_evidence_when_document_has_no_valid_extraction(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    graph.upsert_entities(_entities())
    graph.upsert_relations([_relation()])
    document = _document()

    CartographerAgent(_config(tmp_path), graph).run([document], [])

    assert graph.counts() == {"entities": 2, "edges": 0, "evidence": 0}


def test_evidence_queries_expose_assertion_type(tmp_path) -> None:
    graph = SQLiteGraph(tmp_path / "graph.sqlite")
    relation = _relation(assertion_type=AssertionType.inference)
    graph.upsert_relations([relation])

    assert graph.evidence_for_entity("ent_a")[0]["assertion_type"] == "inference"
    assert graph.all_evidence()[0]["assertion_type"] == "inference"


def _entities() -> list[CanonicalEntity]:
    return [
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


def _relation(
    *,
    edge_id: str = "edge_ab",
    evidence_id: str = "ev_ab",
    document_id: str = "doc_1",
    object_id: str = "ent_b",
    object_name: str = "B",
    predicate: str = "CO_MENTIONED_WITH",
    quote: str = "A and B appear in the same excerpt.",
    source_name: str = "source",
    source_side: str = "side",
    assertion_type: AssertionType = AssertionType.evidence,
    confidence: float = 0.5,
) -> ResolvedRelation:
    return ResolvedRelation(
        edge_id=edge_id,
        subject_id="ent_a",
        subject_name="A",
        subject_type=EntityType.person,
        predicate=predicate,
        object_id=object_id,
        object_name=object_name,
        object_type=EntityType.organization,
        assertion_type=assertion_type,
        confidence=confidence,
        evidence_id=evidence_id,
        document_id=document_id,
        quote=quote,
        source_name=source_name,
        source_side=source_side,
        url=f"https://example.org/{document_id}",
        title=f"Doc {document_id}",
        published_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
        extraction_method="test",
    )


def _document() -> RawDocument:
    return RawDocument(
        id="doc_1",
        source_name="source",
        source_kind="test",
        source_side="side",
        url="https://example.org/doc_1",
        title="Doc doc_1",
        text="A and B appear in the same excerpt.",
        fetched_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
    )


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        project=ProjectConfig(
            user_agent="test",
            database_path=str(tmp_path / "graph.sqlite"),
            output_dir=str(tmp_path / "output"),
        ),
        llm=LLMConfig(provider="dev"),
        resolver=ResolverConfig(),
        graph=GraphConfig(),
    )


def _create_legacy_graph(path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE graph_edges (
              edge_id TEXT PRIMARY KEY,
              subject_id TEXT NOT NULL,
              predicate TEXT NOT NULL,
              object_id TEXT NOT NULL,
              assertion_type TEXT NOT NULL,
              confidence REAL NOT NULL,
              first_seen_at TEXT,
              last_seen_at TEXT,
              source_sides_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE graph_evidence (
              evidence_id TEXT PRIMARY KEY,
              edge_id TEXT NOT NULL,
              document_id TEXT NOT NULL,
              quote TEXT NOT NULL,
              source_name TEXT NOT NULL,
              source_side TEXT NOT NULL,
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              published_at TEXT,
              extraction_method TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            INSERT INTO graph_edges VALUES (
              'edge_ab', 'ent_a', 'WORKED_WITH', 'ent_b', 'evidence', 0.99,
              '2026-06-08T00:00:00+00:00', '2026-06-08T00:00:00+00:00',
              '["legacy_side"]', '2026-06-08T00:00:00+00:00'
            );
            INSERT INTO graph_evidence VALUES (
              'ev_ab', 'edge_ab', 'doc_1', 'A worked with B.', 'legacy_source',
              'legacy_side', 'https://example.org/doc_1', 'Legacy document',
              '2026-06-08T00:00:00+00:00', 'legacy:model',
              '2026-06-08T00:00:00+00:00'
            );
            """
        )
