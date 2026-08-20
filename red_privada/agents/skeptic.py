from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import networkx as nx

from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import (
    BridgeCandidate,
    IndependenceScore,
    NullModelScore,
    ScoringConfig,
    SkepticReport,
    SpecificityScore,
    TemporalAnomalyScore,
)


class SkepticAgent:
    """Aplica controles contra la apofenia antes de priorizar un puente del grafo."""

    def __init__(self, graph: SQLiteGraph, config: ScoringConfig):
        self.graph = graph
        self.config = config
        self.nx_graph = graph.to_networkx()
        self.all_evidence = graph.all_evidence()
        self._null_betweenness = self._build_null_betweenness()

    def review(self, candidate: BridgeCandidate) -> SkepticReport:
        evidence = self.graph.edge_evidence_for_entity(candidate.entity_id)
        independence = self._independence(candidate, evidence)
        null_model = self._null_model(candidate)
        specificity = self._specificity(candidate, evidence)
        temporal = self._temporal_anomaly(candidate, evidence)
        final_score = clamp01(
            0.30 * independence.score
            + 0.30 * null_model.score
            + 0.25 * specificity.score
            + 0.15 * temporal.score
        )
        status = self._status(final_score, independence, null_model, specificity)
        caveats = self._caveats(independence, null_model, specificity, temporal)
        return SkepticReport(
            candidate_id=candidate.entity_id,
            candidate_name=candidate.entity_name,
            final_score=final_score,
            status=status,
            independence=independence,
            null_model=null_model,
            specificity=specificity,
            temporal_anomaly=temporal,
            simplest_explanation=self._simplest_explanation(candidate, independence, specificity),
            caveats=caveats,
        )

    def _independence(
        self,
        candidate: BridgeCandidate,
        evidence: list[dict],
    ) -> IndependenceScore:
        units: dict[str, dict] = {}
        source_names: set[str] = set()
        sides: set[str] = set()
        documents: set[str] = set()
        for item in evidence:
            source_side = item.get("source_side") or "unknown"
            source_name = item.get("source_name") or "unknown"
            unit_key = f"{source_side}:{source_name}"
            units.setdefault(unit_key, item)
            source_names.add(source_name)
            sides.add(source_side)
            documents.add(item.get("document_id") or "")

        unit_count = len(units)
        source_count = len(source_names)
        side_count = len(sides)
        total = len(evidence)
        collapsed = max(0, total - unit_count)
        source_diversity = min(1.0, math.log2(unit_count + 1) / math.log2(4)) if unit_count else 0.0
        side_diversity = 1.0 if side_count >= 2 else (0.35 if source_count >= 2 else 0.15)
        document_diversity = min(1.0, len(documents) / 3) if documents else 0.0
        score = clamp01(0.45 * source_diversity + 0.45 * side_diversity + 0.10 * document_diversity)
        notes: list[str] = []
        if collapsed:
            notes.append(f"collapsed_correlated_evidence={collapsed}")
        if side_count < 2:
            notes.append("single_source_side")
        if unit_count < 2:
            notes.append("single_independent_unit")
        return IndependenceScore(
            score=score,
            passed=score >= self.config.independence_threshold and side_count >= 2,
            source_sides=sorted(sides),
            source_names=sorted(source_names),
            independent_units=sorted(units),
            collapsed_evidence_count=collapsed,
            total_evidence_count=total,
            notes=notes,
        )

    def _null_model(self, candidate: BridgeCandidate) -> NullModelScore:
        samples = self._null_betweenness.get(candidate.entity_id, [])
        observed = candidate.bridge_score
        if not samples:
            return NullModelScore(
                score=0.0,
                passed=False,
                observed_betweenness=observed,
                null_mean=0.0,
                null_std=0.0,
                p_value=1.0,
                z_score=0.0,
                samples=0,
                notes=["insufficient_graph_for_null_model"],
            )
        mean = statistics.fmean(samples)
        std = statistics.pstdev(samples)
        p_value = (sum(1 for value in samples if value >= observed) + 1) / (len(samples) + 1)
        z_score = (observed - mean) / std if std > 0 else (0.0 if observed <= mean else 10.0)
        score = clamp01(1.0 - p_value)
        notes = []
        if observed <= mean:
            notes.append("not_above_null_mean")
        if score < self.config.improbability_threshold:
            notes.append("below_improbability_threshold")
        return NullModelScore(
            score=score,
            passed=p_value <= self.config.null_model_alpha
            and score >= self.config.improbability_threshold,
            observed_betweenness=observed,
            null_mean=mean,
            null_std=std,
            p_value=p_value,
            z_score=z_score,
            samples=len(samples),
            notes=notes,
        )

    def _specificity(
        self,
        candidate: BridgeCandidate,
        evidence: list[dict],
    ) -> SpecificityScore:
        specificity_evidence = [item for item in evidence if _is_evidence_assertion(item)]
        predicates = [item.get("predicate", "") for item in specificity_evidence]
        non_comention_count = sum(1 for predicate in predicates if predicate != "CO_MENTIONED_WITH")
        predicate_score = (
            0.35 if not predicates else 0.35 + 0.45 * (non_comention_count / len(predicates))
        )
        type_score = {
            "person": 0.78,
            "organization": 0.72,
            "company": 0.72,
            "event": 0.74,
            "place": 0.48,
            "other": 0.10,
        }.get(
            str(
                candidate.entity_type.value
                if hasattr(candidate.entity_type, "value")
                else candidate.entity_type
            ),
            0.35,
        )
        quote_score = self._quote_specificity(specificity_evidence)
        degree_penalty = 1 / (1 + max(0, candidate.degree - 6) / 6)
        score = clamp01(
            0.35 * predicate_score + 0.25 * quote_score + 0.25 * type_score + 0.15 * degree_penalty
        )
        notes: list[str] = []
        excluded_inferences = len(evidence) - len(specificity_evidence)
        if excluded_inferences:
            notes.append(f"inference_assertions_excluded_from_specificity={excluded_inferences}")
        if non_comention_count == 0:
            notes.append("only_co_mentions")
        if (
            candidate.entity_type == "place"
            or getattr(candidate.entity_type, "value", "") == "place"
        ):
            notes.append("place_entities_are_often_broad")
        return SpecificityScore(
            score=score,
            passed=score >= self.config.specificity_threshold,
            predicate_score=clamp01(predicate_score),
            quote_score=quote_score,
            type_score=type_score,
            degree_penalty=degree_penalty,
            minimal_claim=(
                f"{candidate.entity_name} aparece como entidad puente entre "
                f"{', '.join(candidate.source_sides) or 'fuentes sin clasificar'}; "
                "la afirmación concreta requiere verificación humana."
            ),
            notes=notes,
        )

    def _temporal_anomaly(
        self,
        candidate: BridgeCandidate,
        evidence: list[dict],
    ) -> TemporalAnomalyScore:
        dates = sorted(
            dt for dt in (_parse_dt(item.get("published_at")) for item in evidence) if dt
        )
        corpus_dates = sorted(
            dt for dt in (_parse_dt(item.get("published_at")) for item in self.all_evidence) if dt
        )
        if len(dates) < 2 or not corpus_dates:
            return TemporalAnomalyScore(
                score=0.0,
                passed=False,
                window_days=self.config.temporal_window_days,
                max_window_evidence_count=len(dates),
                total_evidence_count=len(dates),
                corpus_span_days=0,
                lift_over_uniform=0.0,
                notes=["insufficient_temporal_evidence"],
            )

        corpus_span = max(1, (corpus_dates[-1].date() - corpus_dates[0].date()).days + 1)
        best_start = dates[0]
        best_end = dates[0] + timedelta(days=self.config.temporal_window_days)
        best_count = 0
        best_sides: set[str] = set()
        for start in dates:
            end = start + timedelta(days=self.config.temporal_window_days)
            window_items = [
                item
                for item in evidence
                if (dt := _parse_dt(item.get("published_at"))) and start <= dt <= end
            ]
            if len(window_items) > best_count:
                best_count = len(window_items)
                best_start = start
                best_end = end
                best_sides = {item.get("source_side", "unknown") for item in window_items}

        total = len(dates)
        observed_ratio = best_count / total
        if corpus_span <= self.config.temporal_window_days:
            lift = 1.0
            score = 0.15 if total >= 2 else 0.0
            notes = ["corpus_span_not_wider_than_window"]
        else:
            baseline_ratio = min(1.0, self.config.temporal_window_days / corpus_span)
            lift = observed_ratio / baseline_ratio if baseline_ratio else 0.0
            score = clamp01((lift - 1) / 3)
            notes = []
        if len(best_sides) >= 2:
            score = clamp01(score + 0.15)
            notes.append("multi_side_evidence_inside_window")
        return TemporalAnomalyScore(
            score=score,
            passed=score >= self.config.temporal_threshold,
            window_days=self.config.temporal_window_days,
            max_window_evidence_count=best_count,
            total_evidence_count=total,
            corpus_span_days=corpus_span,
            lift_over_uniform=lift,
            window_start=best_start,
            window_end=best_end,
            notes=notes,
        )

    def _build_null_betweenness(self) -> dict[str, list[float]]:
        node_ids = list(self.nx_graph.nodes())
        edge_count = self.nx_graph.number_of_edges()
        if len(node_ids) < 4 or edge_count < 2 or self.config.null_model_samples <= 0:
            return {}
        degree_sequence = [self.nx_graph.degree(node_id) for node_id in node_ids]
        samples_by_node: dict[str, list[float]] = defaultdict(list)
        for sample_index in range(self.config.null_model_samples):
            multi = nx.configuration_model(
                degree_sequence,
                seed=self.config.null_model_seed + sample_index,
            )
            random_graph = nx.Graph()
            random_graph.add_nodes_from(range(len(node_ids)))
            random_graph.add_edges_from((u, v) for u, v in multi.edges() if u != v)
            scores = nx.betweenness_centrality(random_graph, normalized=True)
            for idx, node_id in enumerate(node_ids):
                samples_by_node[node_id].append(scores.get(idx, 0.0))
        return dict(samples_by_node)

    @staticmethod
    def _quote_specificity(evidence: list[dict]) -> float:
        if not evidence:
            return 0.0
        scores = []
        for item in evidence:
            quote = item.get("quote") or ""
            has_number = any(char.isdigit() for char in quote)
            has_role = any(
                word in quote.lower()
                for word in ["director", "coordinador", "secretar", "gobernador", "president"]
            )
            proper_markers = sum(
                1 for token in quote.split() if token[:1].isupper() and len(token) > 3
            )
            length_score = 1.0 if 80 <= len(quote) <= 450 else 0.65
            scores.append(
                clamp01(
                    0.20
                    + (0.20 if has_number else 0.0)
                    + (0.20 if has_role else 0.0)
                    + min(0.30, proper_markers * 0.04)
                    + 0.10 * length_score
                )
            )
        return statistics.fmean(scores)

    def _status(
        self,
        final_score: float,
        independence: IndependenceScore,
        null_model: NullModelScore,
        specificity: SpecificityScore,
    ) -> str:
        if independence.score < 0.2 or specificity.score < 0.25:
            return "discard"
        if (
            independence.passed
            and null_model.passed
            and specificity.passed
            and final_score >= self.config.min_final_score
        ):
            return "promote_for_human_review"
        if final_score >= 0.35:
            return "needs_more_evidence"
        return "degraded"

    @staticmethod
    def _caveats(
        independence: IndependenceScore,
        null_model: NullModelScore,
        specificity: SpecificityScore,
        temporal: TemporalAnomalyScore,
    ) -> list[str]:
        caveats: list[str] = []
        if not independence.passed:
            caveats.append("La convergencia independiente no alcanza el umbral configurado.")
        if not null_model.passed:
            caveats.append("La intermediación no supera con claridad el modelo nulo.")
        if not specificity.passed:
            caveats.append("La afirmación todavía es demasiado amplia.")
        if not temporal.passed:
            caveats.append("No hay una anomalía temporal fuerte en esta ventana.")
        return caveats

    @staticmethod
    def _simplest_explanation(
        candidate: BridgeCandidate,
        independence: IndependenceScore,
        specificity: SpecificityScore,
    ) -> str:
        if len(independence.source_sides) >= 2:
            return (
                f"La explicación más simple es una convergencia temática pública alrededor de "
                f"{candidate.entity_name}; no implica coordinación ni causalidad."
            )
        return (
            f"La explicación más simple es la prominencia local de {candidate.entity_name} "
            "dentro de una misma familia de fuentes."
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_evidence_assertion(item: dict) -> bool:
    assertion_type = item.get("assertion_type", "evidence")
    return getattr(assertion_type, "value", assertion_type) == "evidence"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
