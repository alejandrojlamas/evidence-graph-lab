from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from html import unescape
from typing import Any

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

WHITESPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿])")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_ws(value: str) -> str:
    return WHITESPACE_RE.sub(" ", unescape(value or "")).strip()


def stable_hash(*parts: object, length: int = 16) -> str:
    raw = "\n".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_ws(text).encode("utf-8")).hexdigest()


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_name(value: str) -> str:
    value = strip_accents(normalize_ws(value)).lower()
    value = re.sub(
        r"\b(presidenta|presidente|secretaria|secretario|gobernadora|gobernador|dra|dr|lic)\b\.?",
        " ",
        value,
    )
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return normalize_ws(value)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def split_sentences(text: str, max_sentence_chars: int = 650) -> list[str]:
    sentences: list[str] = []
    for paragraph in re.split(r"\n{2,}", text):
        paragraph_text = normalize_ws(paragraph)
        paragraph_text = re.sub(r"([.!?])(?=[A-ZÁÉÍÓÚÑ¿])", r"\1 ", paragraph_text)
        for sentence in SENTENCE_RE.split(paragraph_text):
            if not sentence:
                continue
            if len(sentence) <= max_sentence_chars:
                sentences.append(sentence)
                continue
            chunks = [
                sentence[i : i + max_sentence_chars]
                for i in range(0, len(sentence), max_sentence_chars)
            ]
            sentences.extend(normalize_ws(chunk) for chunk in chunks if normalize_ws(chunk))
    return sentences


def extract_json_ld_objects(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    objects: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                objects.append(item)
    return objects


def _json_ld_article(html: str) -> tuple[str | None, dict[str, Any]]:
    for obj in extract_json_ld_objects(html):
        article_type = obj.get("@type")
        types = article_type if isinstance(article_type, list) else [article_type]
        if not any(t in {"NewsArticle", "Article", "BlogPosting"} for t in types):
            continue
        if obj.get("isAccessibleForFree") is False:
            return None, {"skip_reason": "json_ld_declares_not_free", **obj}
        body = obj.get("articleBody")
        if isinstance(body, str) and normalize_ws(body):
            return normalize_ws(body), obj
    return None, {}


def extract_article_text(html: str) -> tuple[str, dict[str, Any]]:
    json_text, json_meta = _json_ld_article(html)
    if json_text:
        return json_text, {"extraction_selector": "json-ld:articleBody", **json_meta}
    if json_meta.get("skip_reason"):
        return "", json_meta

    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        ".article-body",
        "#content-body",
        ".mrf-article-body",
        "article",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        text = normalize_ws(node.get_text(" "))
        if len(text) >= 300:
            return text, {"extraction_selector": selector}

    try:
        import trafilatura

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    except Exception:
        extracted = None
    return normalize_ws(extracted or ""), {"extraction_selector": "trafilatura"}


def extract_meta(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, str] = {}
    title = soup.find("title")
    if title:
        result["title"] = normalize_ws(title.get_text(" "))
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name")
        value = meta.get("content")
        if key and value:
            result[key] = normalize_ws(value)
    return result


def find_quote_window(text: str, needle: str, window: int = 220) -> str:
    text_norm = normalize_ws(text)
    needle_norm = normalize_ws(needle)
    idx = text_norm.lower().find(needle_norm.lower())
    if idx < 0:
        return needle_norm[:window]
    start = max(0, idx - window // 2)
    end = min(len(text_norm), idx + len(needle_norm) + window // 2)
    return normalize_ws(text_norm[start:end])


def unwrap_gob_archive_fragment(raw: str) -> str:
    return raw.replace(r"\/", "/").replace(r"\"", '"').replace(r"\n", "\n")
