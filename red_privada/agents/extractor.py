from __future__ import annotations

import logging

from red_privada.llm import build_extractor, extraction_policy_fingerprint, sanitize_extraction
from red_privada.models import AppConfig, DocumentExtraction, RawDocument
from red_privada.storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


class ExtractorAgent:
    def __init__(self, config: AppConfig, store: SQLiteStore):
        self.config = config
        self.store = store
        self.extractor = build_extractor(config)
        self.cache_provider = self.extractor.provider
        self.cache_model = self.extractor.model
        self.cache_policy_fingerprint = extraction_policy_fingerprint(
            config,
            provider=self.cache_provider,
        )

    def run(self, documents: list[RawDocument] | None = None) -> list[DocumentExtraction]:
        documents = documents if documents is not None else self.store.list_documents()
        extractions: list[DocumentExtraction] = []
        for document in documents:
            cached = None
            if self.config.llm.cache_extractions:
                cached = self.store.get_extraction(
                    document.id,
                    document.text_hash,
                    expected_provider=self.cache_provider,
                    expected_model=self.cache_model,
                    expected_policy_fingerprint=self.cache_policy_fingerprint,
                )
            if cached:
                extractions.append(cached)
                LOGGER.info("usando extracción en caché document=%s", document.id)
                continue
            extraction = self.extractor.extract(document)
            extraction = sanitize_extraction(extraction, document)
            extraction = extraction.model_copy(
                update={
                    "provider": self.cache_provider,
                    "model": self.cache_model,
                    "cache_policy_fingerprint": self.cache_policy_fingerprint,
                }
            )
            self.store.upsert_extraction(extraction)
            extractions.append(extraction)
            LOGGER.info(
                "documento extraído=%s entities=%s relations=%s provider=%s",
                document.id,
                len(extraction.entities),
                len(extraction.relations),
                extraction.provider,
            )
        return extractions
