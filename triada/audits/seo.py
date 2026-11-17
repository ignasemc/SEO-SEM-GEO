"""Auditoría SEO (posicionamiento orgánico)."""

from __future__ import annotations

from ..config import Config
from ..models import SiteData
from .base import Coleccion, agrupar_duplicados, paginas_contenido

S = {"seo"}


def auditar(site: SiteData, cfg: Config, c: Coleccion) -> None:
    paginas = paginas_contenido(site)
    indexables = [p for p in paginas if p.indexable]

    # --- rastreo e indexación
    if site.robots_status != 200:
        c.add("robots_txt_falta", S, "media", "No hay robots.txt accesible.",
              "Publica /robots.txt con la ruta del sitemap y las reglas de rastreo.")
    if not site.sitemaps:
        c.add("sitemap_falta", S, "alta", "No se encontró ningún sitemap XML.",
              "Publica /sitemap.xml con las URLs indexables, decláralo en robots.txt y envíalo en Search Console.")
    else:
        fuera = [p.final_url for p in indexables if p.final_url not in site.urls_sitemap]
        c.add_si(fuera, "fuera_sitemap", S, "baja", "Páginas indexables que no están en el sitemap.",
                 "Inclúyelas en el sitemap para facilitar su descubrimiento.")
        malas = []
        for u in site.urls_sitemap:
            p = site.pages.get(u)
            if p and (p.status != 200 or p.redirecciones or not p.indexable):
                malas.append(u)
        c.add_si(malas, "sitemap_sucio", S, "media",
                 "El sitemap contiene URLs con error, redirección o noindex.",
                 "Deja en el sitemap solo URLs finales, con estado 200 e indexables.")

    cadenas = [p.url for p in site.pages.values() if len(p.redirecciones) > 1]
    c.add_si(cadenas, "cadena_redirecciones", S, "media", "Cadenas de varias redirecciones seguidas.",
             "Redirige directamente al destino final con un único 301.")
    a_redir = sorted({e for p in paginas for e in p.enlaces_internos
                      if (d := site.pages.get(e)) and d.redirecciones})
    c.add_si(a_redir, "enlace_a_redireccion", S, "baja", "Enlaces internos que apuntan a URLs que redirigen.",
             "Actualiza los enlaces para que apunten a la URL final.")

    noindex = [p.final_url for p in paginas if not p.indexable]
    c.add_si(noindex, "noindex", S, "baja", "Páginas marcadas como noindex (revisa que sea intencionado).",
             "Si deben posicionar, elimina el noindex de la meta robots o de la cabecera X-Robots-Tag.")

    # --- títulos y descripciones
    c.add_si([p.final_url for p in indexables if not p.title], "titulo_falta", S, "alta",
             "Páginas sin etiqueta <title>.", "Escribe un título único de 30-60 caracteres con la consulta principal.")
    c.add_si([p.final_url for p in indexables if p.title and len(p.title) > 60], "titulo_largo", S, "baja",
             "Títulos de más de 60 caracteres (Google puede cortarlos).", "Acórtalos dejando lo importante al principio.")
    c.add_si([p.final_url for p in indexables if p.title and len(p.title) < 20], "titulo_corto", S, "baja",
             "Títulos de menos de 20 caracteres.", "Hazlos más descriptivos: qué es, para quién y la marca.")
    dup = agrupar_duplicados(indexables, lambda p: p.title)
    c.add_si([u for g in dup.values() for u in g], "titulo_duplicado", S, "media",
             "Varias páginas comparten el mismo título.", "Cada página necesita un título único.")

    c.add_si([p.final_url for p in indexables if not p.meta_description], "meta_desc_falta", S, "media",
             "Páginas sin meta descripción.", "Escribe 70-160 caracteres que resuman la página e inviten al clic.")
    c.add_si([p.final_url for p in indexables if p.meta_description and len(p.meta_description) > 160],
             "meta_desc_larga", S, "baja", "Meta descripciones de más de 160 caracteres.", "Acórtalas.")
    c.add_si([p.final_url for p in indexables if p.meta_description and len(p.meta_description) < 70],
             "meta_desc_corta", S, "baja", "Meta descripciones de menos de 70 caracteres.", "Amplíalas.")
    dup = agrupar_duplicados(indexables, lambda p: p.meta_description)
    c.add_si([u for g in dup.values() for u in g], "meta_desc_duplicada", S, "media",
             "Meta descripciones repetidas en varias páginas.", "Escribe una descripción única por página.")

    # --- estructura y contenido
    c.add_si([p.final_url for p in indexables if not p.h1], "h1_falta", S, "media",
             "Páginas sin encabezado H1.", "Añade un H1 que describa el tema principal.")
    c.add_si([p.final_url for p in indexables if len(p.h1) > 1], "h1_multiple", S, "baja",
             "Páginas con varios H1.", "Deja un único H1 y usa H2/H3 para las secciones.")
    c.add_si([p.final_url for p in indexables if not p.canonical], "canonical_falta", S, "baja",
             "Páginas sin etiqueta canonical.", "Añade <link rel=\"canonical\"> apuntando a la URL preferida.")
    c.add_si([p.final_url for p in indexables if not p.lang], "lang_falta", S, "baja",
             "El HTML no declara idioma.", 'Añade <html lang="es"> (o el idioma que corresponda).')

    sin_alt = [p.final_url for p in indexables if any(i.alt is None or not i.alt.strip() for i in p.imagenes)]
    c.add_si(sin_alt, "alt_falta", S, "media", "Imágenes sin texto alternativo.",
             "Describe cada imagen relevante en el atributo alt (las decorativas pueden llevar alt=\"\").")

    escasas = [p.final_url for p in indexables if p.palabras < 250 and p.tipo not in ("contacto", "home")]
    c.add_si(escasas, "contenido_escaso", S, "media", "Páginas con menos de 250 palabras de contenido propio.",
             "Amplía con información útil y original: beneficios, casos, preguntas frecuentes, especificaciones.")

    huerfanas = [p.final_url for p in indexables
                 if p.tipo != "home" and site.enlaces_entrantes.get(p.final_url, 0) == 0]
    c.add_si(huerfanas, "pagina_huerfana", S, "media", "Páginas sin ningún enlace interno que apunte a ellas.",
             "Enlázalas desde su categoría, el menú o páginas relacionadas.")

    c.add_si([p.final_url for p in paginas if p.jsonld_errores], "jsonld_invalido", S, "media",
             "Bloques JSON-LD con errores de sintaxis.", "Corrígelos y valida con la Prueba de resultados enriquecidos.")
    migas = [p.final_url for p in indexables if p.tipo in ("producto", "servicio", "articulo")
             and "BreadcrumbList" not in p.tipos_schema()]
    c.add_si(migas, "breadcrumb_falta", S, "baja", "Páginas internas sin schema BreadcrumbList.",
             "Añade migas de pan visibles y su JSON-LD BreadcrumbList.")
