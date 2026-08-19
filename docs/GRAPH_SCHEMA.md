# Graph schema

## Provenance principle

No edge is stored without provenance. Direct evidence and inference use distinct `assertion_type`
values; an inference must retain its own method and supporting evidence. The included pipeline
writes excerpt-backed relationships, including co-mentions that must not be interpreted as
causality.

## Optional Neo4j model

### Nodes

- `(:Entity)`
  - `id`: stable canonical identifier.
  - `name`: canonical name.
  - `type`: `person | organization | company | place | event | other`.
  - `aliases`: known aliases.
  - `resolution_reason`: rule used to resolve the identity.
  - `confidence`: resolution confidence.

- `(:Document)`
  - `id`: stable identifier derived from source and URL.
  - `url`, `title`, `source_name`, `source_side`, `published_at`.

- `(:Evidence)`
  - `id`: stable identifier derived from edge, document, and excerpt.
  - `quote`: citable excerpt.
  - `source_side`: operator-defined editorial label for the source.
  - `extraction_method`: extractor provider and model.

### Relationships

- `(subject:Entity)-[:ASSERTS]->(object:Entity)`
  - `id`: stable identifier.
  - `predicate`: relationship type, such as `OFFICIAL_ROLE`, `MENTIONS`, or
    `CO_MENTIONED_WITH`.
  - `assertion_type`: `evidence` or `inference`.
  - `confidence`.

- `(evidence:Evidence)-[:SUPPORTS]->(assertion)`
- `(evidence:Evidence)-[:FROM_DOCUMENT]->(document:Document)`

## Analytical outputs

Scoring and signals do not mutate the graph. They produce reviewable artifacts in `data/output/`:

- `bridge_candidates.json`: ranked candidates with scores, labels, and evidence.
- `skeptic_reports.json`: independence, null-model, specificity, and temporal-concentration checks.
- `small_notes.json`: low-attention documents with structural novelty.
- `morning_readings.json`: textual comparison with the configured official corpus.
- `signals.json`: combined signal output.

The Skeptic does not turn an edge into a proven fact. It only assigns
`promote_for_human_review`, `needs_more_evidence`, `degraded`, or `discard` to a lead.

## SQLite backend

SQLite is the local analytical-workflow backend. It uses these tables:

- `graph_entities`
- `graph_edges`
- `graph_evidence`

`canonical_id`, `edge_id`, and `evidence_id` serve as primary keys, making writes idempotent.
Running the same pipeline twice does not duplicate entities, edges, or evidence.

The `graph` command can export the resolved dataset to Neo4j when that backend is configured.
Exploration and scoring continue to use SQLite; there is no bidirectional synchronization.
