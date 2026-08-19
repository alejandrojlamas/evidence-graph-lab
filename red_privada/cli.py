from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from red_privada.agents.analyst import AnalystAgent
from red_privada.agents.cartographer import CartographerAgent
from red_privada.agents.collector import CollectorAgent
from red_privada.agents.extractor import ExtractorAgent
from red_privada.agents.orchestrator import Orchestrator
from red_privada.agents.signals import SignalsAgent
from red_privada.config import load_config
from red_privada.graph import Neo4jGraph, SQLiteGraph
from red_privada.storage import SQLiteStore

app = typer.Typer(help="Red Privada")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def load_runtime(config_path: Path):
    config = load_config(config_path)
    store = SQLiteStore(config.project.database_path)
    graph = SQLiteGraph(config.project.database_path)
    return config, store, graph


@app.command()
def ingest(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, _ = load_runtime(config)
    documents = CollectorAgent(cfg, store).run()
    typer.echo(f"documents_collected={len(documents)}")


@app.command()
def extract(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, _ = load_runtime(config)
    extractions = ExtractorAgent(cfg, store).run()
    typer.echo(f"documents_extracted={len(extractions)}")


@app.command(name="graph")
def graph_command(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, graph = load_runtime(config)
    documents = store.list_documents()
    extractions = store.list_extractions()
    if cfg.graph.backend == "neo4j":
        neo4j_graph = Neo4jGraph(cfg.graph.neo4j)
        try:
            stats = CartographerAgent(cfg, neo4j_graph).run(documents, extractions)
        finally:
            neo4j_graph.close()
    else:
        stats = CartographerAgent(cfg, graph).run(documents, extractions)
    typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))


@app.command()
def discover(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    top: int = typer.Option(10, "--top"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, _, graph = load_runtime(config)
    candidates = AnalystAgent(graph, cfg.project.output_dir, cfg.scoring).discover_bridges(top_n=top)
    if json_output:
        typer.echo(json.dumps([c.model_dump(mode="json") for c in candidates], ensure_ascii=False, indent=2))
        return
    for idx, candidate in enumerate(candidates, start=1):
        entity_type = getattr(candidate.entity_type, "value", str(candidate.entity_type))
        typer.echo(
            f"{idx}. {candidate.entity_name} [{entity_type}] "
            f"bridge={candidate.bridge_score:.4f} final={(candidate.final_score or 0):.4f} "
            f"degree={candidate.degree} "
            f"sides={','.join(candidate.source_sides) or 'n/a'}"
        )
        if candidate.skeptic_report:
            typer.echo(f"   status={candidate.skeptic_report['status']}")
        for evidence in candidate.evidence[:3]:
            quote = evidence["quote"][:220].replace("\n", " ")
            typer.echo(f"   - {evidence['source_side']} {evidence['url']}: {quote}")


@app.command()
def score(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    top: int = typer.Option(10, "--top"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, _, graph = load_runtime(config)
    candidates = AnalystAgent(graph, cfg.project.output_dir, cfg.scoring).discover_bridges(top_n=top)
    reports = [candidate.skeptic_report for candidate in candidates if candidate.skeptic_report]
    typer.echo(json.dumps(reports, ensure_ascii=False, indent=2))


@app.command()
def signals(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, graph = load_runtime(config)
    output = SignalsAgent(cfg, store, graph).run()
    if json_output:
        typer.echo(output.model_dump_json(indent=2))
        return
    typer.echo(f"small_notes={len(output.small_notes)} morning_readings={len(output.morning_readings)}")
    for idx, note in enumerate(output.small_notes[:5], start=1):
        typer.echo(
            f"{idx}. small_note score={note.score:.4f} novelty={note.structural_novelty_score:.4f} "
            f"low_attention={note.low_attention_score:.4f} {note.title}"
        )
        typer.echo(f"   {note.source_side} {note.url}")
    for idx, reading in enumerate(output.morning_readings[:5], start=1):
        typer.echo(
            f"{idx}. morning {reading.status} score={reading.score:.4f} "
            f"{reading.entity_name} sides={','.join(reading.source_sides)}"
        )


@app.command()
def run(
    config: Path = typer.Option(Path("config/sources.yaml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, graph = load_runtime(config)
    state = Orchestrator(cfg, store, graph).run()
    typer.echo(
        json.dumps(
            {
                "documents": len(state.get("documents", [])),
                "extractions": len(state.get("extractions", [])),
                "graph_stats": state.get("graph_stats", {}),
                "bridges": [b.model_dump(mode="json") for b in state.get("bridges", [])],
                "signals": state.get("signals", {}).model_dump(mode="json")
                if state.get("signals")
                else {},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
