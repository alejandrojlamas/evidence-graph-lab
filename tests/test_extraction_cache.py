from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

import red_privada.agents.extractor as extractor_module
import red_privada.cli as cli_module
from red_privada.agents.cartographer import CartographerAgent
from red_privada.agents.extractor import ExtractorAgent
from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.llm import extraction_cache_identity
from red_privada.models import (
    AppConfig,
    AssertionType,
    DocumentExtraction,
    EntityType,
    ExtractedRelation,
    GraphConfig,
    LLMConfig,
    ProjectConfig,
    RawDocument,
    ResolverConfig,
)
from red_privada.storage import SQLiteStore


def test_legacy_deepseek_cache_is_reextracted_under_current_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config("deepseek")
    document = _document("Ana negó haber sobornado a Luis.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    unsafe = _extraction(
        config,
        document,
        predicate="SOBORNO",
        assertion_type=AssertionType.evidence,
    )
    _insert_legacy_extraction(store, unsafe)
    fresh = _extraction(
        config,
        document,
        predicate="SOBORNO",
        assertion_type=AssertionType.inference,
    )
    fake = _FakeExtractor(fresh)
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    extraction = ExtractorAgent(config, store).run([document])[0]

    assert fake.calls == 1
    assert extraction.relations[0].assertion_type == AssertionType.inference
    assert extraction.cache_policy_fingerprint != "legacy"
    assert _cached(store, config, document) == extraction


def test_cache_is_not_reused_when_provider_changes_from_dev_to_deepseek(
    tmp_path: Path,
    monkeypatch,
) -> None:
    document = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    dev_config = _config("dev")
    store.upsert_extraction(_extraction(dev_config, document))
    deepseek_config = _config("deepseek")
    fresh = _extraction(deepseek_config, document)
    fake = _FakeExtractor(fresh)
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    extraction = ExtractorAgent(deepseek_config, store).run([document])[0]

    assert fake.calls == 1
    assert extraction.provider == "deepseek"
    assert extraction.model == deepseek_config.llm.model
    assert _cached(store, deepseek_config, document) == extraction
    assert _list_cached(store, dev_config) == []


def test_exact_cache_identity_remains_a_cache_hit(tmp_path: Path, monkeypatch) -> None:
    config = _config("deepseek")
    document = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    cached = _extraction(config, document)
    store.upsert_extraction(cached)
    fake = _FakeExtractor(_extraction(config, document, predicate="OTRA_RELACIÓN"))
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    extraction = ExtractorAgent(config, store).run([document])[0]

    assert fake.calls == 0
    assert extraction == cached


def test_cache_is_not_reused_when_deepseek_model_changes(tmp_path: Path, monkeypatch) -> None:
    initial_config = _config("deepseek", model="deepseek-model-a")
    updated_config = _config("deepseek", model="deepseek-model-b")
    document = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    store.upsert_extraction(_extraction(initial_config, document))
    fresh = _extraction(updated_config, document)
    fake = _FakeExtractor(fresh)
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    extraction = ExtractorAgent(updated_config, store).run([document])[0]

    assert fake.calls == 1
    assert extraction.model == "deepseek-model-b"


def test_cache_policy_changes_with_deepseek_request_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    initial_config = _config("deepseek")
    updated_config = initial_config.model_copy(
        update={
            "llm": initial_config.llm.model_copy(update={"temperature": 0.35}),
        }
    )
    document = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    store.upsert_extraction(_extraction(initial_config, document))
    fresh = _extraction(updated_config, document)
    fake = _FakeExtractor(fresh)
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    extraction = ExtractorAgent(updated_config, store).run([document])[0]

    assert extraction_cache_identity(initial_config) != extraction_cache_identity(updated_config)
    assert fake.calls == 1
    assert extraction.cache_policy_fingerprint == fresh.cache_policy_fingerprint


def test_disabled_cache_forces_a_fresh_extraction(tmp_path: Path, monkeypatch) -> None:
    initial_config = _config("deepseek")
    uncached_config = initial_config.model_copy(
        update={"llm": initial_config.llm.model_copy(update={"cache_extractions": False})}
    )
    document = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    store.upsert_extraction(_extraction(initial_config, document))
    fake = _FakeExtractor(_extraction(uncached_config, document))
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    ExtractorAgent(uncached_config, store).run([document])

    assert fake.calls == 1


def test_failed_reextraction_leaves_legacy_row_stored_but_inaccessible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config("deepseek")
    document = _document("Ana negó haber sobornado a Luis.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(document)
    _insert_legacy_extraction(
        store,
        _extraction(
            config,
            document,
            predicate="SOBORNO",
            assertion_type=AssertionType.evidence,
        ),
    )
    fake = _FailingExtractor(*extraction_cache_identity(config)[:2])
    monkeypatch.setattr(extractor_module, "build_extractor", lambda _config: fake)

    with pytest.raises(RuntimeError, match="fallo de extracción simulado"):
        ExtractorAgent(config, store).run([document])

    with store.connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM extractions WHERE document_id = ?",
            (document.id,),
        ).fetchone()
    assert row is not None
    assert "cache_policy_fingerprint" not in json.loads(row["payload_json"])
    assert _cached(store, config, document) is None


def test_listing_rejects_cache_for_an_outdated_document_hash(tmp_path: Path) -> None:
    config = _config("deepseek")
    original = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    store.upsert_document(original)
    store.upsert_extraction(_extraction(config, original))
    updated = original.model_copy(update={"text": "El documento fue corregido por la fuente."})
    store.upsert_document(updated)

    assert _list_cached(store, config) == []
    assert _cached(store, config, updated) is None


def test_standalone_graph_removes_evidence_from_another_cache_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    document = _document("Ana y Luis aparecen en el mismo fragmento.")
    store = SQLiteStore(tmp_path / "estado.sqlite")
    graph = SQLiteGraph(store.path)
    store.upsert_document(document)
    dev_config = _config("dev")
    dev_extraction = _extraction(dev_config, document)
    store.upsert_extraction(dev_extraction)
    CartographerAgent(dev_config, graph).run([document], [dev_extraction])
    assert graph.all_evidence()
    deepseek_config = _config("deepseek")
    monkeypatch.setattr(
        cli_module,
        "load_runtime",
        lambda _path: (deepseek_config, store, graph),
    )

    cli_module.graph_command(config=Path("configuración-no-usada.yaml"), verbose=False)

    assert graph.all_evidence() == []


def test_auto_provider_identity_changes_when_deepseek_key_appears(monkeypatch) -> None:
    config = _config("auto")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    without_key = extraction_cache_identity(config)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "clave-solo-para-prueba")
    with_key = extraction_cache_identity(config)

    assert without_key[:2] == ("dev", "heuristic-known-entities-v1")
    assert with_key[:2] == ("deepseek", config.llm.model)
    assert without_key != with_key


class _FakeExtractor:
    def __init__(self, extraction: DocumentExtraction):
        self.provider = extraction.provider
        self.model = extraction.model
        self.extraction = extraction
        self.calls = 0

    def extract(self, document: RawDocument) -> DocumentExtraction:
        self.calls += 1
        return self.extraction.model_copy(
            update={
                "document_id": document.id,
                "text_hash": document.text_hash,
            }
        )


class _FailingExtractor:
    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model

    def extract(self, _document: RawDocument) -> DocumentExtraction:
        raise RuntimeError("fallo de extracción simulado")


def _cached(
    store: SQLiteStore,
    config: AppConfig,
    document: RawDocument,
) -> DocumentExtraction | None:
    provider, model, policy_fingerprint = extraction_cache_identity(config)
    return store.get_extraction(
        document.id,
        document.text_hash,
        expected_provider=provider,
        expected_model=model,
        expected_policy_fingerprint=policy_fingerprint,
    )


def _list_cached(store: SQLiteStore, config: AppConfig) -> list[DocumentExtraction]:
    provider, model, policy_fingerprint = extraction_cache_identity(config)
    return store.list_extractions(
        expected_provider=provider,
        expected_model=model,
        expected_policy_fingerprint=policy_fingerprint,
    )


def _insert_legacy_extraction(store: SQLiteStore, extraction: DocumentExtraction) -> None:
    payload = extraction.model_dump(mode="json", exclude={"cache_policy_fingerprint"})
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO extractions (
              document_id, text_hash, provider, model, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                extraction.document_id,
                extraction.text_hash,
                extraction.provider,
                extraction.model,
                json.dumps(payload, ensure_ascii=False),
                "2026-08-20T00:00:00+00:00",
            ),
        )


def _extraction(
    config: AppConfig,
    document: RawDocument,
    *,
    predicate: str = "CO_MENTIONED_WITH",
    assertion_type: AssertionType = AssertionType.evidence,
) -> DocumentExtraction:
    provider, model, policy_fingerprint = extraction_cache_identity(config)
    return DocumentExtraction(
        document_id=document.id,
        text_hash=document.text_hash,
        provider=provider,
        model=model,
        cache_policy_fingerprint=policy_fingerprint,
        relations=[
            ExtractedRelation(
                subject="Ana",
                subject_type=EntityType.person,
                predicate=predicate,
                object="Luis",
                object_type=EntityType.person,
                quote=document.text,
                confidence=0.8,
                assertion_type=assertion_type,
            )
        ],
    )


def _config(
    provider: Literal["auto", "deepseek", "dev"],
    *,
    model: str = "deepseek-cache-test",
) -> AppConfig:
    return AppConfig(
        project=ProjectConfig(user_agent="RedPrivadaTests/1.0 (+https://example.com/contacto)"),
        llm=LLMConfig(provider=provider, model=model),
        resolver=ResolverConfig(),
        graph=GraphConfig(),
    )


def _document(text: str) -> RawDocument:
    return RawDocument(
        id="doc_cache",
        source_name="fuente_prueba",
        source_kind="prueba",
        source_side="prueba",
        url="https://example.com/cache",
        title="Documento de caché",
        text=text,
        fetched_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
