from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from red_privada.llm import (
    DeepSeekExtractor,
    DevHeuristicExtractor,
    extraction_policy_fingerprint,
    sanitize_extraction,
)
from red_privada.models import (
    AppConfig,
    DocumentExtraction,
    GraphConfig,
    KnownEntity,
    LLMConfig,
    ProjectConfig,
    RawDocument,
    ResolverConfig,
)


def test_llm_response_cannot_override_identity_or_canonical_provenance() -> None:
    document = _document(
        "La presidenta Claudia Sheinbaum informó que Pemex mantendrá el plan energético."
    )
    extraction = _extract(
        document,
        {
            "document_id": "doc_controlado_por_el_modelo",
            "text_hash": "hash_controlado_por_el_modelo",
            "provider": "proveedor_controlado_por_el_modelo",
            "model": "modelo_controlado_por_el_modelo",
            "cache_policy_fingerprint": "política_controlada_por_el_modelo",
            "entities": [],
            "relations": [
                {
                    "subject": "Claudia Sheinbaum Pardo",
                    "subject_type": "person",
                    "predicate": "INFORMÓ_SOBRE",
                    "object": "Petróleos Mexicanos",
                    "object_type": "company",
                    "quote": document.text,
                    "confidence": 0.91,
                }
            ],
        },
    )

    assert extraction.document_id == document.id
    assert extraction.text_hash == document.text_hash
    assert extraction.provider == "deepseek"
    assert extraction.model == "deepseek-test"
    assert extraction.cache_policy_fingerprint == extraction_policy_fingerprint(
        _config(),
        provider="deepseek",
    )
    assert len(extraction.relations) == 1
    assert extraction.relations[0].quote == document.text


def test_llm_relations_require_both_endpoints_in_the_verbatim_quote() -> None:
    document = _document(
        "Claudia Sheinbaum informó a Pemex sobre el plan energético. "
        "Persona Inventada y Empresa Inventada aparecieron en una nota anterior."
    )
    extraction = _extract(
        document,
        {
            "document_id": document.id,
            "text_hash": document.text_hash,
            "provider": "deepseek",
            "model": "deepseek-test",
            "entities": [],
            "relations": [
                {
                    "subject": "Claudia Sheinbaum",
                    "subject_type": "person",
                    "predicate": "INFORMÓ_A",
                    "object": "Pemex",
                    "object_type": "company",
                    "quote": "Claudia Sheinbaum informó a Pemex sobre el plan energético.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Persona Inventada",
                    "subject_type": "person",
                    "predicate": "INFORMÓ_A",
                    "object": "Pemex",
                    "object_type": "company",
                    "quote": "Claudia Sheinbaum informó a Pemex sobre el plan energético.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Claudia Sheinbaum",
                    "subject_type": "person",
                    "predicate": "INFORMÓ_A",
                    "object": "Empresa Inventada",
                    "object_type": "company",
                    "quote": "Claudia Sheinbaum informó a Pemex sobre el plan energético.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Claudia Sheinbaum",
                    "subject_type": "person",
                    "predicate": "INFORMÓ_A",
                    "object": "Pemex",
                    "object_type": "company",
                    "quote": "Claudia Sheinbaum informó a Pemex sobre otro plan.",
                    "confidence": 0.9,
                },
            ],
        },
    )

    assert [(relation.subject, relation.object) for relation in extraction.relations] == [
        ("Claudia Sheinbaum", "Pemex")
    ]
    assert "dropped_items_without_verbatim_quote=1" in extraction.warnings
    assert (
        "Relaciones descartadas porque la cita no sustentaba al sujeto y al objeto: 2."
        in extraction.warnings
    )


def test_llm_entities_require_their_name_or_alias_in_the_verbatim_quote() -> None:
    supported_quote = "Claudia Sheinbaum informó a Pemex sobre el plan energético."
    document = _document(f"{supported_quote} Persona Inventada apareció en una nota anterior.")
    extraction = _extract(
        document,
        {
            "document_id": document.id,
            "text_hash": document.text_hash,
            "provider": "deepseek",
            "model": "deepseek-test",
            "entities": [
                {
                    "name": "Claudia Sheinbaum Pardo",
                    "entity_type": "person",
                    "quote": supported_quote,
                    "confidence": 0.9,
                },
                {
                    "name": "Petróleos Mexicanos",
                    "entity_type": "company",
                    "quote": supported_quote,
                    "confidence": 0.9,
                },
                {
                    "name": "Persona Inventada",
                    "entity_type": "person",
                    "quote": supported_quote,
                    "confidence": 0.9,
                },
                {
                    "name": "Empresa Inventada",
                    "entity_type": "company",
                    "quote": "Empresa Inventada apareció en otra versión.",
                    "confidence": 0.9,
                },
            ],
            "relations": [],
        },
    )

    assert [entity.name for entity in extraction.entities] == [
        "Claudia Sheinbaum Pardo",
        "Petróleos Mexicanos",
    ]
    assert all(entity.quote == supported_quote for entity in extraction.entities)
    assert "dropped_items_without_verbatim_quote=1" in extraction.warnings
    assert (
        "Entidades descartadas porque la cita no sustentaba su nombre ni un alias: 1."
        in extraction.warnings
    )


def test_llm_cannot_present_an_invented_predicate_over_a_negation_as_evidence() -> None:
    quote = "Ana negó haber sobornado a Luis."
    document = _document(quote)
    extraction = _extract(
        document,
        {
            "document_id": document.id,
            "text_hash": document.text_hash,
            "provider": "deepseek",
            "model": "deepseek-test",
            "entities": [],
            "relations": [
                {
                    "subject": "Ana",
                    "subject_type": "person",
                    "predicate": "SOBORNO",
                    "object": "Luis",
                    "object_type": "person",
                    "quote": quote,
                    "confidence": 0.99,
                    "assertion_type": "evidence",
                },
                {
                    "subject": "Ana",
                    "subject_type": "person",
                    "predicate": "CO_MENTIONED_WITH",
                    "object": "Luis",
                    "object_type": "person",
                    "quote": quote,
                    "confidence": 0.8,
                    "assertion_type": "evidence",
                },
                {
                    "subject": "Ana",
                    "subject_type": "person",
                    "predicate": "co_mentioned_with",
                    "object": "Luis",
                    "object_type": "person",
                    "quote": quote,
                    "confidence": 0.8,
                    "assertion_type": "evidence",
                },
            ],
        },
    )

    assertions = {relation.predicate: relation.assertion_type for relation in extraction.relations}
    assert assertions == {
        "SOBORNO": "inference",
        "CO_MENTIONED_WITH": "evidence",
        "co_mentioned_with": "inference",
    }
    assert all(relation.quote == quote for relation in extraction.relations)
    assert (
        "Relaciones con predicados distintos de CO_MENTIONED_WITH marcadas como inferencia "
        "para revisión humana: 2." in extraction.warnings
    )


def test_llm_grounding_avoids_generic_words_that_resemble_configured_names() -> None:
    document = _document(
        "Pemex anunció un programa de educación pública. Pemex donó pan a la comunidad. "
        "Pemex dialogó con el PAN."
    )
    extraction = _extract(
        document,
        {
            "document_id": document.id,
            "text_hash": document.text_hash,
            "provider": "deepseek",
            "model": "deepseek-test",
            "entities": [],
            "relations": [
                {
                    "subject": "Secretaría de Educación Pública",
                    "subject_type": "organization",
                    "predicate": "ANUNCIÓ",
                    "object": "Petróleos Mexicanos",
                    "object_type": "company",
                    "quote": "Pemex anunció un programa de educación pública.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Partido Acción Nacional",
                    "subject_type": "organization",
                    "predicate": "DONÓ",
                    "object": "Petróleos Mexicanos",
                    "object_type": "company",
                    "quote": "Pemex donó pan a la comunidad.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Partido Acción Nacional",
                    "subject_type": "organization",
                    "predicate": "DIALOGÓ_CON",
                    "object": "Petróleos Mexicanos",
                    "object_type": "company",
                    "quote": "Pemex dialogó con el PAN.",
                    "confidence": 0.9,
                },
            ],
        },
    )

    assert [(relation.subject, relation.object) for relation in extraction.relations] == [
        ("Partido Acción Nacional", "Petróleos Mexicanos")
    ]


def test_dev_extractor_keeps_entities_and_relations_supported_by_configured_aliases() -> None:
    document = _document("Pemex dialogó con el PAN.")

    extraction = sanitize_extraction(DevHeuristicExtractor(_config()).extract(document), document)

    assert [entity.name for entity in extraction.entities] == [
        "Petróleos Mexicanos",
        "Partido Acción Nacional",
    ]
    assert [(relation.subject, relation.object) for relation in extraction.relations] == [
        ("Petróleos Mexicanos", "Partido Acción Nacional")
    ]


def _extract(document: RawDocument, response_payload: dict) -> DocumentExtraction:
    config = _config()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(response_payload, ensure_ascii=False))
            )
        ]
    )
    extractor = object.__new__(DeepSeekExtractor)
    extractor.config = config
    extractor.model = config.llm.model
    extractor.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response),
        )
    )
    return extractor.extract(document)


def _config() -> AppConfig:
    return AppConfig(
        project=ProjectConfig(user_agent="RedPrivadaTests/1.0 (+https://example.com/contacto)"),
        llm=LLMConfig(provider="deepseek", model="deepseek-test"),
        resolver=ResolverConfig(
            aliases={
                "Claudia Sheinbaum Pardo": ["Claudia Sheinbaum"],
                "Petróleos Mexicanos": ["Pemex"],
            }
        ),
        graph=GraphConfig(),
        known_entities=[
            KnownEntity(
                name="Claudia Sheinbaum Pardo",
                entity_type="person",
                aliases=["Presidenta Claudia Sheinbaum"],
            ),
            KnownEntity(
                name="Petróleos Mexicanos",
                entity_type="company",
                aliases=["Pemex"],
            ),
            KnownEntity(
                name="Secretaría de Educación Pública",
                entity_type="organization",
                aliases=["SEP"],
            ),
            KnownEntity(
                name="Partido Acción Nacional",
                entity_type="organization",
                aliases=["PAN"],
            ),
        ],
    )


def _document(text: str) -> RawDocument:
    return RawDocument(
        id="doc_real",
        source_name="fuente_prueba",
        source_kind="prueba",
        source_side="prueba",
        url="https://example.com/documento",
        title="Documento de prueba",
        text=text,
        fetched_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
