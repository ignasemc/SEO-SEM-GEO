# Tríada · Auditoría y optimización SEO, SEM y GEO

Aplicación que rastrea la web de una empresa, detecta problemas en SEO, SEM y GEO, los ordena por
prioridad y los soluciona: directamente en los archivos de la web (con copia de seguridad y opción de
deshacer) o con un paquete de cambios descargable.

## Abrir la aplicación (doble clic)

Necesitas **Python 3.11 o superior** ([python.org/downloads](https://www.python.org/downloads/)).
En Windows, durante la instalación marca **"Add python.exe to PATH"**.

| Sistema | Archivo |
|---|---|
| Windows | `Abrir Triada (Windows).bat` |
| Mac | `Abrir Triada (Mac).command` (la primera vez: clic derecho → Abrir) |
| Linux | `./abrir-triada-linux.sh` |

La primera vez tarda 1-2 minutos en preparar los componentes (necesita internet). Después se abre sola
en el navegador. Para cerrarla, cierra la ventana negra que se queda abierta.

Todo funciona **en tu ordenador**: los análisis se guardan en la carpeta `Triada` de tu usuario.

### Qué tiene la app

- **Panel**: puntuación de cada vertiente, mejora respecto al análisis anterior, prioridades y evolución.
- **SEO / SEM / GEO**: cada vertiente con dos pestañas:
  - *Análisis*: problemas filtrables por severidad y tablas específicas (páginas, medición y landings, rastreadores de IA).
  - *Solucionar*: correcciones editables y botón **⚡ Solucionar automáticamente**, con tres formas de aplicarlas:
    - **Subir archivos HTML** (páginas sueltas o un .zip de la web): cada archivo se asigna solo a su página
      (por canonical, ruta, nombre, título o H1, o a mano), se corrige una copia y los originales no se tocan.
      Se pueden restablecer los originales en cualquier momento.
    - **Aplicar en una carpeta** de tu ordenador, con copia de seguridad y **Deshacer**.
    - **Descargar paquete de cambios** con instrucciones para WordPress, Shopify o tu desarrollador.
  - *Exportar* (en la propia pestaña Solucionar): resultados del análisis de esa vertiente (PDF, HTML, Excel, JSON)
    y el **HTML modificado** en un .zip, de los archivos subidos o de la carpeta.
- **Informes**: completo o por vertiente, en PDF, HTML, Excel (CSV), JSON o ZIP con todos los archivos.
- **Historial**: todos los análisis guardados.
- **Fuera de la web** (solo SEM y GEO; el **SEO se evalúa solo con lo que hay en la web**):
  - *Nota por capas*: SEM y GEO se evalúan en **en la web**, **señales externas** y **resultado real**.
    Si falta un dato se muestra «sin datos» (nunca un valor inventado) y la nota global va con su **cobertura**.
    Pesos por defecto (en la web / fuera): SEM 40/60, GEO 30/70, ajustables en *Ajustes*.
  - *Plan de acciones*: acciones generadas a partir de tus datos, cada una con el porqué, prioridad, esfuerzo,
    responsable, fecha y evidencia. Exportable a Excel. Cuando un dato nuevo demuestra que una acción está
    resuelta, se marca como **verificada** automáticamente.
  - *Datos externos*: cuestionario de señales, importación de CSV de Google Ads (palabras clave, términos de
    búsqueda) y de Enlaces de Search Console (para GEO), **monitor de IA** (Gemini, ChatGPT, Perplexity con sus APIs
    oficiales), reseñas de Google (Places API) y entidad en Wikidata.

### Claves de API (opcionales)

Se configuran en *Ajustes* y se guardan solo en tu ordenador (`ajustes.json` en la carpeta de datos).

| Servicio | Para qué | Dónde se obtiene |
|---|---|---|
| Gemini | Monitor de IA (búsqueda de Google) | Google AI Studio, vinculada a tu proyecto de Google Cloud |
| OpenAI | Monitor de IA (ChatGPT con búsqueda web) | Plataforma de desarrolladores de OpenAI |
| Perplexity | Monitor de IA (modelo sonar) | Ajustes de API de Perplexity |
| Google Places | Nota y número de reseñas | Google Cloud → activar «Places API (New)» → crear clave |

Wikidata no necesita clave. Todas estas APIs tienen coste por uso: revisa sus precios antes de lanzar muchas consultas.
La demostración usa datos de ejemplo y respuestas simuladas (marcadas como tales), sin coste.
- **Demostración**: una web de ejemplo con errores para ver el ciclo completo sin tocar nada real.

> Aplicar en carpeta funciona con webs estáticas o copias locales. Para WordPress, Shopify, etc. usa el
> paquete de cambios (incluye instrucciones); los conectores directos llegarán en la siguiente fase.

## Línea de comandos (opcional)

Requiere Python 3.11 o superior.

```bash
cd triada
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

```bash
triada app                     # abre la aplicación
```

### Auditoría sin interfaz

```bash
# Las tres vertientes, cada una por separado (modo independiente)
triada auditar https://www.miempresa.com

# Solo una o dos vertientes
triada auditar https://www.miempresa.com --ramas seo
triada auditar https://www.miempresa.com --ramas sem,geo

# Las tres a la vez, resolviendo conflictos entre ellas (recomendado)
triada config-ejemplo > triada.toml      # edítalo con tus datos y campañas
triada auditar --config triada.toml --combinado
```

Opciones: `--salida carpeta`, `--max-paginas N`, `--marca "Nombre"`.

## Qué genera (carpeta `informe-triada/`)

| Archivo | Contenido |
|---|---|
| `informe.html` | Puntuaciones, prioridades, problemas por vertiente y correcciones |
| `correcciones.csv` / `.json` | Cada cambio: página, valor actual, valor propuesto, si requiere revisión |
| `problemas.json` | Todos los problemas con su prioridad |
| `archivos/robots.txt` | robots.txt propuesto (conserva tus reglas, desbloquea bots de búsqueda de IA) |
| `archivos/sitemap.xml` | Sitemap con solo URLs indexables |
| `archivos/jsonld/*.json` | Schema Organization y Product listos para pegar |
| `archivos/snippets/consent-mode-v2.html` | Fragmento de Consent Mode v2 |

Los valores marcados con `«REVISAR»` son datos que el motor no puede conocer (SKU, marca, logo…).

## Modo independiente vs. combinado

- **Independiente**: cada vertiente se audita con sus propias reglas, aunque se contradigan.
- **Combinado**: además, se resuelven conflictos. Hoy: las landings marcadas `solo_anuncios = true`
  dejan de recibir avisos SEO (contenido escaso, página huérfana…). En su lugar se recomienda
  `noindex, follow` y se sacan del sitemap. Los problemas que afectan a varias vertientes suben de prioridad.

## Qué comprueba

- **Común**: HTTPS, enlaces rotos, velocidad del servidor, móvil, contenido que depende de JavaScript y schema de producto.
- **SEO**: robots.txt, sitemap, redirecciones, títulos, descripciones, H1, canonical, idioma, alt, contenido escaso, páginas huérfanas, JSON-LD y migas de pan.
- **SEM**: etiquetado (GTM, GA4, Google Ads), Consent Mode, landings (error, CTA, coincidencia con las palabras clave de la campaña) y GTIN de productos.
- **GEO**: acceso de bots de IA (búsqueda frente a entrenamiento), schema Organization, quiénes somos, contacto, autoría, fechas, datos citables, preguntas frecuentes y respuesta directa.

La puntuación 0-100 es un indicador interno para priorizar. No es una métrica de Google.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Los tests levantan una web de prueba local (`tests/sitio_prueba/`) con errores intencionados.

## Próximas fases

1. Conector con el CMS para aplicar los cambios con aprobación y poder deshacerlos.
2. Redacción de títulos y descripciones con IA (Gemini en Vertex AI) y revisión humana.
3. Search Console, PageSpeed Insights (Core Web Vitals reales) y Google Ads API.
4. Seguimiento de menciones de la marca en ChatGPT, Gemini y Perplexity.
5. App web en Cloud Run.
