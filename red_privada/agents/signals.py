from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import networkx as nx

from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import (
    AppConfig,
    InvestigationSignals,
    MorningReading,
    RawDocument,
    SmallNoteSignal,
)
from red_privada.storage import SQLiteStore
from red_privada.text import normalize_name, parse_datetime, split_sentences

LOGGER = logging.getLogger(__name__)


class SignalsAgent:
    """Prioriza señales de baja atención, novedad estructural y lectura oficial."""

    def __init__(self, config: AppConfig, store: SQLiteStore, graph: SQLiteGraph):
        self.config = config
        self.signals_config = config.signals
        self.store = store
        self.graph = graph
        self.output_dir = Path(config.project.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.source_attention = {
            source.name: source.attention_weight
            for source in config.sources
        }

    def run(self) -> InvestigationSignals:
        documents = self.store.list_documents()
        nx_graph = self.graph.to_networkx()
        betweenness = (
            nx.betweenness_centrality(nx_graph, normalized=True)
            if nx_graph.number_of_nodes() > 0
            else {}
        )
        signals = InvestigationSignals(
            small_notes=self._small_notes(documents, betweenness),
            morning_readings=self._morning_readings(documents, betweenness),
        )
        self._write_outputs(signals)
        LOGGER.info(
            "signals generated small_notes=%s morning_readings=%s",
            len(signals.small_notes),
            len(signals.morning_readings),
        )
        return signals

    def _small_notes(
        self,
        documents: list[RawDocument],
        betweenness: dict[str, float],
    ) -> list[SmallNoteSignal]:
        edge_counts = self.graph.evidence_counts_by_edge()
        window_start, window_end = self._window(
            documents,
            self.signals_config.novelty_window_days,
        )
        candidates: list[SmallNoteSignal] = []
        for document in documents:
            if not _in_window(_doc_dt(document), window_start, window_end):
                continue
            evidence = self.graph.evidence_for_document(document.id)
            if not evidence:
                continue
            attention = self._attention_score(document)
            low_attention = 1.0 - attention
            structural, components, selected = self._document_novelty(evidence, edge_counts, betweenness)
            score = clamp01(0.55 * structural + 0.45 * low_attention)
            if (
                low_attention < self.signals_config.low_attention_threshold
                or structural < self.signals_config.structural_novelty_threshold
                or score < self.signals_config.small_note_min_score
            ):
                continue
            candidates.append(
                SmallNoteSignal(
                    document_id=document.id,
                    title=document.title,
                    url=document.url,
                    source_name=document.source_name,
                    source_side=document.source_side,
                    published_at=document.published_at,
                    score=score,
                    attention_score=attention,
                    low_attention_score=low_attention,
                    structural_novelty_score=structural,
                    novelty_components=components,
                    evidence=[_evidence_payload(item) for item in selected[:5]],
                    rationale=(
                        "Documento de baja atención relativa con aristas raras o estructuralmente "
                        "novedosas. Es una pista de lectura, no una tesis."
                    ),
                )
            )
        candidates.sort(key=lambda signal: signal.score, reverse=True)
        return candidates[: self.signals_config.small_note_top_n]

    def _document_novelty(
        self,
        evidence: list[dict],
        edge_counts: dict[str, int],
        betweenness: dict[str, float],
    ) -> tuple[float, dict[str, float], list[dict]]:
        scored: list[tuple[float, dict[str, float], dict]] = []
        for item in evidence:
            edge_count = max(1, edge_counts.get(item["edge_id"], 1))
            rare_edge = 1.0 / math.sqrt(edge_count)
            source_sides = item.get("source_sides") or [item.get("source_side", "unknown")]
            cross_side = 1.0 if len(set(source_sides)) >= 2 else 0.0
            bridge_endpoint = clamp01(
                4.0
                * max(
                    betweenness.get(item.get("subject_id"), 0.0),
                    betweenness.get(item.get("object_id"), 0.0),
                )
            )
            predicate_specificity = 0.0 if item.get("predicate") == "CO_MENTIONED_WITH" else 1.0
            components = {
                "rare_edge": clamp01(rare_edge),
                "bridge_endpoint": bridge_endpoint,
                "cross_side_edge": cross_side,
                "predicate_specificity": predicate_specificity,
            }
            score = clamp01(
                0.45 * components["rare_edge"]
                + 0.30 * components["bridge_endpoint"]
                + 0.15 * components["cross_side_edge"]
                + 0.10 * components["predicate_specificity"]
            )
            scored.append((score, components, item))
        if not scored:
            return 0.0, {}, []
        scored.sort(key=lambda row: row[0], reverse=True)
        best_score, best_components, _ = scored[0]
        return best_score, best_components, [item for _, _, item in scored]

    def _morning_readings(
        self,
        documents: list[RawDocument],
        betweenness: dict[str, float],
    ) -> list[MorningReading]:
        window_start, window_end = self._window(
            documents,
            self.signals_config.morning_lookback_days,
        )
        entities = {entity["canonical_id"]: entity for entity in self.graph.all_entities()}
        official_docs = [
            doc
            for doc in documents
            if doc.source_side == self.signals_config.official_side
            and _doc_dt(doc)
            and _in_window(_doc_dt(doc), window_start, window_end)
        ]
        evidence_by_entity: dict[str, list[dict]] = defaultdict(list)
        for item in self.graph.all_evidence():
            if item.get("source_side") == self.signals_config.official_side:
                continue
            item_dt = _parse_dt(item.get("published_at"))
            if not _in_window(item_dt, window_start, window_end):
                continue
            evidence_by_entity[item["subject_id"]].append(item)
            evidence_by_entity[item["object_id"]].append(item)

        readings: list[MorningReading] = []
        for entity_id, evidence in evidence_by_entity.items():
            if len(evidence) < self.signals_config.min_non_official_mentions:
                continue
            entity = entities.get(entity_id)
            if not entity:
                continue
            aliases = [entity["canonical_name"], *entity.get("aliases", [])]
            mention_quotes, denial_quotes = self._official_quotes(official_docs, aliases)
            source_sides = sorted({item.get("source_side", "unknown") for item in evidence})
            if denial_quotes:
                status = "official_denial_or_correction"
                official_quotes = denial_quotes
            elif not mention_quotes:
                status = "possible_silence"
                official_quotes = []
            else:
                continue

            caveats = [
                "Lectura textual de ventana: no implica omision deliberada ni falsedad."
            ]
            if official_docs and _latest_doc_dt(official_docs) < _earliest_evidence_dt(evidence):
                caveats.append("La fuente oficial disponible precede a la mencion no oficial.")
            if not official_docs:
                caveats.append("No hay documentos oficiales en la ventana configurada.")

            readings.append(
                MorningReading(
                    entity_id=entity_id,
                    entity_name=entity["canonical_name"],
                    status=status,
                    score=self._morning_score(evidence, source_sides, betweenness.get(entity_id, 0.0), status),
                    source_sides=source_sides,
                    non_official_evidence_count=len(evidence),
                    official_mentions_count=len(mention_quotes),
                    window_start=window_start,
                    window_end=window_end,
                    non_official_evidence=[_evidence_payload(item) for item in evidence[:5]],
                    official_quotes=official_quotes[:5],
                    caveats=caveats,
                )
            )
        readings.sort(key=lambda reading: reading.score, reverse=True)
        return readings[: self.signals_config.morning_top_n]

    def _official_quotes(
        self,
        official_docs: list[RawDocument],
        aliases: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        mentions: list[dict[str, Any]] = []
        denials: list[dict[str, Any]] = []
        for document in official_docs:
            for sentence in split_sentences(document.text):
                if not any(_alias_in_text(sentence, alias) for alias in aliases):
                    continue
                payload = {
                    "document_id": document.id,
                    "title": document.title,
                    "url": document.url,
                    "published_at": document.published_at.isoformat() if document.published_at else None,
                    "quote": sentence,
                }
                mentions.append(payload)
                if self._has_denial_keyword(sentence):
                    denials.append(payload)
        return mentions, denials

    def _has_denial_keyword(self, text: str) -> bool:
        text_norm = normalize_name(text)
        return any(
            normalize_name(keyword) in text_norm
            for keyword in self.signals_config.denial_keywords
        )

    def _attention_score(self, document: RawDocument) -> float:
        weight = float(
            document.metadata.get(
                "source_attention_weight",
                self.source_attention.get(document.source_name, 0.7),
            )
            or 0.7
        )
        rank = (
            document.metadata.get("feed_rank")
            or document.metadata.get("listing_rank")
            or document.metadata.get("archive_rank")
            or 1
        )
        try:
            rank_number = max(1, int(rank))
        except (TypeError, ValueError):
            rank_number = 1
        rank_factor = 1.0 / (1.0 + max(0, rank_number - 1) / 5.0)
        return clamp01(weight * rank_factor)

    @staticmethod
    def _morning_score(
        evidence: list[dict],
        source_sides: list[str],
        betweenness: float,
        status: str,
    ) -> float:
        evidence_score = min(1.0, len(evidence) / 5.0)
        side_score = min(1.0, len(source_sides) / 2.0)
        bridge_score = clamp01(4.0 * betweenness)
        status_bonus = 0.15 if status == "official_denial_or_correction" else 0.0
        return clamp01(0.35 * evidence_score + 0.30 * side_score + 0.20 * bridge_score + status_bonus)

    @staticmethod
    def _window(documents: list[RawDocument], days: int) -> tuple[datetime | None, datetime | None]:
        dates = sorted(_doc_dt(document) for document in documents if _doc_dt(document))
        if not dates:
            return None, None
        end = dates[-1]
        return end - timedelta(days=days), end

    def _write_outputs(self, signals: InvestigationSignals) -> None:
        small_notes_path = self.output_dir / "small_notes.json"
        morning_path = self.output_dir / "morning_readings.json"
        combined_path = self.output_dir / "signals.json"
        small_notes_path.write_text(
            json.dumps([item.model_dump(mode="json") for item in signals.small_notes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        morning_path.write_text(
            json.dumps([item.model_dump(mode="json") for item in signals.morning_readings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        combined_path.write_text(signals.model_dump_json(indent=2), encoding="utf-8")


def _evidence_payload(item: dict) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "edge_id": item.get("edge_id"),
        "document_id": item.get("document_id"),
        "source_name": item.get("source_name"),
        "source_side": item.get("source_side"),
        "url": item.get("url"),
        "title": item.get("title"),
        "published_at": item.get("published_at"),
        "predicate": item.get("predicate"),
        "subject_id": item.get("subject_id"),
        "object_id": item.get("object_id"),
        "quote": item.get("quote"),
    }


def _alias_in_text(text: str, alias: str) -> bool:
    alias_norm = normalize_name(alias)
    if not alias_norm:
        return False
    text_norm = normalize_name(text)
    return re.search(rf"(^|\s){re.escape(alias_norm)}($|\s)", text_norm) is not None


def _doc_dt(document: RawDocument) -> datetime | None:
    return document.published_at or document.fetched_at


def _latest_doc_dt(documents: list[RawDocument]) -> datetime:
    return max(_doc_dt(document) for document in documents if _doc_dt(document))


def _earliest_evidence_dt(evidence: list[dict]) -> datetime:
    dates = [_parse_dt(item.get("published_at")) for item in evidence]
    return min(date for date in dates if date)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    return parse_datetime(value)


def _in_window(value: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
