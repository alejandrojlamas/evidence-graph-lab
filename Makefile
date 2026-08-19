UV ?= uv
PYTHON ?= .venv/bin/python
CONFIG ?= config/sources.yaml

.PHONY: install install-embeddings ingest extract graph discover score signals run test lint build neo4j-up

install:
	$(UV) venv --python 3.11
	$(UV) pip install -e ".[dev]"

install-embeddings:
	$(UV) pip install -e ".[dev,embeddings]"

ingest:
	$(PYTHON) -m evidence_graph_lab ingest --config $(CONFIG)

extract:
	$(PYTHON) -m evidence_graph_lab extract --config $(CONFIG)

graph:
	$(PYTHON) -m evidence_graph_lab graph --config $(CONFIG)

discover:
	$(PYTHON) -m evidence_graph_lab discover --config $(CONFIG)

score:
	$(PYTHON) -m evidence_graph_lab score --config $(CONFIG)

signals:
	$(PYTHON) -m evidence_graph_lab signals --config $(CONFIG)

run:
	$(PYTHON) -m evidence_graph_lab run --config $(CONFIG)

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check evidence_graph_lab red_privada tests

build:
	$(PYTHON) -m build

neo4j-up:
	@echo "Docker is required. If available, run: docker compose up -d neo4j"
