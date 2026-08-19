# Scoring antiapofenia

El agente Escéptico revisa cada entidad-puente propuesta por el Analista. Su tarea es degradar
asociaciones fáciles y explicar por qué una pista merece —o no— lectura humana; no prueba una
tesis.

## Entrada

El Escéptico recibe un `BridgeCandidate` con:

- nodo canónico, tipo y grado;
- `bridge_score` calculado por intermediación;
- evidencia citable de las aristas incidentes;
- etiquetas editoriales configuradas en `source_side`.

## Pruebas

### Independencia

Colapsa evidencia correlacionada por `source_side:source_name`. Cinco notas de la misma fuente
cuentan como una sola unidad. La convergencia pasa únicamente si existe diversidad suficiente y
al menos dos valores de `source_side`.

Esta es una aproximación: dos medios distintos pueden depender de la misma agencia, comunicado o
grupo empresarial. El sistema no reconstruye por sí solo esas dependencias.

### Improbabilidad

Compara la intermediación observada contra grafos aleatorios creados con
`networkx.configuration_model`, preservando la distribución de grados. Reporta media, desviación
estándar, z-score y p-value. El candidato pasa si cumple `null_model_alpha` y
`improbability_threshold`.

El resultado depende del tamaño y del sesgo del corpus; no es una prueba estadística de conducta
en el mundo real.

### Especificidad

Penaliza relaciones vagas: solo co-menciones, entidades demasiado generales y nodos con grado
alto. Premia predicados no triviales, fragmentos con roles, fechas, números y nombres propios. El
resultado es una afirmación mínima para revisar, no una conclusión.

### Concentración temporal

Busca la ventana con mayor concentración de evidencia y la compara con una línea base uniforme
dentro del rango temporal del corpus. Si dos etiquetas editoriales aparecen en la misma ventana,
suma una señal pequeña, pero no basta para promover una pista.

## Estados

- `promote_for_human_review`: supera los principales umbrales y merece revisión prioritaria.
- `needs_more_evidence`: conserva señal, pero falta alguna prueba clave.
- `degraded`: el puente es débil o demasiado esperable.
- `discard`: independencia o especificidad insuficientes.

## Artefactos

- `data/output/bridge_candidates.json`: ranking con evidencia.
- `data/output/skeptic_reports.json`: desglose auditable de cada prueba.

Los puntajes sirven para ordenar trabajo humano. No certifican hechos, intención, causalidad ni
responsabilidad.
