from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from red_privada.http import HTTPFetcher
from red_privada.models import AppConfig, RawDocument, SourceConfig

LOGGER = logging.getLogger(__name__)


class BaseCollector(ABC):
    def __init__(self, source: SourceConfig, fetcher: HTTPFetcher):
        self.source = source
        self.fetcher = fetcher

    @abstractmethod
    def collect(self) -> list[RawDocument]:
        raise NotImplementedError


def build_collectors(config: AppConfig, fetcher: HTTPFetcher) -> list[BaseCollector]:
    from red_privada.sources.gob_presidency import GobPresidencyCollector
    from red_privada.sources.rss_feed import RSSFeedCollector
    from red_privada.sources.web_column import WebColumnCollector

    collectors: list[BaseCollector] = []
    for source in config.sources:
        if not source.enabled:
            LOGGER.info("source disabled name=%s note=%s", source.name, source.note)
            continue
        if source.kind == "gob_presidency":
            collectors.append(GobPresidencyCollector(source, fetcher))
        elif source.kind == "rss_feed":
            collectors.append(RSSFeedCollector(source, fetcher))
        elif source.kind in {"sitemap_column", "listing_column"}:
            collectors.append(WebColumnCollector(source, fetcher))
        else:
            LOGGER.warning("unknown source kind name=%s kind=%s", source.name, source.kind)
    return collectors
