UV ?= uv
PYTHON ?= .venv/bin/python
CONFIG ?= config/sources.yaml

.PHONY: install install-embeddings ingest extract graph discover score signals run test lint neo4j-up

install:
	$(UV) venv --python 3.11
	$(UV) pip install -e ".[dev]"

install-embeddings:
	$(UV) pip install -e ".[dev,embeddings]"

ingest:
	$(PYTHON) -m red_privada.cli ingest --config $(CONFIG)

extract:
	$(PYTHON) -m red_privada.cli extract --config $(CONFIG)

graph:
	$(PYTHON) -m red_privada.cli graph --config $(CONFIG)

discover:
	$(PYTHON) -m red_privada.cli discover --config $(CONFIG)

score:
	$(PYTHON) -m red_privada.cli score --config $(CONFIG)

signals:
	$(PYTHON) -m red_privada.cli signals --config $(CONFIG)

run:
	$(PYTHON) -m red_privada.cli run --config $(CONFIG)

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check red_privada tests

neo4j-up:
	@echo "Docker is required. If available, run: docker compose up -d neo4j"
