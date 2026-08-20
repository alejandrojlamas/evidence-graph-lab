from __future__ import annotations

import logging

from red_privada.agents.resolver import EntityResolver
from red_privada.graph import Neo4jGraph, SQLiteGraph
from red_privada.models import AppConfig, DocumentExtraction, RawDocument

LOGGER = logging.getLogger(__name__)


class CartographerAgent:
    def __init__(self, config: AppConfig, graph: SQLiteGraph | Neo4jGraph):
        self.config = config
        self.graph = graph
        self.resolver = EntityResolver(config)

    def run(
        self,
        documents: list[RawDocument],
        extractions: list[DocumentExtraction],
    ) -> dict[str, int]:
        entities, relations = self.resolver.resolve_extractions(documents, extractions)
        entity_count = self.graph.upsert_entities(entities)
        if isinstance(self.graph, SQLiteGraph):
            edge_count, evidence_count = self.graph.upsert_relations(
                relations,
                replace_document_ids={document.id for document in documents},
            )
        else:
            edge_count, evidence_count = self.graph.upsert_relations(relations)
        LOGGER.info(
            "cartografía terminada entities=%s edge_writes=%s evidence_writes=%s",
            entity_count,
            edge_count,
            evidence_count,
        )
        return {
            "entities": entity_count,
            "edge_writes": edge_count,
            "evidence_writes": evidence_count,
        }
