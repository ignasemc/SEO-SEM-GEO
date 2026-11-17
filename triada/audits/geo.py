"""Auditoría GEO: visibilidad y citabilidad en buscadores con IA.

Nota: la guía oficial de Google (mayo 2026) dice que AI Overviews y AI Mode se basan en el
SEO normal y que no hacen falta llms.txt, "chunking" ni schema especial. Por eso aquí solo se
comprueba lo que tiene respaldo: acceso de los rastreadores de IA, entidad de marca clara,
señales de confianza (autoría, fechas, contacto) y contenido citable.
"""

from __future__ import annotations

from urllib.robotparser import RobotFileParser

from ..config import Config
from ..models import Page, SiteData
from .base import Coleccion, paginas_contenido

G = {"geo"}

# Bots que recuperan contenido para responder (afectan a aparecer citado)
BOTS_BUSQUEDA = ["OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "Claude-SearchBot", "Claude-User"]
# Bots de entrenamiento de modelos (bloquearlos es una decisión de negocio legítima)
BOTS_ENTRENAMIENTO = ["GPTBot", "ClaudeBot", "CCBot", "Google-Extended", "Applebot-Extended"]


def estado_bots(site: SiteData) -> dict[str, bool]:
    rp = RobotFileParser()
    rp.parse((site.robots_txt or "").splitlines())
    destino = site.url_base + "/"
    return {bot: rp.can_fetch(bot, destino) for bot in ["Googlebot", "Bingbot"] + BOTS_BUSQUEDA + BOTS_ENTRENAMIENTO}


def _es_articulo(p: Page) -> bool:
    return p.tipo == "articulo"


def auditar(site: SiteData, cfg: Config, c: Coleccion) -> None:
    paginas = paginas_contenido(site)
    indexables = [p for p in paginas if p.indexable]
    bots = estado_bots(site)

    if not bots["Googlebot"]:
        c.add("bloquea_googlebot", {"seo", "geo"}, "critica",
              "robots.txt bloquea a Googlebot en la home.",
              "Desbloquéalo: sin rastreo no hay ni resultados orgánicos ni AI Overviews/AI Mode.")
    if not bots["Bingbot"]:
        c.add("bloquea_bingbot", {"seo", "geo"}, "alta", "robots.txt bloquea a Bingbot.",
              "Desbloquéalo: el índice de Bing alimenta a Copilot y a otros asistentes.")
    bloq_busq = [b for b in BOTS_BUSQUEDA if not bots[b]]
    if bloq_busq:
        c.add("bloquea_bots_busqueda_ia", G, "alta",
              f"robots.txt bloquea a bots de búsqueda de IA: {', '.join(bloq_busq)}.",
              "Permítelos si quieres aparecer citado en ChatGPT, Perplexity o Claude cuando buscan en la web.")
    bloq_ent = [b for b in BOTS_ENTRENAMIENTO if not bots[b]]
    if bloq_ent:
        c.add("bloquea_bots_entrenamiento", G, "baja",
              f"robots.txt bloquea a bots de entrenamiento: {', '.join(bloq_ent)}.",
              "Es una decisión de negocio. Bloquearlos evita que tu contenido entrene modelos, pero puede reducir "
              "lo que los modelos 'saben' de tu marca. Bloquear Google-Extended no afecta a AI Overviews.")

    # --- entidad de marca
    home = site.home()
    if home:
        orgs = home.nodos_schema("Organization") + home.nodos_schema("LocalBusiness") + \
            [n for t in ("Corporation", "Store", "OnlineStore", "ProfessionalService") for n in home.nodos_schema(t)]
        if not orgs:
            c.add("organizacion_falta", {"geo", "seo"}, "alta",
                  "La home no tiene schema Organization (ni LocalBusiness).",
                  "Añade JSON-LD Organization con nombre, URL, logo, contacto y sameAs a tus perfiles oficiales. "
                  "Ayuda a buscadores e IA a identificar tu marca como entidad.", [home.final_url])
        else:
            o = orgs[0]
            faltan = [k for k in ("name", "url", "logo", "sameAs") if not o.get(k)]
            if faltan:
                c.add("organizacion_incompleta", {"geo", "seo"}, "media",
                      f"El schema Organization no tiene: {', '.join(faltan)}.",
                      "Complétalo. sameAs debe enlazar a LinkedIn, redes, Wikidata, Google Business Profile, etc.",
                      [home.final_url])

    tipos = {p.tipo for p in paginas}
    if "quienes_somos" not in tipos:
        c.add("quienes_somos_falta", {"geo", "seo"}, "media", "No se encontró página de 'Quiénes somos'.",
              "Crea una página con historia, equipo, ubicación y datos verificables de la empresa (confianza / E-E-A-T).")
    if "contacto" not in tipos and not any("tel" in p.ctas or "mailto" in p.ctas for p in paginas):
        c.add("contacto_falta", {"geo", "seo", "sem"}, "media", "No se encontró página ni datos de contacto.",
              "Publica una página de contacto con dirección, teléfono y email. Es una señal básica de confianza.")

    # --- autoría y frescura
    articulos = [p for p in indexables if _es_articulo(p)]
    c.add_si([p.final_url for p in articulos if not p.autor], "autor_falta", {"geo", "seo"}, "media",
             "Artículos sin autor identificado.",
             "Firma con nombre y biografía del autor (y schema Person). Es clave en temas YMYL.")
    c.add_si([p.final_url for p in articulos if not p.fecha_modificacion], "fecha_falta", G, "baja",
             "Artículos sin fecha de publicación o actualización.",
             "Muestra la fecha y añade datePublished/dateModified al schema.")

    # --- contenido citable (buenas prácticas editoriales; no son requisitos de Google)
    clave = [p for p in indexables if p.tipo in ("servicio", "producto", "articulo") and p.palabras >= 300]
    c.add_si([p.final_url for p in clave if p.cifras == 0], "sin_datos", G, "baja",
             "Páginas largas sin cifras ni datos concretos.",
             "Añade datos propios verificables (precios, plazos, resultados, estudios): es lo que las IA citan.")
    c.add_si([p.final_url for p in indexables if p.tipo in ("servicio", "producto") and not p.tiene_faq],
             "faq_falta", G, "baja", "Servicios/productos sin sección de preguntas frecuentes.",
             "Responde las dudas reales de los clientes (precio, plazos, garantías) con respuestas directas.")
    sin_resp = [p.final_url for p in clave if not p.primer_parrafo or len(p.primer_parrafo.split()) > 80]
    c.add_si(sin_resp, "sin_respuesta_directa", G, "baja",
             "Páginas que no resumen la idea principal al inicio.",
             "Abre con 1-2 frases que respondan directamente a qué es / qué ofreces / para quién.")
