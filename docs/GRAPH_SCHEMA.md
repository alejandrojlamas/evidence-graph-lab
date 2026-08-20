# Esquema del grafo

## Principio de procedencia

Red Privada no almacena ninguna arista sin su procedencia. La evidencia directa y las inferencias
emplean valores distintos en `assertion_type`; cada inferencia debe conservar su propio método y
la evidencia que la sustenta. El flujo de procesamiento incluido registra relaciones respaldadas
por fragmentos textuales, incluidas las coapariciones, que no deben interpretarse como causalidad.

## Modelo opcional para Neo4j

### Nodos

- `(:Entity)`
  - `id`: identificador canónico estable.
  - `name`: nombre canónico.
  - `type`: `person | organization | company | place | event | other`.
  - `aliases`: alias conocidos.
  - `resolution_reason`: regla empleada para resolver la identidad.
  - `confidence`: confianza de la resolución.

- `(:Document)`
  - `id`: identificador estable derivado de la fuente y la URL.
  - `url`, `title`, `source_name`, `source_side`, `published_at`.

- `(:Evidence)`
  - `id`: identificador estable derivado de la arista, el documento y el fragmento.
  - `quote`: fragmento textual citable.
  - `source_side`: etiqueta editorial de la fuente, definida por el operador.
  - `extraction_method`: proveedor y modelo del extractor.

- `(:Assertion)`
  - `id`: identificador estable compartido con la relación lógica.
  - `predicate`: tipo de relación propuesta.
  - `assertion_type`: `evidence` o `inference`.
  - `confidence`: confianza acumulada de la afirmación.

### Relaciones

- `(assertion:Assertion)-[:SUBJECT]->(subject:Entity)`
- `(assertion:Assertion)-[:OBJECT]->(object:Entity)`
- `(evidence:Evidence)-[:SUPPORTS]->(assertion:Assertion)`
- `(evidence:Evidence)-[:FROM_DOCUMENT]->(document:Document)`

Como proyección compatible para consultas directas, la exportación también conserva:

- `(subject:Entity)-[:ASSERTS]->(object:Entity)`
  - `id`: identificador estable.
  - `predicate`: tipo de relación, como `OFFICIAL_ROLE`, `MENTIONS` o
    `CO_MENTIONED_WITH`.
  - `assertion_type`: `evidence` o `inference`.
  - `confidence`.

El nodo `Assertion` permite enlazar la evidencia con una afirmación concreta; Neo4j no admite
relaciones dirigidas hacia otra relación. La arista directa `ASSERTS` es una proyección de consulta
y no sustituye al nodo cuando se necesita examinar la procedencia.

## Resultados analíticos

La puntuación y las señales no modifican el grafo. Producen artefactos revisables en
`data/output/`:

- `bridge_candidates.json`: candidatos ordenados por puntuación, con etiquetas y evidencia.
- `skeptic_reports.json`: comprobaciones de independencia, modelo nulo, especificidad y
  concentración temporal.
- `small_notes.json`: documentos de baja atención con novedad estructural.
- `morning_readings.json`: comparación textual con el corpus oficial configurado.
- `signals.json`: resultado combinado de las señales.

El Escéptico no convierte una arista en un hecho demostrado. Solo asigna a cada indicio uno de
los estados `promote_for_human_review`, `needs_more_evidence`, `degraded` o `discard`.

## Motor SQLite

SQLite es el motor local del flujo de trabajo analítico. Utiliza las siguientes tablas:

- `graph_entities`
- `graph_edges`
- `graph_evidence`

`canonical_id`, `edge_id` y `evidence_id` funcionan como claves primarias, por lo que las
escrituras son idempotentes. Ejecutar el mismo flujo dos veces no duplica entidades, aristas ni
registros de evidencia.

El comando `graph` puede exportar a Neo4j el conjunto de datos con identidades resueltas cuando
ese motor está configurado. La exploración y la puntuación continúan utilizando SQLite; no existe
sincronización bidireccional.

La exportación Neo4j es incremental y no elimina automáticamente relaciones de ejecuciones
anteriores. Si un documento o su extracción cambian, genera una instantánea limpia en una base o
un volumen dedicado nuevo. No uses esta exportación sobre una base compartida cuyos datos no
pertenezcan exclusivamente a Red Privada.
