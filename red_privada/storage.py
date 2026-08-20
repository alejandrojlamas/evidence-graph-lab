from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from red_privada.models import DocumentExtraction, RawDocument
from red_privada.text import now_utc


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class SQLiteStore:
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
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_side TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    author TEXT,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw_html_path TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS extractions (
                    document_id TEXT PRIMARY KEY,
                    text_hash TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                );
                """
            )

    def upsert_document(self, document: RawDocument) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                  id, source_name, source_kind, source_side, url, title, author,
                  published_at, fetched_at, text_hash, text, raw_html_path, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  source_name=excluded.source_name,
                  source_kind=excluded.source_kind,
                  source_side=excluded.source_side,
                  url=excluded.url,
                  title=excluded.title,
                  author=excluded.author,
                  published_at=excluded.published_at,
                  fetched_at=excluded.fetched_at,
                  text_hash=excluded.text_hash,
                  text=excluded.text,
                  raw_html_path=excluded.raw_html_path,
                  metadata_json=excluded.metadata_json
                """,
                (
                    document.id,
                    document.source_name,
                    document.source_kind,
                    document.source_side,
                    document.url,
                    document.title,
                    document.author,
                    _dt(document.published_at),
                    _dt(document.fetched_at),
                    document.text_hash,
                    document.text,
                    document.raw_html_path,
                    json.dumps(document.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_documents(self) -> list[RawDocument]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM documents ORDER BY published_at DESC, id").fetchall()
        return [self._row_to_document(row) for row in rows]

    def get_document(self, document_id: str) -> RawDocument | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._row_to_document(row) if row else None

    def get_extraction(
        self,
        document_id: str,
        expected_hash: str,
        *,
        expected_provider: str,
        expected_model: str,
        expected_policy_fingerprint: str,
    ) -> DocumentExtraction | None:
        self._require_current_policy(expected_policy_fingerprint)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT document_id, text_hash, provider, model, payload_json
                FROM extractions
                WHERE document_id = ?
                """,
                (document_id,),
            ).fetchone()
        if not row:
            return None
        return self._validated_cached_extraction(
            row,
            expected_hash=expected_hash,
            expected_provider=expected_provider,
            expected_model=expected_model,
            expected_policy_fingerprint=expected_policy_fingerprint,
        )

    def upsert_extraction(self, extraction: DocumentExtraction) -> None:
        self._require_current_policy(extraction.cache_policy_fingerprint)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO extractions (
                  document_id, text_hash, provider, model, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                  text_hash=excluded.text_hash,
                  provider=excluded.provider,
                  model=excluded.model,
                  payload_json=excluded.payload_json,
                  created_at=excluded.created_at
                """,
                (
                    extraction.document_id,
                    extraction.text_hash,
                    extraction.provider,
                    extraction.model,
                    extraction.model_dump_json(),
                    now_utc().isoformat(),
                ),
            )

    def list_extractions(
        self,
        *,
        expected_provider: str,
        expected_model: str,
        expected_policy_fingerprint: str,
    ) -> list[DocumentExtraction]:
        self._require_current_policy(expected_policy_fingerprint)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  e.document_id,
                  e.text_hash,
                  e.provider,
                  e.model,
                  e.payload_json,
                  d.text_hash AS current_text_hash
                FROM extractions AS e
                JOIN documents AS d ON d.id = e.document_id
                ORDER BY e.document_id
                """
            ).fetchall()
        extractions: list[DocumentExtraction] = []
        for row in rows:
            extraction = self._validated_cached_extraction(
                row,
                expected_hash=row["current_text_hash"],
                expected_provider=expected_provider,
                expected_model=expected_model,
                expected_policy_fingerprint=expected_policy_fingerprint,
            )
            if extraction:
                extractions.append(extraction)
        return extractions

    @staticmethod
    def _require_current_policy(policy_fingerprint: str) -> None:
        if not policy_fingerprint.strip() or policy_fingerprint == "legacy":
            raise ValueError("la extracción no tiene una política de caché vigente")

    @staticmethod
    def _validated_cached_extraction(
        row: sqlite3.Row,
        *,
        expected_hash: str,
        expected_provider: str,
        expected_model: str,
        expected_policy_fingerprint: str,
    ) -> DocumentExtraction | None:
        if (
            row["text_hash"] != expected_hash
            or row["provider"] != expected_provider
            or row["model"] != expected_model
        ):
            return None
        try:
            extraction = DocumentExtraction.model_validate_json(row["payload_json"])
        except ValueError:
            return None
        if (
            extraction.document_id != row["document_id"]
            or extraction.text_hash != row["text_hash"]
            or extraction.provider != row["provider"]
            or extraction.model != row["model"]
            or extraction.cache_policy_fingerprint != expected_policy_fingerprint
        ):
            return None
        return extraction

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> RawDocument:
        return RawDocument(
            id=row["id"],
            source_name=row["source_name"],
            source_kind=row["source_kind"],
            source_side=row["source_side"],
            url=row["url"],
            title=row["title"],
            author=row["author"],
            published_at=datetime.fromisoformat(row["published_at"])
            if row["published_at"]
            else None,
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            text=row["text"],
            raw_html_path=row["raw_html_path"],
            metadata=json.loads(row["metadata_json"]),
        )
