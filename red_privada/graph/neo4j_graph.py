from __future__ import annotations

from neo4j import GraphDatabase

from red_privada.models import CanonicalEntity, Neo4jConfig, ResolvedRelation


class Neo4jGraph:
    def __init__(self, config: Neo4jConfig):
        self.config = config
        self.driver = GraphDatabase.driver(config.uri, auth=(config.user, config.password))
        self.init()

    def close(self) -> None:
        self.driver.close()

    def init(self) -> None:
        queries = [
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT evidence_id IF NOT EXISTS FOR (ev:Evidence) REQUIRE ev.id IS UNIQUE",
        ]
        for query in queries:
            self.driver.execute_query(query, database_=self.config.database)

    def upsert_entities(self, entities: list[CanonicalEntity]) -> int:
        query = """
        UNWIND $entities AS entity
        MERGE (e:Entity {id: entity.canonical_id})
        SET e.name = entity.canonical_name,
            e.type = entity.entity_type,
            e.aliases = entity.aliases,
            e.resolution_reason = entity.resolution_reason,
            e.confidence = entity.confidence,
            e.updated_at = datetime()
        """
        payload = [
            {
                **entity.model_dump(mode="json"),
                "entity_type": entity.entity_type.value,
            }
            for entity in entities
        ]
        self.driver.execute_query(query, entities=payload, database_=self.config.database)
        return len(entities)

    def upsert_relations(self, relations: list[ResolvedRelation]) -> tuple[int, int]:
        query = """
        UNWIND $relations AS rel
        MERGE (s:Entity {id: rel.subject_id})
        SET s.name = rel.subject_name, s.type = rel.subject_type
        MERGE (o:Entity {id: rel.object_id})
        SET o.name = rel.object_name, o.type = rel.object_type
        MERGE (d:Document {id: rel.document_id})
        SET d.url = rel.url,
            d.title = rel.title,
            d.source_name = rel.source_name,
            d.source_side = rel.source_side,
            d.published_at = rel.published_at
        MERGE (s)-[r:ASSERTS {id: rel.edge_id}]->(o)
        SET r.predicate = rel.predicate,
            r.assertion_type = rel.assertion_type,
            r.confidence = CASE
                WHEN r.confidence IS NULL OR rel.confidence > r.confidence THEN rel.confidence
                ELSE r.confidence
            END,
            r.updated_at = datetime()
        MERGE (ev:Evidence {id: rel.evidence_id})
        SET ev.quote = rel.quote,
            ev.extraction_method = rel.extraction_method,
            ev.source_side = rel.source_side,
            ev.created_at = coalesce(ev.created_at, datetime())
        MERGE (ev)-[:SUPPORTS]->(r)
        MERGE (ev)-[:FROM_DOCUMENT]->(d)
        """
        payload = [
            {
                **relation.model_dump(mode="json"),
                "subject_type": relation.subject_type.value,
                "object_type": relation.object_type.value,
                "assertion_type": relation.assertion_type.value,
                "published_at": relation.published_at.isoformat() if relation.published_at else None,
            }
            for relation in relations
        ]
        self.driver.execute_query(query, relations=payload, database_=self.config.database)
        return len(relations), len(relations)

