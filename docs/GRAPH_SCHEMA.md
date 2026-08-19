# Esquema del grafo

## Principio de procedencia

Ninguna arista se almacena sin procedencia. La evidencia directa y la inferencia usan valores
distintos en `assertion_type`; una inferencia debe conservar su propio método y evidencia. El
pipeline incluido escribe relaciones sustentadas por fragmentos del corpus, incluidas
co-menciones que no deben interpretarse como causalidad.

## Modelo Neo4j opcional

### Nodos

- `(:Entity)`
  - `id`: identificador canónico estable.
  - `name`: nombre canónico.
  - `type`: `person | organization | company | place | event | other`.
  - `aliases`: alias conocidos.
  - `resolution_reason`: regla que resolvió la identidad.
  - `confidence`: confianza de resolución.

- `(:Document)`
  - `id`: identificador estable por fuente y URL.
  - `url`, `title`, `source_name`, `source_side`, `published_at`.

- `(:Evidence)`
  - `id`: identificador estable por arista, documento y fragmento.
  - `quote`: fragmento citable.
  - `source_side`: etiqueta editorial configurada para la fuente.
  - `extraction_method`: proveedor y modelo del extractor.

### Relaciones

- `(subject:Entity)-[:ASSERTS]->(object:Entity)`
  - `id`: identificador estable.
  - `predicate`: tipo de relación, por ejemplo `OFFICIAL_ROLE`, `MENTIONS` o
    `CO_MENTIONED_WITH`.
  - `assertion_type`: `evidence` o `inference`.
  - `confidence`.

- `(evidence:Evidence)-[:SUPPORTS]->(assertion)`
- `(evidence:Evidence)-[:FROM_DOCUMENT]->(document:Document)`

## Salidas analíticas

El scoring y las señales no modifican el grafo; producen artefactos revisables en `data/output/`:

- `bridge_candidates.json`: candidatos ordenados con puntajes, etiquetas y evidencia.
- `skeptic_reports.json`: independencia, modelo nulo, especificidad y concentración temporal.
- `small_notes.json`: documentos de baja atención relativa con novedad estructural.
- `morning_readings.json`: contraste textual con el corpus oficial configurado.
- `signals.json`: salida combinada de señales.

El Escéptico no convierte una arista en hecho probado. Solo asigna
`promote_for_human_review`, `needs_more_evidence`, `degraded` o `discard` a una pista.

## Backend SQLite

SQLite es el backend del flujo analítico local. Usa las tablas:

- `graph_entities`
- `graph_edges`
- `graph_evidence`

La idempotencia se obtiene con `canonical_id`, `edge_id` y `evidence_id` como claves primarias.
Ejecutar dos veces el mismo pipeline no duplica entidades, aristas ni evidencia.

El comando `graph` puede exportar el conjunto resuelto a Neo4j cuando se configura ese backend.
La exploración y el scoring continúan sobre SQLite; no existe sincronización bidireccional.
