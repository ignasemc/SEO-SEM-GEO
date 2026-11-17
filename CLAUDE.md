# Tríada · contexto del proyecto para Claude Code

Aplicación local que audita y optimiza la web de una empresa en tres vertientes: **SEO**, **SEM** y **GEO**
(visibilidad en buscadores con IA). Versión actual: **0.5.0**. Todo el producto (interfaz, código, comentarios
e identificadores) está **en español**: mantén ese idioma.

Documentación de traspaso (léela al empezar a trabajar en el proyecto):
- `docs/CONTEXTO_COMPLETO.md`: funcionalidad, fórmulas, API local, decisiones, limitaciones y próximos pasos en detalle.
- `docs/HISTORIAL.md`: cómo evolucionó el proyecto en la conversación en la que nació.

## Cómo ejecutar

```bash
python -m venv .venv && source .venv/bin/activate      # Python ≥ 3.11 (usa tomllib)
pip install -e ".[dev]"
triada app                     # abre la app en http://127.0.0.1:8765 (o python -m triada.app)
triada auditar https://web.com --combinado --config triada.toml   # modo línea de comandos
pytest                         # 27 tests; levantan servidores locales, no necesitan internet
triada conectar-claude         # registra el conector MCP en Claude Desktop (requiere pip install -e ".[claude]")
python tests/e2e_interfaz.py   # prueba de la interfaz con Playwright (opcional: pip install playwright && playwright install chromium)
```

Los datos del usuario se guardan en `~/Triada` (o en `$TRIADA_DATOS`). Los tests usan carpetas temporales.
Lanzadores de doble clic: `Abrir Triada (Windows).bat` (CRLF), `Abrir Triada (Mac).command`, `abrir-triada-linux.sh`.

## Arquitectura

```
triada/
  crawler.py, parser.py, urls.py   Rastreo asíncrono (httpx), robots.txt, sitemaps, extracción de señales
  audits/                          Reglas: comun.py (multi-rama), seo.py, sem.py, geo.py; __init__.py orquesta
                                   y resuelve conflictos en modo combinado (landings solo-anuncios → noindex)
  fixes.py                         Correcciones deterministas (títulos, metas, JSON-LD, robots, sitemap, Consent Mode)
  report.py + templates/           Datos del análisis (dict serializable) e informe HTML (Jinja2)
  externo/                         Fuera de la web (solo SEM y GEO):
    cuestionario.py                  señales declarables con pesos
    importar.py                      CSV de Google Ads (palabras clave, términos) y Search Console (enlaces)
    monitor.py                       monitor de IA: Gemini (Interactions API + google_search), OpenAI
                                     (Responses + web_search), Perplexity (sonar) y motor simulado para la demo
    senales.py                       Google Places (reseñas) y Wikidata
    capas.py                         nota por capas: en la web / señales externas / resultado real + cobertura
    acciones.py                      plan de acciones con estados y verificación automática
    almacen.py, servicio.py          persistencia por proyecto y ajustes (claves de API en local)
  conector_claude.py               Conector para Claude (servidor MCP local, paquete opcional «mcp» 2.x): herramientas
                                   analizar_web, ver_problemas, ver_correcciones, editar_correccion, aplicar_correcciones,
                                   exportar_informe… La lógica no depende de mcp; las ediciones se guardan en ediciones.json
  instalar_claude.py               `triada conectar-claude`: añade la entrada a claude_desktop_config.json (con copia)
  app/
    server.py                      Servidor stdlib (http.server) solo en 127.0.0.1, API JSON
    servicio.py                    Ejecutar/guardar/listar análisis
    aplicar.py                     Aplicar correcciones en carpeta (copia de seguridad + deshacer) y paquete .zip
    subidas.py                     Aplicar correcciones sobre archivos HTML subidos y exportar el HTML modificado
    static/index.html              Interfaz: SPA en un solo archivo, JS vanilla, sin dependencias externas
    demo_sitio/, demo_datos/       Web de demostración con errores a propósito y CSV de ejemplo
tests/                             pytest (+ servidor.py para la web de prueba)
```

## Decisiones de producto (no cambiarlas sin hablarlo con el usuario)

- **SEO es solo interno**: su nota es la de la web, sin capas externas, cuestionario ni acciones de fuera.
- **SEM y GEO tienen tres capas**: en la web, señales externas y resultado real. Pesos por defecto (en la web/fuera):
  SEM 40/60, GEO 30/70, ajustables. Son un criterio propio, no una cifra oficial: decirlo así en la interfaz.
- **Nunca inventar datos**: si falta un dato se muestra «sin datos», jamás 0 o 100. La nota global solo existe si hay
  datos de fuera y siempre va con su cobertura (provisional si < 70 %).
- **Verificación de acciones**: solo es «verificada» si un dato medido/importado lo demuestra. Lo marcado por el usuario
  en el cuestionario queda «hecho» pero no verificado. Que la IA deje de citar una web no la resuelve.
- **Datos de ejemplo y simulados siempre etiquetados** como tales. El motor simulado solo existe en el proyecto `demo`.
- **Solo APIs oficiales** (nada de automatizar la web de ChatGPT). Nunca generar reseñas o menciones falsas.
- **Las correcciones con «REVISAR» no se aplican** hasta que el usuario complete el dato.
- Las puntuaciones son un indicador interno: no prometer posiciones ni citas.
- Los datos y las claves de API se quedan en el ordenador del usuario.

## Convenciones

- Español en todo: textos, identificadores, mensajes de error. Decimales con coma en la interfaz (`pc()`, `_p()`, `_d()`).
- Sin dependencias nuevas salvo necesidad clara (hoy: httpx, beautifulsoup4, lxml, jinja2; opcional `mcp` para Claude).
- El rastreo se limita al dominio indicado (con o sin www): `urls.mismo_sitio`. Nunca analizar otras webs.
- Las acciones POST/DELETE de la API exigen la cabecera `X-Triada: 1` (protección CSRF frente a otras webs).
- Rutas de archivos de usuario siempre validadas (sin `..`); ver `subidas._nombre_seguro` y `aplicar.ruta_archivo`.
- Clientes de API externos: aceptar `httpx.Client` inyectable y probarlos con `httpx.MockTransport`.
- Tras cambios de interfaz, comprobar con capturas (Playwright) que no hay errores de consola.

## Pendiente / próximos pasos

0. **Conector con Claude (0.5)**: probado con una conversación MCP real por stdio en los tests; falta probarlo en
   Claude Desktop en Windows/Mac del usuario. Las herramientas de «fuera de la web» aún no están en el conector.
1. **Conector con el CMS** del usuario (aplicar cambios directamente): falta saber si su web es WordPress, Shopify,
   Webflow o código propio.
2. **Google Ads API** en lugar de importar CSV. Desde el 9-sep-2026 ya no hay developer token: el acceso va por
   proyecto de Google Cloud (niveles Test / Explorer / Basic / Standard). El usuario tiene proyecto verificado con
   300 € de prueba gratuita; Google puede rechazar Explorer/Basic con facturación de prueba → pasar a facturación de pago.
3. **Probar en real** las APIs externas (Gemini, OpenAI, Perplexity, Places, Wikidata): hasta ahora solo se han probado
   con respuestas simuladas. Revisar los modelos por defecto (`gemini-3.8-flash`, `gpt-5.5`, `sonar`).
4. **Probar los lanzadores** de Windows y Mac (solo se probó el de Linux).
5. PageSpeed Insights API para Core Web Vitals reales; redacción de títulos/descripciones con IA y revisión humana;
   analizar los archivos HTML subidos para medir la mejora; versión web en Cloud Run.
