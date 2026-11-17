# Tríada · contexto completo del proyecto

Documento de traspaso para seguir el desarrollo en Claude Code sin haber visto la conversación original.
`CLAUDE.md` es el resumen corto que Claude Code carga siempre; aquí está todo el detalle.

---

## 1. Qué es y para quién

Tríada es una aplicación **local** (se ejecuta en el ordenador del usuario y se usa desde el navegador) que:

1. **Rastrea** la web de una empresa.
2. **Audita** tres vertientes: **SEO** (posicionamiento orgánico), **SEM** (publicidad de pago, sobre todo Google Ads)
   y **GEO** (visibilidad en buscadores con IA: ChatGPT, Perplexity, AI Overviews, Gemini, Claude).
3. **Prioriza** los problemas y **propone correcciones** concretas.
4. **Aplica** las correcciones (sobre archivos HTML subidos, en una carpeta local o como paquete descargable).
5. **Evalúa lo de fuera de la web** en SEM y GEO (reseñas, menciones, datos de Google Ads, presencia en respuestas de IA)
   y genera un **plan de acciones**.
6. **Exporta** informes y el HTML modificado.

El usuario es una persona de habla hispana (zona horaria Europe/Paris) que quiere optimizar la web de su empresa y,
posiblemente, convertir Tríada en un producto. Toda la interfaz y el código están en español.

**Contexto del usuario:**
- Tiene un proyecto de **Google Cloud** creado y verificado, con **300 € de crédito de prueba gratuita**.
- **No ha dicho todavía en qué plataforma está su web** (WordPress, Shopify, Webflow, código propio…). Es necesario
  para el conector con el CMS.
- Prefiere respuestas directas, en español, y ver las cosas funcionando (por eso existe la demostración).

---

## 2. Cómo ejecutar

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                  # Python ≥ 3.11 (usa tomllib)
triada app                     # abre la app (puerto 8765 o el siguiente libre); = python -m triada.app
triada auditar URL [--ramas seo,sem,geo] [--combinado] [--config triada.toml] [--salida carpeta]
triada config-ejemplo > triada.toml
pytest                         # 22 tests, sin internet (servidores locales y APIs simuladas)
python tests/e2e_interfaz.py   # prueba de la interfaz con Playwright (opcional), deja capturas en ./capturas
```

- Datos del usuario: `~/Triada` o `$TRIADA_DATOS` (análisis, aplicaciones, subidas, proyectos, ajustes).
- Lanzadores de doble clic en la raíz (crean `.venv` e instalan la primera vez):
  `Abrir Triada (Windows).bat` (debe tener fin de línea CRLF), `Abrir Triada (Mac).command`, `abrir-triada-linux.sh`.
  Solo se ha probado el de Linux.
- Demostración: botón «Empezar la demostración». Copia `triada/app/demo_sitio/` (web con errores a propósito) a la
  carpeta de datos, la sirve en un puerto local, carga datos externos de ejemplo (`demo_datos/`) y la analiza.

---

## 3. Arquitectura

```
triada/
  models.py            Page, SiteData, Issue, Fix; pesos de severidad
  config.py            Config (TOML): sitio, organización (schema), campañas SEM
  crawler.py           Rastreo asíncrono con httpx: robots.txt, sitemaps, BFS de enlaces internos, enlaces rotos
  parser.py            Extrae señales del HTML (BeautifulSoup + lxml) y clasifica el tipo de página
  urls.py              Normalización de URLs (quita utm_*, gclid…)
  audits/
    comun.py           Reglas que afectan a varias ramas (HTTPS, enlaces rotos, velocidad, móvil, JS, schema Product)
    seo.py, sem.py, geo.py
    __init__.py        auditar() + resolver_conflictos() (modo combinado) + puntuaciones()
  fixes.py             Correcciones deterministas (sin IA). Marcador «REVISAR» = dato que hay que completar
  report.py            construir_datos() (dict del análisis), render_html() (informe), escribir()
  templates/informe.html
  cli.py
  externo/             Fuera de la web (solo SEM y GEO)
    almacen.py         Persistencia por proyecto (proyectos/<clave>/externo.json) y ajustes globales (ajustes.json)
    cuestionario.py    Señales declarables (id, ramas, peso, pregunta, ayuda, acción, esfuerzo, prioridad)
    importar.py        CSV de Google Ads y Search Console (UTF-8/UTF-16, , ; tab, español/inglés, .zip)
    monitor.py         Monitor de IA: clientes de API, motor simulado, detección de menciones y agregado
    senales.py         Google Places (reseñas) y Wikidata
    capas.py           Nota por capas y cobertura
    acciones.py        Plan de acciones: generar, sincronizar estados, verificación automática, CSV
    servicio.py        Operaciones que usa el servidor; cargar_ejemplo() para la demo
  app/
    server.py          Servidor HTTP stdlib en 127.0.0.1; API JSON; trabajos en segundo plano (hilos)
    servicio.py        Ejecutar/leer/listar/borrar análisis
    aplicar.py         Aplicar en carpeta (copias de seguridad, deshacer), paquete .zip, zip de modificados
    subidas.py         Archivos HTML subidos: emparejar con páginas, aplicar, restablecer, exportar
    static/index.html  SPA en un solo archivo (HTML + CSS + JS vanilla), sin dependencias externas
    demo_sitio/        Web de demostración
    demo_datos/        CSV de ejemplo (Google Ads y Search Console)
tests/                 test_triada.py, test_app.py, test_externo.py, test_subidas.py, servidor.py, e2e_interfaz.py
```

Dependencias: `httpx`, `beautifulsoup4`, `lxml`, `jinja2` (y `pytest` para desarrollo). Evitar añadir más sin necesidad.

---

## 4. Funcionalidad en detalle

### 4.1 Análisis en la web (las tres ramas)

Se rastrean hasta `max_paginas` (200 por defecto). Las reglas generan `Issue(codigo, ramas, severidad, mensaje,
recomendacion, urls)`. Una regla puede afectar a varias ramas.

- **Comunes**: `sin_https`, `enlace_roto`, `respuesta_lenta` (> 800 ms o HTML > 1,5 MB), `sin_viewport`,
  `depende_js`, `schema_producto`.
- **SEO**: robots.txt, sitemap (falta, sucio, páginas fuera), redirecciones en cadena, enlaces a redirecciones, noindex,
  títulos (falta, largo > 60, corto < 20, duplicado), meta descripciones (falta, larga > 160, corta < 70, duplicada),
  H1, canonical, lang, alt, contenido escaso (< 250 palabras), páginas huérfanas, JSON-LD inválido, migas de pan.
- **SEM**: landings de campaña (no encontradas, con error, sin CTA), coincidencia anuncio-página (palabras clave en
  título/H1/primer párrafo), etiquetado (GTM, GA4, Google Ads), Consent Mode v2, GTIN en productos.
- **GEO**: acceso de rastreadores (Googlebot, Bingbot, bots de búsqueda de IA frente a bots de entrenamiento),
  schema Organization, «Quiénes somos», contacto, autoría y fechas en artículos, datos citables, FAQ,
  respuesta directa al inicio. (Según la guía de Google de mayo de 2026, AI Overviews se basa en el SEO normal y no
  requiere llms.txt ni marcado especial; por eso las reglas GEO de contenido son de prioridad baja.)

**Puntuación en la web** (por rama, 0-100): `100 − Σ peso_severidad × (0,5 + 0,5 × alcance)`, con pesos
crítica 15, alta 8, media 4, baja 1,5 y alcance = páginas afectadas / páginas (1 si es de todo el sitio).
Es un indicador interno.

**Modo combinado**: resuelve conflictos entre ramas. Hoy: una landing marcada `solo_anuncios` deja de recibir avisos
SEO (contenido escaso, huérfana, etc.) y se recomienda `noindex, follow` y sacarla del sitemap.
Los problemas que afectan a varias ramas suben de prioridad.

### 4.2 Correcciones (`fixes.py`)

Títulos, meta descripciones, H1, canonical, lang, alt, JSON-LD (Organization, Product, BreadcrumbList),
robots.txt (conserva reglas y desbloquea bots de búsqueda de IA; no toca los de entrenamiento), sitemap.xml,
meta robots noindex para landings de anuncios y fragmento de Consent Mode v2. Cada corrección tiene `id` (F001…)
y `ramas`. Las que contienen «REVISAR» **no se aplican** hasta que el usuario edite el valor.

### 4.3 Formas de aplicar (pestaña «Solucionar» de cada rama)

1. **Subir archivos HTML** (`app/subidas.py`): `.html` sueltos o un `.zip`. Cada archivo se empareja con una página del
   análisis por este orden: canonical → ruta del archivo → nombre del archivo → título → H1 (o a mano). Se trabaja
   sobre una copia (`subidas/<análisis>/modificado`), los originales se conservan y se pueden restablecer. Aplicar dos
   veces no duplica. Rutas peligrosas (`../`) se descartan.
2. **Aplicar en una carpeta** (`app/aplicar.py`): modifica archivos de un sitio estático o copia local, con copia de
   seguridad por aplicación y **Deshacer**.
3. **Paquete de cambios**: .zip con CSV de cambios, archivos generados e instrucciones para WordPress/Shopify.

Exportación desde la misma pestaña: resultados del análisis de la rama (PDF, HTML, CSV, JSON) y **HTML modificado**
(.zip de los archivos subidos o de los archivos tocados en la carpeta).

### 4.4 Fuera de la web (solo SEM y GEO)

**SEO es solo interno** por decisión del usuario: su nota es la de la web.

**Nota por capas** (`externo/capas.py`) para SEM y GEO:
- *En la web*: la puntuación anterior.
- *Señales externas*: media ponderada de las respuestas del cuestionario (sí = 1, en parte = 0,5, no = 0,
  «no aplica» no cuenta). Los datos medidos o importados tienen prioridad sobre lo declarado.
- *Resultado real*: SEM = media de (Nivel de Calidad medio × 10) y (100 − % de gasto sin conversión);
  GEO = % de respuestas de IA que mencionan la marca (último monitor).
- *Fuera* = media de las capas externas con datos. *Global* = peso × web + (1 − peso) × fuera.
  Pesos por defecto: SEM 0,4 y GEO 0,3 (ajustables). Sin datos de fuera → no hay global.
- *Cobertura* = peso + (1 − peso) × media(cobertura del cuestionario, 1 si hay resultado). Provisional si < 70 %.

**Fuentes de datos externos:**
- Cuestionario (`cuestionario.py`): SEM (conversiones principales, mejoradas, listas de clientes, revisión de términos,
  Merchant Center, recursos) y GEO (enlaces del sector, directorios, Perfil de Google, Wikidata, reseñas,
  comparativas, prensa, comunidades, perfiles oficiales).
- Importación CSV: Google Ads «Palabras clave» (NC ponderado por impresiones, % de gasto con NC ≤ 4, lista de NC bajo)
  y «Términos de búsqueda» (gasto sin conversión, lista de negativas); Search Console «Enlaces» (dominios que enlazan,
  alimenta la señal GEO de enlaces). El importador también entiende «Rendimiento» de Search Console, pero ya no se usa.
- Monitor de IA (`monitor.py`): preguntas típicas de clientes × buscadores × repeticiones. Mide % de menciones de la
  marca y de cada competidor, si se cita la web propia, qué dominios cita la IA y qué preguntas recomiendan a la
  competencia. Formatos de API (documentación oficial, nov. 2026):
  - Gemini: `POST https://generativelanguage.googleapis.com/v1beta/interactions`, cabecera `x-goog-api-key`,
    `tools: [{"type": "google_search"}]`; citas en `steps[type=model_output].content[].annotations[type=url_citation]`
    (también se entiende el formato antiguo con `groundingMetadata`). Modelo por defecto `gemini-3.8-flash`.
  - OpenAI: `POST https://api.openai.com/v1/responses`, `tools: [{"type": "web_search"}]`; citas en
    `output[type=message].content[type=output_text].annotations[type=url_citation]`. Modelo por defecto `gpt-5.5`.
  - Perplexity: `POST https://api.perplexity.ai/chat/completions`, modelo `sonar`; fuentes en `search_results`/`citations`.
- Google Places (`senales.py`): `POST https://places.googleapis.com/v1/places:searchText` con `X-Goog-FieldMask`
  (rating y userRatingCount son del nivel Enterprise). Criterio: «sí» si reseñas ≥ mediana de competidores y nota ≥ 4,3.
- Wikidata: `wbsearchentities` (gratis, sin clave); coincidencia exacta de nombre → «sí (verifica)».

**Plan de acciones** (`acciones.py`): se genera desde el cuestionario (respuestas «no»/«en parte» o datos medidos),
el monitor (webs que cita la IA sin tu marca; preguntas donde recomienda a otros) y Google Ads (negativas, Nivel de
Calidad). Cada acción: id estable, ramas, acción, porqué con datos, detalle, prioridad, esfuerzo, verificación,
estado (pendiente/en curso/hecho/descartada), responsable, fecha, evidencia. Reglas al dejar de generarse:
- Dato medido/importado que demuestra la solución → **hecho y verificada**.
- Respuesta «sí» del usuario en el cuestionario → hecho, **no** verificada.
- Web citada por la IA: solo verificada si aparece junto a tu marca en ≥ 50 % de sus citas; que deje de citarse no la
  resuelve. Pregunta: verificada si te menciona en ≥ 34 % de respuestas.
- Acciones obsoletas (de SEO, versiones anteriores) desaparecen sin marcarse como hechas.

### 4.5 Informes y exportación

Informe HTML (Jinja2) completo o por rama: puntuaciones, nota por capas, prioridades, problemas, correcciones,
rastreadores, plan de acciones. PDF = informe con diálogo de impresión. También CSV (correcciones y plan de acciones),
JSON y ZIP con toda la carpeta del análisis.

---

## 5. API local (servidor en 127.0.0.1)

POST y DELETE requieren la cabecera `X-Triada: 1` (impide que otras webs abiertas en el navegador lancen acciones).

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/info` | Versión y carpeta de datos |
| GET | `/api/analisis` · `/api/analisis/<id>` | Lista · detalle (incluye `capas` y `aplicaciones`) |
| DELETE | `/api/analisis/<id>` | Borrar análisis |
| POST | `/api/analizar` | Lanza análisis → `{trabajo}`; progreso en `GET /api/trabajo/<id>` |
| POST | `/api/demo` | Prepara la demostración |
| POST | `/api/solucionar` | `{analisis, ids, ediciones, destino: carpeta|paquete, carpeta}` |
| POST | `/api/deshacer` | `{aplicacion}` |
| GET | `/api/exportar/<id>/<html|csv|json|zip>?rama=&imprimir=1&ver=1` | Exportaciones |
| GET | `/api/descargar/<zip>` | Paquetes generados |
| GET | `/api/modificados/<id>` | Zip de archivos modificados en carpeta |
| GET/POST | `/api/subidas/<id>` · `/subir` `/asignar` `/aplicar` `/restablecer` `/borrar` | Archivos HTML subidos |
| GET | `/api/subidas/<id>/descargar` · `/archivo?nombre=` | HTML modificado (.zip o suelto) |
| GET/POST | `/api/ajustes` | Claves de API (nunca se devuelven), modelos y pesos |
| GET | `/api/proyecto/<p>` · `/acciones.csv` | Estado de fuera de la web · plan en CSV |
| POST | `/api/proyecto/<p>/` + `perfil`, `cuestionario`, `importar`, `importacion/borrar`, `monitor/config`, `monitor/ejecutar`, `resenas`, `wikidata`, `accion`, `ejemplo` | Operaciones de fuera de la web |

Clave de proyecto: `demo` para la demostración; si no, el host sin `www.` ni puerto.

---

## 6. Decisiones de producto (acordadas con el usuario)

1. SEO solo interno. SEM y GEO con tres capas.
2. Nunca inventar datos: «sin datos» antes que 0 o 100; nota global con cobertura.
3. Pesos por defecto SEM 40/60 y GEO 30/70: criterio propio, ajustable, presentado como tal.
4. «Verificada» solo con evidencia medida.
5. Datos simulados o de ejemplo siempre etiquetados; el motor simulado solo en la demo.
6. Solo APIs oficiales. Nada de reseñas o menciones falsas (y la app lo desaconseja explícitamente).
7. Correcciones con «REVISAR» bloqueadas hasta completarlas.
8. Datos y claves de API solo en el ordenador del usuario.
9. Las puntuaciones son indicadores internos: no prometer posiciones ni citas.
10. Formato: primero el motor y la app local; la versión web (Cloud Run) más adelante.

---

## 7. Limitaciones conocidas

- Las APIs externas **no se han probado en real** (el entorno de desarrollo no tenía acceso a internet); solo con
  respuestas simuladas según la documentación. Comprobar los modelos por defecto.
- Los lanzadores de Windows y Mac no se han probado en esos sistemas.
- La velocidad se aproxima con el tiempo de respuesta del servidor; no hay Core Web Vitals reales.
- Aplicar en carpeta reescribe el HTML con BeautifulSoup (`html.parser`): puede cambiar el formato del archivo.
- El emparejamiento de archivos subidos puede fallar si varios archivos comparten título y H1; se corrige a mano.
- No se puede volver a analizar el HTML subido para medir la mejora (sí cuando se aplica en carpeta).
- Las claves de API se guardan en texto plano en `ajustes.json` (local).

---

## 8. Próximos pasos propuestos

1. **Conector con el CMS** (preguntar al usuario la plataforma): WordPress (REST API + Yoast/Rank Math), Shopify, etc.
2. **Google Ads API**: desde el 9-sep-2026 no hay developer token; el acceso se pide en el proyecto de Google Cloud
   (niveles Test 15.000 ops/día en cuentas de prueba, Explorer 2.880 ops/día, Basic con verificación de marca,
   Standard con revisión manual). Con facturación de prueba gratuita Google puede rechazar Explorer/Basic.
3. Prueba real de Gemini/OpenAI/Perplexity/Places/Wikidata y ajuste de parsers.
4. PageSpeed Insights API (Core Web Vitals reales).
5. Redacción de títulos y descripciones con IA (p. ej. Gemini) con revisión humana.
6. Analizar el HTML subido (servirlo en local y rastrearlo) para mostrar la mejora.
7. Versión web multiusuario en Google Cloud Run.

## 9. Historial de versiones

0.1 motor de línea de comandos · 0.2 app local con demo · 0.3 fuera de la web (capas, plan de acciones, datos
externos) · 0.4 SEO solo interno, subida de HTML, exportación de resultados y HTML modificado. Ver `docs/HISTORIAL.md`.
