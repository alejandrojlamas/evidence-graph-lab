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
    strip_accents,
)

LOGGER = logging.getLogger(__name__)
EXTRACTION_POLICY_VERSION = "red-privada-grounding-v1"


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
            raise RuntimeError("DEEPSEEK_API_KEY es obligatoria cuando provider=deepseek")
        self.client = OpenAI(api_key=api_key, base_url=config.llm.base_url)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(3))
    def extract(self, document: RawDocument) -> DocumentExtraction:
        text = document.text[: self.config.llm.max_chars_per_document]
        schema = DocumentExtraction.model_json_schema()
        messages = [
            {
                "role": "system",
                "content": (
                    "Extraes entidades y relaciones para investigación guiada por evidencia. "
                    "Devuelve únicamente JSON válido. Nunca inventes nombres ni conexiones. Cada "
                    "entidad y relación debe incluir una cita textual copiada del documento; omite "
                    "cualquier elemento sin cita. Distingue evidencia de inferencia y marca como "
                    "evidencia solo lo que el fragmento sustente directamente. Conserva nombres y "
                    "citas en el idioma de la fuente."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Extrae entidades tipadas y relaciones del documento. "
                    "El JSON debe ajustarse a este esquema Pydantic:\n"
                    f"{json.dumps(schema, ensure_ascii=False)}\n\n"
                    f"document_id={document.id}\ntext_hash={document.text_hash}\n"
                    f"source_side={document.source_side}\nurl={document.url}\n\n"
                    f"TEXTO DEL DOCUMENTO:\n{text}"
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
            LOGGER.error(
                "falló la validación del esquema DeepSeek documento=%s error=%s",
                document.id,
                exc,
            )
            raise
        return _sanitize_llm_extraction(
            extraction,
            document,
            provider=self.provider,
            model=self.model,
            aliases_by_name=_configured_aliases(self.config),
            cache_policy_fingerprint=extraction_policy_fingerprint(
                self.config,
                provider=self.provider,
            ),
        )


class DevHeuristicExtractor:
    provider = "dev"
    model = "heuristic-known-entities-v1"

    def __init__(self, config: AppConfig):
        self.known_entities = config.known_entities
        self.cache_policy_fingerprint = extraction_policy_fingerprint(
            config,
            provider=self.provider,
        )
        self.match_terms_by_name = {
            known.name: [known.name, *known.aliases] for known in config.known_entities
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

        # El extractor de desarrollo es deliberadamente conservador. La extracción
        # abierta corresponde a la ruta DeepSeek; sin clave solo utiliza entidades
        # configuradas, para que la demostración local no infiera nombres por mayúsculas.

        entities = list(entity_by_norm.values())
        relations = self._co_mentions(document, entities)
        return DocumentExtraction(
            document_id=document.id,
            text_hash=document.text_hash,
            provider=self.provider,
            model=self.model,
            cache_policy_fingerprint=self.cache_policy_fingerprint,
            entities=entities,
            relations=relations,
            warnings=[
                "Extractor heurístico de desarrollo: las relaciones CO_MENTIONED_WITH solo "
                "establecen coaparición textual."
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
        name = re_sub(
            r"^(Presidenta|Presidente|Gobernadora|Gobernador|Secretaria|Secretario)\s+", "", name
        )
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

    def _co_mentions(
        self, document: RawDocument, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        by_sentence: list[ExtractedRelation] = []
        indexed = [
            (
                entity,
                [
                    normalize_name(term)
                    for term in self.match_terms_by_name.get(entity.name, [entity.name])
                ],
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
    provider, _model = effective_extractor_identity(config)
    if provider == "deepseek":
        return DeepSeekExtractor(config)
    if provider == "dev":
        return DevHeuristicExtractor(config)
    raise ValueError(f"proveedor LLM no compatible: {provider}")


def effective_extractor_identity(config: AppConfig) -> tuple[str, str]:
    provider = config.llm.provider
    if provider == "auto":
        provider = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "dev"
    if provider == "deepseek":
        return provider, config.llm.model
    if provider == "dev":
        return provider, DevHeuristicExtractor.model
    raise ValueError(f"proveedor LLM no compatible: {provider}")


def extraction_policy_fingerprint(config: AppConfig, *, provider: str) -> str:
    policy_inputs = {
        "version": EXTRACTION_POLICY_VERSION,
        "known_entities": sorted(
            (
                {
                    "name": known.name,
                    "entity_type": known.entity_type.value,
                    "aliases": sorted(set(known.aliases)),
                }
                for known in config.known_entities
            ),
            key=lambda item: (item["name"], item["entity_type"], item["aliases"]),
        ),
        "resolver_aliases": {
            name: sorted(set(config.resolver.aliases[name]))
            for name in sorted(config.resolver.aliases)
        },
    }
    if provider == "deepseek":
        policy_inputs["llm_request"] = {
            "base_url": config.llm.base_url,
            "temperature": config.llm.temperature,
            "max_output_tokens": config.llm.max_output_tokens,
            "max_chars_per_document": config.llm.max_chars_per_document,
            "thinking": config.llm.thinking,
            "user_id": config.llm.user_id,
        }
    serialized = json.dumps(
        policy_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{EXTRACTION_POLICY_VERSION}:{stable_hash(serialized, length=24)}"


def extraction_cache_identity(config: AppConfig) -> tuple[str, str, str]:
    provider, model = effective_extractor_identity(config)
    return provider, model, extraction_policy_fingerprint(config, provider=provider)


def _configured_aliases(config: AppConfig) -> dict[str, set[str]]:
    aliases_by_name: dict[str, set[str]] = {}

    def add_group(name: str, aliases: list[str]) -> None:
        group = {name, *aliases}
        for member in group:
            member_norm = normalize_name(member)
            if member_norm:
                aliases_by_name.setdefault(member_norm, set()).update(group)

    for known in config.known_entities:
        add_group(known.name, known.aliases)
    for name, aliases in config.resolver.aliases.items():
        add_group(name, aliases)
    return aliases_by_name


def _name_or_alias_in_quote(
    name: str,
    quote: str,
    aliases_by_name: dict[str, set[str]],
) -> bool:
    candidates = aliases_by_name.get(normalize_name(name)) or {name}
    return any(_citation_alias_in_text(quote, candidate) for candidate in candidates)


def _citation_alias_in_text(text: str, alias: str) -> bool:
    letters = "".join(character for character in strip_accents(alias) if character.isalpha())
    preserve_case = 1 < len(letters) <= 3 and letters.isupper()
    text_norm = _normalize_citation_text(text, preserve_case=preserve_case)
    alias_norm = _normalize_citation_text(alias, preserve_case=preserve_case)
    if not alias_norm:
        return False
    return re.search(rf"(^|\s){re.escape(alias_norm)}($|\s)", text_norm) is not None


def _normalize_citation_text(value: str, *, preserve_case: bool) -> str:
    value = strip_accents(normalize_ws(value))
    if not preserve_case:
        value = value.casefold()
    characters = (
        character if character.isalnum() or character.isspace() else " " for character in value
    )
    return normalize_ws("".join(characters))


def _sanitize_llm_extraction(
    extraction: DocumentExtraction,
    document: RawDocument,
    *,
    provider: str,
    model: str,
    aliases_by_name: dict[str, set[str]],
    cache_policy_fingerprint: str,
) -> DocumentExtraction:
    sanitized = sanitize_extraction(extraction, document)
    entities = [
        entity
        for entity in sanitized.entities
        if _name_or_alias_in_quote(entity.name, entity.quote, aliases_by_name)
    ]
    grounded_relations = [
        relation
        for relation in sanitized.relations
        if _name_or_alias_in_quote(relation.subject, relation.quote, aliases_by_name)
        and _name_or_alias_in_quote(relation.object, relation.quote, aliases_by_name)
    ]
    relations = [
        relation
        if relation.predicate == "CO_MENTIONED_WITH"
        else relation.model_copy(update={"assertion_type": AssertionType.inference})
        for relation in grounded_relations
    ]
    warnings = list(sanitized.warnings)
    dropped_entities = len(sanitized.entities) - len(entities)
    if dropped_entities:
        warnings.append(
            "Entidades descartadas porque la cita no sustentaba su nombre ni un alias: "
            f"{dropped_entities}."
        )
    dropped_relations = len(sanitized.relations) - len(grounded_relations)
    if dropped_relations:
        warnings.append(
            "Relaciones descartadas porque la cita no sustentaba al sujeto y al objeto: "
            f"{dropped_relations}."
        )
    inferred_relations = sum(
        relation.predicate != "CO_MENTIONED_WITH" for relation in grounded_relations
    )
    if inferred_relations:
        warnings.append(
            "Relaciones con predicados distintos de CO_MENTIONED_WITH marcadas como inferencia "
            f"para revisión humana: {inferred_relations}."
        )
    return sanitized.model_copy(
        update={
            "document_id": document.id,
            "text_hash": document.text_hash,
            "provider": provider,
            "model": model,
            "cache_policy_fingerprint": cache_policy_fingerprint,
            "entities": entities,
            "relations": relations,
            "warnings": warnings,
        }
    )


def sanitize_extraction(
    extraction: DocumentExtraction, document: RawDocument
) -> DocumentExtraction:
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
    return extraction.model_copy(
        update={
            "document_id": document.id,
            "text_hash": document.text_hash,
            "entities": entities,
            "relations": relations,
            "warnings": warnings,
        }
    )
