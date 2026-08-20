from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

from bs4 import BeautifulSoup

from red_privada.models import RawDocument
from red_privada.sources.base import BaseCollector
from red_privada.text import (
    extract_article_text,
    extract_meta,
    normalize_ws,
    now_utc,
    parse_datetime,
)

LOGGER = logging.getLogger(__name__)


class RSSFeedCollector(BaseCollector):
    def collect(self) -> list[RawDocument]:
        if not self.source.feed_url:
            raise ValueError("la fuente rss_feed requiere feed_url")
        xml, path, cached = self.fetcher.fetch(self.source.feed_url)
        items = self._parse_feed(xml)
        documents: list[RawDocument] = []
        for rank, item in enumerate(items[: self.source.limit], start=1):
            try:
                document = self._document_from_item(item, rank, str(path), cached)
            except Exception as exc:  # pragma: no cover - logged integration boundary
                LOGGER.warning(
                    "falló un elemento RSS source=%s url=%s error=%s",
                    self.source.name,
                    item.get("link"),
                    exc,
                )
                continue
            documents.append(document)
        LOGGER.info(
            "fuente RSS=%s recolectados=%s elementos=%s",
            self.source.name,
            len(documents),
            len(items),
        )
        return documents

    def _document_from_item(
        self,
        item: dict[str, Any],
        rank: int,
        feed_path: str,
        feed_cached: bool,
    ) -> RawDocument:
        url = item["link"]
        title = item.get("title") or url
        author = item.get("author")
        published_at = parse_datetime(item.get("published_at"))
        metadata = {
            "cached": feed_cached,
            "feed_url": self.source.feed_url,
            "feed_path": feed_path,
            "feed_rank": rank,
            "source_attention_weight": self.source.attention_weight,
            "source_attention_note": self.source.attention_note,
            "rss_categories": item.get("categories", []),
            "rss_guid": item.get("guid"),
        }

        if self.source.fetch_article:
            html, article_path, article_cached = self.fetcher.fetch(url)
            text, extraction_meta = extract_article_text(html)
            if extraction_meta.get("skip_reason"):
                raise ValueError(extraction_meta["skip_reason"])
            meta = extract_meta(html)
            title = meta.get("og:title") or meta.get("twitter:title") or meta.get("title") or title
            author = author or meta.get("author") or meta.get("article:author")
            published_at = published_at or parse_datetime(
                meta.get("article:published_time") or meta.get("fecha_publicacion")
            )
            metadata.update(
                {
                    "article_cached": article_cached,
                    "article_path": str(article_path),
                    **extraction_meta,
                }
            )
            raw_html_path = str(article_path)
        else:
            text = normalize_ws(f"{title}. {item.get('summary') or ''}")
            raw_html_path = feed_path

        if len(text) < self.source.min_text_chars:
            raise ValueError("el texto del elemento RSS es demasiado breve")

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
            raw_html_path=raw_html_path,
            text=text,
            metadata=metadata,
        )

    @staticmethod
    def _parse_feed(raw_xml: str) -> list[dict[str, Any]]:
        root = ET.fromstring(raw_xml)
        items = root.findall(".//item")
        parsed: list[dict[str, Any]] = []
        for item in items:
            title = _node_text(item, "title")
            link = _node_text(item, "link")
            if not link:
                continue
            parsed.append(
                {
                    "title": title,
                    "link": normalize_ws(link),
                    "summary": _html_text(_node_text(item, "description")),
                    "author": _node_text(item, "author")
                    or _node_text(item, "{http://purl.org/dc/elements/1.1/}creator"),
                    "published_at": _node_text(item, "pubDate"),
                    "guid": _node_text(item, "guid"),
                    "categories": [
                        normalize_ws(category.text or "")
                        for category in item.findall("category")
                        if normalize_ws(category.text or "")
                    ],
                }
            )
        return parsed


def _node_text(item: ET.Element, tag: str) -> str | None:
    node = item.find(tag)
    if node is None or node.text is None:
        return None
    return normalize_ws(node.text)


def _html_text(value: str | None) -> str:
    if not value:
        return ""
    return normalize_ws(BeautifulSoup(value, "html.parser").get_text(" "))
