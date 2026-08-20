# Política de seguridad

## Versiones con soporte

Red Privada es actualmente un proyecto en fase alfa. Solo la rama `main` y la versión `0.1.x`
más reciente reciben correcciones de seguridad. Las versiones anteriores no se mantienen en
paralelo.

## Cómo informar una vulnerabilidad

No publiques detalles que permitan explotar una vulnerabilidad en una incidencia pública. Utiliza
el formulario privado de [avisos de seguridad de GitHub][security-advisories].

Cuando sea posible, incluye:

- la versión o el *commit* afectado;
- el impacto previsto y las condiciones necesarias;
- los pasos mínimos de reproducción;
- las mitigaciones conocidas;
- un canal de contacto en GitHub para dar seguimiento.

No adjuntes credenciales reales, corpus confidenciales ni datos personales. Los informes se
revisarán de buena fe y, cuando se confirme un hallazgo, se coordinará una divulgación
responsable. El proyecto no cuenta con un acuerdo formal de nivel de servicio, por lo que no se
garantiza un plazo de respuesta específico.

## Alcance operativo

Son especialmente valiosos los informes sobre credenciales expuestas, elusión de las directivas
de `robots.txt`, acceso de red inesperado, inyección de contenido en el extractor, servicios Neo4j
expuestos y operaciones de escritura fuera de `data/`. Los errores de contenido o clasificación
que no constituyan una vulnerabilidad de seguridad pueden notificarse mediante una incidencia
ordinaria, siempre sin incluir material sensible.

Las credenciales deben permanecer fuera del repositorio. Si una credencial llega a incorporarse
a un *commit*, revócala y rótala de inmediato: borrar el texto no invalida una clave que ya fue
expuesta.

[security-advisories]: https://github.com/alejandrojlamas/red-privada/security/advisories/new
