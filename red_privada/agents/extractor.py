from __future__ import annotations

import logging

from red_privada.llm import build_extractor, sanitize_extraction
from red_privada.models import AppConfig, DocumentExtraction, RawDocument
from red_privada.storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


class ExtractorAgent:
    def __init__(self, config: AppConfig, store: SQLiteStore):
        self.config = config
        self.store = store
        self.extractor = build_extractor(config)

    def run(self, documents: list[RawDocument] | None = None) -> list[DocumentExtraction]:
        documents = documents if documents is not None else self.store.list_documents()
        extractions: list[DocumentExtraction] = []
        for document in documents:
            cached = self.store.get_extraction(document.id, document.text_hash)
            if cached and self.config.llm.cache_extractions:
                extractions.append(cached)
                LOGGER.info("using cached extraction document=%s", document.id)
                continue
            extraction = self.extractor.extract(document)
            extraction = sanitize_extraction(extraction, document)
            self.store.upsert_extraction(extraction)
            extractions.append(extraction)
            LOGGER.info(
                "extracted document=%s entities=%s relations=%s provider=%s",
                document.id,
                len(extraction.entities),
                len(extraction.relations),
                extraction.provider,
            )
        return extractions

