from __future__ import annotations

from typer.testing import CliRunner

import evidence_graph_lab
from evidence_graph_lab.cli import app


def test_public_package_exposes_version() -> None:
    assert evidence_graph_lab.__version__ == "0.1.0"


def test_public_cli_uses_evidence_graph_lab_brand() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Evidence Graph Lab" in result.stdout
