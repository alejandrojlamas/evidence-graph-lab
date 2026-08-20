from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from red_privada.agents.analyst import AnalystAgent
from red_privada.agents.cartographer import CartographerAgent
from red_privada.agents.collector import CollectorAgent
from red_privada.agents.extractor import ExtractorAgent
from red_privada.agents.signals import SignalsAgent
from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.llm import extraction_cache_identity
from red_privada.models import (
    AppConfig,
    BridgeCandidate,
    DocumentExtraction,
    InvestigationSignals,
    RawDocument,
)
from red_privada.storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    documents: list[RawDocument]
    extractions: list[DocumentExtraction]
    graph_stats: dict[str, int]
    bridges: list[BridgeCandidate]
    signals: InvestigationSignals


class Orchestrator:
    def __init__(self, config: AppConfig, store: SQLiteStore, graph: SQLiteGraph):
        self.config = config
        self.store = store
        self.graph = graph
        self.workflow = self._build_workflow()

    def run(self) -> PipelineState:
        return self.workflow.invoke({})

    def _build_workflow(self):
        builder: StateGraph = StateGraph(PipelineState)
        builder.add_node("collect", self._collect)
        builder.add_node("extract", self._extract)
        builder.add_node("map_graph", self._map_graph)
        builder.add_node("discover", self._discover)
        builder.add_node("signals", self._signals)
        builder.add_edge(START, "collect")
        builder.add_edge("collect", "extract")
        builder.add_edge("extract", "map_graph")
        builder.add_edge("map_graph", "discover")
        builder.add_edge("discover", "signals")
        builder.add_edge("signals", END)
        return builder.compile()

    def _collect(self, state: PipelineState) -> PipelineState:
        collected = CollectorAgent(self.config, self.store).run()
        documents = self.store.list_documents()
        if not collected:
            LOGGER.info(
                "el colector no devolvió documentos nuevos; usando almacenados=%s",
                len(documents),
            )
        elif len(documents) != len(collected):
            LOGGER.info(
                "documentos nuevos=%s; procesando corpus almacenado completo=%s",
                len(collected),
                len(documents),
            )
        return {"documents": documents}

    def _extract(self, state: PipelineState) -> PipelineState:
        documents = state.get("documents") or self.store.list_documents()
        extractions = ExtractorAgent(self.config, self.store).run(documents)
        return {"extractions": extractions}

    def _map_graph(self, state: PipelineState) -> PipelineState:
        documents = state.get("documents") or self.store.list_documents()
        extractions = state.get("extractions")
        if extractions is None:
            provider, model, policy_fingerprint = extraction_cache_identity(self.config)
            extractions = self.store.list_extractions(
                expected_provider=provider,
                expected_model=model,
                expected_policy_fingerprint=policy_fingerprint,
            )
        stats = CartographerAgent(self.config, self.graph).run(documents, extractions)
        return {"graph_stats": stats}

    def _discover(self, state: PipelineState) -> PipelineState:
        bridges = AnalystAgent(
            self.graph,
            self.config.project.output_dir,
            self.config.scoring,
        ).discover_bridges()
        return {"bridges": bridges}

    def _signals(self, state: PipelineState) -> PipelineState:
        if not self.config.signals.enabled:
            return {"signals": InvestigationSignals()}
        signals = SignalsAgent(self.config, self.store, self.graph).run()
        return {"signals": signals}
