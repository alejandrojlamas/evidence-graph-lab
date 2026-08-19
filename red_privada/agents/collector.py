from __future__ import annotations

import logging

from red_privada.http import HTTPFetcher
from red_privada.models import AppConfig, RawDocument
from red_privada.sources import build_collectors
from red_privada.storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


class CollectorAgent:
    def __init__(self, config: AppConfig, store: SQLiteStore):
        self.config = config
        self.store = store

    def run(self) -> list[RawDocument]:
        if not any(source.enabled for source in self.config.sources):
            LOGGER.info("collector skipped because no network sources are enabled")
            return []
        fetcher = HTTPFetcher(
            cache_dir=self.config.project.cache_dir,
            user_agent=self.config.project.user_agent,
            request_delay_seconds=self.config.project.request_delay_seconds,
            allow_robots_unavailable=self.config.project.allow_robots_unavailable,
        )
        documents: list[RawDocument] = []
        try:
            for collector in build_collectors(self.config, fetcher):
                for document in collector.collect():
                    self.store.upsert_document(document)
                    documents.append(document)
        finally:
            fetcher.close()
        LOGGER.info("collector finished documents=%s", len(documents))
        return documents
