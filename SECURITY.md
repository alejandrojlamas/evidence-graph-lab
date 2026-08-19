# Política de seguridad

## Versiones con soporte

El proyecto está en etapa alfa. Solo la rama `main` y la versión `0.1.x` más reciente reciben
correcciones de seguridad. No se mantienen versiones anteriores en paralelo.

## Reportar una vulnerabilidad

No publique detalles explotables en un issue. Use el formulario privado de
[GitHub Security Advisories](https://github.com/alejandrojlamas/Red-privada/security/advisories/new).

Incluya, cuando sea posible:

- versión o commit afectado;
- impacto esperado y condiciones necesarias;
- pasos mínimos para reproducirlo;
- mitigaciones conocidas;
- un medio de contacto para dar seguimiento dentro de GitHub.

No adjunte credenciales reales, corpus confidenciales ni datos personales. El reporte se revisará
de buena fe y se coordinará una divulgación responsable cuando el hallazgo sea confirmado. Al ser
un proyecto mantenido sin un SLA formal, no se garantiza un plazo de respuesta específico.

## Alcance operativo

Considere especialmente filtraciones de claves, evasión de `robots.txt`, accesos de red no
previstos, inyección de contenido hacia el extractor, exposición de Neo4j y escritura de archivos
fuera de `data/`. Los errores de contenido o clasificación que no impliquen una vulnerabilidad
pueden reportarse como issues ordinarios sin incluir material sensible.

Las claves deben vivir fuera del repositorio. Si una credencial aparece en un commit, revóquela y
rótela de inmediato; eliminar el texto del archivo no invalida una clave ya expuesta.
