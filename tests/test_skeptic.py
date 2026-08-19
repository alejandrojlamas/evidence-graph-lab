from __future__ import annotations

from datetime import datetime, timezone

from red_privada.agents.skeptic import SkepticAgent
from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import (
    AssertionType,
    BridgeCandidate,
    CanonicalEntity,
    EntityType,
    ResolvedRelation,
    ScoringConfig,
)


def test_skeptic_collapses_correlated_evidence_and_scores_independence(tmp_path) -> None:
    graph = _scoring_graph(tmp_path)
    candidate = BridgeCandidate(
        entity_id="ent_x",
        entity_name="Mundial 2026",
        entity_type=EntityType.event,
        bridge_score=0.5,
        degree=2,
        source_sides=["official_government", "configured_column_b"],
        evidence=graph.evidence_for_entity("ent_x"),
    )
    report = SkepticAgent(
        graph,
        ScoringConfig(null_model_samples=12, temporal_window_days=2),
    ).review(candidate)

    assert report.independence.passed
    assert report.independence.collapsed_evidence_count == 1
    assert report.independence.source_sides == ["configured_column_b", "official_government"]
    assert report.null_model.samples == 12
    assert report.specificity.passed


def test_temporal_anomaly_uses_corpus_window(tmp_path) -> None:
    graph = _scoring_graph(tmp_path)
    candidate = BridgeCandidate(
        entity_id="ent_x",
        entity_name="Mundial 2026",
        entity_type=EntityType.event,
        bridge_score=0.5,
        degree=2,
        source_sides=["official_government", "configured_column_b"],
        evidence=graph.evidence_for_entity("ent_x"),
    )
    report = SkepticAgent(
        graph,
        ScoringConfig(null_model_samples=8, temporal_window_days=2),
    ).review(candidate)

    assert report.temporal_anomaly.total_evidence_count == 3
    assert report.temporal_anomaly.max_window_evidence_count >= 2
    assert report.temporal_anomaly.corpus_span_days >= 10
    assert report.final_score >= 0


def _scoring_graph(tmp_path) -> SQLiteGraph:
    graph = SQLiteGraph(tmp_path / "scoring.sqlite")
    graph.upsert_entities(
        [
            CanonicalEntity(
                canonical_id="ent_x",
                canonical_name="Mundial 2026",
                entity_type=EntityType.event,
                aliases=["Mundial"],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_a",
                canonical_name="Gobierno de México",
                entity_type=EntityType.organization,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_b",
                canonical_name="Ciudad de México",
                entity_type=EntityType.place,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_c",
                canonical_name="CNTE",
                entity_type=EntityType.organization,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
        ]
    )
    relations = [
        _relation(
            "edge_ax",
            "ent_a",
            "Gobierno de México",
            EntityType.organization,
            "ent_x",
            "Mundial 2026",
            EntityType.event,
            "ev_ax_1",
            "official_government",
            "presidencia_mananera",
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            "Gobierno de México coordina trabajos para el Mundial 2026.",
        ),
        _relation(
            "edge_ax",
            "ent_a",
            "Gobierno de México",
            EntityType.organization,
            "ent_x",
            "Mundial 2026",
            EntityType.event,
            "ev_ax_2",
            "official_government",
            "presidencia_mananera",
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            "Gabriela Cuevas habla del Mundial 2026 desde el Gobierno de México.",
        ),
        _relation(
            "edge_xb",
            "ent_x",
            "Mundial 2026",
            EntityType.event,
            "ent_b",
            "Ciudad de México",
            EntityType.place,
            "ev_xb",
            "configured_column_b",
            "milenio_trascendio",
            datetime(2026, 6, 2, tzinfo=timezone.utc),
            "El Mundial 2026 abre en el estadio Ciudad de México.",
        ),
        _relation(
            "edge_bc",
            "ent_b",
            "Ciudad de México",
            EntityType.place,
            "ent_c",
            "CNTE",
            EntityType.organization,
            "ev_bc",
            "configured_column_b",
            "milenio_trascendio",
            datetime(2026, 6, 10, tzinfo=timezone.utc),
            "La CNTE anuncia movilizaciones en Ciudad de México.",
        ),
    ]
    graph.upsert_relations(relations)
    return graph


def _relation(
    edge_id: str,
    subject_id: str,
    subject_name: str,
    subject_type: EntityType,
    object_id: str,
    object_name: str,
    object_type: EntityType,
    evidence_id: str,
    source_side: str,
    source_name: str,
    published_at: datetime,
    quote: str,
) -> ResolvedRelation:
    return ResolvedRelation(
        edge_id=edge_id,
        subject_id=subject_id,
        subject_name=subject_name,
        subject_type=subject_type,
        predicate="CO_MENTIONED_WITH",
        object_id=object_id,
        object_name=object_name,
        object_type=object_type,
        assertion_type=AssertionType.evidence,
        confidence=0.7,
        evidence_id=evidence_id,
        document_id=f"doc_{evidence_id}",
        quote=quote,
        source_name=source_name,
        source_side=source_side,
        url=f"https://example.org/{evidence_id}",
        title=evidence_id,
        published_at=published_at,
        extraction_method="test",
    )
