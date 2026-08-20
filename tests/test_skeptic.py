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
        entity_name="2026 World Cup",
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
        entity_name="2026 World Cup",
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


def test_inference_relation_gets_no_specificity_bonus_but_remains_reviewable(tmp_path) -> None:
    evidence_graph = _specificity_graph(tmp_path / "evidence.sqlite", AssertionType.evidence)
    inference_graph = _specificity_graph(tmp_path / "inference.sqlite", AssertionType.inference)
    candidate = BridgeCandidate(
        entity_id="ent_a",
        entity_name="Ana",
        entity_type=EntityType.person,
        bridge_score=0.0,
        degree=1,
        source_sides=["configured_column_b"],
        evidence=[],
    )

    evidence_items = evidence_graph.edge_evidence_for_entity("ent_a")
    inference_items = inference_graph.edge_evidence_for_entity("ent_a")
    evidence_specificity = SkepticAgent(evidence_graph, ScoringConfig())._specificity(
        candidate, evidence_items
    )
    inference_agent = SkepticAgent(inference_graph, ScoringConfig())
    inference_specificity = inference_agent._specificity(candidate, inference_items)

    assert evidence_items[0]["assertion_type"] == "evidence"
    assert inference_items[0]["assertion_type"] == "inference"
    assert evidence_specificity.predicate_score == 0.8
    assert inference_specificity.predicate_score == 0.35
    assert evidence_specificity.quote_score > 0
    assert inference_specificity.quote_score == 0
    assert "inference_assertions_excluded_from_specificity=1" in inference_specificity.notes
    assert inference_agent.review(candidate).candidate_id == "ent_a"


def _scoring_graph(tmp_path) -> SQLiteGraph:
    graph = SQLiteGraph(tmp_path / "scoring.sqlite")
    graph.upsert_entities(
        [
            CanonicalEntity(
                canonical_id="ent_x",
                canonical_name="2026 World Cup",
                entity_type=EntityType.event,
                aliases=["World Cup"],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_a",
                canonical_name="Government of Mexico",
                entity_type=EntityType.organization,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_b",
                canonical_name="Mexico City",
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
            "Government of Mexico",
            EntityType.organization,
            "ent_x",
            "2026 World Cup",
            EntityType.event,
            "ev_ax_1",
            "official_government",
            "presidencia_mananera",
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            "The Government of Mexico coordinates work for the 2026 World Cup.",
        ),
        _relation(
            "edge_ax",
            "ent_a",
            "Government of Mexico",
            EntityType.organization,
            "ent_x",
            "2026 World Cup",
            EntityType.event,
            "ev_ax_2",
            "official_government",
            "presidencia_mananera",
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            "Gabriela Cuevas discusses the 2026 World Cup for the Government of Mexico.",
        ),
        _relation(
            "edge_xb",
            "ent_x",
            "2026 World Cup",
            EntityType.event,
            "ent_b",
            "Mexico City",
            EntityType.place,
            "ev_xb",
            "configured_column_b",
            "milenio_trascendio",
            datetime(2026, 6, 2, tzinfo=timezone.utc),
            "The 2026 World Cup opens at the Mexico City stadium.",
        ),
        _relation(
            "edge_bc",
            "ent_b",
            "Mexico City",
            EntityType.place,
            "ent_c",
            "CNTE",
            EntityType.organization,
            "ev_bc",
            "configured_column_b",
            "milenio_trascendio",
            datetime(2026, 6, 10, tzinfo=timezone.utc),
            "CNTE announces demonstrations in Mexico City.",
        ),
    ]
    graph.upsert_relations(relations)
    return graph


def _specificity_graph(path, assertion_type: AssertionType) -> SQLiteGraph:
    graph = SQLiteGraph(path)
    graph.upsert_entities(
        [
            CanonicalEntity(
                canonical_id="ent_a",
                canonical_name="Ana",
                entity_type=EntityType.person,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_b",
                canonical_name="Luis",
                entity_type=EntityType.person,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
        ]
    )
    graph.upsert_relations(
        [
            _relation(
                "edge_ab",
                "ent_a",
                "Ana",
                EntityType.person,
                "ent_b",
                "Luis",
                EntityType.person,
                "ev_ab",
                "configured_column_b",
                "red_privada",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
                "Ana negó haber sobornado a Luis durante la reunión del 14 de mayo.",
                predicate="SOBORNO",
                assertion_type=assertion_type,
            )
        ]
    )
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
    predicate: str = "CO_MENTIONED_WITH",
    assertion_type: AssertionType = AssertionType.evidence,
) -> ResolvedRelation:
    return ResolvedRelation(
        edge_id=edge_id,
        subject_id=subject_id,
        subject_name=subject_name,
        subject_type=subject_type,
        predicate=predicate,
        object_id=object_id,
        object_name=object_name,
        object_type=object_type,
        assertion_type=assertion_type,
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
