from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import networkx as nx

from red_privada.models import CanonicalEntity, ResolvedRelation
from red_privada.text import now_utc


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class SQLiteGraph:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS graph_entities (
                  canonical_id TEXT PRIMARY KEY,
                  canonical_name TEXT NOT NULL,
                  entity_type TEXT NOT NULL,
                  aliases_json TEXT NOT NULL,
                  resolution_reason TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_edges (
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
                CREATE TABLE IF NOT EXISTS graph_evidence (
                  evidence_id TEXT PRIMARY KEY,
                  edge_id TEXT NOT NULL,
                  document_id TEXT NOT NULL,
                  assertion_type TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  quote TEXT NOT NULL,
                  source_name TEXT NOT NULL,
                  source_side TEXT NOT NULL,
                  url TEXT NOT NULL,
                  title TEXT NOT NULL,
                  published_at TEXT,
                  extraction_method TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
                );
                CREATE INDEX IF NOT EXISTS idx_graph_edges_subject ON graph_edges(subject_id);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_object ON graph_edges(object_id);
                CREATE INDEX IF NOT EXISTS idx_graph_evidence_edge ON graph_evidence(edge_id);
                """
            )
            evidence_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(graph_evidence)")
            }
            migrated_evidence_schema = False
            if "assertion_type" not in evidence_columns:
                conn.execute("ALTER TABLE graph_evidence ADD COLUMN assertion_type TEXT")
                migrated_evidence_schema = True
            if "confidence" not in evidence_columns:
                conn.execute("ALTER TABLE graph_evidence ADD COLUMN confidence REAL")
                migrated_evidence_schema = True
            legacy_values_missing = conn.execute(
                """
                SELECT 1
                FROM graph_evidence
                WHERE assertion_type IS NULL OR confidence IS NULL
                LIMIT 1
                """
            ).fetchone()
            if migrated_evidence_schema or legacy_values_missing:
                # Los registros heredados no conservaban estos atributos por evidencia. No es
                # seguro promoverlos a partir del agregado de la arista.
                conn.execute(
                    """
                    UPDATE graph_evidence
                    SET assertion_type = COALESCE(assertion_type, 'inference'),
                        confidence = COALESCE(confidence, 0.0)
                    """
                )
                legacy_edge_ids = {
                    row["edge_id"] for row in conn.execute("SELECT edge_id FROM graph_edges")
                }
                self._reconcile_affected_edges(conn, legacy_edge_ids)

    def upsert_entities(self, entities: list[CanonicalEntity]) -> int:
        with self.connect() as conn:
            for entity in entities:
                conn.execute(
                    """
                    INSERT INTO graph_entities (
                      canonical_id, canonical_name, entity_type, aliases_json,
                      resolution_reason, confidence, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_id) DO UPDATE SET
                      canonical_name=excluded.canonical_name,
                      entity_type=excluded.entity_type,
                      aliases_json=excluded.aliases_json,
                      resolution_reason=excluded.resolution_reason,
                      confidence=excluded.confidence,
                      updated_at=excluded.updated_at
                    """,
                    (
                        entity.canonical_id,
                        entity.canonical_name,
                        entity.entity_type.value,
                        json.dumps(sorted(set(entity.aliases)), ensure_ascii=False),
                        entity.resolution_reason,
                        entity.confidence,
                        now_utc().isoformat(),
                    ),
                )
        return len(entities)

    def upsert_relations(
        self,
        relations: list[ResolvedRelation],
        *,
        replace_document_ids: set[str] | None = None,
    ) -> tuple[int, int]:
        """Escribe relaciones y, opcionalmente, reemplaza el corte completo por documento.

        ``replace_document_ids`` distingue la reconciliación de la API aditiva usada por llamadas
        de bajo nivel. Al proporcionarlo, la evidencia atribuida previamente a esos documentos se
        elimina en la misma transacción antes de escribir las relaciones vigentes. Las aristas
        conservan el soporte de documentos ajenos al reemplazo y solo se eliminan cuando ya no
        queda evidencia.
        """
        replacement_ids = None
        if replace_document_ids is not None:
            replacement_ids = set(replace_document_ids)
            relation_document_ids = {relation.document_id for relation in relations}
            unexpected_ids = relation_document_ids - replacement_ids
            if unexpected_ids:
                unexpected = ", ".join(sorted(unexpected_ids))
                raise ValueError(
                    f"las relaciones incluyen documentos fuera del lote de reemplazo: {unexpected}"
                )

        edges_written = 0
        with self.connect() as conn:
            if replacement_ids is not None:
                conn.execute("BEGIN IMMEDIATE")

            existing_evidence_ids = {
                relation.evidence_id
                for relation in relations
                if conn.execute(
                    "SELECT 1 FROM graph_evidence WHERE evidence_id = ?",
                    (relation.evidence_id,),
                ).fetchone()
            }
            affected_edge_ids = {relation.edge_id for relation in relations}

            if replacement_ids is not None:
                for document_id in sorted(replacement_ids):
                    rows = conn.execute(
                        "SELECT DISTINCT edge_id FROM graph_evidence WHERE document_id = ?",
                        (document_id,),
                    ).fetchall()
                    affected_edge_ids.update(row["edge_id"] for row in rows)
                    conn.execute(
                        "DELETE FROM graph_evidence WHERE document_id = ?",
                        (document_id,),
                    )

            for relation in relations:
                existing = conn.execute(
                    "SELECT source_sides_json, first_seen_at FROM graph_edges WHERE edge_id = ?",
                    (relation.edge_id,),
                ).fetchone()
                sides = {relation.source_side}
                first_seen_at = _dt(relation.published_at)
                if existing:
                    sides.update(json.loads(existing["source_sides_json"]))
                    first_seen_at = existing["first_seen_at"] or first_seen_at
                conn.execute(
                    """
                    INSERT INTO graph_edges (
                      edge_id, subject_id, predicate, object_id, assertion_type,
                      confidence, first_seen_at, last_seen_at, source_sides_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(edge_id) DO UPDATE SET
                      confidence=max(graph_edges.confidence, excluded.confidence),
                      first_seen_at=COALESCE(graph_edges.first_seen_at, excluded.first_seen_at),
                      last_seen_at=excluded.last_seen_at,
                      source_sides_json=excluded.source_sides_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        relation.edge_id,
                        relation.subject_id,
                        relation.predicate,
                        relation.object_id,
                        relation.assertion_type.value,
                        relation.confidence,
                        first_seen_at,
                        _dt(relation.published_at),
                        json.dumps(sorted(sides), ensure_ascii=False),
                        now_utc().isoformat(),
                    ),
                )
                edges_written += 1
                conn.execute(
                    """
                    INSERT INTO graph_evidence (
                      evidence_id, edge_id, document_id, assertion_type, confidence,
                      quote, source_name, source_side, url, title, published_at,
                      extraction_method, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                      edge_id=excluded.edge_id,
                      document_id=excluded.document_id,
                      assertion_type=excluded.assertion_type,
                      confidence=excluded.confidence,
                      quote=excluded.quote,
                      source_name=excluded.source_name,
                      source_side=excluded.source_side,
                      url=excluded.url,
                      title=excluded.title,
                      published_at=excluded.published_at,
                      extraction_method=excluded.extraction_method
                    """,
                    (
                        relation.evidence_id,
                        relation.edge_id,
                        relation.document_id,
                        relation.assertion_type.value,
                        relation.confidence,
                        relation.quote,
                        relation.source_name,
                        relation.source_side,
                        relation.url,
                        relation.title,
                        _dt(relation.published_at),
                        relation.extraction_method,
                        now_utc().isoformat(),
                    ),
                )
            self._reconcile_affected_edges(conn, affected_edge_ids)

            # Preserve the historical meaning of evidence_writes: newly observed evidence IDs,
            # not rows reinserted as part of an otherwise idempotent replacement.
            evidence_written = len(
                {relation.evidence_id for relation in relations} - existing_evidence_ids
            )
        return edges_written, evidence_written

    @staticmethod
    def _reconcile_affected_edges(
        conn: sqlite3.Connection,
        edge_ids: set[str],
    ) -> None:
        for edge_id in sorted(edge_ids):
            evidence = conn.execute(
                """
                SELECT document_id, source_side, published_at, assertion_type, confidence
                FROM graph_evidence
                WHERE edge_id = ?
                """,
                (edge_id,),
            ).fetchall()
            if not evidence:
                conn.execute("DELETE FROM graph_edges WHERE edge_id = ?", (edge_id,))
                continue

            published = sorted(
                row["published_at"] for row in evidence if row["published_at"] is not None
            )
            assertion_type = (
                "evidence"
                if any(row["assertion_type"] == "evidence" for row in evidence)
                else "inference"
            )
            confidence = max(float(row["confidence"] or 0.0) for row in evidence)
            conn.execute(
                """
                UPDATE graph_edges
                SET assertion_type = ?,
                    confidence = ?,
                    first_seen_at = ?,
                    last_seen_at = ?,
                    source_sides_json = ?,
                    updated_at = ?
                WHERE edge_id = ?
                """,
                (
                    assertion_type,
                    confidence,
                    published[0] if published else None,
                    published[-1] if published else None,
                    json.dumps(
                        sorted({row["source_side"] for row in evidence}),
                        ensure_ascii=False,
                    ),
                    now_utc().isoformat(),
                    edge_id,
                ),
            )

    def to_networkx(self) -> nx.Graph:
        graph = nx.Graph()
        with self.connect() as conn:
            entities = conn.execute("SELECT * FROM graph_entities").fetchall()
            for row in entities:
                graph.add_node(
                    row["canonical_id"],
                    name=row["canonical_name"],
                    entity_type=row["entity_type"],
                    aliases=json.loads(row["aliases_json"]),
                )
            edges = conn.execute("SELECT * FROM graph_edges").fetchall()
            for row in edges:
                graph.add_edge(
                    row["subject_id"],
                    row["object_id"],
                    edge_id=row["edge_id"],
                    predicate=row["predicate"],
                    assertion_type=row["assertion_type"],
                    source_sides=json.loads(row["source_sides_json"]),
                    confidence=row["confidence"],
                )
        return graph

    def evidence_for_entity(self, entity_id: str, limit: int = 8) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  ev.*,
                  ge.predicate,
                  ge.assertion_type AS edge_assertion_type,
                  ge.confidence AS edge_confidence,
                  ge.subject_id,
                  ge.object_id
                FROM graph_evidence ev
                JOIN graph_edges ge ON ge.edge_id = ev.edge_id
                WHERE ge.subject_id = ? OR ge.object_id = ?
                ORDER BY ev.published_at DESC, ev.evidence_id
                LIMIT ?
                """,
                (entity_id, entity_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def all_evidence(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  ev.*,
                  ge.predicate,
                  ge.assertion_type AS edge_assertion_type,
                  ge.confidence AS edge_confidence,
                  ge.subject_id,
                  ge.object_id
                FROM graph_evidence ev
                JOIN graph_edges ge ON ge.edge_id = ev.edge_id
                ORDER BY ev.published_at, ev.evidence_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def all_entities(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM graph_entities ORDER BY canonical_name").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["aliases"] = json.loads(item.pop("aliases_json"))
            result.append(item)
        return result

    def evidence_for_document(self, document_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  ev.*,
                  ge.predicate,
                  ge.assertion_type AS edge_assertion_type,
                  ge.confidence AS edge_confidence,
                  ge.subject_id,
                  ge.object_id,
                  ge.source_sides_json,
                  s.canonical_name AS subject_name,
                  s.entity_type AS subject_type,
                  o.canonical_name AS object_name,
                  o.entity_type AS object_type
                FROM graph_evidence ev
                JOIN graph_edges ge ON ge.edge_id = ev.edge_id
                JOIN graph_entities s ON s.canonical_id = ge.subject_id
                JOIN graph_entities o ON o.canonical_id = ge.object_id
                WHERE ev.document_id = ?
                ORDER BY ev.published_at DESC, ev.evidence_id
                """,
                (document_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_sides"] = json.loads(item.pop("source_sides_json"))
            result.append(item)
        return result

    def evidence_counts_by_edge(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT edge_id, COUNT(*) AS evidence_count
                FROM graph_evidence
                GROUP BY edge_id
                """
            ).fetchall()
        return {row["edge_id"]: row["evidence_count"] for row in rows}

    def edge_evidence_for_entity(self, entity_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  ev.*,
                  ge.predicate,
                  ge.assertion_type AS edge_assertion_type,
                  ge.confidence AS edge_confidence,
                  ge.subject_id,
                  ge.object_id,
                  s.canonical_name AS subject_name,
                  s.entity_type AS subject_type,
                  o.canonical_name AS object_name,
                  o.entity_type AS object_type
                FROM graph_evidence ev
                JOIN graph_edges ge ON ge.edge_id = ev.edge_id
                JOIN graph_entities s ON s.canonical_id = ge.subject_id
                JOIN graph_entities o ON o.canonical_id = ge.object_id
                WHERE ge.subject_id = ? OR ge.object_id = ?
                ORDER BY ev.published_at DESC, ev.evidence_id
                """,
                (entity_id, entity_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "entities": conn.execute("SELECT COUNT(*) FROM graph_entities").fetchone()[0],
                "edges": conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0],
                "evidence": conn.execute("SELECT COUNT(*) FROM graph_evidence").fetchone()[0],
            }
