from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

import evidence_graph_lab
import red_privada
from red_privada.cli import app


def test_primary_and_compatibility_packages_expose_same_version() -> None:
    assert red_privada.__version__ == "0.1.0"
    assert evidence_graph_lab.__version__ == red_privada.__version__


def test_public_cli_uses_red_privada_brand() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Red Privada" in result.stdout
    assert "Evidence Graph Lab" not in result.stdout


def test_packaged_configuration_matches_checkout_template() -> None:
    packaged = Path(red_privada.__file__).parent / "recursos" / "fuentes.yaml"

    assert packaged.is_file()
    assert yaml.safe_load(packaged.read_text(encoding="utf-8")) == yaml.safe_load(
        Path("config/sources.yaml").read_text(encoding="utf-8")
    )


def test_init_creates_editable_configuration(tmp_path: Path) -> None:
    destination = tmp_path / "config" / "sources.yaml"

    result = CliRunner().invoke(app, ["init", "--destino", str(destination)])

    assert result.exit_code == 0
    assert destination.is_file()
    assert "configuracion_creada=" in result.stdout


def test_default_installed_workflow_runs_outside_checkout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 0
    assert '"documents": 0' in result.stdout
    assert (tmp_path / "data" / "state" / "red_privada.sqlite").is_file()
