from __future__ import annotations

from red_privada.agents.resolver import EntityResolver
from red_privada.config import load_config
from red_privada.models import EntityType, ExtractedEntity


def test_aliases_resolve_to_same_canonical_entity() -> None:
    config = load_config("config/sources.yaml")
    resolver = EntityResolver(config)
    first = resolver.resolve_entity(
        ExtractedEntity(
            name="Claudia Sheinbaum Pardo",
            entity_type=EntityType.person,
            quote="Claudia Sheinbaum Pardo",
            confidence=1.0,
        )
    )
    second = resolver.resolve_entity(
        ExtractedEntity(
            name="Claudia Sheinbaum",
            entity_type=EntityType.person,
            quote="Claudia Sheinbaum",
            confidence=0.9,
        )
    )
    assert first.canonical_id == second.canonical_id
    assert "Claudia Sheinbaum" in second.aliases
