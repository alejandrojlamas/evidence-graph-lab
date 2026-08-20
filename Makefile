UV ?= uv
PYTHON ?= .venv/bin/python
PYTHON_VERSION ?= 3.11
CONFIG ?= config/sources.yaml

.PHONY: install ingest extract graph discover score signals run test lint build neo4j-up

install:
	$(UV) sync --locked --extra dev --python $(PYTHON_VERSION)

ingest:
	$(PYTHON) -m red_privada ingest --config $(CONFIG)

extract:
	$(PYTHON) -m red_privada extract --config $(CONFIG)

graph:
	$(PYTHON) -m red_privada graph --config $(CONFIG)

discover:
	$(PYTHON) -m red_privada discover --config $(CONFIG)

score:
	$(PYTHON) -m red_privada score --config $(CONFIG)

signals:
	$(PYTHON) -m red_privada signals --config $(CONFIG)

run:
	$(PYTHON) -m red_privada run --config $(CONFIG)

test:
	$(UV) run --locked --extra dev python -m pytest

lint:
	$(UV) run --locked --extra dev python -m ruff check evidence_graph_lab red_privada tests
	$(UV) run --locked --extra dev python -m ruff format --check evidence_graph_lab red_privada tests

build:
	$(UV) run --locked --extra dev python -m build

neo4j-up:
	@echo "Docker es obligatorio. Si está disponible, ejecuta: docker compose up -d neo4j"
