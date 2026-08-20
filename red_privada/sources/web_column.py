from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from red_privada.models import RawDocument
from red_privada.sources.base import BaseCollector
from red_privada.text import (
    extract_article_text,
    extract_json_ld_objects,
    extract_meta,
    normalize_ws,
    now_utc,
    parse_datetime,
)

LOGGER = logging.getLogger(__name__)


class WebColumnCollector(BaseCollector):
    def collect(self) -> list[RawDocument]:
        urls = self._discover_urls()
        documents: list[RawDocument] = []
        for rank, url in enumerate(urls[: self.source.limit], start=1):
            try:
                document = self._fetch_article(url, rank)
            except Exception as exc:  # pragma: no cover - logged integration boundary
                LOGGER.warning("falló la descarga de la columna url=%s error=%s", url, exc)
                continue
            documents.append(document)
        return documents

    def _discover_urls(self) -> list[str]:
        if self.source.sitemap_url:
            html, _, _ = self.fetcher.fetch(self.source.sitemap_url)
            urls = self._parse_sitemap(html)
        elif self.source.listing_url:
            html, _, _ = self.fetcher.fetch(self.source.listing_url)
            urls = self._parse_listing(html, self.source.listing_url)
        else:
            raise ValueError("la fuente de columna web requiere sitemap_url o listing_url")
        filtered = [url for url in urls if self._allowed_url(url)]
        LOGGER.info(
            "fuente de columna=%s descubiertos=%s filtrados=%s",
            self.source.name,
            len(urls),
            len(filtered),
        )
        return filtered

    def _allowed_url(self, url: str) -> bool:
        include = self.source.url_include
        if include is None:
            return True
        needles = include if isinstance(include, list) else [include]
        return any(needle in url for needle in needles)

    @staticmethod
    def _parse_sitemap(raw_xml: str) -> list[str]:
        root = ET.fromstring(raw_xml)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [node.text for node in root.findall(".//sm:loc", ns) if node.text]
        return list(dict.fromkeys(urls))

    @staticmethod
    def _parse_listing(html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls = [urljoin(base_url, link["href"]) for link in soup.find_all("a", href=True)]
        return list(dict.fromkeys(urls))

    def _fetch_article(self, url: str, rank: int) -> RawDocument:
        html, path, cached = self.fetcher.fetch(url)
        text, extraction_meta = extract_article_text(html)
        if extraction_meta.get("skip_reason"):
            raise ValueError(extraction_meta["skip_reason"])
        if len(text) < 300:
            raise ValueError("el texto del artículo es demasiado breve")
        meta = extract_meta(html)
        json_meta = self._first_article_json_ld(html)
        title = (
            json_meta.get("headline")
            or meta.get("og:title")
            or meta.get("twitter:title")
            or meta.get("title")
            or url
        )
        author = self._author(json_meta) or meta.get("author") or meta.get("article:author")
        published_at = parse_datetime(
            json_meta.get("datePublished")
            or meta.get("article:published_time")
            or meta.get("fecha_publicacion")
        )
        return RawDocument(
            id=RawDocument.id_for(self.source.name, url),
            source_name=self.source.name,
            source_kind=self.source.kind,
            source_side=self.source.side,
            url=url,
            title=normalize_ws(title),
            author=author,
            published_at=published_at,
            fetched_at=now_utc(),
            raw_html_path=str(path),
            text=text,
            metadata={
                "cached": cached,
                "listing_rank": rank,
                "source_attention_weight": self.source.attention_weight,
                "source_attention_note": self.source.attention_note,
                **extraction_meta,
                **meta,
            },
        )

    @staticmethod
    def _first_article_json_ld(html: str) -> dict:
        for obj in extract_json_ld_objects(html):
            article_type = obj.get("@type")
            types = article_type if isinstance(article_type, list) else [article_type]
            if any(t in {"NewsArticle", "Article", "BlogPosting"} for t in types):
                return obj
        return {}

    @staticmethod
    def _author(json_meta: dict) -> str | None:
        author = json_meta.get("author")
        if isinstance(author, dict):
            return author.get("name")
        if isinstance(author, list) and author:
            first = author[0]
            if isinstance(first, dict):
                return first.get("name")
        if isinstance(author, str):
            return author
        return None
