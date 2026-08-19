from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from red_privada.agents.signals import SignalsAgent
from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import (
    AppConfig,
    AssertionType,
    CanonicalEntity,
    EntityType,
    GraphConfig,
    LLMConfig,
    ProjectConfig,
    RawDocument,
    ResolvedRelation,
    ResolverConfig,
    SignalsConfig,
    SourceConfig,
)
from red_privada.sources.rss_feed import RSSFeedCollector
from red_privada.storage import SQLiteStore


def test_rss_feed_collector_uses_feed_summary_without_fetching_article(tmp_path) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel>
        <item>
          <title>Short item about Pemex</title>
          <link>https://example.org/opinion/pemex</link>
          <dc:creator>Author</dc:creator>
          <pubDate>Mon, 08 Jun 2026 12:00:00 GMT</pubDate>
          <description><![CDATA[Pemex and CNTE appear in a low-attention item.]]></description>
        </item>
      </channel>
    </rss>
    """
    fetcher = _FakeFetcher({"https://example.org/feed.xml": xml}, tmp_path)
    source = SourceConfig(
        name="test_rss",
        kind="rss_feed",
        side="independent_opinion",
        feed_url="https://example.org/feed.xml",
        fetch_article=False,
        min_text_chars=30,
        attention_weight=0.3,
    )

    documents = RSSFeedCollector(source, fetcher).collect()

    assert len(documents) == 1
    assert documents[0].url == "https://example.org/opinion/pemex"
    assert documents[0].metadata["feed_rank"] == 1
    assert documents[0].metadata["source_attention_weight"] == 0.3
    assert "Pemex and CNTE" in documents[0].text


def test_signals_agent_finds_small_notes_and_morning_readings(tmp_path) -> None:
    db_path = tmp_path / "evidence_graph_lab.sqlite"
    output_dir = tmp_path / "output"
    store = SQLiteStore(db_path)
    graph = SQLiteGraph(db_path)
    cfg = _config(tmp_path, db_path, output_dir)
    official = _doc(
        "doc_official",
        "presidencia_mananera",
        "gob_presidency",
        "official_government",
        "https://example.org/mananera",
        "Morning briefing",
        "It is false that Pemex concealed information. The president discussed energy.",
        datetime(2026, 6, 9, tzinfo=timezone.utc),
        {"source_attention_weight": 1.0, "archive_rank": 1},
    )
    small = _doc(
        "doc_small",
        "small_rss",
        "rss_feed",
        "independent_opinion",
        "https://example.org/small",
        "Short item",
        "A secondary item mentions Pemex and CNTE in connection with demonstrations.",
        datetime(2026, 6, 8, tzinfo=timezone.utc),
        {"source_attention_weight": 0.25, "feed_rank": 6},
    )
    store.upsert_document(official)
    store.upsert_document(small)
    graph.upsert_entities(
        [
            CanonicalEntity(
                canonical_id="ent_pemex",
                canonical_name="Pemex",
                entity_type=EntityType.company,
                aliases=["Pemex"],
                resolution_reason="test",
                confidence=1,
            ),
            CanonicalEntity(
                canonical_id="ent_cnte",
                canonical_name="CNTE",
                entity_type=EntityType.organization,
                aliases=[],
                resolution_reason="test",
                confidence=1,
            ),
        ]
    )
    graph.upsert_relations(
        [
            _relation(
                "edge_pemex_cnte",
                "ent_pemex",
                "Pemex",
                EntityType.company,
                "ent_cnte",
                "CNTE",
                EntityType.organization,
                "ev_small",
                small,
                "Pemex and CNTE in connection with demonstrations.",
            )
        ]
    )

    signals = SignalsAgent(cfg, store, graph).run()

    assert signals.small_notes
    assert signals.small_notes[0].document_id == "doc_small"
    statuses = {reading.entity_name: reading.status for reading in signals.morning_readings}
    assert statuses["Pemex"] == "official_denial_or_correction"
    assert statuses["CNTE"] == "possible_silence"
    assert (output_dir / "small_notes.json").exists()
    assert (output_dir / "morning_readings.json").exists()


class _FakeFetcher:
    def __init__(self, responses: dict[str, str], tmp_path: Path):
        self.responses = responses
        self.tmp_path = tmp_path

    def fetch(self, url: str):
        return self.responses[url], self.tmp_path / "feed.xml", True


def _config(tmp_path, db_path, output_dir) -> AppConfig:
    return AppConfig(
        project=ProjectConfig(
            user_agent="test",
            cache_dir=str(tmp_path / "cache"),
            database_path=str(db_path),
            output_dir=str(output_dir),
        ),
        llm=LLMConfig(provider="dev"),
        resolver=ResolverConfig(),
        graph=GraphConfig(),
        signals=SignalsConfig(
            low_attention_threshold=0.3,
            structural_novelty_threshold=0.2,
            small_note_min_score=0.35,
            morning_lookback_days=14,
        ),
        sources=[
            SourceConfig(
                name="small_rss",
                kind="rss_feed",
                side="independent_opinion",
                attention_weight=0.25,
            )
        ],
    )


def _doc(
    document_id: str,
    source_name: str,
    source_kind: str,
    source_side: str,
    url: str,
    title: str,
    text: str,
    published_at: datetime,
    metadata: dict,
) -> RawDocument:
    return RawDocument(
        id=document_id,
        source_name=source_name,
        source_kind=source_kind,
        source_side=source_side,
        url=url,
        title=title,
        text=text,
        author=None,
        published_at=published_at,
        fetched_at=published_at,
        raw_html_path=None,
        metadata=metadata,
    )


def _relation(
    edge_id: str,
    subject_id: str,
    subject_name: str,
    subject_type: EntityType,
    object_id: str,
    object_name: str,
    object_type: EntityType,
    evidence_id: str,
    document: RawDocument,
    quote: str,
) -> ResolvedRelation:
    return ResolvedRelation(
        edge_id=edge_id,
        subject_id=subject_id,
        subject_name=subject_name,
        subject_type=subject_type,
        predicate="CO_MENTIONED_WITH",
        object_id=object_id,
        object_name=object_name,
        object_type=object_type,
        assertion_type=AssertionType.evidence,
        confidence=0.7,
        evidence_id=evidence_id,
        document_id=document.id,
        quote=quote,
        source_name=document.source_name,
        source_side=document.source_side,
        url=document.url,
        title=document.title,
        published_at=document.published_at,
        extraction_method="test",
    )
