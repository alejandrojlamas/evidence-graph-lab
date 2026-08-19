from __future__ import annotations

import pytest
from pydantic import ValidationError

from red_privada.config import load_config
from red_privada.models import GraphConfig, Neo4jConfig, SourceConfig


def test_default_config_is_local_and_sources_are_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("EVIDENCE_GRAPH_USER_AGENT", raising=False)
    monkeypatch.delenv("RED_PRIVADA_USER_AGENT", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    config = load_config("config/sources.yaml")

    assert config.llm.provider == "dev"
    assert config.graph.backend == "sqlite"
    assert config.graph.neo4j.password == ""
    assert not config.project.allow_robots_unavailable
    assert config.sources
    assert all(not source.enabled for source in config.sources)


def test_legacy_user_agent_environment_variable_remains_compatible(monkeypatch) -> None:
    monkeypatch.delenv("EVIDENCE_GRAPH_USER_AGENT", raising=False)
    monkeypatch.setenv(
        "RED_PRIVADA_USER_AGENT",
        "EvidenceGraphLab/0.1 (+https://example.org/contact)",
    )

    config = load_config("config/sources.yaml")

    assert config.project.user_agent == "EvidenceGraphLab/0.1 (+https://example.org/contact)"


def test_new_source_is_disabled_unless_explicitly_enabled() -> None:
    source = SourceConfig(name="example", kind="rss_feed", side="editorial-label")

    assert not source.enabled


def test_neo4j_backend_requires_nonempty_password() -> None:
    with pytest.raises(ValidationError, match="NEO4J_PASSWORD"):
        GraphConfig(backend="neo4j", neo4j=Neo4jConfig(password=""))


def test_neo4j_backend_accepts_configured_password() -> None:
    config = GraphConfig(
        backend="neo4j",
        neo4j=Neo4jConfig(password="unique-test-password"),
    )

    assert config.neo4j.password == "unique-test-password"
