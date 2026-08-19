# Señales de lectura

El módulo de señales prioriza documentos para revisión. No prueba tesis ni agrega inferencias al
grafo.

## Fuentes y etiquetas

El recolector `rss_feed` puede trabajar únicamente con la descripción del feed
(`fetch_article: false`), lo que evita descargar el artículo completo cuando el resumen basta.
Cada fuente conserva:

- `source_side`: etiqueta editorial definida por el usuario;
- `attention_weight`: peso de atención relativa;
- `attention_note`: justificación humana del peso.

Las fuentes de ejemplo están deshabilitadas. Antes de habilitar una, confirme su vigencia,
términos, copyright y política de automatización. Una etiqueta no describe objetivamente al medio
ni demuestra independencia respecto de otras fuentes.

## Baja atención y novedad estructural

Un documento se prioriza cuando combina:

- baja atención relativa, estimada a partir del peso de la fuente y su posición en un feed o
  listado;
- novedad estructural, estimada mediante aristas raras, endpoints con intermediación, cruce de
  etiquetas o predicados más específicos que una co-mención.

El puntaje es una heurística dependiente del corpus. Una nota priorizada no es sospechosa por sí
misma; solo introduce estructura que merece lectura.

## Contraste con un corpus oficial

El agente compara evidencia no oficial con documentos oficiales dentro de una ventana
configurable:

- `possible_silence`: una entidad aparece fuera del corpus oficial y no se encuentra textualmente
  en sus documentos de la ventana;
- `official_denial_or_correction`: el corpus oficial menciona la entidad cerca de expresiones como
  "no es cierto", "falso", "desmentido" o "aclaró".

Limitaciones:

- el análisis es textual, no semántico;
- una ausencia no implica ocultamiento ni intención;
- una coincidencia con palabras de negación puede carecer de contexto;
- si el corpus oficial precede a la evidencia externa, el reporte agrega una advertencia;
- cobertura incompleta, fechas ausentes y errores de extracción distorsionan el resultado.

## Artefactos

- `data/output/small_notes.json`
- `data/output/morning_readings.json`
- `data/output/signals.json`

Todos son colas de lectura que requieren comprobación contra las fuentes originales.
