# Contribuir a Red Privada

Gracias por considerar una contribución. Red Privada es un laboratorio de investigación asistida
por IA para explorar fuentes autorizadas, conservar la procedencia de la evidencia y producir
hallazgos que siempre requieren revisión humana.

Las contribuciones deben favorecer resultados reproducibles, explicables y prudentes. El proyecto
no convierte coincidencias, relaciones de grafo ni señales estadísticas en acusaciones o
conclusiones automáticas.

## Antes de empezar

- Revisa los *issues* existentes para evitar trabajo duplicado.
- Para cambios amplios, abre primero una propuesta de mejora y explica el problema que buscas
  resolver.
- Para errores, incluye una reproducción mínima con datos sintéticos o públicos que puedas
  compartir legítimamente.
- No publiques vulnerabilidades en un *issue*. Repórtalas de forma privada siguiendo
  [la política de seguridad](SECURITY.md).

No incluyas credenciales, datos personales, documentos privados ni corpus confidenciales en
*issues*, discusiones, pruebas, capturas, registros o *pull requests*. Si una credencial fue
expuesta, revócala y rótala antes de continuar.

## Preparar el entorno

Requisitos: Python 3.11 o posterior y `uv`.

```bash
git clone https://github.com/alejandrojlamas/red-privada.git
cd red-privada
cp .env.example .env
make install
make test
```

El archivo `.env` es local y no debe versionarse. Neo4j es opcional; la suite de pruebas utiliza el
flujo local y no necesita credenciales ni acceso a internet.

## Flujo de trabajo

1. Crea una rama breve y descriptiva desde `main`.
2. Mantén el cambio enfocado: una corrección o capacidad coherente por *pull request*.
3. Añade o actualiza pruebas para el comportamiento modificado.
4. Actualiza la documentación cuando cambien la interfaz, la configuración o las garantías del
   sistema.
5. Ejecuta las verificaciones locales y describe sus resultados en el *pull request*.

```bash
make test
make lint
make build
```

No es necesario reformatear archivos ajenos al cambio. Evita incorporar dependencias sin explicar
su propósito, mantenimiento y efecto sobre privacidad, seguridad y reproducibilidad.

## Criterios para datos e IA

Red Privada trabaja con material cuya procedencia importa tanto como el resultado. Toda
contribución que afecte la recolección, extracción, resolución de identidades, puntuación o
generación de señales debe:

- conservar la fuente, la URL y el fragmento de evidencia asociados a cada relación;
- mantener deshabilitadas por defecto las fuentes de red nuevas;
- respetar permisos, licencias, términos de uso, límites de frecuencia y `robots.txt`;
- usar datos sintéticos o redistribuibles en pruebas y ejemplos;
- exponer supuestos, incertidumbre y posibles falsos positivos;
- preservar la revisión humana antes de interpretar o actuar sobre un resultado;
- evitar inferencias adversas sobre personas a partir de coincidencias, ausencia de texto o
  centralidad en el grafo.

Si una opción envía contenido a un proveedor externo de IA, debe ser explícita, documentar qué se
transmite y permanecer desactivada en la configuración segura por defecto.

## Pull requests

Un *pull request* listo para revisión debe incluir:

- el problema y el alcance de la solución;
- las decisiones relevantes y sus límites;
- las pruebas ejecutadas y su resultado;
- cualquier efecto sobre datos, red, privacidad, seguridad o compatibilidad;
- documentación y ejemplos actualizados cuando corresponda.

La revisión puede pedir cambios adicionales antes de integrar una contribución. Este proyecto no
ofrece tiempos de respuesta ni de incorporación garantizados.

## Conducta y seguridad

La participación se rige por el [Código de conducta](CODE_OF_CONDUCT.md). Los fallos funcionales y
las propuestas utilizan los formularios públicos del repositorio; las vulnerabilidades se reportan
exclusivamente mediante
[GitHub Security Advisories](https://github.com/alejandrojlamas/red-privada/security/advisories/new),
de acuerdo con la [política de seguridad](SECURITY.md).
