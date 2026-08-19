from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from red_privada.text import normalize_ws, stable_hash, text_hash


class EntityType(str, Enum):
    person = "person"
    organization = "organization"
    company = "company"
    place = "place"
    event = "event"
    other = "other"


class AssertionType(str, Enum):
    evidence = "evidence"
    inference = "inference"


class SourceConfig(BaseModel):
    name: str
    kind: str
    enabled: bool = False
    side: str
    archive_url: str | None = None
    feed_url: str | None = None
    sitemap_url: str | None = None
    listing_url: str | None = None
    title_include: str | None = None
    url_include: str | list[str] | None = None
    max_pages: int = 1
    limit: int = 3
    fetch_article: bool = True
    min_text_chars: int = 300
    attention_weight: float = Field(default=0.7, ge=0, le=1)
    attention_note: str | None = None
    note: str | None = None


class KnownEntity(BaseModel):
    name: str
    entity_type: EntityType
    aliases: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    user_agent: str
    request_delay_seconds: float = 1.0
    allow_robots_unavailable: bool = False
    cache_dir: str = "data/cache"
    database_path: str = "data/state/red_privada.sqlite"
    output_dir: str = "data/output"


class LLMConfig(BaseModel):
    provider: Literal["auto", "deepseek", "dev"] = "dev"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    temperature: float = 0.1
    max_output_tokens: int = 5000
    max_chars_per_document: int = 18000
    thinking: Literal["enabled", "disabled"] = "disabled"
    user_id: str = "red-privada-extractor"
    cache_extractions: bool = True


class ResolverConfig(BaseModel):
    similarity_threshold: float = 0.92
    ambiguous_low: float = 0.84
    use_embeddings: bool = False
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    aliases: dict[str, list[str]] = Field(default_factory=dict)


class Neo4jConfig(BaseModel):
    uri: str = "neo4j://localhost:7687"
    user: str = "neo4j"
    password: str = ""
    database: str = "neo4j"


class GraphConfig(BaseModel):
    backend: Literal["sqlite", "neo4j"] = "sqlite"
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)

    @model_validator(mode="after")
    def require_neo4j_password(self) -> "GraphConfig":
        if self.backend == "neo4j" and not self.neo4j.password.strip():
            raise ValueError("NEO4J_PASSWORD is required when graph.backend=neo4j")
        return self


class ScoringConfig(BaseModel):
    enabled: bool = True
    null_model_samples: int = 100
    null_model_seed: int = 20260608
    null_model_alpha: float = 0.1
    temporal_window_days: int = 7
    independence_threshold: float = 0.45
    improbability_threshold: float = 0.55
    specificity_threshold: float = 0.45
    temporal_threshold: float = 0.4
    min_final_score: float = 0.5


class SignalsConfig(BaseModel):
    enabled: bool = True
    small_note_top_n: int = 10
    morning_top_n: int = 10
    novelty_window_days: int = 21
    low_attention_threshold: float = 0.35
    structural_novelty_threshold: float = 0.25
    small_note_min_score: float = 0.45
    morning_lookback_days: int = 14
    min_non_official_mentions: int = 1
    official_side: str = "official_government"
    denial_keywords: list[str] = Field(
        default_factory=lambda: [
            "no es cierto",
            "es falso",
            "falso",
            "mentira",
            "desment",
            "rechaz",
            "aclar",
            "no tenemos informacion",
            "no tenemos información",
            "no hay informacion",
            "no hay información",
        ]
    )


class AppConfig(BaseModel):
    project: ProjectConfig
    llm: LLMConfig
    resolver: ResolverConfig
    graph: GraphConfig
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    known_entities: list[KnownEntity] = Field(default_factory=list)
    sources: list[SourceConfig] = Field(default_factory=list)


class RawDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_name: str
    source_kind: str
    source_side: str
    url: str
    title: str
    text: str
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    raw_html_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text", "title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = normalize_ws(value)
        if not value:
            raise ValueError("must not be blank")
        return value

    @property
    def text_hash(self) -> str:
        return text_hash(self.text)

    @classmethod
    def id_for(cls, source_name: str, url: str) -> str:
        return "doc_" + stable_hash(source_name, url, length=24)


class ExtractedEntity(BaseModel):
    name: str
    entity_type: EntityType
    quote: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("name", "quote")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = normalize_ws(value)
        if not value:
            raise ValueError("must not be blank")
        return value


class ExtractedRelation(BaseModel):
    subject: str
    subject_type: EntityType
    predicate: str
    object: str
    object_type: EntityType
    quote: str
    confidence: float = Field(ge=0, le=1)
    assertion_type: AssertionType = AssertionType.evidence

    @field_validator("subject", "predicate", "object", "quote")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = normalize_ws(value)
        if not value:
            raise ValueError("must not be blank")
        return value


class DocumentExtraction(BaseModel):
    document_id: str
    text_hash: str
    provider: str
    model: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CanonicalEntity(BaseModel):
    canonical_id: str
    canonical_name: str
    entity_type: EntityType
    aliases: list[str] = Field(default_factory=list)
    resolution_reason: str
    confidence: float = Field(ge=0, le=1)


class ResolvedRelation(BaseModel):
    edge_id: str
    subject_id: str
    subject_name: str
    subject_type: EntityType
    predicate: str
    object_id: str
    object_name: str
    object_type: EntityType
    assertion_type: AssertionType
    confidence: float
    evidence_id: str
    document_id: str
    quote: str
    source_name: str
    source_side: str
    url: str
    title: str
    published_at: datetime | None = None
    extraction_method: str


class BridgeCandidate(BaseModel):
    entity_id: str
    entity_name: str
    entity_type: EntityType
    bridge_score: float
    degree: int
    source_sides: list[str]
    evidence: list[dict[str, Any]]
    final_score: float | None = None
    skeptic_report: dict[str, Any] | None = None


class IndependenceScore(BaseModel):
    score: float = Field(ge=0, le=1)
    passed: bool
    source_sides: list[str]
    source_names: list[str]
    independent_units: list[str]
    collapsed_evidence_count: int
    total_evidence_count: int
    notes: list[str] = Field(default_factory=list)


class NullModelScore(BaseModel):
    score: float = Field(ge=0, le=1)
    passed: bool
    observed_betweenness: float
    null_mean: float
    null_std: float
    p_value: float
    z_score: float
    samples: int
    notes: list[str] = Field(default_factory=list)


class SpecificityScore(BaseModel):
    score: float = Field(ge=0, le=1)
    passed: bool
    predicate_score: float = Field(ge=0, le=1)
    quote_score: float = Field(ge=0, le=1)
    type_score: float = Field(ge=0, le=1)
    degree_penalty: float = Field(ge=0, le=1)
    minimal_claim: str
    notes: list[str] = Field(default_factory=list)


class TemporalAnomalyScore(BaseModel):
    score: float = Field(ge=0, le=1)
    passed: bool
    window_days: int
    max_window_evidence_count: int
    total_evidence_count: int
    corpus_span_days: int
    lift_over_uniform: float
    window_start: datetime | None = None
    window_end: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class SkepticReport(BaseModel):
    candidate_id: str
    candidate_name: str
    final_score: float = Field(ge=0, le=1)
    status: Literal["promote_for_human_review", "needs_more_evidence", "degraded", "discard"]
    independence: IndependenceScore
    null_model: NullModelScore
    specificity: SpecificityScore
    temporal_anomaly: TemporalAnomalyScore
    simplest_explanation: str
    caveats: list[str] = Field(default_factory=list)


class SmallNoteSignal(BaseModel):
    document_id: str
    title: str
    url: str
    source_name: str
    source_side: str
    published_at: datetime | None = None
    score: float = Field(ge=0, le=1)
    attention_score: float = Field(ge=0, le=1)
    low_attention_score: float = Field(ge=0, le=1)
    structural_novelty_score: float = Field(ge=0, le=1)
    novelty_components: dict[str, float] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str


class MorningReading(BaseModel):
    entity_id: str
    entity_name: str
    status: Literal["possible_silence", "official_denial_or_correction"]
    score: float = Field(ge=0, le=1)
    source_sides: list[str]
    non_official_evidence_count: int
    official_mentions_count: int
    window_start: datetime | None = None
    window_end: datetime | None = None
    non_official_evidence: list[dict[str, Any]] = Field(default_factory=list)
    official_quotes: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class InvestigationSignals(BaseModel):
    small_notes: list[SmallNoteSignal] = Field(default_factory=list)
    morning_readings: list[MorningReading] = Field(default_factory=list)
