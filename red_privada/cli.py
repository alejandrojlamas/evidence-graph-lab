from __future__ import annotations

import json
import logging
import shutil
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
from red_privada.llm import extraction_cache_identity
from red_privada.storage import SQLiteStore

DEFAULT_CONFIG_PATH = Path(__file__).parent / "recursos" / "fuentes.yaml"

app = typer.Typer(
    help="Red Privada: cartografía de relaciones con evidencia, procedencia y revisión humana."
)


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


@app.command(name="init", help="Crea una configuración editable a partir de la plantilla segura.")
def init_config(
    destino: Path = typer.Option(Path("config/sources.yaml"), "--destino", "-d"),
    sobrescribir: bool = typer.Option(False, "--sobrescribir"),
) -> None:
    if destino.exists() and not sobrescribir:
        raise typer.BadParameter(
            f"{destino} ya existe; usa --sobrescribir solo si deseas reemplazarlo"
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_CONFIG_PATH, destino)
    typer.echo(f"configuracion_creada={destino}")


@app.command(help="Recolecta documentos únicamente de fuentes habilitadas de forma explícita.")
def ingest(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, _ = load_runtime(config)
    documents = CollectorAgent(cfg, store).run()
    typer.echo(f"documents_collected={len(documents)}")


@app.command(help="Extrae entidades y relaciones respaldadas por citas de los documentos.")
def extract(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, _ = load_runtime(config)
    extractions = ExtractorAgent(cfg, store).run()
    typer.echo(f"documents_extracted={len(extractions)}")


@app.command(name="graph", help="Construye el grafo local o exporta datos resueltos a Neo4j.")
def graph_command(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, graph = load_runtime(config)
    documents = store.list_documents()
    provider, model, policy_fingerprint = extraction_cache_identity(cfg)
    extractions = store.list_extractions(
        expected_provider=provider,
        expected_model=model,
        expected_policy_fingerprint=policy_fingerprint,
    )
    if cfg.graph.backend == "neo4j":
        neo4j_graph = Neo4jGraph(cfg.graph.neo4j)
        try:
            stats = CartographerAgent(cfg, neo4j_graph).run(documents, extractions)
        finally:
            neo4j_graph.close()
    else:
        stats = CartographerAgent(cfg, graph).run(documents, extractions)
    typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))


@app.command(help="Ordena entidades puente para una revisión humana guiada por evidencia.")
def discover(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
    top: int = typer.Option(10, "--top"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, _, graph = load_runtime(config)
    candidates = AnalystAgent(graph, cfg.project.output_dir, cfg.scoring).discover_bridges(
        top_n=top
    )
    if json_output:
        typer.echo(
            json.dumps(
                [c.model_dump(mode="json") for c in candidates], ensure_ascii=False, indent=2
            )
        )
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


@app.command(help="Genera reportes auditables de puntuación contra asociaciones espurias.")
def score(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
    top: int = typer.Option(10, "--top"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, _, graph = load_runtime(config)
    candidates = AnalystAgent(graph, cfg.project.output_dir, cfg.scoring).discover_bridges(
        top_n=top
    )
    reports = [candidate.skeptic_report for candidate in candidates if candidate.skeptic_report]
    typer.echo(json.dumps(reports, ensure_ascii=False, indent=2))


@app.command(help="Genera señales de baja atención y contraste con el corpus oficial.")
def signals(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging(verbose)
    cfg, store, graph = load_runtime(config)
    output = SignalsAgent(cfg, store, graph).run()
    if json_output:
        typer.echo(output.model_dump_json(indent=2))
        return
    typer.echo(
        f"small_notes={len(output.small_notes)} morning_readings={len(output.morning_readings)}"
    )
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


@app.command(help="Ejecuta el flujo local completo de investigación sobre SQLite.")
def run(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", show_default=False),
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
