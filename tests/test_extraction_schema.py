from __future__ import annotations

import pytest
from pydantic import ValidationError

from red_privada.models import EntityType, ExtractedRelation


def test_relation_requires_quote() -> None:
    with pytest.raises(ValidationError):
        ExtractedRelation(
            subject="Claudia Sheinbaum Pardo",
            subject_type=EntityType.person,
            predicate="MENTIONS",
            object="Pemex",
            object_type=EntityType.company,
            quote="",
            confidence=0.8,
        )


def test_relation_accepts_evidence_quote() -> None:
    relation = ExtractedRelation(
        subject="Claudia Sheinbaum Pardo",
        subject_type=EntityType.person,
        predicate="CO_MENTIONED_WITH",
        object="Pemex",
        object_type=EntityType.company,
        quote="Claudia Sheinbaum menciono a Pemex durante la conferencia.",
        confidence=0.6,
    )
    assert relation.assertion_type == "evidence"

