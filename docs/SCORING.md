# Puntuación contra la apofenia

En Red Privada, el agente Escéptico revisa cada entidad puente propuesta por el Analista. Su
función es restar peso a las asociaciones superficiales y explicar por qué un indicio merece —o
no— atención humana. No demuestra una tesis.

## Entrada

El Escéptico recibe un `BridgeCandidate` que contiene:

- el nodo canónico, su tipo y su grado;
- un `bridge_score` basado en la intermediación;
- evidencia que puede citarse para las aristas incidentes;
- etiquetas editoriales definidas por el operador en `source_side`.

## Comprobaciones

### Independencia

La evidencia correlacionada se agrupa por `source_side:source_name`. Cinco elementos de una misma
fuente cuentan como una sola unidad. La convergencia solo supera la comprobación cuando existe
diversidad suficiente y están presentes al menos dos valores de `source_side`.

Se trata de una aproximación: distintos medios pueden depender de la misma agencia de noticias,
declaración o grupo empresarial. El sistema no reconstruye por sí mismo esas dependencias.

### Improbabilidad

La intermediación observada se compara con grafos aleatorios generados mediante
`networkx.configuration_model`. El modelo parte de la secuencia de grados observada; al retirar
bucles y colapsar aristas paralelas, el grafo simple resultante la aproxima, pero no la conserva
de forma exacta. El informe incluye la media, la desviación estándar, la puntuación z y el valor
p. Un candidato supera la comprobación cuando satisface tanto `null_model_alpha` como
`improbability_threshold`.

El resultado depende del tamaño y de los sesgos del corpus. No constituye una prueba estadística
de conductas en el mundo real.

### Especificidad

La puntuación penaliza las relaciones imprecisas: coapariciones aisladas, entidades demasiado
amplias y nodos de grado elevado. Favorece predicados no triviales y fragmentos que contienen
roles, fechas, cifras y nombres propios. El resultado es una afirmación mínima que debe
examinarse, no una conclusión.

Ese refuerzo solo se aplica a relaciones con `assertion_type=evidence`. Las inferencias se
conservan para revisión humana, pero no reciben bonificaciones de predicado ni de precisión de
la cita.

### Concentración temporal

El método identifica la ventana con mayor concentración de evidencia y la compara con una línea
base uniforme a lo largo de la cronología del corpus. La presencia de dos etiquetas editoriales
en una misma ventana añade una señal menor, pero esa señal no basta por sí sola para promover un
indicio.

## Estados

- `promote_for_human_review`: supera los umbrales principales y merece revisión prioritaria.
- `needs_more_evidence`: conserva una señal, pero le falta una comprobación de respaldo clave.
- `degraded`: el puente es débil o demasiado previsible.
- `discard`: la independencia o la especificidad son insuficientes.

## Artefactos

- `data/output/bridge_candidates.json`: lista ordenada respaldada por evidencia.
- `data/output/skeptic_reports.json`: desglose auditable de cada comprobación.

Las puntuaciones priorizan el trabajo de investigación humana. No certifican hechos,
intenciones, causalidad ni responsabilidad.
