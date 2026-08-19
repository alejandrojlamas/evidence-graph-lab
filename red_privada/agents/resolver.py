from __future__ import annotations

import difflib
import logging

from red_privada.models import (
    AppConfig,
    CanonicalEntity,
    DocumentExtraction,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    RawDocument,
    ResolvedRelation,
)
from red_privada.text import normalize_name, stable_hash

LOGGER = logging.getLogger(__name__)


class EntityResolver:
    def __init__(self, config: AppConfig):
        self.config = config
        self.entities: dict[str, CanonicalEntity] = {}
        self.alias_to_id: dict[tuple[str, EntityType], str] = {}
        self._embedding_model = None
        self._seed_aliases()

    def resolve_extractions(
        self,
        documents: list[RawDocument],
        extractions: list[DocumentExtraction],
    ) -> tuple[list[CanonicalEntity], list[ResolvedRelation]]:
        documents_by_id = {document.id: document for document in documents}
        for extraction in extractions:
            for entity in extraction.entities:
                self.resolve_entity(entity)
            for relation in extraction.relations:
                self.resolve_entity(
                    ExtractedEntity(
                        name=relation.subject,
                        entity_type=relation.subject_type,
                        quote=relation.quote,
                        confidence=relation.confidence,
                    )
                )
                self.resolve_entity(
                    ExtractedEntity(
                        name=relation.object,
                        entity_type=relation.object_type,
                        quote=relation.quote,
                        confidence=relation.confidence,
                    )
                )

        resolved_relations: list[ResolvedRelation] = []
        for extraction in extractions:
            document = documents_by_id.get(extraction.document_id)
            if not document:
                LOGGER.warning("missing document for extraction document_id=%s", extraction.document_id)
                continue
            for relation in extraction.relations:
                resolved_relations.append(self.resolve_relation(document, extraction, relation))
        return list(self.entities.values()), resolved_relations

    def resolve_entity(self, entity: ExtractedEntity) -> CanonicalEntity:
        norm = normalize_name(entity.name)
        key = (norm, entity.entity_type)
        if key in self.alias_to_id:
            canonical = self.entities[self.alias_to_id[key]]
            self._add_alias(canonical, entity.name)
            return canonical

        candidate = self._best_candidate(norm, entity.entity_type)
        if candidate:
            canonical, score = candidate
            self._add_alias(canonical, entity.name)
            LOGGER.info(
                "resolved alias=%s canonical=%s score=%.3f",
                entity.name,
                canonical.canonical_name,
                score,
            )
            return canonical

        canonical_name = entity.name
        canonical_id = "ent_" + stable_hash(entity.entity_type.value, normalize_name(canonical_name), length=24)
        canonical = CanonicalEntity(
            canonical_id=canonical_id,
            canonical_name=canonical_name,
            entity_type=entity.entity_type,
            aliases=[entity.name],
            resolution_reason="new_entity",
            confidence=entity.confidence,
        )
        self.entities[canonical_id] = canonical
        self.alias_to_id[key] = canonical_id
        return canonical

    def resolve_relation(
        self,
        document: RawDocument,
        extraction: DocumentExtraction,
        relation: ExtractedRelation,
    ) -> ResolvedRelation:
        subject = self.resolve_entity(
            ExtractedEntity(
                name=relation.subject,
                entity_type=relation.subject_type,
                quote=relation.quote,
                confidence=relation.confidence,
            )
        )
        obj = self.resolve_entity(
            ExtractedEntity(
                name=relation.object,
                entity_type=relation.object_type,
                quote=relation.quote,
                confidence=relation.confidence,
            )
        )
        edge_id = "edge_" + stable_hash(
            subject.canonical_id,
            relation.predicate,
            obj.canonical_id,
            relation.assertion_type.value,
            length=28,
        )
        evidence_id = "ev_" + stable_hash(edge_id, document.id, relation.quote, length=28)
        return ResolvedRelation(
            edge_id=edge_id,
            subject_id=subject.canonical_id,
            subject_name=subject.canonical_name,
            subject_type=subject.entity_type,
            predicate=relation.predicate,
            object_id=obj.canonical_id,
            object_name=obj.canonical_name,
            object_type=obj.entity_type,
            assertion_type=relation.assertion_type,
            confidence=relation.confidence,
            evidence_id=evidence_id,
            document_id=document.id,
            quote=relation.quote,
            source_name=document.source_name,
            source_side=document.source_side,
            url=document.url,
            title=document.title,
            published_at=document.published_at,
            extraction_method=f"{extraction.provider}:{extraction.model}",
        )

    def _seed_aliases(self) -> None:
        for known in self.config.known_entities:
            entity = ExtractedEntity(
                name=known.name,
                entity_type=known.entity_type,
                quote=known.name,
                confidence=1.0,
            )
            canonical = self.resolve_entity(entity)
            canonical.resolution_reason = "configured_known_entity"
            canonical.confidence = 1.0
            for alias in known.aliases:
                self._add_alias(canonical, alias)

        for canonical_name, aliases in self.config.resolver.aliases.items():
            matched = self._find_by_name(canonical_name)
            if not matched:
                continue
            for alias in aliases:
                self._add_alias(matched, alias)

    def _find_by_name(self, name: str) -> CanonicalEntity | None:
        norm = normalize_name(name)
        for (alias, _type), canonical_id in self.alias_to_id.items():
            if alias == norm:
                return self.entities[canonical_id]
        return None

    def _add_alias(self, canonical: CanonicalEntity, alias: str) -> None:
        if alias not in canonical.aliases:
            canonical.aliases.append(alias)
        self.alias_to_id[(normalize_name(alias), canonical.entity_type)] = canonical.canonical_id

    def _best_candidate(
        self,
        norm: str,
        entity_type: EntityType,
    ) -> tuple[CanonicalEntity, float] | None:
        tokens = norm.split()
        if len(tokens) < 2:
            return None
        best: tuple[CanonicalEntity, float] | None = None
        for canonical in self.entities.values():
            if canonical.entity_type != entity_type:
                continue
            for alias in canonical.aliases:
                alias_norm = normalize_name(alias)
                if len(alias_norm.split()) < 2:
                    continue
                score = difflib.SequenceMatcher(None, norm, alias_norm).ratio()
                if score >= self.config.resolver.similarity_threshold:
                    if not best or score > best[1]:
                        best = (canonical, score)
        return best

