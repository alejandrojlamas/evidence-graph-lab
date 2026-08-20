from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from red_privada.models import RawDocument, SourceConfig
from red_privada.sources.base import BaseCollector
from red_privada.text import (
    extract_article_text,
    extract_meta,
    normalize_ws,
    now_utc,
    parse_datetime,
    unwrap_gob_archive_fragment,
)

LOGGER = logging.getLogger(__name__)


class GobPresidencyCollector(BaseCollector):
    BASE_URL = "https://www.gob.mx"

    def __init__(self, source: SourceConfig, fetcher):
        if not source.archive_url:
            raise ValueError("la fuente gob_presidency requiere archive_url")
        super().__init__(source, fetcher)

    def collect(self) -> list[RawDocument]:
        urls = self._discover_urls()
        documents: list[RawDocument] = []
        for rank, item in enumerate(urls[: self.source.limit], start=1):
            try:
                document = self._fetch_article(item, rank)
            except Exception as exc:  # pragma: no cover - logged integration boundary
                LOGGER.warning("falló la descarga del artículo url=%s error=%s", item["url"], exc)
                continue
            documents.append(document)
        return documents

    def _discover_urls(self) -> list[dict[str, str]]:
        discovered: list[dict[str, str]] = []
        seen: set[str] = set()
        for page in range(1, self.source.max_pages + 1):
            archive_url = self.source.archive_url.format(page=page)
            html, _, _ = self.fetcher.fetch(archive_url)
            soup = BeautifulSoup(unwrap_gob_archive_fragment(html), "html.parser")
            for article in soup.find_all("article"):
                title_node = article.find("h2")
                link_node = article.find("a", href=True)
                time_node = article.find("time")
                if not title_node or not link_node:
                    continue
                title = normalize_ws(title_node.get_text(" "))
                if self.source.title_include and self.source.title_include not in title:
                    continue
                url = urljoin(self.BASE_URL, link_node["href"])
                if url in seen:
                    continue
                seen.add(url)
                discovered.append(
                    {
                        "title": title,
                        "url": url,
                        "published_at": time_node.get("date", "") if time_node else "",
                    }
                )
        LOGGER.info("fuente gubernamental=%s descubiertos=%s", self.source.name, len(discovered))
        return discovered

    def _fetch_article(self, item: dict[str, str], rank: int) -> RawDocument:
        html, path, cached = self.fetcher.fetch(item["url"])
        text, extraction_meta = extract_article_text(html)
        if len(text) < 300:
            raise ValueError(f"el texto del artículo es demasiado breve: {item['url']}")
        meta = extract_meta(html)
        title = item.get("title") or meta.get("og:title") or meta.get("title") or item["url"]
        published_at = parse_datetime(item.get("published_at")) or parse_datetime(
            meta.get("article:published_time") or meta.get("fecha_publicacion")
        )
        document = RawDocument(
            id=RawDocument.id_for(self.source.name, item["url"]),
            source_name=self.source.name,
            source_kind=self.source.kind,
            source_side=self.source.side,
            url=item["url"],
            title=title,
            author=meta.get("author"),
            published_at=published_at,
            fetched_at=now_utc(),
            raw_html_path=str(path),
            text=text,
            metadata={
                "cached": cached,
                "archive_rank": rank,
                "source_attention_weight": self.source.attention_weight,
                "source_attention_note": self.source.attention_note,
                **extraction_meta,
                **meta,
            },
        )
        LOGGER.info(
            "documento recolectado source=%s id=%s title=%s", self.source.name, document.id, title
        )
        return document
