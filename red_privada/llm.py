from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from red_privada.models import (
    AppConfig,
    AssertionType,
    DocumentExtraction,
    EntityType,
    ExtractedEntity,
    ExtractedRelation,
    RawDocument,
)
from red_privada.text import (
    find_quote_window,
    normalize_name,
    normalize_ws,
    split_sentences,
    stable_hash,
)

LOGGER = logging.getLogger(__name__)


class Extractor(Protocol):
    provider: str
    model: str

    def extract(self, document: RawDocument) -> DocumentExtraction:
        raise NotImplementedError


class DeepSeekExtractor:
    provider = "deepseek"

    def __init__(self, config: AppConfig):
        self.config = config
        self.model = config.llm.model
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for provider=deepseek")
        self.client = OpenAI(api_key=api_key, base_url=config.llm.base_url)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(3))
    def extract(self, document: RawDocument) -> DocumentExtraction:
        text = document.text[: self.config.llm.max_chars_per_document]
        schema = DocumentExtraction.model_json_schema()
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract entities and relationships for evidence-led research. Return only "
                    "valid JSON. Never invent names or connections. Every entity and relationship "
                    "must include a verbatim quote copied from the document; omit any item without "
                    "one. Distinguish evidence from inference, and use evidence only when the excerpt "
                    "directly supports the relationship. Preserve names and quotes in their source "
                    "language."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Extract typed entities and relationships from the document. "
                    "The JSON must conform to this Pydantic schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False)}\n\n"
                    f"document_id={document.id}\ntext_hash={document.text_hash}\n"
                    f"source_side={document.source_side}\nurl={document.url}\n\n"
                    f"DOCUMENT TEXT:\n{text}"
                ),
            },
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.config.llm.temperature,
            max_tokens=self.config.llm.max_output_tokens,
            response_format={"type": "json_object"},
            stream=False,
            extra_body={
                "thinking": {"type": self.config.llm.thinking},
                "user_id": self.config.llm.user_id,
            },
        )
        content = response.choices[0].message.content or "{}"
        try:
            extraction = DocumentExtraction.model_validate_json(content)
        except ValidationError as exc:
            LOGGER.error("deepseek schema validation failed document=%s error=%s", document.id, exc)
            raise
        return sanitize_extraction(extraction, document)


class DevHeuristicExtractor:
    provider = "dev"
    model = "heuristic-known-entities-v1"

    def __init__(self, config: AppConfig):
        self.known_entities = config.known_entities
        self.match_terms_by_name = {
            known.name: [known.name, *known.aliases]
            for known in config.known_entities
        }

    def extract(self, document: RawDocument) -> DocumentExtraction:
        entity_by_norm: dict[str, ExtractedEntity] = {}
        for known in self.known_entities:
            for alias in [known.name, *known.aliases]:
                quote = self._quote_for_alias(document.text, alias)
                if not quote:
                    continue
                key = normalize_name(known.name)
                entity_by_norm[key] = ExtractedEntity(
                    name=known.name,
                    entity_type=known.entity_type,
                    quote=quote,
                    confidence=0.85 if alias == known.name else 0.78,
                )

        # The dev extractor is deliberately conservative. Open extraction belongs
        # to the DeepSeek path; without an API key we only use configured known
        # entities so the local demo cannot invent entities from capitalization.

        entities = list(entity_by_norm.values())
        relations = self._co_mentions(document, entities)
        return DocumentExtraction(
            document_id=document.id,
            text_hash=document.text_hash,
            provider=self.provider,
            model=self.model,
            entities=entities,
            relations=relations,
            warnings=[
                "Development heuristic extractor: CO_MENTIONED_WITH relationships establish only "
                "textual co-occurrence."
            ],
        )

    @staticmethod
    def _quote_for_alias(text: str, alias: str) -> str | None:
        if not alias_in_text(text, alias):
            return None
        return find_quote_window(text, alias)

    @staticmethod
    def _capitalized_phrases(sentence: str) -> list[str]:
        pattern = (
            r"\b(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})"
            r"(?:\s+(?:de|del|la|las|los|y|en|para|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})){0,5}"
        )
        blocked = {
            "Adelante",
            "Bueno",
            "Buenos",
            "Conferencia",
            "Gracias",
            "PRESIDENTA DE MÉXICO",
            "REPORTERA",
            "REPORTERO",
            "Versión",
        }
        names = []
        for match in re_finditer(pattern, sentence):
            name = DevHeuristicExtractor._clean_candidate_name(match.group(0))
            if (
                name in blocked
                or len(name) < 4
                or len(normalize_name(name).split()) < 2
                or name.isupper()
            ):
                continue
            names.append(name)
        return names

    @staticmethod
    def _clean_candidate_name(name: str) -> str:
        name = normalize_ws(name)
        name = re_sub(r"^(Presidenta|Presidente|Gobernadora|Gobernador|Secretaria|Secretario)\s+", "", name)
        name = re_sub(r"\s+(de|del|la|las|los|y|en|para)$", "", name, flags="i")
        return normalize_ws(name.strip(" ,.;:—-"))

    @staticmethod
    def _guess_type(name: str) -> EntityType:
        lower = normalize_name(name)
        org_terms = {
            "gobierno",
            "secretaria",
            "presidencia",
            "senado",
            "camara",
            "corte",
            "metro",
            "pemex",
            "morena",
            "pri",
            "pan",
        }
        place_terms = {"mexico", "veracruz", "coatzacoalcos", "ciudad", "xalapa", "minatitlan"}
        if any(term in lower for term in org_terms):
            return EntityType.organization
        if any(term in lower for term in place_terms):
            return EntityType.place
        if len(lower.split()) >= 2:
            return EntityType.person
        return EntityType.other

    def _co_mentions(self, document: RawDocument, entities: list[ExtractedEntity]) -> list[ExtractedRelation]:
        by_sentence: list[ExtractedRelation] = []
        indexed = [
            (
                entity,
                [normalize_name(term) for term in self.match_terms_by_name.get(entity.name, [entity.name])],
            )
            for entity in entities
        ]
        seen: set[str] = set()
        for sentence in split_sentences(document.text[:15000]):
            present = [
                entity
                for entity, terms in indexed
                if any(alias_norm_in_text(sentence, term) for term in terms if term)
            ]
            if len(present) < 2:
                continue
            for left_idx, left in enumerate(present[:8]):
                for right in present[left_idx + 1 : 8]:
                    key = "|".join(sorted([normalize_name(left.name), normalize_name(right.name)]))
                    key = stable_hash(document.id, key, sentence[:80], length=20)
                    if key in seen:
                        continue
                    seen.add(key)
                    by_sentence.append(
                        ExtractedRelation(
                            subject=left.name,
                            subject_type=left.entity_type,
                            predicate="CO_MENTIONED_WITH",
                            object=right.name,
                            object_type=right.entity_type,
                            quote=sentence,
                            confidence=min(left.confidence, right.confidence, 0.55),
                            assertion_type=AssertionType.evidence,
                        )
                    )
                    if len(by_sentence) >= 80:
                        return by_sentence
        return by_sentence


def re_finditer(pattern: str, text: str):
    import re

    return re.finditer(pattern, text)


def re_sub(pattern: str, repl: str, text: str, flags: str | int = 0) -> str:
    re_flags = re.IGNORECASE if flags == "i" else flags
    return re.sub(pattern, repl, text, flags=re_flags)


def alias_norm_in_text(text: str, alias_norm: str) -> bool:
    text_norm = normalize_name(text)
    if not alias_norm:
        return False
    pattern = rf"(^|\s){re.escape(alias_norm)}($|\s)"
    return re.search(pattern, text_norm) is not None


def alias_in_text(text: str, alias: str) -> bool:
    return alias_norm_in_text(text, normalize_name(alias))


def build_extractor(config: AppConfig) -> Extractor:
    provider = config.llm.provider
    if provider == "auto":
        provider = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "dev"
    if provider == "deepseek":
        return DeepSeekExtractor(config)
    if provider == "dev":
        return DevHeuristicExtractor(config)
    raise ValueError(f"unsupported llm provider: {provider}")


def sanitize_extraction(extraction: DocumentExtraction, document: RawDocument) -> DocumentExtraction:
    text_norm = normalize_ws(document.text)
    entities = [
        entity
        for entity in extraction.entities
        if entity.quote and normalize_ws(entity.quote).lower() in text_norm.lower()
    ]
    relations = [
        relation
        for relation in extraction.relations
        if relation.quote and normalize_ws(relation.quote).lower() in text_norm.lower()
    ]
    warnings = list(extraction.warnings)
    dropped = len(extraction.entities) - len(entities) + len(extraction.relations) - len(relations)
    if dropped:
        warnings.append(f"dropped_items_without_verbatim_quote={dropped}")
    return extraction.model_copy(update={"entities": entities, "relations": relations, "warnings": warnings})
