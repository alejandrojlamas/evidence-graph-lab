from __future__ import annotations

import json
import logging
from pathlib import Path

import networkx as nx

from red_privada.agents.skeptic import SkepticAgent
from red_privada.graph.sqlite_graph import SQLiteGraph
from red_privada.models import BridgeCandidate, ScoringConfig

LOGGER = logging.getLogger(__name__)


class AnalystAgent:
    def __init__(
        self,
        graph: SQLiteGraph,
        output_dir: str | Path,
        scoring_config: ScoringConfig | None = None,
    ):
        self.graph = graph
        self.output_dir = Path(output_dir)
        self.scoring_config = scoring_config or ScoringConfig()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def discover_bridges(self, top_n: int = 10) -> list[BridgeCandidate]:
        nx_graph = self.graph.to_networkx()
        if nx_graph.number_of_nodes() == 0:
            return []
        scores = nx.betweenness_centrality(nx_graph, normalized=True)
        candidates: list[BridgeCandidate] = []
        candidate_limit = top_n * 3 if self.scoring_config.enabled else top_n
        for node_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
            if nx_graph.degree(node_id) == 0:
                continue
            node = nx_graph.nodes[node_id]
            if node.get("entity_type") == "other":
                continue
            sides: set[str] = set()
            for neighbor in nx_graph.neighbors(node_id):
                edge = nx_graph.edges[node_id, neighbor]
                sides.update(edge.get("source_sides", []))
            evidence = self.graph.evidence_for_entity(node_id, limit=6)
            candidates.append(
                BridgeCandidate(
                    entity_id=node_id,
                    entity_name=node.get("name", node_id),
                    entity_type=node.get("entity_type", "other"),
                    bridge_score=score,
                    degree=nx_graph.degree(node_id),
                    source_sides=sorted(sides),
                    evidence=evidence,
                )
            )
            if len(candidates) >= candidate_limit:
                break
        if self.scoring_config.enabled:
            candidates = self._review_candidates(candidates)
        candidates = candidates[:top_n]
        self._write_output(candidates)
        LOGGER.info("puentes priorizados por el analista=%s", len(candidates))
        return candidates

    def _review_candidates(self, candidates: list[BridgeCandidate]) -> list[BridgeCandidate]:
        skeptic = SkepticAgent(self.graph, self.scoring_config)
        reviewed: list[BridgeCandidate] = []
        for candidate in candidates:
            report = skeptic.review(candidate)
            reviewed.append(
                candidate.model_copy(
                    update={
                        "final_score": report.final_score,
                        "skeptic_report": report.model_dump(mode="json"),
                    }
                )
            )
        reviewed.sort(
            key=lambda candidate: (
                candidate.final_score or 0.0,
                candidate.bridge_score,
                len(candidate.source_sides),
            ),
            reverse=True,
        )
        report_path = self.output_dir / "skeptic_reports.json"
        report_path.write_text(
            json.dumps(
                [candidate.skeptic_report for candidate in reviewed if candidate.skeptic_report],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return reviewed

    def _write_output(self, candidates: list[BridgeCandidate]) -> None:
        path = self.output_dir / "bridge_candidates.json"
        payload = [candidate.model_dump(mode="json") for candidate in candidates]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
