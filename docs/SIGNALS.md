# Señales para revisión

En Red Privada, el módulo de señales prioriza documentos para su revisión. No demuestra una tesis
ni añade al grafo relaciones inferidas.

## Fuentes y etiquetas

El recolector `rss_feed` puede operar únicamente con la descripción incluida en cada entrada del
canal (`fetch_article: false`) y evitar la descarga del artículo completo cuando el resumen es
suficiente. Cada fuente conserva:

- `source_side`: etiqueta editorial definida por el operador;
- `attention_weight`: peso relativo de atención;
- `attention_note`: justificación humana de esa ponderación.

Las fuentes de ejemplo están deshabilitadas. Antes de habilitar una, comprueba que siga vigente y
revisa sus condiciones de uso, derechos de autor y política de automatización. Una etiqueta no
caracteriza objetivamente a un medio ni demuestra que sea independiente de otras fuentes.

## Baja atención y novedad estructural

Un documento adquiere prioridad cuando combina:

- atención relativamente baja, estimada a partir del peso de la fuente y de su posición en un
  canal o listado;
- novedad estructural, estimada mediante aristas poco frecuentes, nodos extremos con
  intermediación elevada, aristas que cruzan etiquetas editoriales o predicados más específicos
  que una coaparición.

Las relaciones con `assertion_type=inference` permanecen disponibles para revisión, pero no
aportan puntuación de novedad estructural.

La puntuación es una heurística dependiente del corpus. Un documento priorizado no es
intrínsecamente sospechoso: simplemente aporta una estructura que merece revisión.

## Comparación con un corpus oficial

El agente compara evidencia no oficial con documentos oficiales dentro de una ventana
configurable:

- `possible_silence`: una entidad aparece fuera del corpus oficial, pero no se encuentra de forma
  literal en los documentos oficiales de la ventana;
- `official_denial_or_correction`: el corpus oficial menciona la entidad en proximidad textual a
  expresiones de negación o corrección. El léxico predeterminado incluye expresiones en español
  porque el corpus de muestra está en ese idioma.

Limitaciones:

- el análisis es textual, no semántico;
- la ausencia no implica ocultamiento ni intención;
- una coincidencia con una expresión de negación puede carecer de contexto;
- cuando el corpus oficial precede a la evidencia externa, el informe añade una advertencia;
- una cobertura incompleta, las fechas ausentes y los errores de extracción distorsionan el
  resultado.

## Artefactos

- `data/output/small_notes.json`
- `data/output/morning_readings.json`
- `data/output/signals.json`

Todos los artefactos son listas de lectura que deben contrastarse con las fuentes originales.
